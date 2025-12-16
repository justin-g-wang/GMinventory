import os
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo
import smtplib
from email.message import EmailMessage

from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2 import sql, errorcodes

app = Flask(__name__)
app.secret_key = "575GummyMaker123"  

PST_ZONE = ZoneInfo("America/Los_Angeles")
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "50"))
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@app.template_filter("comma")
def format_comma(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return value

def to_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def log_dashboard_event(cursor, message, username=None):
    cursor.execute(
        "INSERT INTO dashboard_history (message, username) VALUES (%s, %s)",
        (message, username),
    )

def send_low_stock_email(item_number, lot, remaining_qty, unit, item_name, supplier, triggered_by):
    """Send a notification when inventory dips below threshold."""
    recipient = os.getenv("LOW_STOCK_EMAIL", "jwang@gummymaker.us")
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_SENDER") or username
    port = int(os.getenv("SMTP_PORT", "587"))
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() not in ("0", "false", "no")

    if not (host and username and password and sender):
        print("Low-stock email skipped: SMTP settings incomplete.")
        return

    subject = f"Low Stock Alert: {item_name or item_number}"
    headline = f"LOW STOCK ON ITEM {item_name or item_number} ORDER FROM {supplier or 'supplier'}"
    details = f"Item {item_number} (lot {lot}) now has {remaining_qty} {unit} remaining."
    body = f"{headline}\n{details}\n\nTriggered by: {triggered_by or 'system'}"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(message)
        print(f"Low-stock email sent for {item_number} lot {lot}.")
    except Exception as exc:
        print(f"Low-stock email failed: {exc}")

# -------------------------
# DATABASE SETUP
# -------------------------
def connect_db():
    db_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if db_url:
        return psycopg2.connect(db_url)

    db_params = dict(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "gummy_inventory"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )

    try:
        return psycopg2.connect(**db_params)
    except psycopg2.OperationalError as exc:
        if _is_missing_database_error(exc):
            create_database_if_missing(db_params)
            return psycopg2.connect(**db_params)
        raise

def _is_missing_database_error(exc):
    if getattr(exc, "pgcode", None) == errorcodes.INVALID_CATALOG_NAME:
        return True
    message = (getattr(exc, "pgerror", None) or str(exc) or "").lower()
    return "does not exist" in message

def create_database_if_missing(db_params):
    """Create the target database if it does not exist."""
    target_db = db_params["dbname"]
    admin_db = os.getenv("POSTGRES_DEFAULT_DB", "postgres")
    admin_params = db_params.copy()
    admin_params["dbname"] = admin_db

    admin_conn = psycopg2.connect(**admin_params)
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
            if cursor.fetchone():
                return
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db))
            )
    finally:
        admin_conn.close()

def format_timestamp_pst(ts):
    """Convert timestamp (string or datetime) into PST display text."""
    if not ts:
        return ts
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            return ts
    else:
        dt = ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(PST_ZONE).strftime("%Y-%m-%d %I:%M %p %Z")

