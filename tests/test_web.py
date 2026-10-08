import re

import pytest

from olemiss_snatch import db
from olemiss_snatch.banner import parse_section
from olemiss_snatch.links import unsubscribe_url
from olemiss_snatch.web import create_app, term_name

API = {"X-Requested-With": "fetch"}


class FakeMailer:
    configured = True

    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


def raw(crn, seats, cap=40, number="1150", section="001", title="Elementary Statistics"):
    return {
        "term": "202710", "courseReferenceNumber": crn, "subject": "MATH",
        "courseNumber": number, "sequenceNumber": section, "courseTitle": title,
        "seatsAvailable": seats, "maximumEnrollment": cap, "waitAvailable": 0,
        "faculty": [{"displayName": "Smith, Jane", "primaryIndicator": True}],
        "meetingsFaculty": [{"meetingTime": {"monday": True, "wednesday": True, "friday": True,
                                             "beginTime": "0900", "endTime": "0950"}}],
    }


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "test.db")
    conn = db.connect(path)
    db.save_snapshot(conn, [parse_section(r) for r in [
        raw("10001", 0, section="001"),
        raw("10002", 5, section="002"),
        raw("10003", 0, cap=0, section="003"),
        raw("20001", 0, number="2611", title="Unified Calculus &amp; Analytic Geometry"),
    ]])
    conn.close()
    app = create_app(db_path=path, mailer=FakeMailer())
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, app, email="cody@go.olemiss.edu"):
    client.post("/login", data={"email": email})
    _, _, body = app.extensions["mailer"].sent[-1]
    path = re.search(r"https?://[^/]+(/auth/\S+)", body).group(1)
    return client.post(path)


def test_term_names():
    assert term_name("202710") == "Fall 2026"
    assert term_name("202730") == "Spring 2027"
    assert term_name("202750") == "Summer 2027"


def test_landing_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Sign in with your Ole Miss email" in r.data


def test_rejects_non_olemiss_email(client, app):
    r = client.post("/login", data={"email": "someone@gmail.com"}, follow_redirects=True)
    assert b"Use your Ole Miss email" in r.data
    assert app.extensions["mailer"].sent == []


def test_magic_link_login_and_single_use(client, app):
    r = login(client, app)
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")
    assert b"My Subscriptions" in client.get("/dashboard").data

    # GET on the link (e.g. an email scanner) must not consume it; a second POST fails.
    _, _, body = app.extensions["mailer"].sent[-1]
    path = re.search(r"https?://[^/]+(/auth/\S+)", body).group(1)
    other = app.test_client()
    assert b"Continue" in other.get(path).data
    r = other.post(path, follow_redirects=True)
    assert b"expired or was already used" in r.data


def test_login_rate_limit(client, app):
    for _ in range(5):
        client.post("/login", data={"email": "cody@go.olemiss.edu"})
    r = client.post("/login", data={"email": "cody@go.olemiss.edu"}, follow_redirects=True)
    assert b"Too many" in r.data
    assert len(app.extensions["mailer"].sent) == 5


def test_pages_require_login(client):
    assert client.get("/dashboard").status_code == 302
    assert client.get("/api/search?q=math").status_code == 401


def test_search(client, app):
    login(client, app)
    for q in ["MATH 1150", "math1150", "statistics", "smith"]:
        data = client.get("/api/search", query_string={"q": q}).get_json()
        assert [c["number"] for c in data][:1] == ["1150"], q
    math = client.get("/api/search?q=MATH").get_json()
    assert {c["number"] for c in math} == {"1150", "2611"}
    assert next(c for c in math if c["number"] == "1150")["full"] == 1  # closed one isn't "full"
    assert client.get("/api/search?q=2611").get_json()[0]["title"] == "Unified Calculus & Analytic Geometry"


def test_course_page(client, app):
    login(client, app)
    r = client.get("/course/202710/MATH/1150")
    assert r.status_code == 200
    html = r.data.decode()
    assert "MWF 9:00-9:50am" in html and "Smith, Jane" in html
    assert 'data-crn="10001"' in html       # full -> switch
    assert 'data-crn="10002"' not in html   # open -> register link instead
    assert "Closed" in html
    assert client.get("/course/202710/MATH/9999").status_code == 404


