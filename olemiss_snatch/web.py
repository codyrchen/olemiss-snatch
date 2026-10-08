"""RebelSnatch website: sign in with an Ole Miss email, search courses, snatch full sections.

Run locally:
    python -m flask --app olemiss_snatch.web run --debug
"""

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, google_auth
from .banner import has_active_waitlist
from .links import (base_url, make_alert_email_token, read_alert_email_token,
                    read_unsubscribe_token, secret_key)
from . import schedule
from .notify import REGISTRATION_URL, Mailer, Texter, load_env, normalize_us_phone
from .trades import SWAP_STEPS, refresh_matches

SITE_NAME = "RebelSnatch"
LOGIN_LINK_MINUTES = 15
LOGIN_REQUESTS_PER_HOUR = 5


def max_subscriptions() -> int:
    return int(os.environ.get("MAX_SUBSCRIPTIONS", "15"))


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}


def allowed_domains() -> list[str]:
    raw = os.environ.get("ALLOWED_EMAIL_DOMAINS", "go.olemiss.edu,olemiss.edu")
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def time_ago(iso: str | None) -> str:
    if not iso:
        return "never"
    seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    return f"{int(seconds // 86400)} days ago"


def term_name(code: str) -> str:
    seasons = {"10": "Fall", "20": "Winter", "30": "Spring", "50": "Summer", "90": "Full Year"}
    year, season = int(code[:4]), code[4:]
    # Banner academic years are named by their spring: 202710 is Fall 2026.
    if season == "10":
        return f"Fall {year - 1}"
    if season in ("20", "90"):
        return f"{seasons[season]} {year - 1}-{str(year)[2:]}"
    return f"{seasons.get(season, season)} {year}"


