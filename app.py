"""Online Book Store - Flask + SQLite backend (JSON API).

Roles: customer, staff, admin. Payment: Cash on Delivery only.
All rules are checked on the server. The browser is never trusted.
"""
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IST = timezone(timedelta(hours=5, minutes=30))  # all times are stored in Indian time

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["DATABASE"] = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "bookstore.db"))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE") == "1"
app.config["SEED_DEMO"] = os.environ.get("SEED_DEMO", "1") == "1"

MAX_QTY_PER_BOOK = 10
RETURN_WINDOW_DAYS = 7
ACTIVE_SALE_STATUSES = ("placed", "packed", "shipped", "delivered")  # counted as sales
CUSTOMER_CANCEL_REASONS = ["Ordered by mistake", "Found a better price", "Delivery is too late",
                           "Want to change the address", "Other"]
RETURN_REASONS = ["Wrong book received", "Damaged or torn pages", "Not as described",
                  "No longer needed", "Other"]


# ---------------------------------------------------------------- helpers
class ApiError(Exception):
    """An error we show to the user as JSON."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def now_str():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return datetime.now(IST).strftime("%Y-%m-%d")


def get_db():
    """One database connection per request. Autocommit; we use BEGIN ourselves."""
    if "db" not in g:
        db = sqlite3.connect(app.config["DATABASE"], timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        g.db = db
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db):
    """Run several statements as one unit. Any error undoes everything.
    BEGIN IMMEDIATE locks the database for writing, so two people
    cannot buy the last copy of a book at the same time."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def audit(db, action, detail):
    user = current_user()
    db.execute("INSERT INTO audit_log (user_id, user_name, action, detail, at) VALUES (?,?,?,?,?)",
               (user["id"] if user else None, user["name"] if user else "system", action, detail, now_str()))


# ---------------------------------------------------------------- validation
def body():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ApiError("Send the data as a JSON object.")
    return data


def get_text(data, key, label, min_len=1, max_len=100, required=True):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ApiError(f"{label} is required.")
        return ""
    if not isinstance(value, str):
        raise ApiError(f"{label} must be text.")
    value = value.strip()
    if len(value) < min_len:
        raise ApiError(f"{label} must be at least {min_len} characters.")
    if len(value) > max_len:
        raise ApiError(f"{label} can be at most {max_len} characters.")
    return value


def get_int(data, key, label, lo, hi, required=True, default=None):
    value = data.get(key)
    if value is None or value == "":
        if required:
            raise ApiError(f"{label} is required.")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ApiError(f"{label} must be a whole number.")
    try:
        value = int(value)
    except ValueError:
        raise ApiError(f"{label} must be a whole number.")
    if value < lo or value > hi:
        raise ApiError(f"{label} must be between {lo} and {hi}.")
    return value


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_email(data):
    email = get_text(data, "email", "Email", max_len=100).lower()
    if not EMAIL_RE.match(email):
        raise ApiError("Enter a valid email address.")
    return email


def get_phone(data, key="phone"):
    phone = get_text(data, key, "Phone number", max_len=15)
    if not re.fullmatch(r"\d{10}", phone):
        raise ApiError("Phone number must be exactly 10 digits.")
    return phone


def get_isbn(data):
    raw = get_text(data, "isbn", "ISBN", max_len=20)
    isbn = raw.replace("-", "").replace(" ", "").upper()
    if not re.fullmatch(r"\d{13}|\d{9}[\dX]", isbn):
        raise ApiError("ISBN must have 10 or 13 digits.")
    return isbn


def get_url(data, key, label):
    url = get_text(data, key, label, max_len=300, required=False)
    if url and not re.match(r"^https?://", url, re.I):
        raise ApiError(f"{label} must start with http:// or https://")
    return url


def get_date(data, key, label):
    value = get_text(data, key, label, max_len=10, required=False)
    if value:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ApiError(f"{label} must be a date like 2026-12-31.")
    return value


# ---------------------------------------------------------------- login / roles
def current_user():
    if "user" in g:
        return g.user
    g.user = None
    uid = session.get("uid")
    if uid:
        row = get_db().execute(
            "SELECT id, name, email, phone, role, active FROM users WHERE id = ?", (uid,)).fetchone()
        if row and row["active"]:
            g.user = row
        else:
            session.clear()
    return g.user


def user_json(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "phone": u["phone"], "role": u["role"]}


def login_required(*roles):
    """Block the route unless the user is logged in (and has one of the roles)."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                raise ApiError("Please log in to continue.", 401)
            if roles and user["role"] not in roles:
                raise ApiError("You do not have permission to do this.", 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


STAFF = ("staff", "admin")
CUSTOMER = ("customer",)

_failed_logins = {}  # ip -> list of times (simple brute-force guard)


def too_many_attempts(ip):
    recent = [t for t in _failed_logins.get(ip, []) if time.time() - t < 300]
    _failed_logins[ip] = recent
    return len(recent) >= 10


@app.before_request
def api_guard():
    """A form on another website cannot send JSON, so requiring JSON blocks CSRF."""
    if request.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if not request.is_json:
            raise ApiError("Requests must be sent as JSON.", 415)


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify({"error": err.message}), err.status


@app.errorhandler(404)
def handle_404(_e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return "Page not found", 404


@app.errorhandler(405)
def handle_405(_e):
    return jsonify({"error": "Method not allowed."}), 405


@app.errorhandler(500)
def handle_500(_e):
    return jsonify({"error": "Something went wrong on the server."}), 500


# ---------------------------------------------------------------- database setup
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, phone TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('customer','staff','admin')),
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS books (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, author TEXT NOT NULL, isbn TEXT NOT NULL UNIQUE,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  price INTEGER NOT NULL CHECK (price > 0),
  stock INTEGER NOT NULL CHECK (stock >= 0),
  description TEXT NOT NULL DEFAULT '', cover_url TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cart_items (
  user_id INTEGER NOT NULL REFERENCES users(id), book_id INTEGER NOT NULL REFERENCES books(id),
  qty INTEGER NOT NULL CHECK (qty > 0), PRIMARY KEY (user_id, book_id));
CREATE TABLE IF NOT EXISTS coupons (
  code TEXT PRIMARY KEY, percent INTEGER NOT NULL, min_amount INTEGER NOT NULL DEFAULT 0,
  max_discount INTEGER NOT NULL DEFAULT 0, expires_on TEXT NOT NULL DEFAULT '',
  usage_limit INTEGER NOT NULL DEFAULT 100, used_count INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL, payment_method TEXT NOT NULL DEFAULT 'COD',
  payment_status TEXT NOT NULL DEFAULT 'pending',
  subtotal INTEGER NOT NULL, discount INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
  coupon_code TEXT NOT NULL DEFAULT '',
  ship_name TEXT NOT NULL, ship_phone TEXT NOT NULL, ship_address TEXT NOT NULL,
  ship_city TEXT NOT NULL, ship_pincode TEXT NOT NULL,
  cancel_reason TEXT NOT NULL DEFAULT '', cancelled_by TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, delivered_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS order_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
  book_id INTEGER NOT NULL REFERENCES books(id),
  title TEXT NOT NULL, author TEXT NOT NULL,
  price INTEGER NOT NULL,   -- price at purchase time (snapshot)
  qty INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS order_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
  status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS return_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
  reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'requested',
  staff_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, decided_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, book_id INTEGER NOT NULL REFERENCES books(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5), comment TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, UNIQUE (book_id, user_id));
CREATE TABLE IF NOT EXISTS wishlist (
  user_id INTEGER NOT NULL REFERENCES users(id), book_id INTEGER NOT NULL REFERENCES books(id),
  PRIMARY KEY (user_id, book_id));
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, user_name TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL);
"""


