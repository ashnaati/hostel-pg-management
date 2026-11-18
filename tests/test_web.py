import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

import hostel
from app import create_app

TODAY = "2026-11-18"


class WebCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "DATABASE": str(Path(self.tmp.name) / "t.db"),
                               "SECRET_KEY": "test", "TODAY": TODAY})
        self.db = hostel.connect(self.app.config["DATABASE"])
        hostel.create_user(self.db, "warden@hostel.local", "warden123", "warden")
        self.girls = hostel.add_room(self.db, "G101", "Girls", 1, 2, 7000)
        self.boys = hostel.add_room(self.db, "B101", "Boys", 1, 1, 5000)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def token(self, client=None):
        client = client or self.client
        html = client.get("/login").get_data(as_text=True)
        return re.search(r'name="csrf" value="([^"]+)"', html).group(1)

    def login(self, email="warden@hostel.local", password="warden123", client=None):
        client = client or self.client
        return client.post("/login", data={"email": email, "password": password, "csrf": self.token(client)})

    def post(self, url, data=None, client=None):
        client = client or self.client
        with client.session_transaction() as s:
            tok = s.get("csrf") or "none"
        return client.post(url, data={**(data or {}), "csrf": tok}, follow_redirects=True)

    def add_resident(self, name="Asha Rao", phone="9876543210", room=None, gender="F", joined="2026-01-01"):
        rid, pw = hostel.admit_resident(self.db, name=name, gender=gender, phone=phone, email=f"{phone}@ex.com",
                                        room_id=room or self.girls, joined_on=joined, deposit=10000, password="resident123")
        return rid


class AccessTests(WebCase):
    def test_warden_page_without_login_redirects(self):
        r = self.client.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_resident_gets_403_on_warden_pages(self):
        self.add_resident()
        self.login("9876543210@ex.com", "resident123")
        for url in ("/dashboard", "/rooms", "/residents", "/bills", "/complaints", "/notices"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_wrong_password(self):
        self.assertIn("Incorrect email or password", self.login(password="nope").get_data(as_text=True))

    def test_post_without_csrf_token_is_rejected_and_saves_nothing(self):
        self.login()
        r = self.client.post("/rooms/new", data={"number": "X1", "block": "Boys", "floor": 1, "capacity": 2, "rent": 100})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM rooms WHERE number='X1'").fetchone()[0], 0)

    def test_left_resident_cannot_log_in(self):
        rid = self.add_resident()
        hostel.check_out(self.db, rid, date.fromisoformat(TODAY))
        self.assertIn("Account is closed", self.login("9876543210@ex.com", "resident123").get_data(as_text=True))

    def test_open_redirect_is_ignored(self):
        r = self.client.post("/login?next=//evil.example", data={"email": "warden@hostel.local", "password": "warden123",
                                                                "csrf": self.token()})
        self.assertNotIn("evil", r.headers["Location"])

    def test_security_headers(self):
        r = self.client.get("/login")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])


class WardenFlowTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_dashboard_and_room_map(self):
        self.add_resident()
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("33%", html)                       # 1 of 3 beds
        self.assertIn("Asha Rao", self.client.get("/rooms").get_data(as_text=True))

    def test_admit_through_form_and_full_room(self):
        self.add_resident(phone="9000000001")
        self.add_resident(name="B", phone="9000000002")
        form = {"name": "C", "gender": "F", "phone": "9000000003", "email": "c@ex.com", "room_id": self.girls,
                "joined_on": TODAY, "deposit": 5000}
        self.assertIn("Room is full", self.post("/residents/new", form).get_data(as_text=True))

    def test_admit_shows_login_once(self):
        form = {"name": "C", "gender": "F", "phone": "9000000003", "email": "c@ex.com", "room_id": self.girls,
                "joined_on": TODAY, "deposit": 5000}
        html = self.post("/residents/new", form).get_data(as_text=True)
        self.assertIn("Resident admitted", html)
        self.assertIn("c@ex.com", html)

    def test_generate_bills_pay_late_bill_and_csv(self):
        self.add_resident()
        self.client.get("/rooms")
        self.post("/bills/generate", {"month": "2026-10"})
        self.assertIn("Asha Rao", self.client.get("/bills?month=2026-10").get_data(as_text=True))
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        r = self.post(f"/invoices/{inv_id}/pay", {"amount": 7200, "mode": "UPI"})
        self.assertIn("RCP-2026-", r.get_data(as_text=True))
        self.assertIn("₹7,200", r.get_data(as_text=True))
        csv_text = self.client.get("/bills/export?month=2026-10").get_data(as_text=True)
        self.assertIn("Asha Rao", csv_text)
        self.assertIn("7200", csv_text)

    def test_overpayment_refused(self):
        self.add_resident()
        self.post("/bills/generate", {"month": "2026-11"})
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        self.assertIn("more than the balance", self.post(f"/invoices/{inv_id}/pay", {"amount": 99999, "mode": "Cash"}).get_data(as_text=True))

    def test_csv_export_defuses_formulas(self):
        self.add_resident(name="=HYPERLINK(1)")
        self.post("/bills/generate", {"month": "2026-11"})
        self.assertIn("'=HYPERLINK", self.client.get("/bills/export?month=2026-11").get_data(as_text=True))

    def test_bad_month_is_400(self):
        self.assertEqual(self.client.get("/bills?month=banana").status_code, 400)

    def test_move_and_checkout(self):
        rid = self.add_resident()
        spare = hostel.add_room(self.db, "G102", "Girls", 1, 2, 6000)
        self.assertIn("Room changed", self.post(f"/residents/{rid}/move", {"room_id": spare}).get_data(as_text=True))
        self.assertIn("Tick the box", self.post(f"/residents/{rid}/checkout", {}).get_data(as_text=True))
        self.assertIn("Checked out", self.post(f"/residents/{rid}/checkout", {"confirm": "yes"}).get_data(as_text=True))

    def test_complaint_workflow_and_notice(self):
        rid = self.add_resident()
        cid = hostel.raise_complaint(self.db, rid, "Plumbing", "Tap is leaking badly", date.fromisoformat(TODAY))
        self.post(f"/complaints/{cid}", {"status": "in_progress", "remark": "Plumber called"})
        self.assertIn("Plumber called", self.client.get("/complaints").get_data(as_text=True))
        self.assertIn("Notice posted", self.post("/notices", {"title": "Water", "body": "Off on Sunday"}).get_data(as_text=True))


class ResidentPortalTests(WebCase):
    def setUp(self):
        super().setUp()
        self.rid = self.add_resident()
        self.mate = self.add_resident(name="Meera Iyer", phone="9000000002")
        self.resident = self.app.test_client()
        self.login("9876543210@ex.com", "resident123", client=self.resident)

    def test_portal_shows_roommate_and_escapes_input(self):
        html = self.resident.get("/me").get_data(as_text=True)
        self.assertIn("Meera Iyer", html)
        r = self.post("/me/complaints", {"category": "Other", "description": "<b>bold</b> script problem"}, client=self.resident)
        page = r.get_data(as_text=True)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", page)
        self.assertNotIn("<b>bold</b>", page)

    def test_resident_cannot_open_another_residents_receipt(self):
        hostel.generate_bills(self.db, "2026-11", date.fromisoformat(TODAY))
        mate_inv = self.db.execute("SELECT id FROM invoices WHERE resident_id=?", (self.mate,)).fetchone()[0]
        pid, _ = hostel.record_payment(self.db, mate_inv, 1000, "Cash", date.fromisoformat(TODAY))
        self.assertEqual(self.resident.get(f"/receipts/{pid}").status_code, 404)
        own_inv = self.db.execute("SELECT id FROM invoices WHERE resident_id=?", (self.rid,)).fetchone()[0]
        own, _ = hostel.record_payment(self.db, own_inv, 1000, "Cash", date.fromisoformat(TODAY))
        self.assertEqual(self.resident.get(f"/receipts/{own}").status_code, 200)

    def test_resident_sees_only_own_complaints(self):
        hostel.raise_complaint(self.db, self.mate, "Noise", "Roommate's secret complaint here", date.fromisoformat(TODAY))
        self.assertNotIn("secret complaint", self.resident.get("/me").get_data(as_text=True))

    def test_short_complaint_refused(self):
        self.assertIn("at least 10 characters",
                      self.post("/me/complaints", {"category": "Other", "description": "bad"}, client=self.resident).get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()


class ProductionConfigTests(unittest.TestCase):
    def test_secret_key_is_set_when_not_supplied(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app({"DATABASE": str(Path(tmp) / "t.db")})
            self.assertTrue(app.config["SECRET_KEY"])
            self.assertEqual(app.test_client().get("/login").status_code, 200)