def init_db():
    conn = connect_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            item_number TEXT NOT NULL,
            name TEXT,
            quantity NUMERIC,
            unit TEXT,
            lot TEXT NOT NULL,
            supplier TEXT,
            exp TEXT,
            PRIMARY KEY (item_number, lot)
        )
    """)

    # MOVEMENT HISTORY TABLE
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id SERIAL PRIMARY KEY,
            item_number TEXT,
            lot TEXT,
            change NUMERIC,
            remaining NUMERIC,
            unit TEXT,
            action_type TEXT,
            username TEXT,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # USERS TABLE
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            due_date DATE,
            status TEXT DEFAULT 'Pending',
            completed_on DATE,
            bags_bottles INTEGER,
            gummies INTEGER,
            storage_status TEXT,
            quantity_unit TEXT,
            completed_bags INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_history (
            id SERIAL PRIMARY KEY,
            message TEXT,
            username TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

init_db()

def login_required(route_function):
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return route_function(*args, **kwargs)
    wrapper.__name__ = route_function.__name__
    return wrapper

def login_required(route_function):
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return route_function(*args, **kwargs)
    wrapper.__name__ = route_function.__name__
    return wrapper


# -------------------------
# ROUTES
# -------------------------

@app.route("/")
def index():
    if "user" not in session:
        return redirect("/login")
    return redirect("/dashboard")

@app.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard():
    conn = connect_db()
    c = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form["name"].strip()
            notes = request.form.get("description", "").strip()
            due_date_str = request.form.get("due_date", "").strip()
            bags_bottles = to_int_or_none(request.form.get("bags_bottles"))
            gummies = to_int_or_none(request.form.get("gummies"))
            quantity_unit = request.form.get("quantity_unit", "Bags")
            storage_status = request.form.get("storage_status", "Pick")
            completed_bags = to_int_or_none(request.form.get("completed_bags"))

            if not name:
                conn.close()
                return "Project name is required."

            due_date = None
            if due_date_str:
                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                except ValueError:
                    conn.close()
                    return "Invalid due date format."

            c.execute(
                """
                INSERT INTO projects (name, description, due_date, bags_bottles, gummies, storage_status, quantity_unit, completed_bags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (name, notes, due_date, bags_bottles, gummies, storage_status, quantity_unit, completed_bags),
            )
            log_dashboard_event(
                c,
                f"Created project '{name}' targeting {bags_bottles or 0} {quantity_unit}, due {due_date or 'unspecified'}",
                session.get("user"),
            )
            conn.commit()
            conn.close()
            return redirect("/dashboard")

        if action == "update_status":
            project_id = request.form.get("project_id")
            status = request.form.get("status", "Pending")
            completion_date_str = request.form.get("completion_date", "").strip()
            completion_date = None
            if completion_date_str:
                try:
                    completion_date = datetime.strptime(completion_date_str, "%Y-%m-%d").date()
                except ValueError:
                    conn.close()
                    return "Invalid completion date."

            try:
                project_id_int = int(project_id)
            except (TypeError, ValueError):
                conn.close()
                return "Invalid project id."
            c.execute("SELECT name FROM projects WHERE id = %s", (project_id_int,))
            project_row = c.fetchone()
            project_name = project_row[0] if project_row else f"#{project_id_int}"

            if status == "Completed":
                c.execute(
                    """
                    UPDATE projects
                    SET status = %s,
                        completed_on = %s
                    WHERE id = %s
                    """,
                    (status, completion_date, project_id_int),
                )
            else:
                c.execute(
                    """
                    UPDATE projects
                    SET status = %s
                    WHERE id = %s
                    """,
                    (status, project_id_int),
                )
            log_dashboard_event(
                c,
                f"Marked project '{project_name}' as {status}",
                session.get("user"),
            )
            conn.commit()
            conn.close()
            return redirect("/dashboard")

        if action == "edit":
            project_id = request.form.get("project_id")
            name = request.form.get("name", "").strip()
            description = request.form.get("description", "").strip()
            bags_bottles = to_int_or_none(request.form.get("bags_bottles"))
            gummies = to_int_or_none(request.form.get("gummies"))
            storage_status = request.form.get("storage_status", "Pick")
            quantity_unit = request.form.get("quantity_unit", "Bags")
            completed_bags = to_int_or_none(request.form.get("completed_bags"))
            due_date_str = request.form.get("due_date", "").strip()

            if not name:
                conn.close()
                return "Project name is required."

            due_date = None
            if due_date_str:
                try:
                    due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                except ValueError:
                    conn.close()
                    return "Invalid due date."

            try:
                project_id_int = int(project_id)
            except (TypeError, ValueError):
                conn.close()
                return "Invalid project id."

            c.execute(
                "SELECT name FROM projects WHERE id = %s",
                (project_id_int,),
            )
            c.execute(
                """
                UPDATE projects
                SET name = %s,
                    description = %s,
                    due_date = %s,
                    bags_bottles = %s,
                    gummies = %s,
                    storage_status = %s,
                    quantity_unit = %s,
                    completed_bags = %s
                WHERE id = %s
                """,
                (
                    name,
                    description,
                    due_date,
                    bags_bottles,
                    gummies,
                    storage_status,
                    quantity_unit,
                    completed_bags,
                    project_id_int,
                ),
            )
            log_dashboard_event(
                c,
                f"Edited project '{name}': quantity {bags_bottles or 0} {quantity_unit}, produced {completed_bags or 0}, due {due_date or 'N/A'}",
                session.get("user"),
            )
            conn.commit()
            conn.close()
            return redirect("/dashboard")

        if action == "delete":
            project_id = request.form.get("project_id")
            try:
                project_id_int = int(project_id)
            except (TypeError, ValueError):
                conn.close()
                return "Invalid project id."
            c.execute("SELECT name FROM projects WHERE id = %s", (project_id_int,))
            project_row = c.fetchone()
            project_name = project_row[0] if project_row else f"#{project_id_int}"
            c.execute("DELETE FROM projects WHERE id = %s", (project_id_int,))
            log_dashboard_event(c, f"Deleted project '{project_name}'", session.get("user"))
            conn.commit()
            conn.close()
            return redirect("/dashboard")

    c.execute(
        """
        SELECT id, name, description, due_date, status, bags_bottles, gummies, storage_status, quantity_unit, completed_bags, created_at
        FROM projects
        WHERE status <> 'Completed' OR status IS NULL
        ORDER BY due_date NULLS LAST, created_at
        """
    )
    active_rows = c.fetchall()

    c.execute(
        """
        SELECT id, name, description, due_date, status, bags_bottles, gummies, storage_status, quantity_unit, completed_bags, completed_on, created_at
        FROM projects
        WHERE status = 'Completed'
        ORDER BY created_at DESC
        """
    )
    completed_rows = c.fetchall()
    conn.close()

    def map_projects(rows, include_completed_fields=False):
        mapped = []
        for row in rows:
            due_raw = row[3]
            due_date = due_raw.strftime("%Y-%m-%d") if due_raw else None
            due_status = "ok"
            if due_raw and due_raw < date.today():
                due_status = "overdue"
            total_quantity = row[5] or 0
            completed = row[9] or 0
            progress = 0
            if total_quantity and total_quantity > 0:
                progress = min(100, max(0, int((completed / total_quantity) * 100)))
            mapped.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "due_date": due_raw,
                    "due_display": due_date or "TBD",
                    "due_status": due_status,
                    "status": row[4] or "Pending",
                    "bags_bottles": total_quantity,
                    "gummies": row[6],
                    "storage_status": row[7],
                    "quantity_unit": row[8] or "Bags",
                    "completed_bags": completed,
                    "progress_percent": progress,
                    "completed_on": row[10].strftime("%Y-%m-%d") if include_completed_fields and row[10] else None,
                }
            )
        return mapped

    active_projects = map_projects(active_rows)
    completed_projects = map_projects(completed_rows, include_completed_fields=True)

    stats = {
        "total": len(active_projects) + len(completed_projects),
        "pending": len(active_projects),
        "completed": len(completed_projects),
    }

    return render_template(
        "dashboard.html",
        projects=active_projects,
        completed_projects=completed_projects,
        stats=stats,
    )

