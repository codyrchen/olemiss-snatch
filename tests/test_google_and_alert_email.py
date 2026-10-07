import base64
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import pytest

from olemiss_snatch import db, google_auth
from olemiss_snatch.banner import parse_section
from olemiss_snatch.notify import notify_opening
from olemiss_snatch.web import create_app

API = {"X-Requested-With": "fetch"}


class FakeMailer:
    configured = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


def jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shh")
    path = str(tmp_path / "g.db")
    c = db.connect(path)
    db.save_snapshot(c, [parse_section({
        "term": "202730", "courseReferenceNumber": "1", "subject": "MATH", "courseNumber": "1150",
        "sequenceNumber": "001", "courseTitle": "Stats", "seatsAvailable": 0,
        "maximumEnrollment": 40, "waitAvailable": 0})])
    c.close()
    a = create_app(db_path=path, mailer=FakeMailer())
    a.config["TESTING"] = True
    return a


def google_login(app, monkeypatch, claims_override=None, tamper_state=False):
    client = app.test_client()
    r = client.get("/login/google")
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["Location"]).query)
    assert q["hd"] == ["go.olemiss.edu"] and q["scope"] == ["openid email"]
    claims = {"iss": "https://accounts.google.com", "aud": "cid.apps.googleusercontent.com",
              "exp": time.time() + 300, "nonce": q["nonce"][0], "email": "cody@go.olemiss.edu",
              "email_verified": True, "hd": "go.olemiss.edu"}
    claims.update(claims_override or {})

    class R:
        status_code = 200
        def json(self): return {"id_token": jwt(claims)}
    monkeypatch.setattr(google_auth.requests, "post", lambda url, **kw: R())
    state = "wrong" if tamper_state else q["state"][0]
    resp = client.get(f"/auth/google/callback?state={state}&code=abc", follow_redirects=True)
    return client, resp


# ---------- Google sign-in ----------

def test_landing_shows_google_button(app):
    page = app.test_client().get("/").data.decode()
    assert "Sign in with your Ole Miss Google account" in page
    assert "Or get a sign-in link by email" in page


def test_google_button_hidden_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    a = create_app(db_path=str(tmp_path / "n.db"), mailer=FakeMailer())
    assert "Google account" not in a.test_client().get("/").data.decode()
    assert a.test_client().get("/login/google").status_code == 404


def test_google_sign_in_works(app, monkeypatch):
    client, resp = google_login(app, monkeypatch)
    assert b"My Subscriptions" in resp.data
    assert db.get_user(db.connect(app.config["DB_PATH"]), "cody@go.olemiss.edu") is not None


@pytest.mark.parametrize("override, message", [
    ({"hd": None, "email": "cody@gmail.com"}, b"Please choose your Ole Miss Google account"),          # personal Gmail
    ({"hd": "go.olemiss.edu", "email": "cody@gmail.com"}, b"Please choose your Ole Miss Google account"),
    ({"email_verified": False}, b"Please choose your Ole Miss Google account"),
    ({"aud": "someone-else"}, b"didn&#39;t go through"),
    ({"iss": "https://evil.example"}, b"didn&#39;t go through"),
    ({"nonce": "replayed"}, b"didn&#39;t go through"),
    ({"exp": time.time() - 10}, b"expired"),
])
def test_google_rejects_bad_tokens(app, monkeypatch, override, message):
    client, resp = google_login(app, monkeypatch, override)
    assert message in resp.data
    assert client.get("/dashboard").status_code == 302      # not signed in


def test_google_rejects_wrong_state(app, monkeypatch):
    client, resp = google_login(app, monkeypatch, tamper_state=True)
    assert b"expired" in resp.data
    assert client.get("/dashboard").status_code == 302


def test_google_respects_blocklist(app, monkeypatch):
    db.block_user(db.connect(app.config["DB_PATH"]), "cody@go.olemiss.edu")
    client, resp = google_login(app, monkeypatch)
    assert b"suspended" in resp.data


# ---------- personal alert email ----------

def test_alert_email_needs_confirmation_then_receives_alerts(app, monkeypatch):
    client, _ = google_login(app, monkeypatch)
    mailer = app.extensions["mailer"]
    r = client.post("/api/alert-email", json={"email": "Cody.Personal@gmail.com"}, headers=API)
    assert r.get_json() == {"ok": True, "pending": "cody.personal@gmail.com"}
    to, subject, body = mailer.sent[-1]
    assert to == "cody.personal@gmail.com" and "Confirm" in subject

    conn = db.connect(app.config["DB_PATH"])
    assert db.alert_address(conn, "cody@go.olemiss.edu") == "cody@go.olemiss.edu"   # not yet
    path = re.search(r"https?://[^/]+(/confirm-alert-email/\S+)", body).group(1)
    assert b"Confirm" in client.get(path).data                                        # GET changes nothing
    assert db.alert_address(conn, "cody@go.olemiss.edu") == "cody@go.olemiss.edu"
    client.post(path)
    assert db.alert_address(conn, "cody@go.olemiss.edu") == "cody.personal@gmail.com"

    # Seat alerts now go to the personal address.
    db.add_subscription(conn, "cody@go.olemiss.edu", "202730", "1")
    [opening] = db.save_snapshot(conn, [parse_section({
        "term": "202730", "courseReferenceNumber": "1", "subject": "MATH", "courseNumber": "1150",
        "sequenceNumber": "001", "courseTitle": "Stats", "seatsAvailable": 1,
        "maximumEnrollment": 40, "waitAvailable": 0})])
    m = FakeMailer()
    notify_opening(conn, m, opening)
    assert m.sent[0][0] == "cody.personal@gmail.com"

    client.post("/api/alert-email", json={"email": ""}, headers=API)                  # back to school email
    assert db.alert_address(conn, "cody@go.olemiss.edu") == "cody@go.olemiss.edu"


def test_alert_email_bad_input_and_forged_link(app, monkeypatch):
    client, _ = google_login(app, monkeypatch)
    assert client.post("/api/alert-email", json={"email": "nope"}, headers=API).status_code == 400
    r = client.get("/confirm-alert-email/forged", follow_redirects=True)
    assert b"invalid or expired" in r.data
