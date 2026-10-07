import re
from datetime import datetime

import pytest

from olemiss_snatch import db, notify, poll, schedule
from olemiss_snatch.banner import parse_section
from olemiss_snatch.notify import Texter, normalize_us_phone, notify_opening
from olemiss_snatch.web import create_app

API = {"X-Requested-With": "fetch"}


class FakeMailer:
    configured = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


class FakeTexter:
    configured = True

    def __init__(self):
        self.sent = []

    def send(self, to, body):
        self.sent.append((to, body))


def raw(crn, seats=0, cap=40, section="001"):
    return {"term": "202730", "courseReferenceNumber": crn, "subject": "MATH", "courseNumber": "1150",
            "sequenceNumber": section, "courseTitle": "Stats", "seatsAvailable": seats,
            "maximumEnrollment": cap, "waitAvailable": 0}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_SUBSCRIPTIONS", "2")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@go.olemiss.edu")
    monkeypatch.delenv("POLL_WINDOWS", raising=False)
    path = str(tmp_path / "f.db")
    c = db.connect(path)
    db.save_snapshot(c, [parse_section(raw(str(i), section=f"00{i}")) for i in range(1, 5)])
    c.close()
    a = create_app(db_path=path, mailer=FakeMailer())
    a.config["TESTING"] = True
    return a


def login(app, email):
    client = app.test_client()
    client.post("/login", data={"email": email})
    body = app.extensions["mailer"].sent[-1][2]
    client.post(re.search(r"https?://[^/]+(/auth/\S+)", body).group(1))
    return client


def sub(client, crn, on=True):
    return client.post("/api/subscribe", json={"term": "202730", "crn": crn, "subscribe": on}, headers=API)


# ---------- subscription limit ----------

def test_subscription_limit(app):
    c = login(app, "a@go.olemiss.edu")
    assert sub(c, "1").status_code == 200
    assert sub(c, "2").status_code == 200
    r = sub(c, "3")
    assert r.status_code == 400 and "up to 2" in r.get_json()["error"]
    assert sub(c, "2").status_code == 200          # re-saving an existing one is fine
    sub(c, "1", on=False)
    assert sub(c, "3").status_code == 200          # freed a slot


# ---------- alert windows ----------

WINDOWS = "2026-11-02 07:00 to 2026-11-20 23:59; 2027-01-12 06:00 to 2027-01-26 23:59"


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=schedule.TZ)


def test_window_status():
    assert schedule.status(raw="").active                                   # unset = always on
    st = schedule.status(at("2026-11-10 12:00"), WINDOWS)
    assert st.active and st.current_end == at("2026-11-20 23:59")
    st = schedule.status(at("2026-12-01 12:00"), WINDOWS)
    assert not st.active and st.next_start == at("2027-01-12 06:00")
    st = schedule.status(at("2027-03-01 12:00"), WINDOWS)
    assert not st.active and st.next_start is None
    assert "paused until Jan 12" in schedule.describe(schedule.status(at("2026-12-01 12:00"), WINDOWS))


def test_bad_windows_rejected():
    with pytest.raises(ValueError):
        schedule.parse_windows("next tuesday to friday")
    with pytest.raises(ValueError):
        schedule.parse_windows("2026-11-20 00:00 to 2026-11-02 00:00")


