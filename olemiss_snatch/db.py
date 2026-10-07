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
"""


@dataclass(frozen=True)
class Opening:
    section: Section
    seats_before: int


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
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    openings = []
    with conn:
        for s in sections:
            prev = conn.execute(
                "SELECT seats_available, max_enrollment FROM sections WHERE term = ? AND crn = ?",
                (s.term, s.crn),
            ).fetchone()
            was_full = prev is not None and prev["max_enrollment"] > 0 and prev["seats_available"] <= 0
            if was_full and s.seats_available > 0 and not s.is_closed:
                openings.append(Opening(s, prev["seats_available"]))
                conn.execute(
                    "INSERT INTO openings (term, crn, seats_before, seats_after, detected_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (s.term, s.crn, prev["seats_available"], s.seats_available, now),
                )
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
