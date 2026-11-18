import unittest
from datetime import date

import rules


class RentTests(unittest.TestCase):
    def test_full_month(self):
        self.assertEqual(rules.rent_for_month(7000, date(2026, 1, 1), "2026-11"), 7000)

    def test_joining_month_is_prorated(self):
        self.assertEqual(rules.rent_for_month(7000, date(2026, 11, 16), "2026-11"), 3500)
        self.assertEqual(rules.rent_for_month(6000, date(2026, 2, 15), "2026-02"), 3000)

    def test_rounding_half_up(self):
        # 18 of 31 days of 5500 = 3193.5
        self.assertEqual(rules.rent_for_month(5500, date(2026, 10, 14), "2026-10"), 3194)

    def test_month_before_joining_is_free(self):
        self.assertEqual(rules.rent_for_month(7000, date(2026, 11, 16), "2026-10"), 0)

    def test_billable_months(self):
        joined, left = date(2026, 9, 20), date(2026, 11, 5)
        self.assertFalse(rules.is_billable(joined, left, "2026-08"))
        self.assertTrue(rules.is_billable(joined, left, "2026-09"))
        self.assertTrue(rules.is_billable(joined, left, "2026-11"))     # leaving month is billed
        self.assertFalse(rules.is_billable(joined, left, "2026-12"))
        self.assertTrue(rules.is_billable(joined, None, "2027-03"))


class DueDateAndLateFeeTests(unittest.TestCase):
    def test_due_dates(self):
        self.assertEqual(rules.due_date(date(2026, 1, 1), "2026-11"), date(2026, 11, 10))
        self.assertEqual(rules.due_date(date(2026, 11, 3), "2026-11"), date(2026, 11, 10))
        self.assertEqual(rules.due_date(date(2026, 11, 20), "2026-11"), date(2026, 11, 27))

    def test_late_fee_rules(self):
        due = date(2026, 11, 10)
        self.assertFalse(rules.late_fee_applies(5000, due, due))                    # on the due date
        self.assertTrue(rules.late_fee_applies(5000, due, date(2026, 11, 11)))      # after it
        self.assertFalse(rules.late_fee_applies(0, due, date(2026, 11, 20)))        # already paid
        self.assertFalse(rules.late_fee_applies(5000, due, date(2026, 11, 20), True))  # charged once


class AllocationAndSettlementTests(unittest.TestCase):
    def test_allocation(self):
        self.assertTrue(rules.can_allocate("Boys", "M", 2, 1))
        self.assertFalse(rules.can_allocate("Boys", "M", 2, 2))
        self.assertFalse(rules.can_allocate("Girls", "M", 2, 0))

    def test_settlement(self):
        self.assertEqual(rules.settlement(10000, 2500), ("refund", 7500))
        self.assertEqual(rules.settlement(8000, 9500), ("collect", 1500))
        self.assertEqual(rules.settlement(5000, 5000), ("refund", 0))


class ValidationAndFormatTests(unittest.TestCase):
    def test_months(self):
        self.assertTrue(rules.valid_month("2026-02"))
        for bad in ("2026-13", "2026-1", "26-01", "", None):
            self.assertFalse(rules.valid_month(bad))
        self.assertEqual(rules.previous_month("2026-01"), "2025-12")
        self.assertEqual(rules.month_bounds("2028-02")[1], date(2028, 2, 29))   # leap year
        self.assertEqual(rules.month_bounds("2027-02")[1], date(2027, 2, 28))

    def test_phone_and_email(self):
        self.assertEqual(rules.normalize_phone("+91 98765-43210"), "9876543210")
        self.assertIsNone(rules.normalize_phone("12345"))
        self.assertIsNone(rules.normalize_phone("5876543210"))
        self.assertTrue(rules.valid_email("a@b.co"))
        self.assertFalse(rules.valid_email("not-an-email"))

    def test_receipt_and_occupancy(self):
        self.assertEqual(rules.receipt_no(2026, 42), "RCP-2026-00042")
        self.assertEqual(rules.occupancy_pct(22, 28), 79)
        self.assertEqual(rules.occupancy_pct(0, 0), 0)

    def test_rupee_format(self):
        self.assertEqual(rules.format_rupees(125000), "₹1,25,000")
        self.assertEqual(rules.format_rupees(999), "₹999")
        self.assertEqual(rules.format_rupees(1000), "₹1,000")
        self.assertEqual(rules.format_rupees(12345678), "₹1,23,45,678")


if __name__ == "__main__":
    unittest.main()