def init_db():
    """Create tables, and add demo data the first time."""
    db = sqlite3.connect(app.config["DATABASE"], isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seed(db)
    db.close()


def isbn13(prefix12):
    """Make a correctly formatted ISBN-13 (demo data only)."""
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(prefix12))
    return prefix12 + str((10 - total % 10) % 10)


SAMPLE_BOOKS = [
    ("Fiction", "The Alchemist", "Paulo Coelho", 299, 25, "A shepherd boy follows a dream across the desert and learns what the journey is really for."),
    ("Fiction", "The God of Small Things", "Arundhati Roy", 399, 14, "Twins in Kerala, a family secret, and one season that changed everything."),
    ("Fiction", "The Guide", "R. K. Narayan", 249, 18, "A tourist guide in Malgudi is mistaken for a holy man and slowly grows into the role."),
    ("Fiction", "Train to Pakistan", "Khushwant Singh", 279, 3, "A quiet border village faces the violence of Partition in the summer of 1947."),
    ("Classics", "Pride and Prejudice", "Jane Austen", 199, 30, "Elizabeth Bennet, Mr Darcy, and the slow art of changing your mind about someone."),
    ("Classics", "1984", "George Orwell", 249, 22, "A clerk in a watched society tries to keep one private thought alive."),
    ("Classics", "To Kill a Mockingbird", "Harper Lee", 349, 16, "A lawyer in a small Southern town defends a man everyone has already judged."),
    ("Classics", "The Great Gatsby", "F. Scott Fitzgerald", 189, 4, "Parties, money and longing in 1920s New York, told by a neighbour who watches it all."),
    ("Non-Fiction", "Wings of Fire", "A. P. J. Abdul Kalam", 299, 40, "The life story of India's missile scientist and president, from Rameswaram to the lab."),
    ("Non-Fiction", "Sapiens", "Yuval Noah Harari", 499, 20, "A fast tour of how humans went from one animal among many to running the planet."),
    ("Non-Fiction", "The Diary of a Young Girl", "Anne Frank", 229, 12, "A teenager's honest diary from two years in hiding during the Second World War."),
    ("Non-Fiction", "The Story of My Experiments with Truth", "M. K. Gandhi", 199, 26, "Gandhi's own account of his early life, his mistakes and the ideas he tested."),
    ("Self-Help", "Atomic Habits", "James Clear", 549, 35, "Small daily changes, a clear system, and why tiny habits add up over years."),
    ("Self-Help", "Ikigai", "Hector Garcia and Francesc Miralles", 299, 28, "What people in Okinawa can teach us about a long life with purpose."),
    ("Self-Help", "The Power of Now", "Eckhart Tolle", 349, 15, "A guide to dropping worry about the past and future and paying attention to today."),
    ("Self-Help", "Deep Work", "Cal Newport", 449, 5, "Why focused, distraction-free work is rare and valuable, and how to protect it."),
    ("Science & Tech", "A Brief History of Time", "Stephen Hawking", 399, 19, "From the Big Bang to black holes, explained without the heavy maths."),
    ("Science & Tech", "The Selfish Gene", "Richard Dawkins", 449, 11, "Evolution seen from the point of view of the gene instead of the animal."),
    ("Science & Tech", "Clean Code", "Robert C. Martin", 599, 21, "Practical rules for writing code that other people can read and change safely."),
    ("Science & Tech", "Cosmos", "Carl Sagan", 499, 13, "A walk through the universe, from ancient astronomers to distant galaxies."),
]

SAMPLE_COUPONS = [
    ("WELCOME10", 10, 300, 200, "", 500, 0, 1),
    ("BIG20", 20, 1000, 500, "", 100, 0, 1),
    ("OLD5", 5, 0, 50, "2024-01-01", 100, 0, 1),  # expired on purpose, for testing
]


