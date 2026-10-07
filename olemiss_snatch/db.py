"""SQLite storage for section snapshots and seat-opening events."""

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .banner import Section

SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    term            TEXT NOT NULL,
    crn             TEXT NOT NULL,
    subject         TEXT NOT NULL,
    course_number   TEXT NOT NULL,
    section         TEXT NOT NULL,
    title           TEXT NOT NULL,
    seats_available INTEGER NOT NULL,
    max_enrollment  INTEGER NOT NULL,
    wait_available  INTEGER NOT NULL,
    updated_at      TEXT NOT NULL,
    instructor      TEXT NOT NULL DEFAULT '',
    meetings        TEXT NOT NULL DEFAULT '',
    wait_capacity   INTEGER NOT NULL DEFAULT 0,
    wait_count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (term, crn)
);

CREATE TABLE IF NOT EXISTS openings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    term         TEXT NOT NULL,
    crn          TEXT NOT NULL,
    seats_before INTEGER NOT NULL,
    seats_after  INTEGER NOT NULL,
    detected_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL,
    term       TEXT NOT NULL,
    crn        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (email, term, crn)
);

CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id INTEGER NOT NULL,
    opening_id      INTEGER NOT NULL,
    sent_at         TEXT NOT NULL,
    UNIQUE (subscription_id, opening_id)
);

CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0
);

-- Trades: self-reported current section per course, plus sections wanted in exchange.
CREATE TABLE IF NOT EXISTS enrollments (
    email         TEXT NOT NULL,
    term          TEXT NOT NULL,
    subject       TEXT NOT NULL,
    course_number TEXT NOT NULL,
    crn           TEXT NOT NULL,
    open_to_trade INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (email, term, subject, course_number)
);

CREATE TABLE IF NOT EXISTS trade_wants (
    email      TEXT NOT NULL,
    term       TEXT NOT NULL,
    crn        TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (email, term, crn)
);

-- Pairs already emailed about a match (email_a < email_b), so nobody is emailed twice.
CREATE TABLE IF NOT EXISTS trade_matches (
    term        TEXT NOT NULL,
    email_a     TEXT NOT NULL,
    crn_a       TEXT NOT NULL,
    email_b     TEXT NOT NULL,
    crn_b       TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    PRIMARY KEY (term, email_a, crn_a, email_b, crn_b)
);

