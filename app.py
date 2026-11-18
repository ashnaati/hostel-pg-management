"""Web layer: requests, sessions, roles, CSRF and templates."""
import csv
import io
import os
import secrets
from datetime import date
from functools import wraps
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

import hostel
import rules
from hostel import HostelError


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(exist_ok=True)
    app.config.update(
        DATABASE=os.environ.get("HOSTEL_DB", str(Path(app.instance_path) / "hostel.db")),
        TODAY=None,                         # fixed date for tests; None means the real date
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("HOSTEL_HTTPS") == "1",
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):     # Flask's default is None, so setdefault would not work
        app.config["SECRET_KEY"] = os.environ.get("HOSTEL_SECRET_KEY") or _persistent_key(app)

    with hostel.connect(app.config["DATABASE"]) as boot:    # create tables on first run
        hostel.init_db(boot)

    # ------------------------------------------------------------ helpers
    def today():
        fixed = app.config["TODAY"]
        if fixed:
            return fixed if isinstance(fixed, date) else date.fromisoformat(fixed)
        return date.today()

    def get_db():
        if "db" not in g:
            g.db = hostel.connect(app.config["DATABASE"])
        return g.db

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def role_required(role):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if g.user is None:
                    return redirect(url_for("login", next=request.path))
                if g.user["role"] != role:
                    abort(403)
                return view(*args, **kwargs)
            return wrapped
        return decorator

    warden_only = role_required("warden")
    resident_only = role_required("resident")

    def safe_next(target):
        return target if target and target.startswith("/") and not target.startswith("//") else None

    def refresh_fees():
        hostel.apply_late_fees(get_db(), today())

    # ------------------------------------------------------------ request hooks
    @app.before_request
    def load_user():
        g.user = None
        if session.get("user_id"):
            row = get_db().execute("""SELECT u.id, u.email, u.role, u.resident_id, s.status AS resident_status
                                      FROM users u LEFT JOIN residents s ON s.id = u.resident_id
                                      WHERE u.id = ?""", (session["user_id"],)).fetchone()
            if row is None or row["resident_status"] == "left":
                session.clear()
            else:
                g.user = row

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400)

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        if g.get("user") is not None:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    app.jinja_env.globals.update(csrf_token=csrf_token, today=today, rules=rules)
    app.jinja_env.filters["rupees"] = rules.format_rupees
    app.jinja_env.filters["dmy"] = lambda v: (date.fromisoformat(v).strftime("%d %b %Y") if v else "")
    app.jinja_env.filters["month_label"] = lambda v: rules.month_bounds(v)[0].strftime("%B %Y")

    for code, title in ((400, "Request rejected"), (403, "Not allowed"), (404, "Not found"), (413, "Too large")):
        app.register_error_handler(code, lambda e, c=code, t=title: (render_template("error.html", code=c, title=t), c))

    # ------------------------------------------------------------ auth
    @app.route("/")
    def index():
        if g.user is None:
            return redirect(url_for("login"))
        return redirect(url_for("dashboard" if g.user["role"] == "warden" else "portal"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            row = get_db().execute("""SELECT u.*, s.status AS resident_status FROM users u
                                      LEFT JOIN residents s ON s.id = u.resident_id
                                      WHERE u.email = ?""", (email,)).fetchone()
            if row is None or not check_password_hash(row["password_hash"], request.form.get("password", "")):
                flash("Incorrect email or password.", "error")
            elif row["resident_status"] == "left":
                flash("Account is closed. Contact the warden if you think this is a mistake.", "error")
            else:
                session.clear()
                session["user_id"] = row["id"]
                session["csrf"] = secrets.token_urlsafe(32)
                default = "dashboard" if row["role"] == "warden" else "portal"
                return redirect(safe_next(request.args.get("next")) or url_for(default))
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------ warden: dashboard and rooms
    @app.route("/dashboard")
    @warden_only
    def dashboard():
        refresh_fees()
        db = get_db()
        return render_template("dashboard.html", stats=hostel.dashboard(db, today()),
                               overdue=hostel.overdue_invoices(db, today()),
                               complaints=hostel.list_complaints(db, "open")[:5])

    @app.route("/rooms")
    @warden_only
    def rooms():
        return render_template("rooms.html", blocks=hostel.room_map(get_db()))

    @app.route("/rooms/new", methods=["GET", "POST"])
    @warden_only
    def room_new():
        if request.method == "POST":
            f = request.form
            try:
                hostel.add_room(get_db(), f.get("number"), f.get("block"), f.get("floor"),
                                f.get("capacity"), f.get("rent"), f.get("ac") == "1")
                flash(f"Room {f.get('number', '').strip().upper()} added.", "ok")
                return redirect(url_for("rooms"))
            except HostelError as exc:
                flash(str(exc), "error")
        return render_template("room_new.html", form=request.form)

    # ------------------------------------------------------------ warden: residents
    @app.route("/residents")
    @warden_only
    def residents():
        status = request.args.get("status", "active")
        q = request.args.get("q", "").strip()
        return render_template("residents.html", rows=hostel.list_residents(get_db(), q, status),
                               q=q, status=status)

    @app.route("/residents/new", methods=["GET", "POST"])
    @warden_only
    def resident_new():
        db = get_db()
        if request.method == "POST":
            f = request.form
            try:
                rid, password = hostel.admit_resident(
                    db, name=f.get("name"), gender=f.get("gender"), phone=f.get("phone"),
                    email=f.get("email"), room_id=f.get("room_id"),
                    joined_on=f.get("joined_on"), deposit=f.get("deposit"), course=f.get("course"),
                    guardian_name=f.get("guardian_name"), guardian_phone=f.get("guardian_phone"),
                    address=f.get("address"))
                flash(f"Resident admitted. Login: {f.get('email').strip().lower()} / {password} "
                      "(shown once; ask the resident to keep it safe).", "ok")
                return redirect(url_for("resident_detail", rid=rid))
            except HostelError as exc:
                flash(str(exc), "error")
        return render_template("resident_new.html", form=request.form, rooms=hostel.vacant_rooms(db))

    @app.route("/residents/<int:rid>")
    @warden_only
    def resident_detail(rid):
        refresh_fees()
        db = get_db()
        res = hostel.get_resident(db, rid)
        if res is None:
            abort(404)
        return render_template("resident_detail.html", r=res,
                               invoices=hostel.invoices_for_resident(db, rid),
                               payments=hostel.payments_for_resident(db, rid),
                               rooms=[x for x in hostel.vacant_rooms(db)
                                      if x["block"] == rules.BLOCK_FOR_GENDER[res["gender"]]
                                      and x["id"] != res["room_id"]])

    @app.post("/residents/<int:rid>/move")
    @warden_only
    def resident_move(rid):
        try:
            hostel.move_resident(get_db(), rid, int(request.form.get("room_id") or 0))
            flash("Room changed.", "ok")
        except (HostelError, ValueError) as exc:
            flash(str(exc) if isinstance(exc, HostelError) else "Choose a room", "error")
        return redirect(url_for("resident_detail", rid=rid))

    @app.post("/residents/<int:rid>/checkout")
    @warden_only
    def resident_checkout(rid):
        if request.form.get("confirm") != "yes":
            flash("Tick the box to confirm the check-out.", "error")
            return redirect(url_for("resident_detail", rid=rid))
        try:
            s = hostel.check_out(get_db(), rid, today())
            verb = "Refund" if s["kind"] == "refund" else "Collect"
            flash(f"Checked out. Dues {rules.format_rupees(s['dues'])}. "
                  f"{verb} {rules.format_rupees(s['amount'])} against the deposit.", "ok")
        except HostelError as exc:
            flash(str(exc), "error")
        return redirect(url_for("resident_detail", rid=rid))

    # ------------------------------------------------------------ warden: bills and payments
    @app.route("/bills")
    @warden_only
    def bills():
        refresh_fees()
        month = request.args.get("month") or rules.month_of(today())
        if not rules.valid_month(month):
            abort(400)
        unpaid = request.args.get("unpaid") == "1"
        rows = hostel.invoices_for_month(get_db(), month, unpaid)
        return render_template("bills.html", rows=rows, month=month, unpaid=unpaid,
                               total=sum(r["total"] for r in rows),
                               balance=sum(r["balance"] for r in rows))

    @app.post("/bills/generate")
    @warden_only
    def bills_generate():
        month = request.form.get("month", "")
        try:
            n = hostel.generate_bills(get_db(), month, today())
            flash(f"{n} new bill(s) generated." if n else "All bills for this month already exist.", "ok")
        except HostelError as exc:
            flash(str(exc), "error")
        return redirect(url_for("bills", month=month if rules.valid_month(month) else None))

    @app.route("/bills/export")
    @warden_only
    def bills_export():
        month = request.args.get("month", "")
        if not rules.valid_month(month):
            abort(400)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Month", "Resident", "Room", "Rent", "Late fee", "Total", "Paid", "Balance", "Due on"])
        for r in hostel.invoices_for_month(get_db(), month):
            w.writerow([r["month"], _csv_safe(r["resident_name"]), r["room_number"], r["rent"],
                        r["late_fee"], r["total"], r["paid"], r["balance"], r["due_on"]])
        return Response(out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=bills-{month}.csv"})

    @app.post("/invoices/<int:iid>/pay")
    @warden_only
    def invoice_pay(iid):
        db = get_db()
        inv = db.execute("SELECT resident_id FROM invoices WHERE id = ?", (iid,)).fetchone()
        if inv is None:
            abort(404)
        try:
            pid, _ = hostel.record_payment(db, iid, request.form.get("amount"), request.form.get("mode"), today())
            return redirect(url_for("receipt", pid=pid))
        except HostelError as exc:
            flash(str(exc), "error")
            return redirect(url_for("resident_detail", rid=inv["resident_id"]))

    @app.route("/receipts/<int:pid>")
    @role_required_any
    def receipt(pid):
        rec = hostel.get_receipt(get_db(), pid)
        if rec is None or (g.user["role"] == "resident" and rec["resident_id"] != g.user["resident_id"]):
            abort(404)                       # residents can't tell whether another receipt exists
        return render_template("receipt.html", p=rec)

    # ------------------------------------------------------------ complaints and notices
    @app.route("/complaints")
    @warden_only
    def complaints():
        status = request.args.get("status", "")
        return render_template("complaints.html", rows=hostel.list_complaints(get_db(), status or None),
                               status=status)

    @app.post("/complaints/<int:cid>")
    @warden_only
    def complaint_update(cid):
        try:
            hostel.update_complaint(get_db(), cid, request.form.get("status"), request.form.get("remark"), today())
            flash("Complaint updated.", "ok")
        except HostelError as exc:
            flash(str(exc), "error")
        return redirect(url_for("complaints", status=request.form.get("filter") or None))

    @app.route("/notices", methods=["GET", "POST"])
    @warden_only
    def notices():
        if request.method == "POST":
            try:
                hostel.post_notice(get_db(), request.form.get("title"), request.form.get("body"), today())
                flash("Notice posted.", "ok")
                return redirect(url_for("notices"))
            except HostelError as exc:
                flash(str(exc), "error")
        return render_template("notices.html", rows=hostel.list_notices(get_db()), form=request.form)

    # ------------------------------------------------------------ resident portal
    @app.route("/me")
    @resident_only
    def portal():
        refresh_fees()
        db = get_db()
        rid = g.user["resident_id"]
        return render_template("portal.html", r=hostel.get_resident(db, rid),
                               mates=hostel.roommates(db, rid),
                               invoices=hostel.invoices_for_resident(db, rid),
                               payments=hostel.payments_for_resident(db, rid),
                               notices=hostel.list_notices(db, 5),
                               complaints=hostel.list_complaints(db, resident_id=rid),
                               categories=rules.COMPLAINT_CATEGORIES, form=request.form)

    @app.post("/me/complaints")
    @resident_only
    def portal_complaint():
        try:
            hostel.raise_complaint(get_db(), g.user["resident_id"], request.form.get("category"),
                                   request.form.get("description"), today())
            flash("Complaint sent to the warden.", "ok")
        except HostelError as exc:
            flash(str(exc), "error")
        return redirect(url_for("portal") + "#complaints")

    return app


def role_required_any(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _csv_safe(value):
    """Stop spreadsheet formula injection from names typed into the system."""
    return "'" + value if value and value[0] in "=+-@\t\r" else value


def _persistent_key(app):
    path = Path(app.instance_path) / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