def seed(db):
    """Add categories, a store admin, and (if SEED_DEMO) demo users, books and orders."""
    now = now_str()
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@bookstore.com").lower()
    admin_pass = os.environ.get("ADMIN_PASSWORD", "Admin@123")
    db.execute("INSERT INTO users (name,email,phone,password_hash,role,created_at) VALUES (?,?,?,?,?,?)",
               ("Store Admin", admin_email, "9000000000", generate_password_hash(admin_pass), "admin", now))
    if not app.config["SEED_DEMO"]:
        for name in ("Fiction", "Classics", "Non-Fiction", "Self-Help", "Science & Tech"):
            db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        return
    for name, email, phone, role in [("Store Staff", "staff@bookstore.com", "9000000001", "staff"),
                                     ("Demo Reader", "demo@bookstore.com", "9000000002", "customer"),
                                     ("Priya Sharma", "priya@bookstore.com", "9000000003", "customer")]:
        pw = "Staff@123" if role == "staff" else "Demo@123"
        db.execute("INSERT INTO users (name,email,phone,password_hash,role,created_at) VALUES (?,?,?,?,?,?)",
                   (name, email, phone, generate_password_hash(pw), role, now))
    cats = {}
    for cat, *_ in SAMPLE_BOOKS:
        if cat not in cats:
            cats[cat] = db.execute("INSERT INTO categories (name) VALUES (?)", (cat,)).lastrowid
    for i, (cat, title, author, price, stock, desc) in enumerate(SAMPLE_BOOKS):
        db.execute("INSERT INTO books (title,author,isbn,category_id,price,stock,description,created_at) "
                   "VALUES (?,?,?,?,?,?,?,?)",
                   (title, author, isbn13("978810000%03d" % (i + 1)), cats[cat], price, stock, desc, now))
    db.executemany("INSERT INTO coupons VALUES (?,?,?,?,?,?,?,?)", SAMPLE_COUPONS)

    # A few finished orders so the dashboard, charts, reviews and recommendations have data.
    demo = db.execute("SELECT id FROM users WHERE email='demo@bookstore.com'").fetchone()["id"]
    priya = db.execute("SELECT id FROM users WHERE email='priya@bookstore.com'").fetchone()["id"]
    plan = [  # (user, days ago, status, [(book number, qty)])
        (demo, 9, "delivered", [(13, 1), (16, 1)]), (priya, 7, "delivered", [(13, 1), (10, 1)]),
        (demo, 6, "delivered", [(1, 1), (6, 1)]), (priya, 4, "delivered", [(1, 2), (9, 1)]),
        (demo, 3, "delivered", [(19, 1), (13, 1)]), (priya, 2, "shipped", [(14, 1)]),
        (demo, 1, "packed", [(9, 1), (5, 1)]), (priya, 0, "placed", [(7, 1)]),
    ]
    for uid, days_ago, status, lines in plan:
        when = datetime.now(IST) - timedelta(days=days_ago, hours=2)
        stamp = when.strftime("%Y-%m-%d %H:%M:%S")
        delivered_at = (when + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S") if status == "delivered" else ""
        rows = [(db.execute("SELECT * FROM books WHERE id=?", (n,)).fetchone(), q) for n, q in lines]
        total = sum(b["price"] * q for b, q in rows)
        oid = db.execute(
            "INSERT INTO orders (user_id,status,payment_status,subtotal,discount,total,ship_name,ship_phone,"
            "ship_address,ship_city,ship_pincode,created_at,delivered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, status, "paid" if status == "delivered" else "pending", total, 0, total, "Demo Reader",
             "9000000002", "12, Lake View Road", "Chennai", "600001", stamp, delivered_at)).lastrowid
        for b, q in rows:
            db.execute("INSERT INTO order_items (order_id,book_id,title,author,price,qty) VALUES (?,?,?,?,?,?)",
                       (oid, b["id"], b["title"], b["author"], b["price"], q))
            db.execute("UPDATE books SET stock = MAX(stock - ?, 0) WHERE id=?", (q, b["id"]))
        db.execute("INSERT INTO order_events (order_id,status,note,at) VALUES (?,?,?,?)", (oid, "placed", "", stamp))
        if status != "placed":
            db.execute("INSERT INTO order_events (order_id,status,note,at) VALUES (?,?,?,?)", (oid, "packed", "", stamp))
        if status in ("shipped", "delivered"):
            db.execute("INSERT INTO order_events (order_id,status,note,at) VALUES (?,?,?,?)", (oid, "shipped", "", stamp))
        if status == "delivered":
            db.execute("INSERT INTO order_events (order_id,status,note,at) VALUES (?,?,?,?)",
                       (oid, "delivered", "", delivered_at))
    for uid, book, rating, comment in [
            (demo, 13, 5, "Short chapters and easy to apply. I finished it in a week."),
            (priya, 13, 4, "Great ideas. The last third repeats itself a little."),
            (demo, 1, 5, "A calm, beautiful read."), (priya, 1, 4, "Simple story with a lot of heart."),
            (demo, 19, 5, "Every developer should read this once."), (priya, 9, 5, "Inspiring from the first page.")]:
        db.execute("INSERT INTO reviews (book_id,user_id,rating,comment,created_at) VALUES (?,?,?,?,?)",
                   (book, uid, rating, comment, now))


# ---------------------------------------------------------------- pages
@app.get("/")
def home():
    return render_template("index.html")


@app.get("/api/config")
def config():
    return jsonify({"demo": app.config["SEED_DEMO"], "return_days": RETURN_WINDOW_DAYS,
                    "cancel_reasons": CUSTOMER_CANCEL_REASONS, "return_reasons": RETURN_REASONS,
                    "max_qty": MAX_QTY_PER_BOOK})


@app.get("/invoice/<int:order_id>")
def invoice(order_id):
    """A clean printable invoice page. The customer can 'Save as PDF' from the browser."""
    user = current_user()
    if not user:
        return "Please log in first.", 401
    db = get_db()
    order = db.execute("SELECT o.*, u.email AS customer_email FROM orders o JOIN users u ON u.id=o.user_id "
                       "WHERE o.id=?", (order_id,)).fetchone()
    if not order or (user["role"] == "customer" and order["user_id"] != user["id"]):
        return "Invoice not found.", 404
    items = db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
    return render_template("invoice.html", o=order, items=items, code=order_code(order_id))


# ---------------------------------------------------------------- accounts
@app.post("/api/register")
def register():
    data = body()
    name = get_text(data, "name", "Name", 2, 50)
    email = get_email(data)
    phone = get_phone(data)
    password = get_text(data, "password", "Password", 6, 64)
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError("An account with this email already exists.", 409)
    uid = db.execute("INSERT INTO users (name,email,phone,password_hash,role,created_at) VALUES (?,?,?,?,?,?)",
                     (name, email, phone, generate_password_hash(password), "customer", now_str())).lastrowid
    session.clear()
    session["uid"] = uid
    return jsonify({"user": user_json(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())}), 201


@app.post("/api/login")
def login():
    ip = request.remote_addr or "?"
    if too_many_attempts(ip):
        raise ApiError("Too many failed attempts. Please wait a few minutes.", 429)
    data = body()
    email = get_email(data)
    password = get_text(data, "password", "Password", 1, 64)
    row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], password):
        _failed_logins.setdefault(ip, []).append(time.time())
        raise ApiError("Wrong email or password.", 401)
    if not row["active"]:
        raise ApiError("This account is disabled. Contact the store admin.", 403)
    session.clear()
    session["uid"] = row["id"]
    return jsonify({"user": user_json(row)})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    user = current_user()
    return jsonify({"user": user_json(user) if user else None})


