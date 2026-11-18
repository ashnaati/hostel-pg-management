"""Create a demo database: 14 rooms, 22 current residents and one former resident,
four months of bills and payments (some unpaid, with late fees), complaints and
notices. All dates are counted back from today.

    python seed_demo.py            # writes instance/hostel.db (asks before replacing)
    python seed_demo.py --force    # replaces it without asking
"""
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import hostel
import rules
from app import create_app

WARDEN = ("warden@hostel.local", "warden123")
RESIDENT_PASSWORD = "resident123"

# number, block, floor, beds, rent, ac   (28 beds in total)
ROOMS = [
    ("B101", "Boys", 1, 2, 5500, 0), ("B102", "Boys", 1, 2, 5500, 0), ("B103", "Boys", 1, 3, 4500, 0),
    ("B201", "Boys", 2, 3, 4500, 0), ("B202", "Boys", 2, 2, 7000, 1), ("B203", "Boys", 2, 2, 7000, 1),
    ("B204", "Boys", 2, 2, 6000, 0),
    ("G101", "Girls", 1, 2, 5500, 0), ("G102", "Girls", 1, 1, 8000, 1), ("G103", "Girls", 1, 2, 5500, 0),
    ("G201", "Girls", 2, 2, 7000, 1), ("G202", "Girls", 2, 2, 6000, 0), ("G203", "Girls", 2, 1, 8000, 1),
    ("G204", "Girls", 2, 2, 5500, 0),
]
BOYS = ["Aarav Sharma", "Vivaan Gupta", "Aditya Verma", "Arjun Singh", "Rohan Mehta", "Karan Yadav",
        "Siddharth Rao", "Harsh Tiwari", "Mohit Chauhan", "Nikhil Jain", "Pranav Joshi", "Yash Malhotra",
        "Devansh Kapoor"]
GIRLS = ["Ananya Mishra", "Diya Patel", "Isha Agarwal", "Kavya Nair", "Meera Iyer", "Neha Saxena",
         "Riya Bansal", "Sanya Khanna", "Tanvi Reddy"]
FORMER = "Tanya Bose"
COURSES = ["B.Tech CSE", "B.Tech ECE", "B.Tech ME", "BBA", "B.Com", "BCA", "MBA"]
COMPLAINTS = [
    ("Electrical", "The ceiling fan makes a loud noise and wobbles at high speed.", "open"),
    ("Plumbing", "Tap in the common bathroom has been leaking since Monday.", "in_progress"),
    ("Internet", "Wi-Fi drops every evening between 8 and 10 pm on our floor.", "open"),
    ("Cleaning", "Corridor is not being swept on weekends.", "resolved"),
    ("Furniture", "The study table drawer lock is broken and will not close.", "in_progress"),
    ("Noise", "Loud music in the next room after 11 pm on most nights.", "resolved"),
]
NOTICES = [
    ("Water supply", "Water will be off on Sunday from 10 am to 1 pm for tank cleaning."),
    ("Rent due date", "Monthly rent is due by the 10th. A late fee of ₹200 applies after that."),
    ("Gate timing", "The main gate closes at 10:30 pm. Inform the warden for late entry."),
]


def main(force=False):
    app = create_app()
    path = Path(app.config["DATABASE"])
    if path.exists() and path.stat().st_size and not force:
        if input(f"{path} exists. Replace it? [y/N] ").strip().lower() != "y":
            sys.exit("Left the database as it was.")
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    db = hostel.connect(path)
    hostel.init_db(db)

    rng = random.Random(2026)
    today = app.config["TODAY"] or date.today()
    hostel.create_user(db, *WARDEN, "warden")
    room_ids = {number: hostel.add_room(db, number, block, floor, beds, rent, ac)
                for number, block, floor, beds, rent, ac in ROOMS}

    # One bed per name, filled in room order: 13 of 16 Boys beds and 9 of 12 Girls beds
    # (22 of 28 = 79%). G202 keeps one bed free, which the former resident used to hold.
    boy_beds = [n for n, b, _, beds, *_ in ROOMS if b == "Boys" for _ in range(beds)][:len(BOYS)]
    girl_beds = [n for n, b, _, beds, *_ in ROOMS if b == "Girls" for _ in range(beds)]
    girl_beds.remove("G202")
    girl_beds = girl_beds[:len(GIRLS)]

    def admit(name, gender, room, days_ago):
        slug = name.lower().replace(" ", ".")
        return hostel.admit_resident(
            db, name=name, gender=gender, phone=f"98{rng.randint(10000000, 99999999)}",
            email=f"{slug}@example.com", room_id=room_ids[room], joined_on=today - timedelta(days=days_ago),
            deposit=rng.choice([5000, 8000, 10000]), course=rng.choice(COURSES),
            guardian_name=f"Parent of {name.split()[0]}", guardian_phone=f"97{rng.randint(10000000, 99999999)}",
            address="Gurugram, Haryana", password=RESIDENT_PASSWORD)

    ids = []
    for name, room in zip(BOYS, boy_beds):
        ids.append(admit(name, "M", room, rng.randint(25, 420))[0])
    for name, room in zip(GIRLS, girl_beds):
        ids.append(admit(name, "F", room, rng.randint(25, 420))[0])
    former = admit(FORMER, "F", "G202", 200)[0]

    # Four months of bills; most are paid, some are not.
    month = rules.month_of(today)
    months = [month]
    for _ in range(3):
        months.append(rules.previous_month(months[-1]))
    for m in reversed(months):
        first, last = rules.month_bounds(m)
        as_of = min(today, last)
        hostel.generate_bills(db, m, as_of)
        for row in hostel.invoices_for_month(db, m):
            current = (m == month)
            if rng.random() < (0.45 if current else 0.88):
                # re-read: an earlier payment may have added late fees to this bill
                inv = db.execute("SELECT * FROM invoice_summary WHERE id = ?", (row["id"],)).fetchone()
                due = date.fromisoformat(inv["due_on"])
                paid_on = min(as_of, due + timedelta(days=rng.randint(-6, 4)))
                fee = rules.late_fee_applies(inv["balance"], due, paid_on, inv["late_fee"] > 0)
                hostel.record_payment(db, inv["id"], inv["balance"] + (rules.LATE_FEE if fee else 0),
                                      rng.choice(rules.PAYMENT_MODES), paid_on)
        hostel.apply_late_fees(db, as_of)

    hostel.check_out(db, former, today)           # the one former resident; deposit settled against dues

    for (cat, text, status), rid in zip(COMPLAINTS, rng.sample(ids, len(COMPLAINTS))):
        cid = hostel.raise_complaint(db, rid, cat, text, today - timedelta(days=rng.randint(1, 20)))
        if status != "open":
            hostel.update_complaint(db, cid, status, "Maintenance has been informed." if status == "in_progress"
                                    else "Fixed by the maintenance team.", today - timedelta(days=rng.randint(0, 3)))
    for i, (title, body) in enumerate(NOTICES):
        hostel.post_notice(db, title, body, today - timedelta(days=i * 6 + 1))

    s = hostel.dashboard(db, today)
    print(f"Demo database created at {path}")
    print(f"  {s['occupied']} residents in {s['beds']} beds ({s['occupancy']}% occupied), "
          f"{s['pending_bills']} unpaid bills")
    print(f"  Warden:   {WARDEN[0]} / {WARDEN[1]}")
    print(f"  Resident: aarav.sharma@example.com / {RESIDENT_PASSWORD}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