def create_app(db_path: str | None = None, mailer: Mailer | None = None) -> Flask:
    load_env()
    if base_url().startswith("https://") and secret_key() == "dev-only-change-me":
        raise RuntimeError("Set SECRET_KEY before running the site on a public https BASE_URL.")
    app = Flask(__name__)
    if not app.debug:  # make app/module errors show up in Railway's logs
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Railway (and most hosts) sit behind a proxy that terminates HTTPS.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=secret_key(),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=base_url().startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        DB_PATH=db_path or os.environ.get("SNATCH_DB", "snatch.db"),
    )
    app.extensions["mailer"] = mailer or Mailer()
    app.extensions["texter"] = Texter()
    app.jinja_env.globals.update(SITE_NAME=SITE_NAME, term_name=term_name,
                                 REGISTRATION_URL=REGISTRATION_URL,
                                 has_active_waitlist=has_active_waitlist)

    def get_db():
        if "db" not in g:
            g.db = db.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    def current_term() -> str | None:
        conn = get_db()
        terms = db.terms(conn)
        wanted = request.args.get("term") or session.get("term") or os.environ.get("SNATCH_TERM")
        if wanted in terms:
            session["term"] = wanted
            return wanted
        return db.busiest_term(conn)

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if "email" in session and db.is_blocked(get_db(), session["email"]):
                session.clear()
            if "email" not in session:
                if request.path.startswith("/api/"):
                    return jsonify(error="Please sign in again."), 401
                return redirect(url_for("index"))
            return view(*args, **kwargs)
        return wrapper

    def json_post(view):
        """API writes must be JSON from our own pages (blocks cross-site form posts)."""
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not request.is_json or request.headers.get("X-Requested-With") != "fetch":
                abort(400)
            return view(*args, **kwargs)
        return wrapper

    @app.context_processor
    def inject_user():
        email = session.get("email")
        return {"user_email": email, "is_admin": bool(email) and email in admin_emails()}

    def admin_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if session.get("email") not in admin_emails():
                abort(404)
            return view(*args, **kwargs)
        return wrapper

    # ---------- sign in ----------

    @app.get("/")
    def index():
        if "email" in session:
            return redirect(url_for("dashboard"))
        return render_template("index.html", domains=allowed_domains(),
                               google_enabled=google_auth.configured())

    @app.post("/login")
    def login():
        email = (request.form.get("email") or "").strip().lower()
        domain = email.rpartition("@")[2]
        if "@" not in email or domain not in allowed_domains():
            flash(f"Use your Ole Miss email ({' or '.join('@' + d for d in allowed_domains())}).",
                  "danger")
            return redirect(url_for("index"))
        conn = get_db()
        if db.is_blocked(conn, email):
            flash("This account has been suspended. Contact the site owner if you think that's a mistake.",
                  "danger")
            return redirect(url_for("index"))
        now = datetime.now(timezone.utc)
        if db.recent_login_requests(conn, email, _iso(now - timedelta(hours=1))) >= LOGIN_REQUESTS_PER_HOUR:
            flash("Too many sign-in links requested. Try again in an hour.", "danger")
            return redirect(url_for("index"))
        token = secrets.token_urlsafe(32)
        db.save_login_token(conn, _hash(token), email,
                            _iso(now + timedelta(minutes=LOGIN_LINK_MINUTES)))
        link = f"{base_url()}{url_for('auth', token=token)}"
        try:
            app.extensions["mailer"].send(
                email, f"Your {SITE_NAME} sign-in link",
                f"Click to sign in to {SITE_NAME}:\n\n{link}\n\n"
                f"This link works once and expires in {LOGIN_LINK_MINUTES} minutes. "
                f"If you didn't ask for it, ignore this email.\n",
            )
        except Exception as e:
            app.logger.error("sign-in email to %s failed: %s", email, e)
            # Not the user's fault, so it shouldn't count toward the hourly limit.
            db.delete_login_token(conn, _hash(token))
            flash("We couldn't send the sign-in email right now. Please try again in a minute.",
                  "danger")
            return redirect(url_for("index"))
        return render_template("check_email.html", email=email)

    # GET only shows a button; the POST signs in. Email security scanners
    # open links automatically, and that must not use up the token.
    @app.route("/auth/<token>", methods=["GET", "POST"])
    def auth(token):
        if request.method == "GET":
            return render_template("confirm_login.html", token=token)
        conn = get_db()
        email = db.use_login_token(conn, _hash(token))
        if email is not None and db.is_blocked(conn, email):
            email = None
        if email is None:
            flash("That sign-in link expired or was already used. Request a new one.", "danger")
            return redirect(url_for("index"))
        return finish_sign_in(email)

    @app.get("/login/google")
    def login_google():
        if not google_auth.configured():
            abort(404)
        redirect_uri = base_url() + url_for("google_callback")
        url, state, nonce = google_auth.authorization_url(redirect_uri, allowed_domains()[0])
        session["google_state"], session["google_nonce"] = state, nonce
        return redirect(url)

    @app.get("/auth/google/callback")
    def google_callback():
        state, nonce = session.pop("google_state", None), session.pop("google_nonce", None)
        if request.args.get("error"):
            flash("Google sign-in was cancelled.", "danger")
            return redirect(url_for("index"))
        if not state or request.args.get("state") != state or not request.args.get("code"):
            flash("That sign-in attempt expired. Please try again.", "danger")
            return redirect(url_for("index"))
        try:
            email = google_auth.verified_email(request.args["code"],
                                               base_url() + url_for("google_callback"),
                                               nonce, allowed_domains())
        except google_auth.GoogleAuthError as e:
            flash(str(e), "danger")
            return redirect(url_for("index"))
        except Exception as e:  # network trouble talking to Google
            app.logger.error("google sign-in failed: %s", e)
            flash("Google sign-in didn't go through. Please try again.", "danger")
            return redirect(url_for("index"))
        return finish_sign_in(email)

    def finish_sign_in(email):
        conn = get_db()
        if db.is_blocked(conn, email):
            flash("This account has been suspended. Contact the site owner if you think that's a mistake.",
                  "danger")
            return redirect(url_for("index"))
        session.clear()
        session.permanent = True
        session["email"] = email
        db.touch_user(conn, email)
        return redirect(url_for("dashboard"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # ---------- pages ----------

    @app.get("/dashboard")
    @login_required
    def dashboard():
        conn = get_db()
        term = current_term()
        subs = db.list_subscriptions(conn, session["email"])
        user = db.get_user(conn, session["email"])
        return render_template("dashboard.html", term=term, terms=db.terms(conn), subs=subs,
                               phone=(user["phone"] if user else None),
                               alert_email=(user["alert_email"] if user else None),
                               sms_enabled=app.extensions["texter"].configured,
                               max_subs=max_subscriptions(),
                               alert_status=schedule.describe(schedule.status()),
                               matches=db.find_trade_matches(conn, session["email"]),
                               swap_steps=SWAP_STEPS,
                               multi_term=len({s["term"] for s in subs}) > 1,
                               updated=time_ago(db.last_updated(conn, term)) if term else None)

    @app.get("/course/<term>/<subject>/<course_number>")
    @login_required
    def course(term, subject, course_number):
        conn = get_db()
        sections = db.course_sections(conn, term, subject.upper(), course_number.upper(),
                                      session["email"])
        if not sections:
            abort(404)
        session["term"] = term
        c = sections[0]
        email = session["email"]
        return render_template(
            "course.html", term=term, terms=db.terms(conn), sections=sections, course=c,
            updated=time_ago(db.last_updated(conn, term)),
            enrollment=db.get_enrollment(conn, email, term, c["subject"], c["course_number"]),
            matches=db.find_trade_matches(conn, email, term, c["subject"], c["course_number"]),
        )

    @app.get("/healthz")
    def healthz():
        get_db().execute("SELECT 1")
        return "ok"

    @app.get("/stats")
    def stats_page():
        return render_template("stats.html", stats=db.stats(get_db()),
                               alert_status=schedule.describe(schedule.status()))

    @app.get("/admin")
    @login_required
    @admin_required
    def admin():
        conn = get_db()
        return render_template("admin.html", stats=db.stats(conn, top_n=25),
                               blocked=db.blocked_users(conn),
                               alert_status=schedule.describe(schedule.status()),
                               max_subs=max_subscriptions(),
                               sms_enabled=app.extensions["texter"].configured)

    @app.post("/api/admin/block")
    @login_required
    @admin_required
    @json_post
    def api_admin_block():
        body = request.get_json()
        email = str(body.get("email", "")).strip().lower()
        if "@" not in email:
            return jsonify(error="Enter an email address."), 400
        if body.get("block", True):
            db.block_user(get_db(), email, str(body.get("reason", ""))[:200])
        else:
            db.unblock_user(get_db(), email)
        return jsonify(ok=True)

    @app.post("/api/admin/clear-section")
    @login_required
    @admin_required
    @json_post
    def api_admin_clear_section():
        body = request.get_json()
        n = db.clear_section_subscriptions(get_db(), str(body.get("term")), str(body.get("crn")))
        return jsonify(ok=True, removed=n)

    @app.post("/api/alert-email")
    @login_required
    @json_post
    def api_alert_email():
        """Send alerts to a personal address. It must be confirmed from that inbox first."""
        email = session["email"]
        addr = str(request.get_json().get("email") or "").strip().lower()
        if not addr:
            db.set_alert_email(get_db(), email, None)
            return jsonify(ok=True, alert_email=None)
        if "@" not in addr or "." not in addr.rpartition("@")[2] or len(addr) > 200:
            return jsonify(error="Enter a valid email address."), 400
        if addr == email:
            db.set_alert_email(get_db(), email, None)
            return jsonify(ok=True, alert_email=None)
        link = base_url() + url_for("confirm_alert_email", token=make_alert_email_token(email, addr))
        try:
            app.extensions["mailer"].send(
                addr, f"Confirm where {SITE_NAME} sends your alerts",
                f"{email} asked {SITE_NAME} to send seat and trade alerts to this address.\n\n"
                f"Confirm here (the link works for 24 hours):\n{link}\n\n"
                f"If that wasn't you, ignore this email and nothing will change.\n",
            )
        except Exception as e:
            app.logger.error("alert-email confirmation to %s failed: %s", addr, e)
            return jsonify(error="We couldn't send the confirmation email. Try again in a minute."), 502
        return jsonify(ok=True, pending=addr)

    @app.route("/confirm-alert-email/<token>", methods=["GET", "POST"])
    def confirm_alert_email(token):
        data = read_alert_email_token(token)
        if data is None:
            flash("That confirmation link is invalid or expired. Request a new one from your dashboard.",
                  "danger")
            return redirect(url_for("index"))
        email, addr = data
        if request.method == "GET":   # email scanners open links; only the button press counts
            return render_template("confirm_alert_email.html", addr=addr)
        db.set_alert_email(get_db(), email, addr)
        flash(f"Alerts will now go to {addr}.", "success")
        return redirect(url_for("dashboard") if session.get("email") == email else url_for("index"))

    @app.post("/api/phone")
    @login_required
    @json_post
    def api_phone():
        raw = str(request.get_json().get("phone") or "").strip()
        try:
            phone = normalize_us_phone(raw) if raw else None
        except ValueError as e:
            return jsonify(error=str(e)), 400
        db.set_phone(get_db(), session["email"], phone)
        return jsonify(ok=True, phone=phone)

    @app.get("/about")
    def about():
        return render_template("about.html")

    @app.route("/unsubscribe/<token>", methods=["GET", "POST"])
    def unsubscribe_link(token):
        data = read_unsubscribe_token(token)
        if data is None:
            abort(404)
        email, term, crn = data
        conn = get_db()
        sec = db.get_section(conn, term, crn)
        if request.method == "POST":
            db.remove_subscription(conn, email, term, crn)
            return render_template("unsubscribed.html", sec=sec, crn=crn, done=True)
        return render_template("unsubscribed.html", sec=sec, crn=crn, done=False)

    # ---------- JSON API used by the pages ----------

    @app.get("/api/search")
    @login_required
    def api_search():
        term = current_term()
        rows = db.search_courses(get_db(), term, request.args.get("q", "")) if term else []
        return jsonify([
            {"subject": r["subject"], "number": r["course_number"], "title": r["title"],
             "sections": r["sections"], "full": r["full_sections"],
             "url": url_for("course", term=term, subject=r["subject"],
                            course_number=r["course_number"])}
            for r in rows
        ])

    @app.post("/api/subscribe")
    @login_required
    @json_post
    def api_subscribe():
        body = request.get_json()
        term, crn, on = str(body.get("term")), str(body.get("crn")), bool(body.get("subscribe"))
        conn = get_db()
        sec = db.get_section(conn, term, crn)
        if sec is None:
            return jsonify(error="Section not found."), 404
        email = session["email"]
        if on:
            if sec["max_enrollment"] <= 0:
                return jsonify(error="This section is closed."), 400
            if sec["seats_available"] > 0:
                return jsonify(error="This section has open seats. Register now!"), 400
            if has_active_waitlist(sec["wait_capacity"], sec["wait_available"], sec["wait_count"]):
                return jsonify(error="This section has an official waitlist. Join it in Experience "
                                     "so the seat is held for you."), 400
            already = conn.execute("SELECT 1 FROM subscriptions WHERE email = ? AND term = ? AND crn = ?",
                                   (email, term, crn)).fetchone()
            if not already and db.count_subscriptions(conn, email, term) >= max_subscriptions():
                return jsonify(error=f"You can watch up to {max_subscriptions()} sections per term. "
                                     "Turn one off to add another."), 400
            db.add_subscription(conn, email, term, crn)
        else:
            db.remove_subscription(conn, email, term, crn)
        watchers = len(db.subscribers_for(conn, term, crn))
        return jsonify(subscribed=on, position=db.subscription_position(conn, email, term, crn),
                       watchers=watchers)

    def _matches_json(conn, email, term, subject, course_number):
        mailer = app.extensions["mailer"]
        matches = refresh_matches(conn, mailer, email, term, subject, course_number)
        return [{"give": f'{m["subject"]} {m["course_number"]}-{m["my_section"]}',
                 "get": f'{m["subject"]} {m["course_number"]}-{m["their_section"]}',
                 "partner": m["partner"]} for m in matches]

    @app.post("/api/enrollment")
    @login_required
    @json_post
    def api_enrollment():
        """Set (or clear, with crn=null) the section I'm in for a course, and my trade opt-in."""
        body = request.get_json()
        term = str(body.get("term"))
        email = session["email"]
        conn = get_db()
        crn = body.get("crn")
        if crn:
            try:
                sec = db.set_enrollment(conn, email, term, str(crn), bool(body.get("open_to_trade", True)))
            except ValueError as e:
                return jsonify(error=str(e)), 400
            subject, number = sec["subject"], sec["course_number"]
        else:
            subject, number = str(body.get("subject", "")).upper(), str(body.get("course_number", "")).upper()
            db.clear_enrollment(conn, email, term, subject, number)
        return jsonify(matches=_matches_json(conn, email, term, subject, number))

    @app.post("/api/trade-want")
    @login_required
    @json_post
    def api_trade_want():
        body = request.get_json()
        term, crn = str(body.get("term")), str(body.get("crn"))
        email = session["email"]
        conn = get_db()
        try:
            sec = db.set_trade_want(conn, email, term, crn, bool(body.get("want")))
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(matches=_matches_json(conn, email, term, sec["subject"], sec["course_number"]))

    return app


app = create_app()
