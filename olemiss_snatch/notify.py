"""Email subscribers when a section they're watching opens up."""

import os
import smtplib
import sqlite3
from email.message import EmailMessage

import requests

from . import db
from .links import base_url, unsubscribe_url

REGISTRATION_URL = "https://experience.elluciancloud.com/umsaasproduction"

# Email this many people per open seat, in waitlist order. Some people won't
# act on the alert, so a few extra get it.
BATCH_MULTIPLIER = 3


def load_env(path: str = ".env"):
    """Load KEY=VALUE lines into os.environ (existing variables win)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


RESEND_URL = "https://api.resend.com/emails"


class Mailer:
    """Sends via Resend's API or SMTP, whichever is configured; otherwise prints (dry run).

    Resend is for hosting (Railway blocks SMTP on cheaper plans); SMTP/Gmail works locally.
    """

    def __init__(self):
        self.resend_key = os.environ.get("RESEND_API_KEY")
        self.host = os.environ.get("SMTP_HOST")
        self.port = int(os.environ.get("SMTP_PORT", "587"))
        self.user = os.environ.get("SMTP_USER")
        self.password = os.environ.get("SMTP_PASSWORD")
        self.sender = os.environ.get("MAIL_FROM") or self.user

    @property
    def configured(self) -> bool:
        return bool(self.resend_key) or bool(self.host and self.user and self.password)

    def send(self, to: str, subject: str, body: str):
        if self.resend_key:
            r = requests.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self.resend_key}"},
                json={"from": self.sender, "to": [to], "subject": subject, "text": body},
                timeout=30,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"Resend error {r.status_code}: {r.text[:200]}")
            return
        if not self.configured:
            print(f"--- (dry run, email not configured) email to {to} ---\n"
                  f"Subject: {subject}\n\n{body}\n---")
            return
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(self.user, self.password)
            smtp.send_message(msg)


def opening_email(opening: db.Opening, email: str) -> tuple[str, str]:
    s = opening.section
    seats = "1 seat" if s.seats_available == 1 else f"{s.seats_available} seats"
    subject = f"Seat open: {s.label} {s.title} (CRN {s.crn})"
    body = (
        f"{s.label} {s.title} has {seats} open "
        f"({s.seats_available} of {s.max_enrollment}).\n\n"
        f"CRN: {s.crn}\n"
        f"Term: {s.term}\n\n"
        f"Register now: {REGISTRATION_URL}\n"
        f"Student > Registration > Register for Classes, then add CRN {s.crn}.\n\n"
        f"Seats go fast; other students may have been notified too.\n\n"
        f"Stop alerts for this section: {unsubscribe_url(email, s.term, s.crn)}\n"
        f"Manage your subscriptions: {base_url()}/dashboard\n"
    )
    return subject, body


def notify_opening(conn: sqlite3.Connection, mailer: Mailer, opening: db.Opening) -> list[str]:
    """Email the next batch of subscribers in waitlist order. Returns who was emailed."""
    s = opening.section
    if s.wait_count > 0:
        # Banner offers this seat to the official waitlist first; an alert would mislead.
        print(f"  (skipping alerts for {s.crn}: {s.wait_count} on the official waitlist)")
        return []
    batch = max(s.seats_available, 1) * BATCH_MULTIPLIER
    sent = []
    for sub in db.subscribers_for(conn, s.term, s.crn)[:batch]:
        if db.was_notified(conn, sub["id"], opening.opening_id):
            continue
        subject, body = opening_email(opening, sub["email"])
        try:
            mailer.send(sub["email"], subject, body)
        except Exception as e:  # one bad address shouldn't stop the rest
            print(f"  ! email to {sub['email']} failed: {e}")
            continue
        db.record_notification(conn, sub["id"], opening.opening_id)
        sent.append(sub["email"])
    return sent
