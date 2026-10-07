"""RebelSnatch Trades: connect students who each want the other's section of a course."""

import sqlite3

from . import db
from .links import base_url
from .notify import REGISTRATION_URL, Mailer

SWAP_STEPS = (
    "How to swap safely:\n"
    "1. Email each other and pick an exact time when you're both at a computer.\n"
    "2. At that time, both of you open Experience > Registration > Register for Classes.\n"
    "3. Drop your current section and add the other's CRN at the same moment.\n"
    "Heads-up: if a section has students on its official waitlist, a dropped seat goes to\n"
    "them first. Swaps work best when the waitlist is empty or closed; otherwise ask the\n"
    "instructor about a permission override.\n"
)


def _label(row, which: str) -> str:
    return f'{row["subject"]} {row["course_number"]}-{row[which]}'


def match_email(match, me: str) -> tuple[str, str]:
    give, get = _label(match, "my_section"), _label(match, "their_section")
    subject = f"Trade match: your {give} for {get}"
    body = (
        f"Good news: another student is in {get} ({match['title']}) and wants your section, "
        f"{give}. You want theirs, so you can swap.\n\n"
        f"Your partner: {match['partner']}\n"
        f"You give: {give} (CRN {match['my_crn']})\n"
        f"You get:  {get} (CRN {match['their_crn']})\n\n"
        f"{SWAP_STEPS}\n"
        f"Register: {REGISTRATION_URL}\n"
        f"See your trades: {base_url()}/dashboard\n"
    )
    return subject, body


def refresh_matches(conn: sqlite3.Connection, mailer: Mailer, email: str,
                    term: str, subject: str, course_number: str) -> list:
    """Find this user's matches in a course and email both sides of any new pair."""
    matches = db.find_trade_matches(conn, email, term, subject, course_number)
    for m in matches:
        if not db.record_trade_match(conn, term, email, m["my_crn"], m["partner"], m["their_crn"]):
            continue
        # The partner's view of the same match.
        partner_view = next(
            (pm for pm in db.find_trade_matches(conn, m["partner"], term, subject, course_number)
             if pm["partner"] == email.lower() and pm["my_crn"] == m["their_crn"]),
            None,
        )
        for to, view in ((email, m), (m["partner"], partner_view)):
            if view is None:
                continue
            try:
                mailer.send(to, *match_email(view, to))
            except Exception as e:  # the match still shows on both dashboards
                print(f"  ! trade email to {to} failed: {e}")
    return matches