# ---------------------------------------------------------------- books
BOOK_SELECT = """
SELECT b.id, b.title, b.author, b.isbn, b.category_id, c.name AS category, b.price, b.stock,
       b.description, b.cover_url, b.active,
       (SELECT ROUND(AVG(rating),1) FROM reviews r WHERE r.book_id=b.id) AS rating,
       (SELECT COUNT(*) FROM reviews r WHERE r.book_id=b.id) AS review_count
FROM books b JOIN categories c ON c.id=b.category_id
"""
SORTS = {"newest": "b.id DESC", "price_asc": "b.price ASC", "price_desc": "b.price DESC",
         "rating": "COALESCE(rating,0) DESC, review_count DESC", "title": "b.title COLLATE NOCASE ASC"}


def search_books(include_inactive, per_page):
    args = request.args
    where, params = [], []
    if not include_inactive:
        where.append("b.active=1")
    q = args.get("q", "").strip()[:60]
    if q:
        like = f"%{q}%"
        where.append("(b.title LIKE ? OR b.author LIKE ? OR b.isbn LIKE ?)")
        params += [like, like, like]
    if args.get("category", "").isdigit():
        where.append("b.category_id=?")
        params.append(int(args["category"]))
    for key, op in (("min", ">="), ("max", "<=")):
        if args.get(key, "").isdigit():
            where.append(f"b.price {op} ?")
            params.append(int(args[key]))
    if args.get("low_stock") == "1":
        where.append("b.stock <= 5")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM books b" + clause, params).fetchone()[0]
    page = max(int(args["page"]) if args.get("page", "").isdigit() else 1, 1)
    order = SORTS.get(args.get("sort", ""), SORTS["newest"])
    rows = db.execute(f"{BOOK_SELECT}{clause} ORDER BY {order} LIMIT ? OFFSET ?",
                      params + [per_page, (page - 1) * per_page]).fetchall()
    return jsonify({"books": [dict(r) for r in rows], "total": total, "page": page,
                    "pages": max((total + per_page - 1) // per_page, 1)})


@app.get("/api/books")
def list_books():
    return search_books(False, 12)


@app.get("/api/categories")
def categories():
    rows = get_db().execute("SELECT c.id, c.name, (SELECT COUNT(*) FROM books b WHERE b.category_id=c.id AND b.active=1) "
                            "AS count FROM categories c ORDER BY c.name").fetchall()
    return jsonify({"categories": [dict(r) for r in rows]})


@app.get("/api/books/<int:book_id>")
def book_detail(book_id):
    db = get_db()
    book = db.execute(BOOK_SELECT + " WHERE b.id=?", (book_id,)).fetchone()
    user = current_user()
    is_staff = user and user["role"] in STAFF
    if not book or (not book["active"] and not is_staff):
        raise ApiError("Book not found.", 404)
    reviews = db.execute("SELECT r.rating, r.comment, r.created_at, u.name FROM reviews r JOIN users u ON u.id=r.user_id "
                         "WHERE r.book_id=? ORDER BY r.id DESC LIMIT 30", (book_id,)).fetchall()
    # "Readers also bought": books found in the same orders as this one
    also = db.execute(BOOK_SELECT.replace("SELECT b.id,", "SELECT b.id, (SELECT SUM(1) FROM order_items x JOIN orders xo "
                      "ON xo.id=x.order_id WHERE x.book_id=b.id AND xo.id IN (SELECT order_id FROM order_items WHERE book_id=?) "
                      "AND xo.status IN ('placed','packed','shipped','delivered')) AS together,", 1)
                      + " WHERE b.active=1 AND b.id!=? AND together > 0 ORDER BY together DESC, rating DESC LIMIT 4",
                      (book_id, book_id)).fetchall()
    can_review, reviewed = False, False
    if user and user["role"] == "customer":
        reviewed = bool(db.execute("SELECT 1 FROM reviews WHERE book_id=? AND user_id=?", (book_id, user["id"])).fetchone())
        bought = db.execute("SELECT 1 FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.user_id=? AND i.book_id=? "
                            "AND o.status='delivered' LIMIT 1", (user["id"], book_id)).fetchone()
        can_review = bool(bought) and not reviewed
    return jsonify({"book": dict(book), "reviews": [dict(r) for r in reviews],
                    "also_bought": [{k: v for k, v in dict(r).items() if k != "together"} for r in also],
                    "can_review": can_review, "reviewed": reviewed})


@app.post("/api/books/<int:book_id>/reviews")
@login_required(*CUSTOMER)
def add_review(book_id):
    data = body()
    rating = get_int(data, "rating", "Rating", 1, 5)
    comment = get_text(data, "comment", "Comment", max_len=500, required=False)
    db, user = get_db(), current_user()
    if not db.execute("SELECT 1 FROM orders o JOIN order_items i ON i.order_id=o.id WHERE o.user_id=? AND i.book_id=? "
                      "AND o.status='delivered' LIMIT 1", (user["id"], book_id)).fetchone():
        raise ApiError("You can review a book only after it has been delivered to you.", 403)
    try:
        db.execute("INSERT INTO reviews (book_id,user_id,rating,comment,created_at) VALUES (?,?,?,?,?)",
                   (book_id, user["id"], rating, comment, now_str()))
    except sqlite3.IntegrityError:
        raise ApiError("You have already reviewed this book.", 409)
    return jsonify({"ok": True}), 201


# ---------------------------------------------------------------- wishlist
@app.get("/api/wishlist")
@login_required(*CUSTOMER)
def get_wishlist():
    rows = get_db().execute(BOOK_SELECT + " JOIN wishlist w ON w.book_id=b.id WHERE w.user_id=? AND b.active=1 "
                            "ORDER BY b.title", (current_user()["id"],)).fetchall()
    return jsonify({"books": [dict(r) for r in rows], "ids": [r["id"] for r in rows]})


@app.post("/api/wishlist/<int:book_id>")
@login_required(*CUSTOMER)
def toggle_wishlist(book_id):
    db, uid = get_db(), current_user()["id"]
    if not db.execute("SELECT 1 FROM books WHERE id=? AND active=1", (book_id,)).fetchone():
        raise ApiError("Book not found.", 404)
    if db.execute("SELECT 1 FROM wishlist WHERE user_id=? AND book_id=?", (uid, book_id)).fetchone():
        db.execute("DELETE FROM wishlist WHERE user_id=? AND book_id=?", (uid, book_id))
        return jsonify({"saved": False})
    db.execute("INSERT INTO wishlist (user_id, book_id) VALUES (?,?)", (uid, book_id))
    return jsonify({"saved": True})


# ---------------------------------------------------------------- cart
def cart_payload(db, uid):
    rows = db.execute("SELECT c.book_id, c.qty, b.title, b.author, b.price, b.stock, b.cover_url, b.active, cat.name AS category "
                      "FROM cart_items c JOIN books b ON b.id=c.book_id JOIN categories cat ON cat.id=b.category_id "
                      "WHERE c.user_id=? ORDER BY c.rowid", (uid,)).fetchall()
    items = [dict(r) for r in rows]
    subtotal = sum(i["price"] * i["qty"] for i in items if i["active"])
    return {"items": items, "subtotal": subtotal, "count": sum(i["qty"] for i in items)}


@app.get("/api/cart")
@login_required(*CUSTOMER)
def get_cart():
    return jsonify(cart_payload(get_db(), current_user()["id"]))


def check_stock(book, qty):
    if not book or not book["active"]:
        raise ApiError("This book is not available.", 404)
    if book["stock"] <= 0:
        raise ApiError(f"\"{book['title']}\" is out of stock.", 409)
    if qty > book["stock"]:
        raise ApiError(f"Only {book['stock']} copies of \"{book['title']}\" are available.", 409)
    if qty > MAX_QTY_PER_BOOK:
        raise ApiError(f"You can order at most {MAX_QTY_PER_BOOK} copies of one book.", 409)


@app.post("/api/cart")
@login_required(*CUSTOMER)
def add_to_cart():
    data = body()
    book_id = get_int(data, "book_id", "Book", 1, 10**9)
    qty = get_int(data, "qty", "Quantity", 1, MAX_QTY_PER_BOOK, required=False, default=1)
    db, uid = get_db(), current_user()["id"]
    book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    existing = db.execute("SELECT qty FROM cart_items WHERE user_id=? AND book_id=?", (uid, book_id)).fetchone()
    check_stock(book, qty + (existing["qty"] if existing else 0))
    db.execute("INSERT INTO cart_items (user_id,book_id,qty) VALUES (?,?,?) "
               "ON CONFLICT(user_id,book_id) DO UPDATE SET qty = qty + excluded.qty", (uid, book_id, qty))
    return jsonify(cart_payload(db, uid))


@app.put("/api/cart/<int:book_id>")
@login_required(*CUSTOMER)
def set_cart_qty(book_id):
    qty = get_int(body(), "qty", "Quantity", 1, MAX_QTY_PER_BOOK)
    db, uid = get_db(), current_user()["id"]
    if not db.execute("SELECT 1 FROM cart_items WHERE user_id=? AND book_id=?", (uid, book_id)).fetchone():
        raise ApiError("That book is not in your cart.", 404)
    check_stock(db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone(), qty)
    db.execute("UPDATE cart_items SET qty=? WHERE user_id=? AND book_id=?", (qty, uid, book_id))
    return jsonify(cart_payload(db, uid))


@app.delete("/api/cart/<int:book_id>")
@login_required(*CUSTOMER)
def remove_from_cart(book_id):
    db, uid = get_db(), current_user()["id"]
    db.execute("DELETE FROM cart_items WHERE user_id=? AND book_id=?", (uid, book_id))
    return jsonify(cart_payload(db, uid))


# ---------------------------------------------------------------- coupons
def apply_coupon(db, code, subtotal):
    """Check every coupon rule. Returns (code, discount). Raises ApiError if not valid."""
    code = (code or "").strip().upper()
    row = db.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()
    if not row or not row["active"]:
        raise ApiError("This coupon code is not valid.", 422)
    if row["expires_on"] and row["expires_on"] < today_str():
        raise ApiError("This coupon has expired.", 422)
    if row["used_count"] >= row["usage_limit"]:
        raise ApiError("This coupon has reached its usage limit.", 422)
    if subtotal < row["min_amount"]:
        raise ApiError(f"Add books worth Rs {row['min_amount'] - subtotal} more to use {code}.", 422)
    discount = subtotal * row["percent"] // 100
    if row["max_discount"] > 0:
        discount = min(discount, row["max_discount"])
    return code, discount


@app.get("/api/coupons/check")
@login_required(*CUSTOMER)
def check_coupon():
    db = get_db()
    cart = cart_payload(db, current_user()["id"])
    code, discount = apply_coupon(db, request.args.get("code"), cart["subtotal"])
    return jsonify({"code": code, "discount": discount, "total": cart["subtotal"] - discount})


# ---------------------------------------------------------------- orders
def order_code(order_id):
    return f"BK{order_id:05d}"


def parse_time(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)


def order_json(db, o, full=True):
    d = dict(o)
    d["code"] = order_code(o["id"])
    d["items"] = [dict(r) for r in db.execute(
        "SELECT book_id, title, author, price, qty FROM order_items WHERE order_id=?", (o["id"],))]
    d["can_cancel"] = o["status"] in ("placed", "packed")
    ret = db.execute("SELECT * FROM return_requests WHERE order_id=?", (o["id"],)).fetchone()
    d["return_request"] = dict(ret) if ret else None
    d["can_return"] = False
    if o["status"] == "delivered" and o["delivered_at"] and not ret:
        deadline = parse_time(o["delivered_at"]) + timedelta(days=RETURN_WINDOW_DAYS)
        d["return_until"] = deadline.strftime("%d %b %Y")
        d["can_return"] = datetime.now(IST) <= deadline
    if full:
        d["events"] = [dict(r) for r in db.execute(
            "SELECT status, note, at FROM order_events WHERE order_id=? ORDER BY id", (o["id"],))]
    return d


ORDER_SELECT = "SELECT o.*, u.name AS customer_name, u.email AS customer_email, u.phone AS customer_phone " \
               "FROM orders o JOIN users u ON u.id=o.user_id"


def load_order(db, order_id, user):
    o = db.execute(ORDER_SELECT + " WHERE o.id=?", (order_id,)).fetchone()
    if not o or (user["role"] == "customer" and o["user_id"] != user["id"]):
        raise ApiError("Order not found.", 404)
    return o


def add_event(db, order_id, status, note=""):
    db.execute("INSERT INTO order_events (order_id,status,note,at) VALUES (?,?,?,?)", (order_id, status, note, now_str()))


def restore_stock(db, order_id):
    """Put the copies of an order back on the shelf, and give the coupon use back."""
    db.execute("UPDATE books SET stock = stock + (SELECT qty FROM order_items WHERE order_id=? AND book_id=books.id) "
               "WHERE id IN (SELECT book_id FROM order_items WHERE order_id=?)", (order_id, order_id))


def release_coupon(db, order):
    if order["coupon_code"]:
        db.execute("UPDATE coupons SET used_count = MAX(used_count - 1, 0) WHERE code=?", (order["coupon_code"],))


@app.post("/api/orders")
@login_required(*CUSTOMER)
def place_order():
    data = body()
    ship = {
        "ship_name": get_text(data, "name", "Name", 2, 50),
        "ship_phone": get_phone(data),
        "ship_address": get_text(data, "address", "Address", 5, 200),
        "ship_city": get_text(data, "city", "City", 2, 50),
        "ship_pincode": get_text(data, "pincode", "Pincode", 6, 6),
    }
    if not re.fullmatch(r"\d{6}", ship["ship_pincode"]):
        raise ApiError("Pincode must be exactly 6 digits.")
    if data.get("payment_method", "COD") != "COD":
        raise ApiError("Only Cash on Delivery is available right now.")
    coupon_code = get_text(data, "coupon", "Coupon", max_len=20, required=False)
    db, uid = get_db(), current_user()["id"]

    with transaction(db):  # everything below succeeds together or not at all
        lines = db.execute("SELECT c.qty, b.id, b.title, b.author, b.price, b.stock, b.active FROM cart_items c "
                           "JOIN books b ON b.id=c.book_id WHERE c.user_id=?", (uid,)).fetchall()
        if not lines:
            raise ApiError("Your cart is empty.")
        subtotal = 0
        for ln in lines:
            check_stock(ln, ln["qty"])
            subtotal += ln["price"] * ln["qty"]
        discount, used = 0, ""
        if coupon_code:
            used, discount = apply_coupon(db, coupon_code, subtotal)
            ok = db.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code=? AND used_count < usage_limit",
                            (used,)).rowcount
            if not ok:
                raise ApiError("This coupon has reached its usage limit.", 422)
        total = subtotal - discount
        oid = db.execute(
            "INSERT INTO orders (user_id,status,payment_method,payment_status,subtotal,discount,total,coupon_code,"
            "ship_name,ship_phone,ship_address,ship_city,ship_pincode,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, "placed", "COD", "pending", subtotal, discount, total, used, ship["ship_name"], ship["ship_phone"],
             ship["ship_address"], ship["ship_city"], ship["ship_pincode"], now_str())).lastrowid
        for ln in lines:
            # stock check and reduce in one statement: it can never go below 0
            ok = db.execute("UPDATE books SET stock = stock - ? WHERE id=? AND stock >= ?",
                            (ln["qty"], ln["id"], ln["qty"])).rowcount
            if not ok:
                raise ApiError(f"\"{ln['title']}\" just went out of stock.", 409)
            db.execute("INSERT INTO order_items (order_id,book_id,title,author,price,qty) VALUES (?,?,?,?,?,?)",
                       (oid, ln["id"], ln["title"], ln["author"], ln["price"], ln["qty"]))
        add_event(db, oid, "placed")
        db.execute("DELETE FROM cart_items WHERE user_id=?", (uid,))
    return jsonify({"order": order_json(db, load_order(db, oid, current_user()))}), 201


@app.get("/api/orders")
@login_required()
def list_orders():
    db, user = get_db(), current_user()
    where, params = [], []
    if user["role"] == "customer":
        where.append("o.user_id=?")
        params.append(user["id"])
    status = request.args.get("status", "")
    if status:
        where.append("o.status=?")
        params.append(status)
    q = request.args.get("q", "").strip()[:40]
    if q and user["role"] in STAFF:
        digits = re.sub(r"\D", "", q.upper().replace("BK", "")) if q.upper().startswith("BK") else ""
        where.append("(u.name LIKE ? OR o.ship_phone LIKE ? OR o.id=?)")
        params += [f"%{q}%", f"%{q}%", int(digits) if digits else -1]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(f"{ORDER_SELECT}{clause} ORDER BY o.id DESC LIMIT 100", params).fetchall()
    return jsonify({"orders": [order_json(db, r, full=False) for r in rows]})


@app.get("/api/orders/<int:order_id>")
@login_required()
def get_order(order_id):
    db = get_db()
    return jsonify({"order": order_json(db, load_order(db, order_id, current_user()))})


@app.post("/api/orders/<int:order_id>/cancel")
@login_required()
def cancel_order(order_id):
    data = body()
    user, db = current_user(), get_db()
    if user["role"] == "customer":
        reason = get_text(data, "reason", "Reason", max_len=100)
        if reason not in CUSTOMER_CANCEL_REASONS:
            raise ApiError("Please choose a reason from the list.")
    else:
        reason = get_text(data, "reason", "Reason", 3, 200)
    with transaction(db):
        order = load_order(db, order_id, user)  # read again inside the lock
        if order["status"] == "cancelled":
            raise ApiError("This order is already cancelled.", 409)
        if order["status"] not in ("placed", "packed"):
            raise ApiError("This order can no longer be cancelled. "
                           + ("Refuse delivery when it arrives." if order["status"] == "shipped" else ""), 409)
        restore_stock(db, order_id)
        release_coupon(db, order)
        db.execute("UPDATE orders SET status='cancelled', payment_status='not_applicable', cancel_reason=?, "
                   "cancelled_by=? WHERE id=?", (reason, user["role"], order_id))
        add_event(db, order_id, "cancelled", reason)
        if user["role"] in STAFF:
            audit(db, "order_cancelled", f"{order_code(order_id)}: {reason}")
    return jsonify({"order": order_json(db, load_order(db, order_id, user))})


NEXT_STATUS = {"placed": ("packed",), "packed": ("shipped",), "shipped": ("delivered", "refused")}


@app.post("/api/orders/<int:order_id>/status")
@login_required(*STAFF)
def update_status(order_id):
    new = get_text(body(), "status", "Status", max_len=20)
    user, db = current_user(), get_db()
    with transaction(db):
        order = load_order(db, order_id, user)
        if new not in NEXT_STATUS.get(order["status"], ()):
            raise ApiError(f"An order that is '{order['status']}' cannot move to '{new}'.", 409)
        if new == "delivered":
            db.execute("UPDATE orders SET status='delivered', payment_status='paid', delivered_at=? WHERE id=?",
                       (now_str(), order_id))
            add_event(db, order_id, "delivered", "Cash collected")
        elif new == "refused":
            restore_stock(db, order_id)
            release_coupon(db, order)
            db.execute("UPDATE orders SET status='refused', payment_status='not_applicable' WHERE id=?", (order_id,))
            add_event(db, order_id, "refused", "Customer refused delivery. Returned to store.")
        else:
            db.execute("UPDATE orders SET status=? WHERE id=?", (new, order_id))
            add_event(db, order_id, new)
        audit(db, "order_status", f"{order_code(order_id)}: {order['status']} -> {new}")
    return jsonify({"order": order_json(db, load_order(db, order_id, user))})


@app.post("/api/orders/<int:order_id>/return")
@login_required(*CUSTOMER)
def request_return(order_id):
    reason = get_text(body(), "reason", "Reason", max_len=100)
    if reason not in RETURN_REASONS:
        raise ApiError("Please choose a reason from the list.")
    user, db = current_user(), get_db()
    with transaction(db):
        order = load_order(db, order_id, user)
        if order["status"] != "delivered":
            raise ApiError("Only delivered orders can be returned.", 409)
        if db.execute("SELECT 1 FROM return_requests WHERE order_id=?", (order_id,)).fetchone():
            raise ApiError("A return was already requested for this order.", 409)
        if datetime.now(IST) > parse_time(order["delivered_at"]) + timedelta(days=RETURN_WINDOW_DAYS):
            raise ApiError(f"The {RETURN_WINDOW_DAYS}-day return window has ended.", 409)
        db.execute("INSERT INTO return_requests (order_id,reason,created_at) VALUES (?,?,?)", (order_id, reason, now_str()))
        add_event(db, order_id, "return_requested", reason)
    return jsonify({"order": order_json(db, load_order(db, order_id, user))}), 201


@app.get("/api/returns")
@login_required(*STAFF)
def list_returns():
    status = request.args.get("status", "requested")
    rows = get_db().execute(
        "SELECT r.*, o.total, o.ship_name, o.ship_phone, u.name AS customer_name FROM return_requests r "
        "JOIN orders o ON o.id=r.order_id JOIN users u ON u.id=o.user_id WHERE r.status=? ORDER BY r.id DESC", (status,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["code"] = order_code(r["order_id"])
        out.append(d)
    return jsonify({"returns": out})


@app.post("/api/returns/<int:return_id>/decide")
@login_required(*STAFF)
def decide_return(return_id):
    data = body()
    approve = data.get("approve")
    if not isinstance(approve, bool):
        raise ApiError("Say whether to approve or reject.")
    note = get_text(data, "note", "Note", max_len=200, required=False)
    if not approve and not note:
        raise ApiError("Write a short reason when rejecting a return.")
    db = get_db()
    with transaction(db):
        ret = db.execute("SELECT * FROM return_requests WHERE id=?", (return_id,)).fetchone()
        if not ret:
            raise ApiError("Return request not found.", 404)
        if ret["status"] != "requested":
            raise ApiError("This return was already decided.", 409)
        db.execute("UPDATE return_requests SET status=?, staff_note=?, decided_at=? WHERE id=?",
                   ("approved" if approve else "rejected", note, now_str(), return_id))
        oid = ret["order_id"]
        if approve:
            restore_stock(db, oid)
            # Cash was paid at delivery, so the refund is paid back by hand for now.
            db.execute("UPDATE orders SET status='returned', payment_status='refund_pending' WHERE id=?", (oid,))
            add_event(db, oid, "returned", "Return approved. Refund pending (manual).")
        else:
            add_event(db, oid, "return_rejected", note)
        audit(db, "return_decision", f"{order_code(oid)}: {'approved' if approve else 'rejected'}")
    return jsonify({"ok": True})


# ---------------------------------------------------------------- staff & admin: books
@app.get("/api/admin/books")
@login_required(*STAFF)
def admin_books():
    return search_books(True, 20)


def book_fields(data, db, book_id=None):
    title = get_text(data, "title", "Title", 1, 150)
    author = get_text(data, "author", "Author", 1, 100)
    isbn = get_isbn(data)
    category_id = get_int(data, "category_id", "Category", 1, 10**9)
    if not db.execute("SELECT 1 FROM categories WHERE id=?", (category_id,)).fetchone():
        raise ApiError("Choose a valid category.")
    price = get_int(data, "price", "Price", 1, 100000)
    stock = get_int(data, "stock", "Stock", 0, 100000)
    desc = get_text(data, "description", "Description", max_len=1000, required=False)
    cover = get_url(data, "cover_url", "Cover link")
    dup = db.execute("SELECT id FROM books WHERE isbn=? AND id IS NOT ?", (isbn, book_id)).fetchone()
    if dup:
        raise ApiError("A book with this ISBN already exists.", 409)
    return title, author, isbn, category_id, price, stock, desc, cover


@app.post("/api/admin/books")
@login_required("admin")
def create_book():
    db = get_db()
    f = book_fields(body(), db)
    bid = db.execute("INSERT INTO books (title,author,isbn,category_id,price,stock,description,cover_url,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)", (*f, now_str())).lastrowid
    audit(db, "book_added", f"{f[0]} (stock {f[5]}, Rs {f[4]})")
    return jsonify({"id": bid}), 201


@app.put("/api/admin/books/<int:book_id>")
@login_required("admin")
def update_book(book_id):
    db = get_db()
    old = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not old:
        raise ApiError("Book not found.", 404)
    data = body()
    f = book_fields(data, db, book_id)
    active = 1 if data.get("active", True) else 0
    db.execute("UPDATE books SET title=?,author=?,isbn=?,category_id=?,price=?,stock=?,description=?,cover_url=?,active=? "
               "WHERE id=?", (*f, active, book_id))
    changes = []
    if old["price"] != f[4]:
        changes.append(f"price {old['price']} -> {f[4]}")
    if old["stock"] != f[5]:
        changes.append(f"stock {old['stock']} -> {f[5]}")
    if old["active"] != active:
        changes.append("shown" if active else "hidden")
    audit(db, "book_edited", f"{f[0]}: {', '.join(changes) or 'details changed'}")
    return jsonify({"ok": True})


@app.patch("/api/admin/books/<int:book_id>/stock")
@login_required(*STAFF)
def update_stock(book_id):
    stock = get_int(body(), "stock", "Stock", 0, 100000)
    db = get_db()
    old = db.execute("SELECT title, stock FROM books WHERE id=?", (book_id,)).fetchone()
    if not old:
        raise ApiError("Book not found.", 404)
    db.execute("UPDATE books SET stock=? WHERE id=?", (stock, book_id))
    audit(db, "stock_changed", f"{old['title']}: stock {old['stock']} -> {stock}")
    return jsonify({"stock": stock})


@app.post("/api/admin/categories")
@login_required("admin")
def add_category():
    name = get_text(body(), "name", "Category name", 2, 40)
    db = get_db()
    try:
        cid = db.execute("INSERT INTO categories (name) VALUES (?)", (name,)).lastrowid
    except sqlite3.IntegrityError:
        raise ApiError("This category already exists.", 409)
    audit(db, "category_added", name)
    return jsonify({"id": cid}), 201


# ---------------------------------------------------------------- admin: coupons, users, dashboard
@app.get("/api/admin/coupons")
@login_required("admin")
def admin_coupons():
    rows = get_db().execute("SELECT * FROM coupons ORDER BY rowid DESC").fetchall()
    return jsonify({"coupons": [dict(r) for r in rows], "today": today_str()})


@app.post("/api/admin/coupons")
@login_required("admin")
def create_coupon():
    data = body()
    code = get_text(data, "code", "Code", 3, 20).upper()
    if not re.fullmatch(r"[A-Z0-9]+", code):
        raise ApiError("Code can have only letters and numbers.")
    percent = get_int(data, "percent", "Discount percent", 1, 90)
    min_amount = get_int(data, "min_amount", "Minimum order", 0, 1000000, required=False, default=0)
    max_discount = get_int(data, "max_discount", "Maximum discount", 0, 1000000, required=False, default=0)
    expires = get_date(data, "expires_on", "Expiry date")
    limit = get_int(data, "usage_limit", "Usage limit", 1, 100000)
    db = get_db()
    try:
        db.execute("INSERT INTO coupons (code,percent,min_amount,max_discount,expires_on,usage_limit) VALUES (?,?,?,?,?,?)",
                   (code, percent, min_amount, max_discount, expires, limit))
    except sqlite3.IntegrityError:
        raise ApiError("This coupon code already exists.", 409)
    audit(db, "coupon_added", f"{code} {percent}%")
    return jsonify({"ok": True}), 201


@app.patch("/api/admin/coupons/<code>")
@login_required("admin")
def toggle_coupon(code):
    db = get_db()
    row = db.execute("SELECT active FROM coupons WHERE code=?", (code.upper(),)).fetchone()
    if not row:
        raise ApiError("Coupon not found.", 404)
    db.execute("UPDATE coupons SET active=? WHERE code=?", (0 if row["active"] else 1, code.upper()))
    audit(db, "coupon_toggled", code.upper())
    return jsonify({"active": not row["active"]})


@app.get("/api/admin/users")
@login_required("admin")
def admin_users():
    rows = get_db().execute("SELECT id,name,email,phone,role,active,created_at FROM users ORDER BY id").fetchall()
    return jsonify({"users": [dict(r) for r in rows]})


@app.post("/api/admin/users")
@login_required("admin")
def create_user():
    data = body()
    name = get_text(data, "name", "Name", 2, 50)
    email = get_email(data)
    phone = get_phone(data)
    password = get_text(data, "password", "Password", 6, 64)
    role = get_text(data, "role", "Role", max_len=10)
    if role not in ("customer", "staff", "admin"):
        raise ApiError("Role must be customer, staff or admin.")
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError("An account with this email already exists.", 409)
    db.execute("INSERT INTO users (name,email,phone,password_hash,role,created_at) VALUES (?,?,?,?,?,?)",
               (name, email, phone, generate_password_hash(password), role, now_str()))
    audit(db, "user_added", f"{email} as {role}")
    return jsonify({"ok": True}), 201


@app.patch("/api/admin/users/<int:user_id>")
@login_required("admin")
def update_user(user_id):
    data = body()
    me_id = current_user()["id"]
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ApiError("User not found.", 404)
    if user_id == me_id:
        raise ApiError("You cannot change your own role or status.", 409)
    role = data.get("role", row["role"])
    active = 1 if data.get("active", bool(row["active"])) else 0
    if role not in ("customer", "staff", "admin"):
        raise ApiError("Role must be customer, staff or admin.")
    db.execute("UPDATE users SET role=?, active=? WHERE id=?", (role, active, user_id))
    audit(db, "user_changed", f"{row['email']}: role {role}, {'active' if active else 'disabled'}")
    return jsonify({"ok": True})


@app.get("/api/admin/dashboard")
@login_required("admin")
def dashboard():
    db = get_db()
    marks = ",".join("?" * len(ACTIVE_SALE_STATUSES))
    today, month = today_str(), today_str()[:7]

    def one(sql, *params):
        return db.execute(sql, params).fetchone()[0] or 0

    sales_today = one(f"SELECT SUM(total) FROM orders WHERE substr(created_at,1,10)=? AND status IN ({marks})",
                      today, *ACTIVE_SALE_STATUSES)
    sales_month = one(f"SELECT SUM(total) FROM orders WHERE substr(created_at,1,7)=? AND status IN ({marks})",
                      month, *ACTIVE_SALE_STATUSES)
    top = db.execute(f"SELECT i.title, SUM(i.qty) AS sold FROM order_items i JOIN orders o ON o.id=i.order_id "
                     f"WHERE o.status IN ({marks}) GROUP BY i.book_id ORDER BY sold DESC, i.title LIMIT 5",
                     ACTIVE_SALE_STATUSES).fetchall()
    low = db.execute("SELECT id, title, stock FROM books WHERE active=1 AND stock <= 5 ORDER BY stock, title").fetchall()
    days = []
    for n in range(6, -1, -1):
        day = (datetime.now(IST) - timedelta(days=n)).strftime("%Y-%m-%d")
        days.append({"day": day, "sales": one(f"SELECT SUM(total) FROM orders WHERE substr(created_at,1,10)=? "
                                              f"AND status IN ({marks})", day, *ACTIVE_SALE_STATUSES)})
    status_rows = db.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status").fetchall()
    return jsonify({
        "sales_today": sales_today, "sales_month": sales_month,
        "orders_today": one("SELECT COUNT(*) FROM orders WHERE substr(created_at,1,10)=?", today),
        "pending_orders": one("SELECT COUNT(*) FROM orders WHERE status IN ('placed','packed')"),
        "pending_returns": one("SELECT COUNT(*) FROM return_requests WHERE status='requested'"),
        "customers": one("SELECT COUNT(*) FROM users WHERE role='customer'"),
        "top_books": [dict(r) for r in top], "low_stock": [dict(r) for r in low],
        "last_7_days": days, "by_status": {r["status"]: r["n"] for r in status_rows}})


@app.get("/api/admin/audit")
@login_required("admin")
def audit_log():
    rows = get_db().execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify({"log": [dict(r) for r in rows]})


# ---------------------------------------------------------------- start
init_db()  # runs under gunicorn too, so tables always exist

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