@app.route("/projects/completed")
@login_required
def completed_projects_view():
    conn = connect_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, name, description, due_date, status, bags_bottles, gummies, storage_status, quantity_unit, completed_bags, completed_on, created_at
        FROM projects
        WHERE status = 'Completed'
        ORDER BY completed_on DESC NULLS LAST, created_at DESC
        """
    )
    rows = c.fetchall()
    conn.close()

    projects = []
    for row in rows:
        projects.append(
            {
                "id": row[0],
                "name": row[1],
                "description": row[2],
                "due_date": row[3].strftime("%Y-%m-%d") if row[3] else None,
                "status": row[4],
                "bags_bottles": row[5],
                "gummies": row[6],
                "storage_status": row[7],
                "quantity_unit": row[8] or "Bags",
                "completed_bags": row[9],
                "completed_on": row[10].strftime("%Y-%m-%d") if row[10] else None,
            }
        )

    return render_template("completed_projects.html", completed_projects=projects)

@app.route("/dashboard/history")
@login_required
def dashboard_history():
    conn = connect_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT message, username, created_at
        FROM dashboard_history
        ORDER BY created_at DESC
        LIMIT 50
        """
    )
    rows = c.fetchall()
    conn.close()
    entries = [
        {
            "message": row[0],
            "username": row[1],
            "timestamp": format_timestamp_pst(row[2]),
        }
        for row in rows
    ]
    return render_template("dashboard_history.html", entries=entries)

