"""Data layer: every database operation, each inside a transaction.

Functions take an open sqlite3 connection (isolation_level=None, so BEGIN and
COMMIT are explicit) and a `today` date, which keeps them easy to test.
"""
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from werkzeug.security import generate_password_hash

import rules

SCHEMA = Path(__file__).with_name("schema.sql")


class HostelError(Exception):
    """A problem the user can fix; the message is safe to show."""


# ---------------------------------------------------------------- connection
def connect(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        db.execute("PRAGMA journal_mode = WAL")
    return db


def init_db(db):
    db.executescript(SCHEMA.read_text(encoding="utf-8"))


@contextmanager
def transaction(db):
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-write is safe."""
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")


def _int(value, label, low=None, high=None):
    try:
        return rules.parse_int(value, label, low, high)
    except ValueError as exc:
        raise HostelError(str(exc))


def _text(value, label, maxlen=200, required=True):
    value = (value or "").strip()
    if required and not value:
        raise HostelError(f"{label} is required")
    if len(value) > maxlen:
        raise HostelError(f"{label} is too long (max {maxlen} characters)")
    return value or None


def random_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------- users
def create_user(db, email, password, role="warden", resident_id=None):
    email = (email or "").strip().lower()
    if not rules.valid_email(email):
        raise HostelError("Enter a valid email address")
    if len(password or "") < 8:
        raise HostelError("Password must be at least 8 characters")
    try:
        db.execute("INSERT INTO users (email, password_hash, role, resident_id) VALUES (?, ?, ?, ?)",
                   (email, generate_password_hash(password), role, resident_id))
    except sqlite3.IntegrityError:
        raise HostelError("A user with this email already exists")


# ---------------------------------------------------------------- rooms
def add_room(db, number, block, floor, capacity, rent, ac=False):
    number = _text(number, "Room number", 10).upper()
    if block not in ("Boys", "Girls"):
        raise HostelError("Block must be Boys or Girls")
    floor = _int(floor, "Floor", 0, 20)
    capacity = _int(capacity, "Beds", 1, 4)
    rent = _int(rent, "Rent", 1, 1_000_000)
    try:
        cur = db.execute("INSERT INTO rooms (number, block, floor, capacity, rent, ac) VALUES (?,?,?,?,?,?)",
                         (number, block, floor, capacity, rent, 1 if ac else 0))
    except sqlite3.IntegrityError:
        raise HostelError(f"Room {number} already exists")
    return cur.lastrowid


def list_rooms(db):
    return db.execute("""
        SELECT r.*, COUNT(s.id) AS occupied
        FROM rooms r LEFT JOIN residents s ON s.room_id = r.id AND s.status = 'active'
        GROUP BY r.id ORDER BY r.block, r.number""").fetchall()


def room_map(db):
    """Rooms with the residents of each, grouped by block."""
    people = {}
    for p in db.execute("SELECT id, name, room_id FROM residents WHERE status = 'active' ORDER BY name"):
        people.setdefault(p["room_id"], []).append(p)
    blocks = {"Boys": [], "Girls": []}
    for room in list_rooms(db):
        blocks[room["block"]].append({"room": room, "residents": people.get(room["id"], []),
                                      "free": room["capacity"] - room["occupied"]})
    return blocks


def _check_room(db, room_id, gender, exclude_resident=None):
    room = db.execute("""
        SELECT r.*, (SELECT COUNT(*) FROM residents s
                     WHERE s.room_id = r.id AND s.status = 'active' AND s.id IS NOT ?) AS occupied
        FROM rooms r WHERE r.id = ?""", (exclude_resident, room_id)).fetchone()
    if room is None:
        raise HostelError("Choose a room")
    problem = rules.allocation_problem(room["block"], gender, room["capacity"], room["occupied"])
    if problem:
        raise HostelError(problem)
    return room


# ---------------------------------------------------------------- residents
def admit_resident(db, *, name, gender, phone, email, room_id, joined_on, deposit=0,
                   course=None, guardian_name=None, guardian_phone=None, address=None, password=None):
    """Admit a resident and create their login. Returns (resident_id, password)."""
    name = _text(name, "Name", 100)
    if gender not in ("M", "F"):
        raise HostelError("Choose a gender")
    phone_n = rules.normalize_phone(phone)
    if not phone_n:
        raise HostelError("Enter a 10-digit mobile number")
    email = (email or "").strip().lower()
    if not rules.valid_email(email):
        raise HostelError("Enter a valid email address")
    guardian_phone_n = None
    if (guardian_phone or "").strip():
        guardian_phone_n = rules.normalize_phone(guardian_phone)
        if not guardian_phone_n:
            raise HostelError("Guardian phone must be a 10-digit mobile number")
    joined = joined_on if isinstance(joined_on, date) else rules.parse_date(joined_on)
    if joined is None:
        raise HostelError("Enter a valid joining date")
    deposit = _int(deposit or 0, "Deposit", 0, 1_000_000)
    room_id = _int(room_id or 0, "Room", 0)
    password = password or random_password()
    if len(password) < 8:
        raise HostelError("Password must be at least 8 characters")

    db.execute("BEGIN IMMEDIATE")        # lock: two wardens can't give away the same last bed
    try:
        _check_room(db, room_id, gender)
        cur = db.execute("""INSERT INTO residents
            (name, gender, phone, email, course, guardian_name, guardian_phone, address,
             room_id, joined_on, deposit)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name, gender, phone_n, email, _text(course, "Course", 100, False),
             _text(guardian_name, "Guardian name", 100, False), guardian_phone_n,
             _text(address, "Address", 300, False), room_id, joined.isoformat(), deposit))
        db.execute("INSERT INTO users (email, password_hash, role, resident_id) VALUES (?, ?, 'resident', ?)",
                   (email, generate_password_hash(password), cur.lastrowid))
        db.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        db.execute("ROLLBACK")
        msg = str(exc)
        if "phone" in msg:
            raise HostelError("A resident with this phone number already exists")
        if "email" in msg:
            raise HostelError("A resident with this email already exists")
        if "full" in msg:
            raise HostelError("Room is full")
        raise HostelError("Could not save the resident")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return cur.lastrowid, password


