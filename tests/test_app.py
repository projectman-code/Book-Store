"""Backend tests. Run with:  python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as store  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.path)
        store.app.config["DATABASE"] = self.path
        store._failed_logins.clear()
        store.init_db()
        store.app.testing = True

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    # helpers
    def client(self, email=None, password=None):
        c = store.app.test_client()
        if email:
            r = c.post("/api/login", json={"email": email, "password": password})
            self.assertEqual(r.status_code, 200, r.get_json())
        return c

    def customer(self):
        return self.client("demo@bookstore.com", "Demo@123")

    def staff(self):
        return self.client("staff@bookstore.com", "Staff@123")

    def admin(self):
        return self.client("admin@bookstore.com", "Admin@123")

    def new_customer(self, email="new@test.com"):
        c = store.app.test_client()
        r = c.post("/api/register", json={"name": "New Reader", "email": email, "phone": "9876543210", "password": "secret1"})
        self.assertEqual(r.status_code, 201, r.get_json())
        return c

    def book(self, **where):
        db = store.sqlite3.connect(self.path)
        db.row_factory = store.sqlite3.Row
        row = db.execute("SELECT * FROM books WHERE " + " AND ".join(f"{k}=?" for k in where), list(where.values())).fetchone()
        db.close()
        return row

    def stock_of(self, book_id):
        db = store.sqlite3.connect(self.path)
        s = db.execute("SELECT stock FROM books WHERE id=?", (book_id,)).fetchone()[0]
        db.close()
        return s

    ADDRESS = {"name": "Asha K", "phone": "9876543210", "address": "12 Main Road", "city": "Chennai", "pincode": "600001"}

    def order(self, c, book_id=1, qty=1, coupon="", **over):
        c.post("/api/cart", json={"book_id": book_id, "qty": qty})
        data = dict(self.ADDRESS, coupon=coupon, **over)
        return c.post("/api/orders", json=data)


class AccountTests(Base):
    def test_register_login_logout_me(self):
        c = self.new_customer()
        self.assertEqual(c.get("/api/me").get_json()["user"]["role"], "customer")
        c.post("/api/logout", json={})
        self.assertIsNone(c.get("/api/me").get_json()["user"])
        r = c.post("/api/login", json={"email": "NEW@test.com", "password": "secret1"})
        self.assertEqual(r.status_code, 200)

    def test_register_validation(self):
        c = store.app.test_client()
        good = {"name": "Asha", "email": "a@b.com", "phone": "9876543210", "password": "secret1"}
        for bad in [dict(good, name="A"), dict(good, email="nope"), dict(good, phone="12345"), dict(good, phone="98765abcde"),
                    dict(good, password="123"), {k: v for k, v in good.items() if k != "email"}, dict(good, name=123)]:
            self.assertEqual(c.post("/api/register", json=bad).status_code, 400, bad)
        self.assertEqual(c.post("/api/register", json=good).status_code, 201)
        self.assertEqual(store.app.test_client().post("/api/register", json=good).status_code, 409)  # duplicate email

    def test_wrong_password_and_lockout(self):
        c = store.app.test_client()
        for _ in range(10):
            self.assertEqual(c.post("/api/login", json={"email": "demo@bookstore.com", "password": "bad"}).status_code, 401)
        self.assertEqual(c.post("/api/login", json={"email": "demo@bookstore.com", "password": "Demo@123"}).status_code, 429)

    def test_json_required_and_bad_body(self):
        c = store.app.test_client()
        self.assertEqual(c.post("/api/login", data="email=a", content_type="application/x-www-form-urlencoded").status_code, 415)
        self.assertEqual(c.post("/api/login", json=[1, 2]).status_code, 400)

    def test_login_required_and_roles(self):
        anon = store.app.test_client()
        self.assertEqual(anon.get("/api/cart").status_code, 401)
        self.assertEqual(anon.get("/api/orders").status_code, 401)
        self.assertEqual(anon.get("/api/admin/dashboard").status_code, 401)
        cust = self.customer()
        for url in ("/api/admin/dashboard", "/api/admin/users", "/api/admin/coupons", "/api/admin/audit", "/api/admin/books", "/api/returns"):
            self.assertEqual(cust.get(url).status_code, 403, url)
        staff = self.staff()
        self.assertEqual(staff.get("/api/admin/dashboard").status_code, 403)
        self.assertEqual(staff.get("/api/admin/books").status_code, 200)
        self.assertEqual(staff.get("/api/cart").status_code, 403)  # staff do not shop
        self.assertEqual(self.admin().get("/api/admin/dashboard").status_code, 200)

    def test_disabled_user_cannot_login(self):
        a = self.admin()
        uid = [u for u in a.get("/api/admin/users").get_json()["users"] if u["email"] == "priya@bookstore.com"][0]["id"]
        self.assertEqual(a.patch(f"/api/admin/users/{uid}", json={"active": False}).status_code, 200)
        r = store.app.test_client().post("/api/login", json={"email": "priya@bookstore.com", "password": "Demo@123"})
        self.assertEqual(r.status_code, 403)


class CatalogTests(Base):
    def test_list_search_filter_sort_page(self):
        c = store.app.test_client()
        r = c.get("/api/books").get_json()
        self.assertEqual(r["total"], 20)
        self.assertEqual(len(r["books"]), 12)
        self.assertEqual(r["pages"], 2)
        self.assertEqual(len(c.get("/api/books?page=2").get_json()["books"]), 8)
        self.assertEqual(c.get("/api/books?q=atomic").get_json()["total"], 1)
        self.assertEqual(c.get("/api/books?q=Orwell").get_json()["total"], 1)
        isbn = self.book(id=1)["isbn"]
        self.assertEqual(c.get(f"/api/books?q={isbn}").get_json()["books"][0]["id"], 1)
        cats = c.get("/api/categories").get_json()["categories"]
        self.assertEqual(len(cats), 5)
        cid = [x for x in cats if x["name"] == "Classics"][0]["id"]
        self.assertEqual(c.get(f"/api/books?category={cid}").get_json()["total"], 4)
        prices = [b["price"] for b in c.get("/api/books?min=300&max=450").get_json()["books"]]
        self.assertTrue(prices and all(300 <= p <= 450 for p in prices))
        asc = [b["price"] for b in c.get("/api/books?sort=price_asc").get_json()["books"]]
        self.assertEqual(asc, sorted(asc))
        desc = [b["price"] for b in c.get("/api/books?sort=price_desc").get_json()["books"]]
        self.assertEqual(desc, sorted(desc, reverse=True))
        self.assertEqual(c.get("/api/books?sort=rating").get_json()["books"][0]["rating"] is not None, True)
        self.assertEqual(c.get("/api/books?q=zzzznothing").get_json()["total"], 0)

    def test_detail_reviews_recommendations(self):
        c = store.app.test_client()
        atomic = self.book(title="Atomic Habits")
        d = c.get(f"/api/books/{atomic['id']}").get_json()
        self.assertEqual(d["book"]["review_count"], 2)
        self.assertEqual(len(d["reviews"]), 2)
        self.assertTrue(len(d["also_bought"]) >= 1)
        self.assertNotIn(atomic["id"], [b["id"] for b in d["also_bought"]])
        self.assertEqual(c.get("/api/books/9999").status_code, 404)

    def test_review_rules(self):
        c = self.customer()
        # demo user bought and received Atomic Habits already and reviewed it -> only one review
        atomic = self.book(title="Atomic Habits")["id"]
        self.assertEqual(c.post(f"/api/books/{atomic}/reviews", json={"rating": 3}).status_code, 409)
        self.assertTrue(c.get(f"/api/books/{atomic}").get_json()["reviewed"])
        # never bought -> not allowed
        gatsby = self.book(title="The Great Gatsby")["id"]
        self.assertFalse(c.get(f"/api/books/{gatsby}").get_json()["can_review"])
        self.assertEqual(c.post(f"/api/books/{gatsby}/reviews", json={"rating": 5}).status_code, 403)
        # bought and delivered but not yet reviewed -> allowed, and validated
        alch = self.book(title="Deep Work")["id"]
        self.assertTrue(c.get(f"/api/books/{alch}").get_json()["can_review"])
        self.assertEqual(c.post(f"/api/books/{alch}/reviews", json={"rating": 9}).status_code, 400)
        self.assertEqual(c.post(f"/api/books/{alch}/reviews", json={"rating": "x"}).status_code, 400)
        self.assertEqual(c.post(f"/api/books/{alch}/reviews", json={"rating": 4, "comment": "x" * 501}).status_code, 400)
        self.assertEqual(c.post(f"/api/books/{alch}/reviews", json={"rating": 4, "comment": "Useful"}).status_code, 201)

    def test_wishlist(self):
        c = self.customer()
        self.assertTrue(c.post("/api/wishlist/3", json={}).get_json()["saved"])
        self.assertEqual(c.get("/api/wishlist").get_json()["ids"], [3])
        self.assertFalse(c.post("/api/wishlist/3", json={}).get_json()["saved"])
        self.assertEqual(c.get("/api/wishlist").get_json()["ids"], [])
        self.assertEqual(c.post("/api/wishlist/9999", json={}).status_code, 404)


class CartTests(Base):
    def test_cart_flow_and_limits(self):
        c = self.new_customer()
        r = c.post("/api/cart", json={"book_id": 1, "qty": 2}).get_json()
        self.assertEqual(r["count"], 2)
        r = c.post("/api/cart", json={"book_id": 1}).get_json()  # adds one more
        self.assertEqual(r["items"][0]["qty"], 3)
        self.assertEqual(r["subtotal"], 3 * 299)
        self.assertEqual(c.put("/api/cart/1", json={"qty": 5}).get_json()["items"][0]["qty"], 5)
        self.assertEqual(c.put("/api/cart/1", json={"qty": 0}).status_code, 400)
        self.assertEqual(c.put("/api/cart/1", json={"qty": 11}).status_code, 400)
        self.assertEqual(c.put("/api/cart/2", json={"qty": 1}).status_code, 404)  # not in cart
        self.assertEqual(c.post("/api/cart", json={"book_id": 1, "qty": 10}).status_code, 409)  # 5 + 10 > 10
        self.assertEqual(c.post("/api/cart", json={"book_id": 9999}).status_code, 404)
        self.assertEqual(c.post("/api/cart", json={"book_id": "abc"}).status_code, 400)
        self.assertEqual(c.delete("/api/cart/1", json={}).get_json()["count"], 0)

    def test_cart_cannot_exceed_stock(self):
        c = self.new_customer()
        train = self.book(title="Train to Pakistan")  # stock 3 minus demo orders
        left = self.stock_of(train["id"])
        self.assertEqual(c.post("/api/cart", json={"book_id": train["id"], "qty": left + 1}).status_code, 409)
        self.assertEqual(c.post("/api/cart", json={"book_id": train["id"], "qty": left}).status_code, 200)

    def test_out_of_stock_and_hidden_books(self):
        a, c = self.admin(), self.new_customer()
        c.post("/api/cart", json={"book_id": 1})
        a.patch("/api/admin/books/2/stock", json={"stock": 0})
        self.assertEqual(c.post("/api/cart", json={"book_id": 2}).status_code, 409)
        b = self.book(id=3)
        a.put("/api/admin/books/3", json={"title": b["title"], "author": b["author"], "isbn": b["isbn"], "category_id": b["category_id"],
                                           "price": b["price"], "stock": b["stock"], "active": False})
        self.assertEqual(c.post("/api/cart", json={"book_id": 3}).status_code, 404)
        self.assertEqual(c.get("/api/books/3").status_code, 404)
        self.assertEqual(store.app.test_client().get("/api/books?q=Guide").get_json()["total"], 0)


class OrderTests(Base):
    def test_place_order_cod(self):
        c = self.new_customer()
        before = self.stock_of(1)
        r = self.order(c, 1, 2)
        self.assertEqual(r.status_code, 201, r.get_json())
        o = r.get_json()["order"]
        self.assertEqual((o["status"], o["payment_method"], o["payment_status"]), ("placed", "COD", "pending"))
        self.assertEqual(o["total"], 598)
        self.assertEqual(o["items"][0]["price"], 299)
        self.assertEqual(self.stock_of(1), before - 2)
        self.assertEqual(c.get("/api/cart").get_json()["count"], 0)  # cart cleared
        self.assertEqual(o["code"], f"BK{o['id']:05d}")
        self.assertEqual(c.get(f"/invoice/{o['id']}").status_code, 200)
        self.assertIn(b"BK", c.get(f"/invoice/{o['id']}").data)

    def test_order_validation(self):
        c = self.new_customer()
        self.assertEqual(c.post("/api/orders", json=self.ADDRESS).status_code, 400)  # empty cart
        c.post("/api/cart", json={"book_id": 1})
        for bad in [dict(self.ADDRESS, name="A"), dict(self.ADDRESS, phone="123"), dict(self.ADDRESS, address="x"),
                    dict(self.ADDRESS, city=""), dict(self.ADDRESS, pincode="12345"), dict(self.ADDRESS, pincode="12345a"),
                    dict(self.ADDRESS, payment_method="CARD")]:
            self.assertEqual(c.post("/api/orders", json=bad).status_code, 400, bad)
        self.assertEqual(c.get("/api/cart").get_json()["count"], 1)  # nothing was lost
        self.assertEqual(c.post("/api/orders", json=self.ADDRESS).status_code, 201)

    def test_stock_race_last_copy(self):
        """Two customers want the last copy. Only one order may succeed."""
        db = store.sqlite3.connect(self.path)
        db.execute("UPDATE books SET stock=1 WHERE id=1")
        db.commit()
        db.close()
        a, b = self.new_customer("a@t.com"), self.new_customer("b@t.com")
        a.post("/api/cart", json={"book_id": 1})
        b.post("/api/cart", json={"book_id": 1})
        results = []
        t = [threading.Thread(target=lambda c=c: results.append(c.post("/api/orders", json=self.ADDRESS).status_code)) for c in (a, b)]
        [x.start() for x in t]
        [x.join() for x in t]
        self.assertEqual(sorted(results), [201, 409])
        self.assertEqual(self.stock_of(1), 0)

    def test_failed_order_changes_nothing(self):
        """If one book is out of stock the whole order rolls back (no stock lost, coupon not used)."""
        c = self.new_customer()
        c.post("/api/cart", json={"book_id": 1, "qty": 2})
        c.post("/api/cart", json={"book_id": 2, "qty": 1})
        self.admin().patch("/api/admin/books/2/stock", json={"stock": 0})
        s1 = self.stock_of(1)
        r = c.post("/api/orders", json=dict(self.ADDRESS, coupon="WELCOME10"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.stock_of(1), s1)
        used = store.sqlite3.connect(self.path).execute("SELECT used_count FROM coupons WHERE code='WELCOME10'").fetchone()[0]
        self.assertEqual(used, 0)

    def test_coupon_rules(self):
        c = self.new_customer()
        c.post("/api/cart", json={"book_id": 1})  # Rs 299
        self.assertEqual(c.get("/api/coupons/check?code=WELCOME10").status_code, 422)  # below minimum 300
        self.assertEqual(c.get("/api/coupons/check?code=NOPE").status_code, 422)
        self.assertEqual(c.get("/api/coupons/check?code=OLD5").status_code, 422)  # expired
        c.post("/api/cart", json={"book_id": 1})  # Rs 598
        ok = c.get("/api/coupons/check?code=welcome10").get_json()
        self.assertEqual((ok["discount"], ok["total"]), (59, 539))
        r = c.post("/api/orders", json=dict(self.ADDRESS, coupon="WELCOME10")).get_json()["order"]
        self.assertEqual((r["discount"], r["total"], r["coupon_code"]), (59, 539, "WELCOME10"))
        # maximum discount cap: BIG20 on Rs 3000 -> 20% = 600, capped at 500
        c.post("/api/cart", json={"book_id": 19, "qty": 5})  # Clean Code 599 x5 = 2995
        self.assertEqual(c.get("/api/coupons/check?code=BIG20").get_json()["discount"], 500)

    def test_coupon_usage_limit_and_inactive(self):
        a = self.admin()
        self.assertEqual(a.post("/api/admin/coupons", json={"code": "ONCE", "percent": 10, "usage_limit": 1}).status_code, 201)
        c1, c2 = self.new_customer("c1@t.com"), self.new_customer("c2@t.com")
        self.assertEqual(self.order(c1, 1, 1, "ONCE").status_code, 201)
        r = self.order(c2, 1, 1, "ONCE")
        self.assertEqual(r.status_code, 422)
        self.assertIn("limit", r.get_json()["error"])
        a.patch("/api/admin/coupons/ONCE", json={})  # switch off
        self.assertEqual(c2.get("/api/coupons/check?code=ONCE").status_code, 422)

    def test_customer_cancel_returns_stock_and_coupon(self):
        c = self.new_customer()
        s0 = self.stock_of(1)
        o = self.order(c, 1, 2, "WELCOME10").get_json()["order"]
        self.assertEqual(self.stock_of(1), s0 - 2)
        self.assertEqual(c.post(f"/api/orders/{o['id']}/cancel", json={}).status_code, 400)  # reason needed
        self.assertEqual(c.post(f"/api/orders/{o['id']}/cancel", json={"reason": "free text"}).status_code, 400)
        r = c.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Ordered by mistake"})
        self.assertEqual(r.status_code, 200)
        o2 = r.get_json()["order"]
        self.assertEqual((o2["status"], o2["payment_status"], o2["cancel_reason"]), ("cancelled", "not_applicable", "Ordered by mistake"))
        self.assertEqual(self.stock_of(1), s0)
        used = store.sqlite3.connect(self.path).execute("SELECT used_count FROM coupons WHERE code='WELCOME10'").fetchone()[0]
        self.assertEqual(used, 0)
        # a second cancel (double click) must not return stock twice
        self.assertEqual(c.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Ordered by mistake"}).status_code, 409)
        self.assertEqual(self.stock_of(1), s0)
        self.assertEqual(o2["events"][-1]["status"], "cancelled")

    def test_cancel_rules_by_status(self):
        c, s = self.new_customer(), self.staff()
        o = self.order(c).get_json()["order"]
        s.post(f"/api/orders/{o['id']}/status", json={"status": "packed"})
        self.assertEqual(c.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Other"}).status_code, 200)  # packed: ok
        o = self.order(c).get_json()["order"]
        s.post(f"/api/orders/{o['id']}/status", json={"status": "packed"})
        s.post(f"/api/orders/{o['id']}/status", json={"status": "shipped"})
        r = c.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Other"})
        self.assertEqual(r.status_code, 409)  # shipped: no cancel
        self.assertIn("Refuse", r.get_json()["error"])

    def test_staff_cancel_needs_reason_and_is_audited(self):
        c, s = self.new_customer(), self.staff()
        o = self.order(c).get_json()["order"]
        self.assertEqual(s.post(f"/api/orders/{o['id']}/cancel", json={"reason": "ab"}).status_code, 400)
        self.assertEqual(s.post(f"/api/orders/{o['id']}/cancel", json={"reason": "Book damaged in store"}).status_code, 200)
        log = self.admin().get("/api/admin/audit").get_json()["log"]
        self.assertTrue(any(l["action"] == "order_cancelled" for l in log))

    def test_status_flow_and_refusal(self):
        c, s = self.new_customer(), self.staff()
        o = self.order(c).get_json()["order"]
        oid = o["id"]
        self.assertEqual(s.post(f"/api/orders/{oid}/status", json={"status": "delivered"}).status_code, 409)  # cannot skip
        self.assertEqual(s.post(f"/api/orders/{oid}/status", json={"status": "bogus"}).status_code, 409)
        self.assertEqual(c.post(f"/api/orders/{oid}/status", json={"status": "packed"}).status_code, 403)  # customer cannot
        for st in ("packed", "shipped"):
            self.assertEqual(s.post(f"/api/orders/{oid}/status", json={"status": st}).status_code, 200)
        s0 = self.stock_of(1)
        r = s.post(f"/api/orders/{oid}/status", json={"status": "refused"}).get_json()["order"]
        self.assertEqual((r["status"], r["payment_status"]), ("refused", "not_applicable"))
        self.assertEqual(self.stock_of(1), s0 + 1)
        self.assertEqual(s.post(f"/api/orders/{oid}/status", json={"status": "delivered"}).status_code, 409)  # final state

    def test_delivery_marks_cash_paid(self):
        c, s = self.new_customer(), self.staff()
        oid = self.order(c).get_json()["order"]["id"]
        for st in ("packed", "shipped", "delivered"):
            r = s.post(f"/api/orders/{oid}/status", json={"status": st})
        o = r.get_json()["order"]
        self.assertEqual((o["status"], o["payment_status"]), ("delivered", "paid"))
        self.assertTrue(o["can_return"])

    def test_order_visibility(self):
        a, b = self.new_customer("a@t.com"), self.new_customer("b@t.com")
        oid = self.order(a).get_json()["order"]["id"]
        self.assertEqual(b.get(f"/api/orders/{oid}").status_code, 404)
        self.assertEqual(b.get(f"/invoice/{oid}").status_code, 404)
        self.assertEqual(b.post(f"/api/orders/{oid}/cancel", json={"reason": "Other"}).status_code, 404)
        self.assertEqual(len(a.get("/api/orders").get_json()["orders"]), 1)
        self.assertEqual(len(b.get("/api/orders").get_json()["orders"]), 0)
        s = self.staff()
        self.assertEqual(s.get(f"/api/orders/{oid}").status_code, 200)
        self.assertGreater(len(s.get("/api/orders").get_json()["orders"]), 1)
        self.assertEqual(len(s.get("/api/orders?status=placed").get_json()["orders"]), 2)
        self.assertEqual(len(s.get(f"/api/orders?q=BK{oid:05d}").get_json()["orders"]), 1)
        self.assertEqual(store.app.test_client().get(f"/invoice/{oid}").status_code, 401)

    def test_price_snapshot(self):
        c, a = self.new_customer(), self.admin()
        oid = self.order(c).get_json()["order"]["id"]
        b = self.book(id=1)
        a.put("/api/admin/books/1", json={"title": b["title"], "author": b["author"], "isbn": b["isbn"], "category_id": b["category_id"],
                                           "price": 999, "stock": b["stock"]})
        o = c.get(f"/api/orders/{oid}").get_json()["order"]
        self.assertEqual((o["items"][0]["price"], o["total"]), (299, 299))


class ReturnTests(Base):
    def delivered(self):
        c, s = self.new_customer(), self.staff()
        oid = self.order(c, 1, 2).get_json()["order"]["id"]
        for st in ("packed", "shipped", "delivered"):
            s.post(f"/api/orders/{oid}/status", json={"status": st})
        return c, s, oid

    def test_return_approved(self):
        c, s, oid = self.delivered()
        self.assertEqual(c.post(f"/api/orders/{oid}/return", json={"reason": "made up"}).status_code, 400)
        r = c.post(f"/api/orders/{oid}/return", json={"reason": "Damaged or torn pages"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(c.post(f"/api/orders/{oid}/return", json={"reason": "Other"}).status_code, 409)  # only once
        rid = s.get("/api/returns").get_json()["returns"][0]["id"]
        before = self.stock_of(1)
        self.assertEqual(s.post(f"/api/returns/{rid}/decide", json={"approve": "yes"}).status_code, 400)
        self.assertEqual(s.post(f"/api/returns/{rid}/decide", json={"approve": True}).status_code, 200)
        o = c.get(f"/api/orders/{oid}").get_json()["order"]
        self.assertEqual((o["status"], o["payment_status"]), ("returned", "refund_pending"))
        self.assertEqual(self.stock_of(1), before + 2)
        self.assertEqual(s.post(f"/api/returns/{rid}/decide", json={"approve": True}).status_code, 409)  # decided already

    def test_return_rejected_needs_reason(self):
        c, s, oid = self.delivered()
        c.post(f"/api/orders/{oid}/return", json={"reason": "Other"})
        rid = s.get("/api/returns").get_json()["returns"][0]["id"]
        self.assertEqual(s.post(f"/api/returns/{rid}/decide", json={"approve": False}).status_code, 400)
        self.assertEqual(s.post(f"/api/returns/{rid}/decide", json={"approve": False, "note": "Book looks used"}).status_code, 200)
        o = c.get(f"/api/orders/{oid}").get_json()["order"]
        self.assertEqual(o["status"], "delivered")
        self.assertEqual(o["return_request"]["status"], "rejected")

    def test_return_window_and_status(self):
        c, s, oid = self.delivered()
        db = store.sqlite3.connect(self.path)
        db.execute("UPDATE orders SET delivered_at='2020-01-01 10:00:00' WHERE id=?", (oid,))
        db.commit()
        db.close()
        self.assertFalse(c.get(f"/api/orders/{oid}").get_json()["order"]["can_return"])
        self.assertEqual(c.post(f"/api/orders/{oid}/return", json={"reason": "Other"}).status_code, 409)
        placed = self.order(c, 2).get_json()["order"]["id"]
        self.assertEqual(c.post(f"/api/orders/{placed}/return", json={"reason": "Other"}).status_code, 409)  # not delivered


class AdminTests(Base):
    BOOK = {"title": "New Book", "author": "Some Author", "isbn": "9781234567897", "category_id": 1, "price": 350, "stock": 10}

    def test_add_edit_book_validation(self):
        a = self.admin()
        self.assertEqual(a.post("/api/admin/books", json=self.BOOK).status_code, 201)
        self.assertEqual(a.post("/api/admin/books", json=self.BOOK).status_code, 409)  # duplicate ISBN
        for key, val in [("price", 0), ("price", -5), ("price", "abc"), ("price", 100001), ("stock", -1), ("stock", 1.5 if False else "x"),
                         ("isbn", "123"), ("isbn", "abcdefghij"), ("category_id", 9999), ("title", ""), ("cover_url", "ftp://x")]:
            bad = dict(self.BOOK, isbn="9780000000002")
            bad[key] = val
            self.assertIn(a.post("/api/admin/books", json=bad).status_code, (400,), (key, val))
        self.assertEqual(self.staff().post("/api/admin/books", json=self.BOOK).status_code, 403)
        new = a.get("/api/admin/books?q=New Book").get_json()["books"][0]
        edit = dict(self.BOOK, price=400, stock=3)
        self.assertEqual(a.put(f"/api/admin/books/{new['id']}", json=edit).status_code, 200)
        self.assertEqual(a.put("/api/admin/books/9999", json=edit).status_code, 404)
        clash = dict(edit, isbn=self.book(id=1)["isbn"])
        self.assertEqual(a.put(f"/api/admin/books/{new['id']}", json=clash).status_code, 409)
        self.assertEqual(a.get("/api/admin/books?low_stock=1").get_json()["total"] >= 1, True)

    def test_staff_can_change_stock_only(self):
        s = self.staff()
        self.assertEqual(s.patch("/api/admin/books/1/stock", json={"stock": 77}).status_code, 200)
        self.assertEqual(self.stock_of(1), 77)
        self.assertEqual(s.patch("/api/admin/books/1/stock", json={"stock": -1}).status_code, 400)
        self.assertEqual(s.patch("/api/admin/books/9999/stock", json={"stock": 1}).status_code, 404)
        log = self.admin().get("/api/admin/audit").get_json()["log"]
        self.assertIn("77", log[0]["detail"])

    def test_categories(self):
        a = self.admin()
        self.assertEqual(a.post("/api/admin/categories", json={"name": "Comics"}).status_code, 201)
        self.assertEqual(a.post("/api/admin/categories", json={"name": "Comics"}).status_code, 409)
        self.assertEqual(a.post("/api/admin/categories", json={"name": "x"}).status_code, 400)

    def test_coupon_validation(self):
        a = self.admin()
        ok = {"code": "SAVE5", "percent": 5, "usage_limit": 10}
        self.assertEqual(a.post("/api/admin/coupons", json=ok).status_code, 201)
        self.assertEqual(a.post("/api/admin/coupons", json=ok).status_code, 409)
        for bad in [dict(ok, code="a b"), dict(ok, code="AB"), dict(ok, percent=0), dict(ok, percent=95), dict(ok, usage_limit=0),
                    dict(ok, expires_on="31-12-2030"), dict(ok, min_amount=-1)]:
            self.assertEqual(a.post("/api/admin/coupons", json=dict(bad, code=bad["code"] if bad["code"] != "SAVE5" else "OTHER1")).status_code, 400, bad)
        self.assertEqual(a.patch("/api/admin/coupons/NOPE", json={}).status_code, 404)

    def test_users(self):
        a = self.admin()
        u = {"name": "New Staff", "email": "ns@t.com", "phone": "9123456780", "password": "secret1", "role": "staff"}
        self.assertEqual(a.post("/api/admin/users", json=u).status_code, 201)
        self.assertEqual(a.post("/api/admin/users", json=u).status_code, 409)
        self.assertEqual(a.post("/api/admin/users", json=dict(u, email="x@t.com", role="boss")).status_code, 400)
        self.assertEqual(self.client("ns@t.com", "secret1").get("/api/admin/books").status_code, 200)
        users = a.get("/api/admin/users").get_json()["users"]
        me = [x for x in users if x["email"] == "admin@bookstore.com"][0]["id"]
        self.assertEqual(a.patch(f"/api/admin/users/{me}", json={"role": "customer"}).status_code, 409)  # not yourself
        self.assertEqual(a.patch("/api/admin/users/9999", json={"role": "staff"}).status_code, 404)
        self.assertEqual(a.patch("/api/admin/users/2", json={"role": "wizard"}).status_code, 400)

    def test_dashboard(self):
        a, c = self.admin(), self.new_customer()
        d0 = a.get("/api/admin/dashboard").get_json()
        self.assertEqual(len(d0["last_7_days"]), 7)
        self.assertTrue(d0["top_books"] and d0["low_stock"])
        self.assertTrue(all(b["stock"] <= 5 for b in d0["low_stock"]))
        self.order(c, 19, 1)  # Rs 599
        d1 = a.get("/api/admin/dashboard").get_json()
        self.assertEqual(d1["sales_today"], d0["sales_today"] + 599)
        oid = c.get("/api/orders").get_json()["orders"][0]["id"]
        c.post(f"/api/orders/{oid}/cancel", json={"reason": "Other"})
        d2 = a.get("/api/admin/dashboard").get_json()
        self.assertEqual(d2["sales_today"], d0["sales_today"])  # cancelled orders are not sales


class PageTests(Base):
    def test_pages_and_unknown_routes(self):
        c = store.app.test_client()
        self.assertEqual(c.get("/").status_code, 200)
        self.assertEqual(c.get("/api/nothing").status_code, 404)
        self.assertEqual(c.get("/static/app.js").status_code, 200)
        self.assertEqual(c.get("/static/style.css").status_code, 200)
        cfg = c.get("/api/config").get_json()
        self.assertTrue(cfg["demo"])

    def test_xss_text_is_stored_as_plain_text(self):
        """The server keeps text as-is; the frontend escapes it. The invoice page is escaped by Jinja."""
        c = self.new_customer()
        c.post("/api/cart", json={"book_id": 1})
        r = c.post("/api/orders", json=dict(self.ADDRESS, name="<script>alert(1)</script>"))
        oid = r.get_json()["order"]["id"]
        html = c.get(f"/invoice/{oid}").data
        self.assertNotIn(b"<script>alert(1)</script>", html)
        self.assertIn(b"&lt;script&gt;", html)


if __name__ == "__main__":
    unittest.main()
