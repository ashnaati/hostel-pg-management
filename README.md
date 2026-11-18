# Hostel and PG Management System

A small, secure web application that replaces the paper registers of a hostel or paying-guest (PG) house.
Built with **Python, Flask and SQLite** as a B.Tech mini project (BCC351).

The warden manages rooms, admits residents, bills rent and records payments. Residents log in to a separate
portal to see their room, dues, receipts and notices, and to raise complaints.

## Features

| Area | What it does |
| --- | --- |
| Rooms | Boys and Girls blocks, 1 to 4 beds per room, rent per bed, AC flag, live **room map** |
| Admission | Only into a vacant bed in the resident's gender block; creates a hashed-password login |
| Room change / check-out | Move to another room; check-out bills the leaving month, frees the bed, settles the deposit |
| Billing | One-click monthly bills (never twice per resident and month), prorated joining month, late fee added once |
| Payments | Partial payments, never above the balance, numbered printable receipts (`RCP-2026-00042`) |
| Resident portal | Own room, roommates, bills, receipts, notices; raise complaints; sees nothing of anyone else |
| Complaints, notices | Status workflow with warden remarks; notice board |
| Dashboard | Occupancy, collection this month, pending rent, open complaints, overdue list |
| Export | Monthly bills as CSV (formula-injection safe) |

Out of scope: online payment, mess management, biometric attendance, visitor management.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/<your-username>/hostel-pg-management.git
cd hostel-pg-management

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python seed_demo.py               # demo data: 14 rooms, 22 residents, 4 months of bills
python app.py                     # open http://127.0.0.1:5000
```

Demo logins (created by `seed_demo.py` only, never use them for real data):

| Role | Email | Password |
| --- | --- | --- |
| Warden | `warden@hostel.local` | `warden123` |
| Resident | `aarav.sharma@example.com` | `resident123` |

### Real use (no demo data)

```bash
python manage.py init-db
python manage.py create-warden you@yourhostel.in      # asks for a password
python app.py
```

## Tests

```bash
python -m unittest -v
```

The suite covers the pure business rules (`tests/test_rules.py`), the database layer with a fresh in-memory
database per test (`tests/test_hostel.py`), and the web layer through Flask's test client with a fixed date
(`tests/test_web.py`): access control, CSRF, escaping, receipts, CSV export. GitHub Actions runs it on Python
3.10, 3.12 and 3.13.

## Project structure

```
app.py          Flask layer: routes, sessions, roles, CSRF, security headers
hostel.py       Data layer: all SQL, every write inside a transaction
rules.py        Rules layer: pure functions (rent, due date, late fee, allocation, settlement)
schema.sql      Tables, constraints, capacity triggers, invoice_summary view
seed_demo.py    Demo data, dates counted back from today
manage.py       init-db / create-warden
templates/      Jinja templates (autoescaped)      static/   CSS and one tiny JS file
tests/          unittest suite
docs/design.md Architecture, DFDs, ER diagram, business rules, flowcharts (Mermaid)
```

## Business rules (all in `rules.py`)

| Rule | Value |
| --- | --- |
| Due date | 10th of the month; in the joining month, 7 days after joining if that is later |
| Joining-month rent | rent × days stayed ÷ days in month, to the nearest rupee (half rounds up) |
| Leaving-month rent | Full month |
| Late fee | ₹200, once per bill, if a balance remains after the due date |
| Payment | ₹1 up to the balance; partial payments allowed |
| Allocation | Same block as gender; occupied < capacity |
| Deposit settlement | refund = deposit − dues, or collect = dues − deposit |

To change the due day or late fee, edit the constants at the top of `rules.py`.

## Security

Hashed passwords (Werkzeug) · role-based access (`warden` / `resident`) · CSRF token on every POST ·
parameterised SQL everywhere · Jinja autoescaping · a strict Content-Security-Policy (no inline scripts or styles) ·
`HttpOnly` + `SameSite=Lax` session cookie · open-redirect check on login · residents get a 404, not a 403, for
other people's receipts · database constraints and `BEGIN IMMEDIATE` transactions stop double allocation and overpayment.

For a real deployment: serve behind HTTPS and set `HOSTEL_HTTPS=1` (secure cookies); set `HOSTEL_SECRET_KEY`
(otherwise one is generated in `instance/secret_key`); run with a production server such as
`gunicorn "app:create_app()"`; never run with `FLASK_DEBUG=1`; back up `instance/hostel.db`.
Login has no rate limiting yet (see future scope).

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `HOSTEL_DB` | Database file | `instance/hostel.db` |
| `HOSTEL_SECRET_KEY` | Session signing key | generated in `instance/secret_key` |
| `HOSTEL_HTTPS` | `1` marks the session cookie `Secure` | off |

## Notes on design decisions

* **Late fees are applied lazily.** There is no scheduler: the fee is added the first time a bill is viewed, paid
  or checked out after its due date, using one idempotent `UPDATE` that skips bills which already have a fee.
  A payment after the due date must therefore include the ₹200.
* **Check-out settles through payments.** Dues are cleared by `Deposit` (and `Cash`, if the deposit falls short)
  payment rows, so every bill ends at zero balance and the history stays auditable. `Deposit` is reserved for
  check-out and cannot be chosen from the payment form.
* **Bills keep a room snapshot.** `invoices.room_number` is copied at billing time, so changing rooms does not
  rewrite history.

## Future scope

Online rent payment (UPI link or gateway) · SMS/email reminders · mess menu and billing · visitor log and
night-out requests · collection and occupancy charts · login rate limiting · several hostels under one owner.

## License

MIT, see `LICENSE`.