def get_resident(db, resident_id):
    return db.execute("""SELECT s.*, r.number AS room_number, r.block, r.rent AS room_rent
                         FROM residents s LEFT JOIN rooms r ON r.id = s.room_id
                         WHERE s.id = ?""", (resident_id,)).fetchone()


def list_residents(db, query="", status="active"):
    sql = """SELECT s.*, r.number AS room_number,
                    COALESCE((SELECT SUM(balance) FROM invoice_summary v WHERE v.resident_id = s.id), 0) AS due
             FROM residents s LEFT JOIN rooms r ON r.id = s.room_id WHERE 1=1"""
    args = []
    if status in ("active", "left"):
        sql += " AND s.status = ?"
        args.append(status)
    if query:
        sql += " AND (s.name LIKE ? ESCAPE '\\' OR s.phone LIKE ? ESCAPE '\\' OR r.number LIKE ? ESCAPE '\\')"
        like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like, like]
    return db.execute(sql + " ORDER BY s.name", args).fetchall()


def roommates(db, resident_id):
    return db.execute("""SELECT s.id, s.name, s.course FROM residents s
                         WHERE s.status = 'active' AND s.id != ?
                           AND s.room_id = (SELECT room_id FROM residents WHERE id = ?)
                         ORDER BY s.name""", (resident_id, resident_id)).fetchall()


def vacant_rooms(db):
    return [r for r in list_rooms(db) if r["occupied"] < r["capacity"]]


def move_resident(db, resident_id, new_room_id):
    with transaction(db):
        res = get_resident(db, resident_id)
        if res is None or res["status"] != "active":
            raise HostelError("Only a current resident can change rooms")
        if res["room_id"] == new_room_id:
            raise HostelError("The resident is already in this room")
        _check_room(db, new_room_id, res["gender"], exclude_resident=resident_id)
        try:
            db.execute("UPDATE residents SET room_id = ? WHERE id = ?", (new_room_id, resident_id))
        except sqlite3.IntegrityError:
            raise HostelError("Room is full")


# ---------------------------------------------------------------- billing
def generate_bills(db, month, today):
    """Create bills for all current residents. Never twice for the same month."""
    if not rules.valid_month(month):
        raise HostelError("Choose a valid month")
    if month > rules.month_of(today):
        raise HostelError("Bills cannot be generated for a future month")
    created = 0
    with transaction(db):
        for r in db.execute("""SELECT s.id, s.joined_on, s.left_on, rm.number, rm.rent
                               FROM residents s JOIN rooms rm ON rm.id = s.room_id
                               WHERE s.status = 'active'""").fetchall():
            joined = date.fromisoformat(r["joined_on"])
            if not rules.is_billable(joined, None, month):
                continue
            cur = db.execute("""INSERT OR IGNORE INTO invoices (resident_id, month, room_number, rent, due_on)
                                VALUES (?, ?, ?, ?, ?)""",
                             (r["id"], month, r["number"], rules.rent_for_month(r["rent"], joined, month),
                              rules.due_date(joined, month).isoformat()))
            created += cur.rowcount          # 0 if the bill already existed
    return created


