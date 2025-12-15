from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

app = Flask(__name__)
app.secret_key = "575GummyMaker123"  

#codex is op
# -------------------------
# DATABASE SETUP
# -------------------------
def connect_db():
    return sqlite3.connect("inventory.db")

def init_db():
    conn = connect_db()
    c = conn.cursor()

    def create_inventory_table():
        c.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                item_number TEXT NOT NULL,
                name TEXT,
                quantity INTEGER,
                unit TEXT,
                lot TEXT NOT NULL,
                mfg_date TEXT,
                supplier TEXT,
                exp TEXT,
                PRIMARY KEY (item_number, lot)
            )
        """)

    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='inventory'")
    if c.fetchone():
        c.execute("PRAGMA table_info(inventory)")
        columns = [row[1] for row in c.fetchall()]
        if "id" in columns:
            c.execute("ALTER TABLE inventory RENAME TO inventory_old")
            create_inventory_table()
            c.execute("""
                INSERT INTO inventory (item_number, name, quantity, unit, lot, mfg_date, supplier, exp)
                SELECT item_number, name, quantity, unit, lot, mfg_date, supplier, exp
                FROM inventory_old
            """)
            c.execute("DROP TABLE inventory_old")
        else:
            create_inventory_table()
    else:
        create_inventory_table()

    # MOVEMENT HISTORY TABLE
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_number TEXT,
            lot TEXT,
            change INTEGER,
            remaining INTEGER,
            unit TEXT,
            action_type TEXT,
            username TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)


    # USERS TABLE
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
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
    return redirect("/current")   # or render a dashboard page if you have one

@app.route("/current")
@login_required
def current_inventory():
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT * FROM inventory ORDER BY item_number, lot")
    items = c.fetchall()
    conn.close()
    return render_template("current_inventory.html", items=items)

@app.route("/add", methods=["GET", "POST"])
@login_required
def add_item():
    if request.method == "POST":
        item_number = request.form["item_number"]
        name = request.form["name"]
        quantity = int(request.form["quantity"])
        unit = request.form["unit"]
        lot = request.form["lot"]
        mfg_date = request.form["mfg_date"]
        supplier = request.form["supplier"]
        exp = request.form["exp"]

        conn = connect_db()
        c = conn.cursor()

        # insert into current inventory table (upsert by item + lot)
        c.execute(
            """
            INSERT INTO inventory (item_number, name, quantity, unit, lot, mfg_date, supplier, exp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_number, lot)
            DO UPDATE SET
                name = excluded.name,
                unit = excluded.unit,
                mfg_date = excluded.mfg_date,
                supplier = excluded.supplier,
                exp = excluded.exp,
                quantity = inventory.quantity + excluded.quantity
            """,
            (item_number, name, quantity, unit, lot, mfg_date, supplier, exp),
        )
        c.execute(
            """
            SELECT quantity FROM inventory
            WHERE item_number = ? AND lot = ?
            """,
            (item_number, lot),
        )
        remaining_row = c.fetchone()
        remaining_qty = remaining_row[0] if remaining_row else quantity
    
        #inserts into history
        c.execute("""
        INSERT INTO history (item_number, lot, change, remaining, unit, action_type, username)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (item_number, lot, quantity, remaining_qty, unit, "ADD", session["user"]))

        conn.commit()
        conn.close()

        return redirect("/current")

    return render_template("add_item.html")

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
            SELECT quantity, unit FROM inventory 
            WHERE item_number = ? AND lot = ?
        """, (item_number, lot))
        row = c.fetchone()

        if not row:
            conn.close()
            return "ERROR: Lot does not exist."

        current_qty, unit = row

        # ⚠️ Block removal if quantity too high
        if qty_remove > current_qty:
            conn.close()
            return f"ERROR: Cannot remove {qty_remove} {unit}. Only {current_qty} {unit} available!"

        # Update inventory
        new_qty = current_qty - qty_remove
        c.execute("""
            UPDATE inventory 
            SET quantity = ?
            WHERE item_number = ? AND lot = ?
        """, (new_qty, item_number, lot))

        # Log to history
        c.execute("""
            INSERT INTO history (item_number, lot, change, remaining, unit, action_type, username)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (item_number, lot, -qty_remove, new_qty, unit, "REMOVE", session["user"]))

        conn.commit()
        conn.close()

        return redirect("/current")

    return render_template("remove_item.html", items=items)


@app.route("/adjust", methods=["GET", "POST"])
@login_required
def adjust_item():
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT DISTINCT item_number FROM inventory")
    items = c.fetchall()
    conn.close()

    if request.method == "POST":
        item_number = request.form["item_number"]
        lot = request.form["lot"]
        new_quantity = int(request.form["new_quantity"])

        conn = connect_db()
        c = conn.cursor()

        # Get current quantity + unit
        c.execute("""
            SELECT quantity, unit FROM inventory
            WHERE item_number = ? AND lot = ?
        """, (item_number, lot))
        row = c.fetchone()

        if not row:
            conn.close()
            return "ERROR: Item/Lot not found"

        old_quantity, unit = row

        # Update inventory
        c.execute("""
            UPDATE inventory SET quantity = ?
            WHERE item_number = ? AND lot = ?
        """, (new_quantity, item_number, lot))

        # Log to history
        change = new_quantity - old_quantity
        c.execute("""
        INSERT INTO history (item_number, lot, change, remaining, unit, action_type, username)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (item_number, lot, change, new_quantity, unit, "ADJUST", session["user"]))

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
        SELECT item_number, name, unit, supplier, mfg_date, exp
        FROM inventory WHERE item_number = ?
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
            "mfg_date": row[4],
            "exp": row[5]
        }
    else:
        return {"found": False}
    
@app.route("/get_lots/<item_number>")
@login_required
def get_lots(item_number):
    conn = connect_db()
    c = conn.cursor()
    c.execute("SELECT lot FROM inventory WHERE item_number = ?", (item_number,))
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
        WHERE item_number = ? AND lot = ?
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

    # Full history
    c.execute("SELECT * FROM history ORDER BY timestamp DESC")
    logs = c.fetchall()

    search_results = None
    if search_term:
        c.execute("SELECT * FROM history WHERE item_number = ? ORDER BY timestamp DESC",
                  (search_term,))
        search_results = c.fetchall()

    conn.close()

    return render_template("history.html",
                           logs=logs,
                           search_results=search_results,
                           search_term=search_term)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(
        request.form["password"],
        method="pbkdf2:sha256"
    )
        conn = connect_db()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)", 
                      (username, password))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return "Username already taken."

        conn.close()
        return redirect("/login")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = connect_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user[2], password):
            session["user"] = username
            return redirect("/current")   # << send user to inventory page
        else:
            return "Invalid username or password."

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/login")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