CREATE INDEX IF NOT EXISTS sections_course ON sections (term, subject, course_number);
CREATE INDEX IF NOT EXISTS subscriptions_section ON subscriptions (term, crn);
"""

# Columns added after the first release; connect() adds them to older databases.
MIGRATIONS = {
    "sections": {
        "instructor": "TEXT NOT NULL DEFAULT ''",
        "meetings": "TEXT NOT NULL DEFAULT ''",
        "wait_capacity": "INTEGER NOT NULL DEFAULT 0",
        "wait_count": "INTEGER NOT NULL DEFAULT 0",
    },
}


@dataclass(frozen=True)
class Opening:
    section: Section
    seats_before: int
    opening_id: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_initialized: set[str] = set()
_init_lock = threading.Lock()


def _setup(conn: sqlite3.Connection, path: str):
    """Create tables, apply migrations, and switch the file to WAL mode."""
    # WAL lets the website read while the poller writes. The setting is stored
    # in the file, so only switch if needed (switching takes an exclusive lock).
    if path != ":memory:" and conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        conn.execute("PRAGMA journal_mode = WAL")
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if existing:
            for name, decl in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.executescript(SCHEMA)


def connect(path: str = "snatch.db") -> sqlite3.Connection:
    # timeout: wait up to 15s for another process's write instead of failing.
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    if path == ":memory:":
        _setup(conn, path)
        return conn
    # The website opens a connection per request; only the first one in each
    # process runs setup. Several processes (web workers, poller) may start at
    # once, so retry briefly if another one holds the lock.
    with _init_lock:
        if path not in _initialized:
            for attempt in range(20):
                try:
                    _setup(conn, path)
                    break
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e) or attempt == 19:
                        raise
                    time.sleep(0.25)
            _initialized.add(path)
    return conn


def save_snapshot(conn: sqlite3.Connection, sections: list[Section]) -> list[Opening]:
    """Store the latest seat counts and return sections that went from full to open.

    Sections seen for the first time never count as openings; we have no
    "before" to compare against.
    """
    now = _now()
    openings = []
    with conn:
        for s in sections:
            prev = conn.execute(
                "SELECT seats_available, max_enrollment FROM sections WHERE term = ? AND crn = ?",
                (s.term, s.crn),
            ).fetchone()
            was_full = prev is not None and prev["max_enrollment"] > 0 and prev["seats_available"] <= 0
            if was_full and s.seats_available > 0 and not s.is_closed:
                cur = conn.execute(
                    "INSERT INTO openings (term, crn, seats_before, seats_after, detected_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (s.term, s.crn, prev["seats_available"], s.seats_available, now),
                )
                openings.append(Opening(s, prev["seats_available"], cur.lastrowid))
            conn.execute(
                "INSERT INTO sections (term, crn, subject, course_number, section, title,"
                " seats_available, max_enrollment, wait_available, updated_at, instructor, meetings,"
                " wait_capacity, wait_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (term, crn) DO UPDATE SET"
                " subject = excluded.subject, course_number = excluded.course_number,"
                " section = excluded.section, title = excluded.title,"
                " seats_available = excluded.seats_available,"
                " max_enrollment = excluded.max_enrollment,"
                " wait_available = excluded.wait_available, updated_at = excluded.updated_at,"
                " instructor = excluded.instructor, meetings = excluded.meetings,"
                " wait_capacity = excluded.wait_capacity, wait_count = excluded.wait_count",
                (s.term, s.crn, s.subject, s.course_number, s.section, s.title,
                 s.seats_available, s.max_enrollment, s.wait_available, now,
                 s.instructor, s.meetings, s.wait_capacity, s.wait_count),
            )
    return openings


def get_section(conn: sqlite3.Connection, term: str, crn: str):
    return conn.execute(
        "SELECT * FROM sections WHERE term = ? AND crn = ?", (term, crn)
    ).fetchone()


def add_subscription(conn: sqlite3.Connection, email: str, term: str, crn: str) -> bool:
    """Returns False if this email was already subscribed to the section."""
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO subscriptions (email, term, crn, created_at) VALUES (?, ?, ?, ?)",
            (email.lower(), term, crn, _now()),
        )
    return cur.rowcount == 1


def remove_subscription(conn: sqlite3.Connection, email: str, term: str, crn: str) -> bool:
    with conn:
        cur = conn.execute(
            "DELETE FROM subscriptions WHERE email = ? AND term = ? AND crn = ?",
            (email.lower(), term, crn),
        )
    return cur.rowcount == 1


def list_subscriptions(conn: sqlite3.Connection, email: str | None = None):
    sql = (
        "SELECT sub.*, sec.subject, sec.course_number, sec.section, sec.title,"
        " sec.seats_available, sec.max_enrollment, sec.instructor, sec.meetings,"
        " sec.wait_capacity, sec.wait_available, sec.wait_count,"
        " (SELECT COUNT(*) FROM subscriptions s2"
        "  WHERE s2.term = sub.term AND s2.crn = sub.crn AND s2.id <= sub.id) AS position,"
        " (SELECT COUNT(*) FROM subscriptions s3"
        "  WHERE s3.term = sub.term AND s3.crn = sub.crn) AS watchers"
        " FROM subscriptions sub LEFT JOIN sections sec USING (term, crn)"
    )
    if email:
        return conn.execute(sql + " WHERE sub.email = ? ORDER BY sub.id", (email.lower(),)).fetchall()
    return conn.execute(sql + " ORDER BY sub.term, sub.crn, sub.id").fetchall()


def subscribers_for(conn: sqlite3.Connection, term: str, crn: str):
    """Subscribers in waitlist order (first to subscribe comes first)."""
    return conn.execute(
        "SELECT * FROM subscriptions WHERE term = ? AND crn = ? ORDER BY id", (term, crn)
    ).fetchall()


def was_notified(conn: sqlite3.Connection, subscription_id: int, opening_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM notifications WHERE subscription_id = ? AND opening_id = ?",
        (subscription_id, opening_id),
    ).fetchone() is not None


def record_notification(conn: sqlite3.Connection, subscription_id: int, opening_id: int):
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO notifications (subscription_id, opening_id, sent_at) VALUES (?, ?, ?)",
            (subscription_id, opening_id, _now()),
        )


def terms(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT term FROM sections ORDER BY term DESC")]


def busiest_term(conn: sqlite3.Connection) -> str | None:
    """The term with the most sections, a sensible default for the term picker."""
    row = conn.execute(
        "SELECT term FROM sections GROUP BY term ORDER BY COUNT(*) DESC, term DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def search_courses(conn: sqlite3.Connection, term: str, query: str, limit: int = 50):
    """Courses matching a code ("MATH 1150", "math1150"), title words, or instructor."""
    q = " ".join(query.split())
    if not q:
        return []
    compact = q.replace(" ", "").upper()
    like = f"%{q}%"
    return conn.execute(
        "SELECT subject, course_number, MIN(title) AS title,"
        " COUNT(*) AS sections,"
        " SUM(max_enrollment > 0 AND seats_available <= 0) AS full_sections"
        " FROM sections WHERE term = ? AND ("
        "   (subject || course_number) LIKE ? OR course_number LIKE ?"
        "   OR title LIKE ? OR instructor LIKE ?"
        " )"
        " GROUP BY subject, course_number"
        " ORDER BY (subject || course_number) LIKE ? DESC, subject, course_number"
        " LIMIT ?",
        (term, f"{compact}%", f"{compact}%", like, like, f"{compact}%", limit),
    ).fetchall()


def course_sections(conn: sqlite3.Connection, term: str, subject: str, course_number: str,
                    email: str | None = None):
    """Sections of one course with watcher counts and whether `email` is subscribed."""
    return conn.execute(
        "SELECT sec.*,"
        " (SELECT COUNT(*) FROM subscriptions s WHERE s.term = sec.term AND s.crn = sec.crn) AS watchers,"
        " EXISTS (SELECT 1 FROM subscriptions s WHERE s.term = sec.term AND s.crn = sec.crn"
        "         AND s.email = ?) AS subscribed,"
        " EXISTS (SELECT 1 FROM trade_wants w WHERE w.term = sec.term AND w.crn = sec.crn"
        "         AND w.email = ?) AS trade_wanted"
        " FROM sections sec WHERE term = ? AND subject = ? AND course_number = ?"
        " ORDER BY section",
        ((email or "").lower(), (email or "").lower(), term, subject, course_number),
    ).fetchall()


def subscription_position(conn: sqlite3.Connection, email: str, term: str, crn: str) -> int | None:
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM subscriptions s2"
        "        WHERE s2.term = s.term AND s2.crn = s.crn AND s2.id <= s.id)"
        " FROM subscriptions s WHERE email = ? AND term = ? AND crn = ?",
        (email.lower(), term, crn),
    ).fetchone()
    return row[0] if row else None


def last_updated(conn: sqlite3.Connection, term: str) -> str | None:
    return conn.execute("SELECT MAX(updated_at) FROM sections WHERE term = ?", (term,)).fetchone()[0]


def save_login_token(conn: sqlite3.Connection, token_hash: str, email: str, expires_at: str):
    with conn:
        conn.execute(
            "INSERT INTO login_tokens (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, email.lower(), _now(), expires_at),
        )


def delete_login_token(conn: sqlite3.Connection, token_hash: str):
    with conn:
        conn.execute("DELETE FROM login_tokens WHERE token_hash = ?", (token_hash,))


def recent_login_requests(conn: sqlite3.Connection, email: str, since: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM login_tokens WHERE email = ? AND created_at >= ?",
        (email.lower(), since),
    ).fetchone()[0]


def use_login_token(conn: sqlite3.Connection, token_hash: str) -> str | None:
    """Consume a one-time login token; returns the email if it was valid."""
    with conn:
        row = conn.execute(
            "SELECT email, expires_at, used FROM login_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None or row["used"] or row["expires_at"] < _now():
            return None
        conn.execute("UPDATE login_tokens SET used = 1 WHERE token_hash = ?", (token_hash,))
        return row["email"]


# ---------- Trades ----------

def get_enrollment(conn: sqlite3.Connection, email: str, term: str, subject: str, course_number: str):
    return conn.execute(
        "SELECT * FROM enrollments WHERE email = ? AND term = ? AND subject = ? AND course_number = ?",
        (email.lower(), term, subject, course_number),
    ).fetchone()


def set_enrollment(conn: sqlite3.Connection, email: str, term: str, crn: str, open_to_trade: bool = True):
    """Record which section `email` is in for that section's course. Returns the section row."""
    sec = get_section(conn, term, crn)
    if sec is None:
        raise ValueError("Section not found.")
    email = email.lower()
    with conn:
        conn.execute(
            "INSERT INTO enrollments (email, term, subject, course_number, crn, open_to_trade, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (email, term, subject, course_number) DO UPDATE SET"
            " crn = excluded.crn, open_to_trade = excluded.open_to_trade",
            (email, term, sec["subject"], sec["course_number"], crn, int(open_to_trade), _now()),
        )
        # You can't want the section you're already in.
        conn.execute("DELETE FROM trade_wants WHERE email = ? AND term = ? AND crn = ?", (email, term, crn))
    return sec


