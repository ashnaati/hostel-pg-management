-- Hostel and PG Management System: database schema (SQLite)
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS rooms (
    id        INTEGER PRIMARY KEY,
    number    TEXT    NOT NULL UNIQUE,
    block     TEXT    NOT NULL CHECK (block IN ('Boys', 'Girls')),
    floor     INTEGER NOT NULL CHECK (floor BETWEEN 0 AND 20),
    capacity  INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 4),
    rent      INTEGER NOT NULL CHECK (rent > 0),          -- rupees per bed per month
    ac        INTEGER NOT NULL DEFAULT 0 CHECK (ac IN (0, 1))
);

CREATE TABLE IF NOT EXISTS residents (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    gender           TEXT    NOT NULL CHECK (gender IN ('M', 'F')),
    phone            TEXT    NOT NULL UNIQUE,
    email            TEXT    NOT NULL UNIQUE,
    course           TEXT,
    guardian_name    TEXT,
    guardian_phone   TEXT,
    address          TEXT,
    room_id          INTEGER REFERENCES rooms(id),
    joined_on        TEXT    NOT NULL,                     -- ISO date
    left_on          TEXT,
    deposit          INTEGER NOT NULL DEFAULT 0 CHECK (deposit >= 0),
    status           TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'left')),
    settlement_kind  TEXT    CHECK (settlement_kind IN ('refund', 'collect')),
    settlement_amount INTEGER,
    CHECK (status = 'left' OR room_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('warden', 'resident')),
    resident_id    INTEGER UNIQUE REFERENCES residents(id),
    CHECK ((role = 'warden' AND resident_id IS NULL) OR (role = 'resident' AND resident_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS invoices (
    id           INTEGER PRIMARY KEY,
    resident_id  INTEGER NOT NULL REFERENCES residents(id),
    month        TEXT    NOT NULL,                         -- YYYY-MM
    room_number  TEXT    NOT NULL,                         -- snapshot at billing time
    rent         INTEGER NOT NULL CHECK (rent >= 0),
    late_fee     INTEGER NOT NULL DEFAULT 0 CHECK (late_fee >= 0),
    due_on       TEXT    NOT NULL,
    UNIQUE (resident_id, month)
);

CREATE TABLE IF NOT EXISTS payments (
    id          INTEGER PRIMARY KEY,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    mode        TEXT    NOT NULL CHECK (mode IN ('Cash', 'UPI', 'Card', 'Bank transfer', 'Deposit')),
    paid_on     TEXT    NOT NULL,
    receipt_no  TEXT    NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS complaints (
    id           INTEGER PRIMARY KEY,
    resident_id  INTEGER NOT NULL REFERENCES residents(id),
    category     TEXT    NOT NULL,
    description  TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'resolved')),
    remark       TEXT,
    created_on   TEXT    NOT NULL,
    resolved_on  TEXT
);

CREATE TABLE IF NOT EXISTS notices (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    posted_on  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_residents_room    ON residents(room_id, status);
CREATE INDEX IF NOT EXISTS idx_invoices_month    ON invoices(month);
CREATE INDEX IF NOT EXISTS idx_payments_invoice  ON payments(invoice_id);

-- Backstop for double allocation: even a buggy caller cannot overfill a room.
CREATE TRIGGER IF NOT EXISTS residents_capacity_insert
BEFORE INSERT ON residents
WHEN NEW.room_id IS NOT NULL AND NEW.status = 'active'
BEGIN
    SELECT RAISE(ABORT, 'Room is full')
    WHERE (SELECT COUNT(*) FROM residents WHERE room_id = NEW.room_id AND status = 'active')
          >= (SELECT capacity FROM rooms WHERE id = NEW.room_id);
END;

CREATE TRIGGER IF NOT EXISTS residents_capacity_update
BEFORE UPDATE OF room_id ON residents
WHEN NEW.room_id IS NOT NULL AND NEW.status = 'active'
BEGIN
    SELECT RAISE(ABORT, 'Room is full')
    WHERE (SELECT COUNT(*) FROM residents
           WHERE room_id = NEW.room_id AND status = 'active' AND id != NEW.id)
          >= (SELECT capacity FROM rooms WHERE id = NEW.room_id);
END;

-- Invoice with computed totals; "balance" is what is still owed.
CREATE VIEW IF NOT EXISTS invoice_summary AS
SELECT i.id, i.resident_id, i.month, i.room_number, i.rent, i.late_fee, i.due_on,
       r.name AS resident_name,
       i.rent + i.late_fee AS total,
       COALESCE(p.paid, 0) AS paid,
       i.rent + i.late_fee - COALESCE(p.paid, 0) AS balance
FROM invoices i
JOIN residents r ON r.id = i.resident_id
LEFT JOIN (SELECT invoice_id, SUM(amount) AS paid FROM payments GROUP BY invoice_id) p
       ON p.invoice_id = i.id;