@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def new_project():
    if request.method == "POST":
        name = request.form["name"].strip()
        notes = request.form.get("description", "").strip()
        due_date_str = request.form.get("due_date", "").strip()
        bags_bottles = to_int_or_none(request.form.get("bags_bottles"))
        gummies = to_int_or_none(request.form.get("gummies"))
        storage_status = request.form.get("storage_status", "Pick")
        quantity_unit = request.form.get("quantity_unit", "Bags")
        completed_bags = to_int_or_none(request.form.get("completed_bags"))

        if not name:
            return "Project name is required."

        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
            except ValueError:
                return "Invalid due date format."

        conn = connect_db()
        c = conn.cursor()
        c.execute(
            """
                INSERT INTO projects (name, description, due_date, bags_bottles, gummies, storage_status, quantity_unit, completed_bags)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (name, notes, due_date, bags_bottles, gummies, storage_status, quantity_unit, completed_bags),
            )
        log_dashboard_event(
            c,
            f"Created project '{name}' targeting {bags_bottles or 0} {quantity_unit}, due {due_date or 'unspecified'}",
            session.get("user"),
        )
        conn.commit()
        conn.close()
        return redirect("/dashboard")

    return render_template("add_project.html")

@app.route("/current")
@login_required
def current_inventory():
    conn = connect_db()
    c = conn.cursor()
    order = request.args.get("sort", "item_number")
    direction = request.args.get("direction", "asc").lower()
    valid_columns = {"item_number": "item_number", "name": "name"}
    column = valid_columns.get(order, "item_number")
    direction_sql = "DESC" if direction == "desc" else "ASC"
    c.execute(f"""
        SELECT item_number, name, quantity, unit, lot, supplier, exp
        FROM inventory
        ORDER BY {column} {direction_sql}, lot
    """)
    items = c.fetchall()
    conn.close()
    return render_template("current_inventory.html", items=items, sort_column=column, sort_direction=direction_sql.lower())

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_item():
    if request.method == "POST":
        item_number = request.form["item_number"]
        name = request.form["name"]
        quantity = float(request.form["quantity"])
        unit = request.form["unit"]
        lot = request.form["lot"]
        supplier = request.form.get("supplier") or ""
        exp = request.form["exp"]

        conn = connect_db()
        c = conn.cursor()

        # insert into current inventory table (upsert by item + lot)
        c.execute(
            """
            INSERT INTO inventory (item_number, name, quantity, unit, lot, supplier, exp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(item_number, lot)
            DO UPDATE SET
                name = excluded.name,
                unit = excluded.unit,
                supplier = excluded.supplier,
                exp = excluded.exp,
                quantity = inventory.quantity + excluded.quantity
            """,
            (item_number, name, quantity, unit, lot, supplier, exp),
        )
        c.execute(
            """
            SELECT quantity FROM inventory
            WHERE item_number = %s AND lot = %s
            """,
            (item_number, lot),
        )
        remaining_row = c.fetchone()
        remaining_qty = remaining_row[0] if remaining_row else quantity
    
        #inserts into history
        c.execute("""
        INSERT INTO history (item_number, lot, change, remaining, unit, action_type, username)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (item_number, lot, quantity, remaining_qty, unit, "ADD", session["user"]))

        conn.commit()
        conn.close()

        return redirect("/current")

    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT item_number, name FROM inventory ORDER BY item_number")
    items = c.fetchall()
    conn.close()

    return render_template("add_item.html", items=items)

@app.route("/remove", methods=["GET", "POST"])
@login_required
def remove_item():
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT item_number, name FROM inventory")
    items = c.fetchall()
    conn.close()

    if request.method == "POST":
        item_number = request.form["item_number"]
        lot = request.form["lot"]
        qty_remove = int(request.form["quantity"])

        conn = connect_db()
        c = conn.cursor()

        # Get current quantity + unit for that lot
        c.execute("""
            SELECT quantity, unit, name, supplier FROM inventory 
            WHERE item_number = %s AND lot = %s
        """, (item_number, lot))
        row = c.fetchone()

        if not row:
            conn.close()
            return "ERROR: Lot does not exist."

        current_qty, unit, item_name, supplier = row

        # ⚠️ Block removal if quantity too high
        if qty_remove > current_qty:
            conn.close()
            return f"ERROR: Cannot remove {qty_remove} {unit}. Only {current_qty} {unit} available!"

        # Update inventory
        new_qty = current_qty - qty_remove
        c.execute("""
            UPDATE inventory 
            SET quantity = %s
            WHERE item_number = %s AND lot = %s
        """, (new_qty, item_number, lot))

        # Log to history
        c.execute("""
            INSERT INTO history (item_number, lot, change, remaining, unit, action_type, username)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (item_number, lot, -qty_remove, new_qty, unit, "REMOVE", session["user"]))

        conn.commit()
        conn.close()

        if new_qty < LOW_STOCK_THRESHOLD:
            send_low_stock_email(
                item_number,
                lot,
                new_qty,
                unit,
                item_name,
                supplier,
                session.get("user"),
            )

        return redirect("/current")

    return render_template("remove_item.html", items=items)


@app.route("/adjust", methods=["GET", "POST"])
@login_required
def adjust_item():
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT item_number, name FROM inventory")
    items = c.fetchall()
    conn.close()

    if request.method == "POST":
        item_number = request.form["item_number"]
        lot = request.form["lot"]
        new_quantity = int(request.form["new_quantity"])
        new_unit = request.form["unit"]
        description = request.form.get("description", "").strip()

        conn = connect_db()
        c = conn.cursor()

        # Get current quantity + unit
        c.execute("""
            SELECT quantity, unit FROM inventory
            WHERE item_number = %s AND lot = %s
        """, (item_number, lot))
        row = c.fetchone()

        if not row:
            conn.close()
            return "ERROR: Item/Lot not found"

        old_quantity, old_unit = row

        # Update inventory
        c.execute("""
            UPDATE inventory SET quantity = %s, unit = %s
            WHERE item_number = %s AND lot = %s
        """, (new_quantity, new_unit, item_number, lot))

        # Log to history
        change = new_quantity - old_quantity
        action_text = "ADJUST"
        if description:
            action_text = f"ADJUST ({description})"
        c.execute("""
        INSERT INTO history (item_number, lot, change, remaining, unit, action_type, username)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (item_number, lot, change, new_quantity, new_unit, action_text, session["user"]))

        conn.commit()
        conn.close()

        return redirect("/current")

    return render_template("adjust_item.html", items=items)


@app.route("/lookup_item/<item_number>")
@login_required
def lookup_item(item_number):
    conn = connect_db()
    c = conn.cursor()
    c.execute("""
        SELECT item_number, name, unit, supplier, exp
        FROM inventory WHERE item_number = %s
    """, (item_number,))
    row = c.fetchone()
    conn.close()

    if row:
        return {
            "found": True,
            "item_number": row[0],
            "name": row[1],
            "unit": row[2],
            "supplier": row[3],
            "exp": row[4]
        }
    else:
        return {"found": False}
    
@app.route("/get_lots/<item_number>")
@login_required
def get_lots(item_number):
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT lot FROM inventory WHERE item_number = %s", (item_number,))
    lots = [row[0] for row in c.fetchall()]
    conn.close()

    return {"lots": lots}

@app.route("/lot_info/<item>/<lot>")
@login_required
def lot_info(item, lot):
    conn = connect_db()
    c = conn.cursor()
    c.execute("""
        SELECT quantity, unit 
        FROM inventory
        WHERE item_number = %s AND lot = %s
    """, (item, lot))
    row = c.fetchone()
    conn.close()

    if row:
        return {
            "found": True,
            "quantity": row[0],
            "unit": row[1]
        }
    return {"found": False}

@app.route("/history")
@login_required
def history():
    search_term = request.args.get("search")

    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT item_number, name FROM inventory ORDER BY item_number")
    inventory_items = c.fetchall()

    # Full history
    c.execute("SELECT * FROM history ORDER BY timestamp DESC")
    logs = [
        (*row[:8], format_timestamp_pst(row[8]))
        for row in c.fetchall()
    ]

    search_results = None
    if search_term:
        c.execute("SELECT * FROM history WHERE item_number = %s ORDER BY timestamp DESC",
                  (search_term,))
        search_results = [
            (*row[:8], format_timestamp_pst(row[8]))
            for row in c.fetchall()
        ]

    conn.close()

    return render_template("history.html",
                           logs=logs,
                           search_results=search_results,
                           search_term=search_term,
                           inventory_items=inventory_items)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        if not EMAIL_REGEX.fullmatch(username):
            return "Username must be a valid email address."
        password = generate_password_hash(
        request.form["password"],
        method="pbkdf2:sha256"
    )
        conn = connect_db()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password) VALUES (%s, %s)", 
                      (username, password))
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            conn.close()
            return "Username already taken."

        conn.close()
        return redirect("/login")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        if not EMAIL_REGEX.fullmatch(username):
            return "Please enter a valid email."
        password = request.form["password"]

        conn = connect_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            session["user"] = username
            return redirect("/dashboard")   
        else:
            return "Invalid username or password."

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
