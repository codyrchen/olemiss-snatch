"""SQLite storage for section snapshots and seat-opening events."""

import sqlite3
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
"""


@dataclass(frozen=True)
class Opening:
    section: Section
    seats_before: int
    opening_id: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str = "snatch.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
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
                "INSERT INTO sections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (term, crn) DO UPDATE SET"
                " subject = excluded.subject, course_number = excluded.course_number,"
                " section = excluded.section, title = excluded.title,"
                " seats_available = excluded.seats_available,"
                " max_enrollment = excluded.max_enrollment,"
                " wait_available = excluded.wait_available, updated_at = excluded.updated_at",
                (s.term, s.crn, s.subject, s.course_number, s.section, s.title,
                 s.seats_available, s.max_enrollment, s.wait_available, now),
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
        " sec.seats_available, sec.max_enrollment"
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