def clear_enrollment(conn: sqlite3.Connection, email: str, term: str, subject: str, course_number: str):
    """Leave Trades for a course: forget the current section and every want in that course."""
    email = email.lower()
    with conn:
        conn.execute(
            "DELETE FROM trade_wants WHERE email = ? AND term = ? AND crn IN"
            " (SELECT crn FROM sections WHERE term = ? AND subject = ? AND course_number = ?)",
            (email, term, term, subject, course_number),
        )
        conn.execute(
            "DELETE FROM enrollments WHERE email = ? AND term = ? AND subject = ? AND course_number = ?",
            (email, term, subject, course_number),
        )


def set_trade_want(conn: sqlite3.Connection, email: str, term: str, crn: str, want: bool):
    """Mark a section of the same course as wanted in exchange for the user's current one."""
    email = email.lower()
    sec = get_section(conn, term, crn)
    if sec is None:
        raise ValueError("Section not found.")
    if want:
        mine = get_enrollment(conn, email, term, sec["subject"], sec["course_number"])
        if mine is None:
            raise ValueError("First pick the section you're in for this course.")
        if mine["crn"] == crn:
            raise ValueError("That's the section you're already in.")
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO trade_wants (email, term, crn, created_at) VALUES (?, ?, ?, ?)",
                (email, term, crn, _now()),
            )
    else:
        with conn:
            conn.execute("DELETE FROM trade_wants WHERE email = ? AND term = ? AND crn = ?",
                         (email, term, crn))
    return sec


