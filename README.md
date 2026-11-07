# Pagewise: Online Book Store

A mobile-first online book store. Customers browse, order with **Cash on Delivery**, cancel orders and return books.
Staff run the orders, and the admin manages books, coupons and users.

**Stack:** Python Flask (JSON API), SQLite (`sqlite3` module), plain HTML, CSS and JavaScript (no build step).

## What it does

| Role | Can do |
|---|---|
| **Customer** | Register, log in, search and filter books, wishlist, cart, coupon, checkout (COD), track orders, cancel, request a return, write reviews, print an invoice |
| **Staff** | See all orders, move them Packed, Shipped, Delivered, cancel with a reason, approve or reject returns, update book stock |
| **Admin** | Everything staff can do, plus add or edit books and categories, coupons, users, sales dashboard and activity log |

## Rules (good for your viva)

- Stock can never go below 0. You cannot order more than the stock, or more than 10 copies of one book.
- ISBN must be unique (10 or 13 digits). Price and stock are whole numbers.
- **Placing an order is one database transaction** (`BEGIN IMMEDIATE`). Two people cannot buy the last copy. If one step fails, everything is undone.
- The order saves the **price at purchase time**, so later price changes do not change old orders.
- Coupons have a percent, minimum order, maximum discount, expiry date and usage limit.
- Payment: **Cash on Delivery only.** Status is *Pending*, then *Paid (collected)* when delivered. A cancelled order is *Not applicable*.
- Order flow: Placed, Packed, Shipped, Delivered (staff cannot skip steps).
- **Cancel:** the customer can cancel while *Placed* or *Packed* (must choose a reason). Stock and coupon use are returned once (a double click cannot return stock twice). Once *Shipped*, the customer refuses delivery and staff mark it *Returned to store* (stock returned).
- **Return:** within 7 days of delivery, one request per order. Staff approve (stock returned, refund marked *pending, manual*) or reject (reason needed).
- Only customers who received a book can review it, once per book.
- Passwords are hashed. Staff and admin pages are blocked on the server, not just hidden. Login has a simple brute-force limit.
- All user text is escaped in the browser (no XSS). The API accepts JSON only (blocks CSRF from other sites).

## Run on your PC

```
python -m venv venv
venv\Scripts\activate          (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000. The database file `bookstore.db` is created on first start, with demo data.

**Demo logins:** `admin@bookstore.com / Admin@123`, `staff@bookstore.com / Staff@123`, `demo@bookstore.com / Demo@123`
Coupons: `WELCOME10` (10%, min Rs 300), `BIG20` (20%, min Rs 1000). `OLD5` is expired on purpose.

Run the tests: `python -m unittest discover -s tests -v`

## Deploy on Render

1. Put this folder in a GitHub repository.
2. On render.com choose **New > Blueprint** (or **New > Web Service**) and pick the repository. `render.yaml` sets everything.
3. Wait for the build. Open the URL Render gives you.
4. **Before real use:** in Render **Environment**, set `SEED_DEMO` to `0` (no demo accounts or fake orders), and set `ADMIN_EMAIL` and `ADMIN_PASSWORD` for your own admin login. Do this before the first start, or delete the database and restart.

Environment variables: `SECRET_KEY` (auto-generated), `COOKIE_SECURE=1`, `SEED_DEMO`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `DATABASE_PATH`.

## WARNING: free plan disk resets

On Render's free plan the disk is **temporary**. Every time the service restarts or redeploys (and after it sleeps and wakes), the SQLite file is **deleted**, so all books, orders and users are lost and the demo data comes back. This is fine for a college demo. For real data, add a paid Render persistent disk (set `DATABASE_PATH` to its mount path) or move to a hosted PostgreSQL database.
The free plan also sleeps when unused, so the first page load can take about 30 seconds.

## Files

`app.py` (backend, rules, database), `templates/` (page shell and printable invoice), `static/app.js` (the app), `static/style.css`, `tests/test_app.py`, `requirements.txt`, `Procfile`, `render.yaml`, `.gitignore`.

## Future enhancements

Online payment (Razorpay/UPI) with webhook and automatic refunds, email or SMS alerts, e-books, seller marketplace, book rental, real PDF invoices, PostgreSQL.
