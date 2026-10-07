"""RebelSnatch website: sign in with an Ole Miss email, search courses, snatch full sections.

Run locally:
    python -m flask --app olemiss_snatch.web run --debug
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)

from . import db
from .links import base_url, read_unsubscribe_token, secret_key
from .notify import REGISTRATION_URL, Mailer, load_env

SITE_NAME = "RebelSnatch"
LOGIN_LINK_MINUTES = 15
LOGIN_REQUESTS_PER_HOUR = 5


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
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key(),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=base_url().startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        DB_PATH=db_path or os.environ.get("SNATCH_DB", "snatch.db"),
    )
    app.extensions["mailer"] = mailer or Mailer()
    app.jinja_env.globals.update(SITE_NAME=SITE_NAME, term_name=term_name,
                                 REGISTRATION_URL=REGISTRATION_URL)

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
        return {"user_email": session.get("email")}

    # ---------- sign in ----------

    @app.get("/")
    def index():
        if "email" in session:
            return redirect(url_for("dashboard"))
        return render_template("index.html", domains=allowed_domains())

    @app.post("/login")
    def login():
        email = (request.form.get("email") or "").strip().lower()
        domain = email.rpartition("@")[2]
        if "@" not in email or domain not in allowed_domains():
            flash(f"Use your Ole Miss email ({' or '.join('@' + d for d in allowed_domains())}).",
                  "danger")
            return redirect(url_for("index"))
        conn = get_db()
        now = datetime.now(timezone.utc)
        if db.recent_login_requests(conn, email, _iso(now - timedelta(hours=1))) >= LOGIN_REQUESTS_PER_HOUR:
            flash("Too many sign-in links requested. Try again in an hour.", "danger")
            return redirect(url_for("index"))
        token = secrets.token_urlsafe(32)
        db.save_login_token(conn, _hash(token), email,
                            _iso(now + timedelta(minutes=LOGIN_LINK_MINUTES)))
        link = f"{base_url()}{url_for('auth', token=token)}"
        app.extensions["mailer"].send(
            email, f"Your {SITE_NAME} sign-in link",
            f"Click to sign in to {SITE_NAME}:\n\n{link}\n\n"
            f"This link works once and expires in {LOGIN_LINK_MINUTES} minutes. "
            f"If you didn't ask for it, ignore this email.\n",
        )
        return render_template("check_email.html", email=email)

    # GET only shows a button; the POST signs in. Email security scanners
    # open links automatically, and that must not use up the token.
    @app.route("/auth/<token>", methods=["GET", "POST"])
    def auth(token):
        if request.method == "GET":
            return render_template("confirm_login.html", token=token)
        email = db.use_login_token(get_db(), _hash(token))
        if email is None:
            flash("That sign-in link expired or was already used. Request a new one.", "danger")
            return redirect(url_for("index"))
        session.clear()
        session.permanent = True
        session["email"] = email
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
        return render_template("dashboard.html", term=term, terms=db.terms(conn), subs=subs,
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
        return render_template("course.html", term=term, terms=db.terms(conn), sections=sections,
                               course=sections[0], updated=time_ago(db.last_updated(conn, term)))

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
            db.add_subscription(conn, email, term, crn)
        else:
            db.remove_subscription(conn, email, term, crn)
        watchers = len(db.subscribers_for(conn, term, crn))
        return jsonify(subscribed=on, position=db.subscription_position(conn, email, term, crn),
                       watchers=watchers)

    return app


app = create_app()