def trade_wants_for(conn: sqlite3.Connection, email: str, term: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT crn FROM trade_wants WHERE email = ? AND term = ?", (email.lower(), term))}


def find_trade_matches(conn: sqlite3.Connection, email: str, term: str | None = None,
                       subject: str | None = None, course_number: str | None = None):
    """Mutual swaps: I'm in X and want Y; they're in Y and want X. Both opted in."""
    sql = (
        "SELECT me.term, me.subject, me.course_number,"
        " me.crn AS my_crn, mine.section AS my_section,"
        " them.email AS partner, them.crn AS their_crn, theirs.section AS their_section,"
        " theirs.title AS title, theirs.meetings AS their_meetings, mine.meetings AS my_meetings"
        " FROM enrollments me"
        " JOIN trade_wants my_want ON my_want.email = me.email AND my_want.term = me.term"
        " JOIN enrollments them ON them.term = me.term AND them.subject = me.subject"
        "   AND them.course_number = me.course_number AND them.crn = my_want.crn"
        "   AND them.email != me.email AND them.open_to_trade = 1"
        " JOIN trade_wants their_want ON their_want.email = them.email"
        "   AND their_want.term = me.term AND their_want.crn = me.crn"
        " JOIN sections mine ON mine.term = me.term AND mine.crn = me.crn"
        " JOIN sections theirs ON theirs.term = them.term AND theirs.crn = them.crn"
        " WHERE me.email = ? AND me.open_to_trade = 1"
    )
    params: list = [email.lower()]
    for col, val in (("me.term", term), ("me.subject", subject), ("me.course_number", course_number)):
        if val is not None:
            sql += f" AND {col} = ?"
            params.append(val)
    return conn.execute(sql + " ORDER BY me.subject, me.course_number, them.created_at", params).fetchall()


def record_trade_match(conn: sqlite3.Connection, term: str, email_1: str, crn_1: str,
                       email_2: str, crn_2: str) -> bool:
    """Remember that this pair was told about their match. False if already recorded."""
    (ea, ca), (eb, cb) = sorted([(email_1.lower(), crn_1), (email_2.lower(), crn_2)])
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO trade_matches (term, email_a, crn_a, email_b, crn_b, notified_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (term, ea, ca, eb, cb, _now()),
        )
    return cur.rowcount == 1