def test_subscribe_flow(client, app):
    login(client, app)
    r = client.post("/api/subscribe", json={"term": "202710", "crn": "10001", "subscribe": True}, headers=API)
    assert r.get_json() == {"subscribed": True, "position": 1, "watchers": 1}

    other = app.test_client()
    login(other, app, "friend@go.olemiss.edu")
    r = other.post("/api/subscribe", json={"term": "202710", "crn": "10001", "subscribe": True}, headers=API)
    assert r.get_json()["position"] == 2

    html = client.get("/dashboard").data.decode()
    assert "MATH 1150" in html and "#1" in html and "of 2" in html

    r = client.post("/api/subscribe", json={"term": "202710", "crn": "10001", "subscribe": False}, headers=API)
    assert r.get_json()["watchers"] == 1
    assert "not watching any sections" in client.get("/dashboard").data.decode()


def test_subscribe_rejects_open_closed_and_missing(client, app):
    login(client, app)
    post = lambda crn: client.post("/api/subscribe", json={"term": "202710", "crn": crn, "subscribe": True}, headers=API)
    assert post("10002").status_code == 400   # has open seats
    assert post("10003").status_code == 400   # closed
    assert post("99999").status_code == 404


def test_subscribe_requires_fetch_header(client, app):
    login(client, app)
    r = client.post("/api/subscribe", json={"term": "202710", "crn": "10001", "subscribe": True})
    assert r.status_code == 400


def test_unsubscribe_link(client, app):
    conn = db.connect(app.config["DB_PATH"])
    db.add_subscription(conn, "cody@go.olemiss.edu", "202710", "10001")
    path = unsubscribe_url("cody@go.olemiss.edu", "202710", "10001").split("127.0.0.1:5000", 1)[1]

    assert b"Stop alerts for MATH 1150-001" in client.get(path).data
    assert db.subscribers_for(conn, "202710", "10001")  # GET doesn't unsubscribe
    assert b"Unsubscribed" in client.post(path).data
    assert db.subscribers_for(conn, "202710", "10001") == []
    assert client.get("/unsubscribe/forged-token").status_code == 404


def test_login_email_failure_shows_message(app, client, caplog):
    def broken_send(to, subject, body):
        raise RuntimeError("Resend error 403: domain not verified")
    app.extensions["mailer"].send = broken_send

    r = client.post("/login", data={"email": "cody@go.olemiss.edu"})
    assert r.status_code == 302
    page = client.get(r.headers["Location"]).data.decode()
    assert "couldn&#39;t send the sign-in email" in page or "couldn't send the sign-in email" in page
    assert "Resend error 403" in caplog.text


def test_failed_sends_dont_count_toward_rate_limit(app, client):
    real_send = app.extensions["mailer"].send

    def broken_send(to, subject, body):
        raise RuntimeError("Resend error 400: API key is invalid")
    app.extensions["mailer"].send = broken_send
    for _ in range(6):
        client.post("/login", data={"email": "cody@go.olemiss.edu"})

    app.extensions["mailer"].send = real_send
    r = client.post("/login", data={"email": "cody@go.olemiss.edu"})
    assert b"Check your email" in r.data


def test_landing_previews_a_real_course(client, app):
    db.add_subscription(db.connect(app.config["DB_PATH"]), "x@go.olemiss.edu", "202710", "10001")
    page = client.get("/").data.decode()
    assert "Live from the class search" in page and "MATH 1150" in page
    assert "0 / 40" in page and "5 / 40" in page        # real seat counts
    assert "Closed" not in page                          # cancelled sections are left out


def test_landing_falls_back_without_data(tmp_path):
    a = create_app(db_path=str(tmp_path / "empty.db"), mailer=FakeMailer())
    page = a.test_client().get("/").data.decode()
    assert "What a course page looks like" in page