def _apply_late_fees(db, today):
    return db.execute("""
        UPDATE invoices SET late_fee = ?
        WHERE late_fee = 0 AND due_on < ?
          AND rent - COALESCE((SELECT SUM(amount) FROM payments p WHERE p.invoice_id = invoices.id), 0) > 0
        """, (rules.LATE_FEE, today.isoformat())).rowcount


def apply_late_fees(db, today):
    """Add the late fee once to every bill still unpaid after its due date."""
    with transaction(db):
        return _apply_late_fees(db, today)


def invoices_for_month(db, month, unpaid_only=False):
    sql = "SELECT * FROM invoice_summary WHERE month = ?"
    if unpaid_only:
        sql += " AND balance > 0"
    return db.execute(sql + " ORDER BY room_number, resident_name", (month,)).fetchall()


def invoices_for_resident(db, resident_id):
    return db.execute("SELECT * FROM invoice_summary WHERE resident_id = ? ORDER BY month DESC",
                      (resident_id,)).fetchall()


def overdue_invoices(db, today, limit=8):
    return db.execute("""SELECT * FROM invoice_summary WHERE balance > 0 AND due_on < ?
                         ORDER BY due_on, resident_name LIMIT ?""", (today.isoformat(), limit)).fetchall()


def _insert_payment(db, invoice_id, amount, mode, today):
    cur = db.execute("INSERT INTO payments (invoice_id, amount, mode, paid_on, receipt_no) VALUES (?,?,?,?,?)",
                     (invoice_id, amount, mode, today.isoformat(), "TMP-" + secrets.token_hex(8)))
    number = rules.receipt_no(today.year, cur.lastrowid)
    db.execute("UPDATE payments SET receipt_no = ? WHERE id = ?", (number, cur.lastrowid))
    return cur.lastrowid, number


def record_payment(db, invoice_id, amount, mode, today):
    """Record a full or partial payment. Returns (payment_id, receipt_no)."""
    amount = _int(amount, "Amount", 1)
    if mode not in rules.PAYMENT_MODES:
        raise HostelError("Choose a payment mode")
    with transaction(db):
        _apply_late_fees(db, today)
        inv = db.execute("SELECT * FROM invoice_summary WHERE id = ?", (invoice_id,)).fetchone()
        if inv is None:
            raise HostelError("Bill not found")
        if inv["balance"] <= 0:
            raise HostelError("This bill is already paid")
        if amount > inv["balance"]:
            raise HostelError(f"Amount is more than the balance of {rules.format_rupees(inv['balance'])}")
        return _insert_payment(db, invoice_id, amount, mode, today)


def get_receipt(db, payment_id):
    return db.execute("""SELECT p.*, i.month, i.room_number, i.rent, i.late_fee, i.resident_id,
                                s.name AS resident_name, s.phone,
                                v.total, v.paid AS paid_total, v.balance
                         FROM payments p
                         JOIN invoices i ON i.id = p.invoice_id
                         JOIN residents s ON s.id = i.resident_id
                         JOIN invoice_summary v ON v.id = i.id
                         WHERE p.id = ?""", (payment_id,)).fetchone()


def payments_for_resident(db, resident_id):
    return db.execute("""SELECT p.*, i.month FROM payments p JOIN invoices i ON i.id = p.invoice_id
                         WHERE i.resident_id = ? ORDER BY p.id DESC""", (resident_id,)).fetchall()


