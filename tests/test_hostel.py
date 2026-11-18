import sqlite3
import unittest
from datetime import date

import hostel
import rules
from hostel import HostelError

TODAY = date(2026, 11, 18)


def make_db():
    db = hostel.connect(":memory:")
    hostel.init_db(db)
    return db


def admit(db, room_id, name="Asha Rao", gender="F", phone="9876543210", joined=date(2026, 1, 1), deposit=10000,
          email=None):
    return hostel.admit_resident(db, name=name, gender=gender, phone=phone,
                                 email=email or f"{phone}@example.com", room_id=room_id,
                                 joined_on=joined, deposit=deposit)[0]


class HostelCase(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.boys = hostel.add_room(self.db, "B101", "Boys", 1, 2, 5000)
        self.girls = hostel.add_room(self.db, "G101", "Girls", 1, 2, 7000, True)

    def balance(self, rid, month):
        return self.db.execute("SELECT balance FROM invoice_summary WHERE resident_id=? AND month=?",
                               (rid, month)).fetchone()[0]


class RoomAndAdmissionTests(HostelCase):
    def test_duplicate_room_refused(self):
        with self.assertRaisesRegex(HostelError, "already exists"):
            hostel.add_room(self.db, "b101", "Boys", 1, 2, 5000)

    def test_third_person_in_two_bed_room_refused(self):
        admit(self.db, self.girls, phone="9000000001")
        admit(self.db, self.girls, name="B", phone="9000000002")
        with self.assertRaisesRegex(HostelError, "Room is full"):
            admit(self.db, self.girls, name="C", phone="9000000003")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM residents").fetchone()[0], 2)

    def test_boy_in_girls_block_refused_and_nothing_saved(self):
        with self.assertRaisesRegex(HostelError, "Girls block"):
            admit(self.db, self.girls, gender="M")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM residents").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_validation_messages(self):
        admit(self.db, self.girls)
        with self.assertRaisesRegex(HostelError, "phone number already exists"):
            admit(self.db, self.girls, name="Other", email="other@example.com")
        with self.assertRaisesRegex(HostelError, "10-digit"):
            admit(self.db, self.girls, phone="12345")

    def test_admission_creates_hashed_login(self):
        rid, password = hostel.admit_resident(self.db, name="Asha", gender="F", phone="9876543210",
                                              email="Asha@Example.com", room_id=self.girls, joined_on=TODAY)
        user = self.db.execute("SELECT * FROM users WHERE resident_id=?", (rid,)).fetchone()
        self.assertEqual(user["email"], "asha@example.com")
        self.assertNotEqual(user["password_hash"], password)
        self.assertEqual(user["role"], "resident")

    def test_database_trigger_blocks_overfill_even_without_app_check(self):
        admit(self.db, self.girls, phone="9000000001")
        admit(self.db, self.girls, name="B", phone="9000000002")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""INSERT INTO residents (name, gender, phone, email, room_id, joined_on)
                               VALUES ('X','F','9000000009','x@x.co',?, '2026-01-01')""", (self.girls,))


class MoveTests(HostelCase):
    def test_move_rules(self):
        other = hostel.add_room(self.db, "G102", "Girls", 1, 1, 6000)
        a = admit(self.db, self.girls, phone="9000000001")
        admit(self.db, other, name="B", phone="9000000002")
        with self.assertRaisesRegex(HostelError, "full"):
            hostel.move_resident(self.db, a, other)
        with self.assertRaisesRegex(HostelError, "already"):
            hostel.move_resident(self.db, a, self.girls)
        free = hostel.add_room(self.db, "G103", "Girls", 2, 2, 6500)
        hostel.move_resident(self.db, a, free)
        self.assertEqual(hostel.get_resident(self.db, a)["room_number"], "G103")

    def test_cannot_move_to_other_block(self):
        a = admit(self.db, self.girls)
        with self.assertRaisesRegex(HostelError, "Boys block"):
            hostel.move_resident(self.db, a, self.boys)


class BillingTests(HostelCase):
    def test_bills_never_generated_twice_and_mid_month_joiner_prorated(self):
        old = admit(self.db, self.girls, phone="9000000001", joined=date(2026, 1, 1))
        new = admit(self.db, self.girls, name="New", phone="9000000002", joined=date(2026, 11, 16))
        self.assertEqual(hostel.generate_bills(self.db, "2026-11", TODAY), 2)
        self.assertEqual(hostel.generate_bills(self.db, "2026-11", TODAY), 0)
        inv = self.db.execute("SELECT * FROM invoices WHERE resident_id=?", (new,)).fetchone()
        self.assertEqual((inv["rent"], inv["due_on"]), (3500, "2026-11-23"))
        self.assertEqual(self.balance(old, "2026-11"), 7000)

    def test_future_month_refused(self):
        with self.assertRaisesRegex(HostelError, "future"):
            hostel.generate_bills(self.db, "2026-12", TODAY)

    def test_partial_payment_overpay_and_single_late_fee(self):
        rid = admit(self.db, self.girls)
        hostel.generate_bills(self.db, "2026-11", date(2026, 11, 5))
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        _, receipt = hostel.record_payment(self.db, inv_id, 3000, "Cash", date(2026, 11, 5))
        self.assertTrue(receipt.startswith("RCP-2026-"))
        with self.assertRaisesRegex(HostelError, "more than the balance"):
            hostel.record_payment(self.db, inv_id, 4001, "Cash", date(2026, 11, 6))
        self.assertEqual(hostel.apply_late_fees(self.db, date(2026, 11, 11)), 1)
        self.assertEqual(hostel.apply_late_fees(self.db, date(2026, 11, 12)), 0)     # only once
        self.assertEqual(self.balance(rid, "2026-11"), 4200)
        hostel.record_payment(self.db, inv_id, 4200, "UPI", date(2026, 11, 12))
        self.assertEqual(self.balance(rid, "2026-11"), 0)
        with self.assertRaisesRegex(HostelError, "already paid"):
            hostel.record_payment(self.db, inv_id, 1, "UPI", date(2026, 11, 12))

    def test_bill_paid_in_time_gets_no_late_fee(self):
        rid = admit(self.db, self.girls)
        hostel.generate_bills(self.db, "2026-11", date(2026, 11, 1))
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        hostel.record_payment(self.db, inv_id, 7000, "Cash", date(2026, 11, 9))
        hostel.apply_late_fees(self.db, TODAY)
        self.assertEqual(self.db.execute("SELECT late_fee FROM invoices").fetchone()[0], 0)

    def test_late_payment_must_include_fee(self):
        admit(self.db, self.girls)
        hostel.generate_bills(self.db, "2026-10", date(2026, 10, 1))
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        _, rno = hostel.record_payment(self.db, inv_id, 7200, "Cash", TODAY)
        self.assertEqual(hostel.get_receipt(self.db, self.db.execute(
            "SELECT id FROM payments WHERE receipt_no=?", (rno,)).fetchone()[0])["amount"], 7200)

    def test_payment_validation(self):
        admit(self.db, self.girls)
        hostel.generate_bills(self.db, "2026-11", TODAY)
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        for bad in (0, -5, "abc"):
            with self.assertRaises(HostelError):
                hostel.record_payment(self.db, inv_id, bad, "Cash", TODAY)
        with self.assertRaisesRegex(HostelError, "payment mode"):
            hostel.record_payment(self.db, inv_id, 100, "Deposit", TODAY)      # reserved for check-out


class CheckOutTests(HostelCase):
    def test_checkout_bills_month_frees_bed_and_settles_deposit(self):
        rid = admit(self.db, self.girls, joined=date(2026, 1, 1), deposit=10000)
        hostel.generate_bills(self.db, "2026-10", date(2026, 10, 1))        # October unpaid
        result = hostel.check_out(self.db, rid, TODAY)                      # + November; both past due on the 18th
        self.assertEqual(result["dues"], 2 * (7000 + rules.LATE_FEE))
        self.assertEqual((result["kind"], result["amount"]), ("collect", 4400))
        res = hostel.get_resident(self.db, rid)
        self.assertEqual((res["status"], res["room_id"], res["left_on"]), ("left", None, "2026-11-18"))
        self.assertEqual(self.db.execute("SELECT COALESCE(SUM(balance),0) FROM invoice_summary").fetchone()[0], 0)
        self.assertEqual(hostel.generate_bills(self.db, "2026-12", date(2026, 12, 1)), 0)   # no later bills
        self.assertEqual(hostel.dashboard(self.db, TODAY)["occupied"], 0)

    def test_checkout_refund(self):
        rid = admit(self.db, self.girls, deposit=10000)
        result = hostel.check_out(self.db, rid, TODAY)
        self.assertEqual((result["kind"], result["amount"], result["dues"]), ("refund", 2800, 7200))   # 7,000 rent + 200 late fee

    def test_cannot_check_out_twice(self):
        rid = admit(self.db, self.girls)
        hostel.check_out(self.db, rid, TODAY)
        with self.assertRaises(HostelError):
            hostel.check_out(self.db, rid, TODAY)

    def test_freed_bed_can_be_reused(self):
        a = admit(self.db, self.girls, phone="9000000001")
        admit(self.db, self.girls, name="B", phone="9000000002")
        hostel.check_out(self.db, a, TODAY)
        admit(self.db, self.girls, name="C", phone="9000000003")


class ComplaintNoticeDashboardTests(HostelCase):
    def test_complaints(self):
        rid = admit(self.db, self.girls)
        with self.assertRaises(HostelError):
            hostel.raise_complaint(self.db, rid, "Plumbing", "short", TODAY)
        with self.assertRaises(HostelError):
            hostel.raise_complaint(self.db, rid, "Alien", "Something long enough here", TODAY)
        cid = hostel.raise_complaint(self.db, rid, "Plumbing", "Tap is leaking badly", TODAY)
        hostel.update_complaint(self.db, cid, "resolved", "Washer replaced", TODAY)
        row = hostel.list_complaints(self.db)[0]
        self.assertEqual((row["status"], row["resolved_on"], row["remark"]), ("resolved", "2026-11-18", "Washer replaced"))
        hostel.update_complaint(self.db, cid, "open", "", TODAY)
        self.assertIsNone(hostel.list_complaints(self.db)[0]["resolved_on"])

    def test_notice(self):
        hostel.post_notice(self.db, "Water", "Off on Sunday", TODAY)
        self.assertEqual(hostel.list_notices(self.db)[0]["title"], "Water")
        with self.assertRaises(HostelError):
            hostel.post_notice(self.db, " ", "x", TODAY)

    def test_dashboard_occupancy(self):
        db = make_db()
        r = hostel.add_room(db, "G1", "Girls", 1, 4, 5000)
        hostel.add_room(db, "G2", "Girls", 1, 1, 5000)
        admit(db, r)
        s = hostel.dashboard(db, TODAY)
        self.assertEqual((s["beds"], s["occupied"], s["occupancy"]), (5, 1, 20))

    def test_dashboard_money(self):
        admit(self.db, self.girls)
        hostel.generate_bills(self.db, "2026-11", TODAY)
        inv_id = self.db.execute("SELECT id FROM invoices").fetchone()[0]
        hostel.record_payment(self.db, inv_id, 2000, "Cash", TODAY)
        s = hostel.dashboard(self.db, TODAY)
        self.assertEqual((s["collected"], s["pending"], s["pending_bills"]), (2000, 5200, 1))   # 7,000 + 200 late fee - 2,000 paid

    def test_search_treats_wildcards_literally(self):
        admit(self.db, self.girls)
        self.assertEqual(len(hostel.list_residents(self.db, "%")), 0)
        self.assertEqual(len(hostel.list_residents(self.db, "asha")), 1)


if __name__ == "__main__":
    unittest.main()