def test_poller_sleeps_outside_window(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POLL_WINDOWS", "2000-01-01 00:00 to 2000-01-02 00:00")   # long past
    monkeypatch.setattr(poll, "load_env", lambda path=".env": None)
    searched = []

    class Client:
        def __init__(self, **kw): pass
        def get_subjects(self, term): return [{"code": "MATH"}]
        def search_subject(self, term, subject): searched.append(subject); return []

    monkeypatch.setattr(poll, "BannerClient", Client)
    sleeps = []

    def fake_sleep(n):
        sleeps.append(n)
        if len(sleeps) == 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(poll.time, "sleep", fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        poll.main(["--term", "202730", "--db", str(tmp_path / "p.db"), "--every", "60"])
    assert searched == []                                     # never hit Banner
    assert capsys.readouterr().out.count("paused") == 1       # said it once


# ---------- text alerts ----------

def test_normalize_phone():
    assert normalize_us_phone("(662) 555-0123") == "+16625550123"
    assert normalize_us_phone("1-662-555-0123") == "+16625550123"
    for bad in ["555-0123", "+44 20 7946 0958", "0625550123"]:
        with pytest.raises(ValueError):
            normalize_us_phone(bad)


def test_phone_api_and_text_on_opening(app):
    c = login(app, "a@go.olemiss.edu")
    r = c.post("/api/phone", json={"phone": "662.555.0123"}, headers=API)
    assert r.get_json()["phone"] == "+16625550123"
    assert c.post("/api/phone", json={"phone": "12"}, headers=API).status_code == 400
    sub(c, "1")

    conn = db.connect(app.config["DB_PATH"])
    [opening] = db.save_snapshot(conn, [parse_section(raw("1", seats=2))])
    texter = FakeTexter()
    notify_opening(conn, FakeMailer(), opening, texter)
    assert texter.sent and texter.sent[0][0] == "+16625550123"
    assert "CRN 1" in texter.sent[0][1] and "STOP" in texter.sent[0][1]

    c.post("/api/phone", json={"phone": ""}, headers=API)                 # remove
    assert db.get_user(conn, "a@go.olemiss.edu")["phone"] is None


def test_no_text_without_phone(app):
    c = login(app, "a@go.olemiss.edu")
    sub(c, "1")
    conn = db.connect(app.config["DB_PATH"])
    [opening] = db.save_snapshot(conn, [parse_section(raw("1", seats=1))])
    texter = FakeTexter()
    assert notify_opening(conn, FakeMailer(), opening, texter) == ["a@go.olemiss.edu"]
    assert texter.sent == []


def test_twilio_request(monkeypatch):
    monkeypatch.setenv("TWILIO_SID", "AC1")
    monkeypatch.setenv("TWILIO_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+18005550000")
    calls = []

    class R:
        status_code, text = 201, "{}"
    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: calls.append((url, kw)) or R())
    Texter().send("+16625550123", "hi")
    [(url, kw)] = calls
    assert url.endswith("/Accounts/AC1/Messages.json") and kw["auth"] == ("AC1", "tok")
    assert kw["data"] == {"From": "+18005550000", "To": "+16625550123", "Body": "hi"}


def test_phone_card_hidden_without_twilio(app):
    c = login(app, "a@go.olemiss.edu")
    assert "Text alerts" not in c.get("/dashboard").data.decode()


# ---------- stats + admin ----------

def test_stats_page_is_public_and_aggregate(app):
    c = login(app, "a@go.olemiss.edu")
    sub(c, "1")
    page = app.test_client().get("/stats").data.decode()
    assert "Most-watched sections" in page and "MATH 1150-001" in page
    assert "a@go.olemiss.edu" not in page                    # no personal info


def test_admin_only_for_admins(app):
    assert login(app, "a@go.olemiss.edu").get("/admin").status_code == 404
    boss = login(app, "boss@go.olemiss.edu")
    assert "Block a student" in boss.get("/admin").data.decode()
    assert "Admin" in boss.get("/dashboard").data.decode()


def test_block_and_unblock(app):
    a = login(app, "a@go.olemiss.edu")
    sub(a, "1")
    boss = login(app, "boss@go.olemiss.edu")
    assert login(app, "x@go.olemiss.edu").post("/api/admin/block", json={"email": "a@go.olemiss.edu"},
                                                 headers=API).status_code == 404     # non-admin
    assert boss.post("/api/admin/block", json={"email": "a@go.olemiss.edu", "reason": "spam"},
                     headers=API).get_json() == {"ok": True}

    assert a.get("/dashboard").status_code == 302              # kicked out
    conn = db.connect(app.config["DB_PATH"])
    assert db.subscribers_for(conn, "202730", "1") == []        # subscriptions removed
    sent_before = len(app.extensions["mailer"].sent)
    r = app.test_client().post("/login", data={"email": "a@go.olemiss.edu"}, follow_redirects=True)
    assert b"suspended" in r.data and len(app.extensions["mailer"].sent) == sent_before

    boss.post("/api/admin/block", json={"email": "a@go.olemiss.edu", "block": False}, headers=API)
    assert login(app, "a@go.olemiss.edu").get("/dashboard").status_code == 200


def test_admin_clear_section(app):
    for e in ["a@go.olemiss.edu", "b@go.olemiss.edu"]:
        sub(login(app, e), "1")
    boss = login(app, "boss@go.olemiss.edu")
    r = boss.post("/api/admin/clear-section", json={"term": "202730", "crn": "1"}, headers=API)
    assert r.get_json()["removed"] == 2
