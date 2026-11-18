"""Business rules: pure functions with no database or web code.

Everything a warden might want to change (due day, late fee, grace days,
allocation, deposit settlement) lives here so it can be read and tested alone.
"""
import calendar
import re
from datetime import date, timedelta

DUE_DAY = 10            # rent is due on the 10th of the month
JOIN_GRACE_DAYS = 7     # in the joining month: 7 days after joining, if later
LATE_FEE = 200          # rupees, charged once per bill

BLOCK_FOR_GENDER = {"M": "Boys", "F": "Girls"}
PAYMENT_MODES = ("Cash", "UPI", "Card", "Bank transfer")
COMPLAINT_CATEGORIES = ("Electrical", "Plumbing", "Cleaning", "Furniture", "Internet", "Noise", "Other")
COMPLAINT_STATUSES = ("open", "in_progress", "resolved")


# ---------------------------------------------------------------- dates
def valid_month(month):
    return isinstance(month, str) and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) is not None


def month_bounds(month):
    """First and last day of a 'YYYY-MM' month."""
    if not valid_month(month):
        raise ValueError(f"Invalid month: {month!r}")
    year, mon = int(month[:4]), int(month[5:])
    return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])


def month_of(d):
    return f"{d.year:04d}-{d.month:02d}"


def previous_month(month):
    first, _ = month_bounds(month)
    return month_of(first - timedelta(days=1))


def parse_date(value):
    """ISO string -> date, or None if invalid."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- rent
def rent_for_month(rent, joined_on, month):
    """Rent for a month. The joining month is charged for days stayed only."""
    first, last = month_bounds(month)
    if joined_on > last:
        return 0
    if joined_on <= first:
        return rent
    days = (last - joined_on).days + 1
    return (rent * days + last.day // 2) // last.day      # whole rupees, half rounds up


def is_billable(joined_on, left_on, month):
    """Billable from the joining month up to and including the leaving month."""
    _, last = month_bounds(month)
    if joined_on > last:
        return False
    if left_on is not None and month > month_of(left_on):
        return False
    return True


def due_date(joined_on, month):
    """10th of the month; in the joining month, 7 days after joining if later."""
    first, last = month_bounds(month)
    due = first.replace(day=DUE_DAY)
    if first <= joined_on <= last:
        due = max(due, joined_on + timedelta(days=JOIN_GRACE_DAYS))
    return due


def late_fee_applies(balance, due_on, today, already_charged=False):
    """A late fee is added once, only if a balance remains after the due date."""
    return balance > 0 and today > due_on and not already_charged


# ---------------------------------------------------------------- allocation
def allocation_problem(room_block, gender, capacity, occupied):
    """Return a message if the bed can't be given, else None."""
    if BLOCK_FOR_GENDER.get(gender) != room_block:
        return f"This room is in the {room_block} block"
    if occupied >= capacity:
        return "Room is full"
    return None


def can_allocate(room_block, gender, capacity, occupied):
    return allocation_problem(room_block, gender, capacity, occupied) is None


# ---------------------------------------------------------------- deposit
def settlement(deposit, dues):
    """('refund', amount) if the deposit covers dues, else ('collect', amount)."""
    if deposit >= dues:
        return "refund", deposit - dues
    return "collect", dues - deposit


# ---------------------------------------------------------------- validation
def normalize_phone(value):
    """10-digit Indian mobile number (optional +91 / 0 prefix) or None."""
    digits = re.sub(r"[\s\-()]", "", value or "")
    digits = re.sub(r"^(\+91|91|0)(?=\d{10}$)", "", digits)
    return digits if re.fullmatch(r"[6-9]\d{9}", digits) else None


def valid_email(value):
    return bool(value) and len(value) <= 254 and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None


def parse_int(value, label, low=None, high=None):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number")
    if (low is not None and number < low) or (high is not None and number > high):
        raise ValueError(f"{label} must be between {low} and {high}" if None not in (low, high)
                         else f"{label} is out of range")
    return number


# ---------------------------------------------------------------- display
def receipt_no(year, payment_id):
    return f"RCP-{year}-{payment_id:05d}"


def occupancy_pct(occupied, total):
    return round(100 * occupied / total) if total else 0


def format_rupees(amount):
    """Indian digit grouping: 125000 -> ₹1,25,000."""
    amount = int(amount)
    sign = "-" if amount < 0 else ""
    digits = str(abs(amount))
    if len(digits) > 3:
        head, tail, groups = digits[:-3], digits[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{sign}₹{digits}"