# ---------------------------------------------------------------- check-out
def check_out(db, resident_id, today):
    """Bill the leaving month, free the bed and settle the deposit against dues."""
    with transaction(db):
        res = get_resident(db, resident_id)
        if res is None or res["status"] != "active":
            raise HostelError("Only a current resident can be checked out")
        joined = date.fromisoformat(res["joined_on"])
        month = rules.month_of(today)
        if rules.is_billable(joined, None, month):
            db.execute("""INSERT OR IGNORE INTO invoices (resident_id, month, room_number, rent, due_on)
                          VALUES (?, ?, ?, ?, ?)""",
                       (resident_id, month, res["room_number"],
                        rules.rent_for_month(res["room_rent"], joined, month),
                        rules.due_date(joined, month).isoformat()))
        _apply_late_fees(db, today)
        open_bills = db.execute("""SELECT id, balance FROM invoice_summary
                                   WHERE resident_id = ? AND balance > 0 ORDER BY month""",
                                (resident_id,)).fetchall()
        dues = sum(b["balance"] for b in open_bills)
        kind, amount = rules.settlement(res["deposit"], dues)
        deposit_left = res["deposit"]
        for bill in open_bills:                  # pay dues from the deposit, then in cash
            from_deposit = min(bill["balance"], deposit_left)
            if from_deposit:
                _insert_payment(db, bill["id"], from_deposit, "Deposit", today)
                deposit_left -= from_deposit
            if bill["balance"] - from_deposit:
                _insert_payment(db, bill["id"], bill["balance"] - from_deposit, "Cash", today)
        db.execute("""UPDATE residents SET status = 'left', left_on = ?, room_id = NULL,
                      settlement_kind = ?, settlement_amount = ? WHERE id = ?""",
                   (today.isoformat(), kind, amount, resident_id))
    return {"kind": kind, "amount": amount, "dues": dues}


# ---------------------------------------------------------------- complaints and notices
def raise_complaint(db, resident_id, category, description, today):
    if category not in rules.COMPLAINT_CATEGORIES:
        raise HostelError("Choose a category")
    description = (description or "").strip()
    if len(description) < 10:
        raise HostelError("Describe the problem in at least 10 characters")
    if len(description) > 1000:
        raise HostelError("Description is too long (max 1000 characters)")
    return db.execute("INSERT INTO complaints (resident_id, category, description, created_on) VALUES (?,?,?,?)",
                      (resident_id, category, description, today.isoformat())).lastrowid


def update_complaint(db, complaint_id, status, remark, today):
    if status not in rules.COMPLAINT_STATUSES:
        raise HostelError("Choose a status")
    remark = _text(remark, "Remark", 500, False)
    cur = db.execute("UPDATE complaints SET status = ?, remark = ?, resolved_on = ? WHERE id = ?",
                     (status, remark, today.isoformat() if status == "resolved" else None, complaint_id))
    if cur.rowcount == 0:
        raise HostelError("Complaint not found")


def list_complaints(db, status=None, resident_id=None):
    sql = """SELECT c.*, s.name AS resident_name, r.number AS room_number
             FROM complaints c JOIN residents s ON s.id = c.resident_id
             LEFT JOIN rooms r ON r.id = s.room_id WHERE 1=1"""
    args = []
    if status in rules.COMPLAINT_STATUSES:
        sql += " AND c.status = ?"
        args.append(status)
    if resident_id is not None:
        sql += " AND c.resident_id = ?"
        args.append(resident_id)
    return db.execute(sql + " ORDER BY (c.status = 'resolved'), c.id DESC", args).fetchall()


def post_notice(db, title, body, today):
    title = _text(title, "Title", 120)
    body = _text(body, "Notice", 2000)
    return db.execute("INSERT INTO notices (title, body, posted_on) VALUES (?,?,?)",
                      (title, body, today.isoformat())).lastrowid


def list_notices(db, limit=20):
    return db.execute("SELECT * FROM notices ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ---------------------------------------------------------------- dashboard
def dashboard(db, today):
    month = rules.month_of(today)
    beds = db.execute("SELECT COALESCE(SUM(capacity), 0) FROM rooms").fetchone()[0]
    occupied = db.execute("SELECT COUNT(*) FROM residents WHERE status = 'active'").fetchone()[0]
    collected = db.execute("""SELECT COALESCE(SUM(amount), 0) FROM payments
                              WHERE mode != 'Deposit' AND substr(paid_on, 1, 7) = ?""", (month,)).fetchone()[0]
    pending, pending_bills = db.execute(
        "SELECT COALESCE(SUM(balance), 0), COUNT(*) FROM invoice_summary WHERE balance > 0").fetchone()
    open_complaints = db.execute("SELECT COUNT(*) FROM complaints WHERE status != 'resolved'").fetchone()[0]
    return {"beds": beds, "occupied": occupied, "vacant": beds - occupied,
            "occupancy": rules.occupancy_pct(occupied, beds), "collected": collected,
            "pending": pending, "pending_bills": pending_bills, "open_complaints": open_complaints}
