import re

import pytest

from olemiss_snatch import db
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


def raw(crn, section, seats=0, cap=40, number="1150", wait_cap=0, wait_avail=0, wait_count=0):
    return {
        "term": "202710", "courseReferenceNumber": crn, "subject": "MATH",
        "courseNumber": number, "sequenceNumber": section, "courseTitle": "Stats",
        "seatsAvailable": seats, "maximumEnrollment": cap, "waitAvailable": wait_avail,
        "waitCapacity": wait_cap, "waitCount": wait_count,
    }


SECTIONS = [
    raw("1", "001"), raw("2", "002"), raw("3", "003"),
    raw("9", "001", number="2611"),
    raw("5", "005", wait_cap=10, wait_avail=4, wait_count=6),  # active official waitlist
]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.save_snapshot(c, [parse_section(r) for r in SECTIONS])
    return c


# ---------- waitlist awareness ----------

def test_wait_fields_parsed_and_stored(conn):
    s = parse_section(raw("5", "005", wait_cap=10, wait_avail=4, wait_count=6))
    assert (s.wait_capacity, s.wait_count, s.wait_available) == (10, 6, 4)
    assert s.has_active_waitlist
    assert not parse_section(raw("1", "001")).has_active_waitlist
    row = db.get_section(conn, "202710", "5")
    assert (row["wait_capacity"], row["wait_count"]) == (10, 6)


def test_old_database_gets_wait_columns(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE sections (term TEXT, crn TEXT, subject TEXT, course_number TEXT,"
                " section TEXT, title TEXT, seats_available INTEGER, max_enrollment INTEGER,"
                " wait_available INTEGER, updated_at TEXT, PRIMARY KEY (term, crn))")
    old.commit()
    old.close()
    cols = {r[1] for r in db.connect(path).execute("PRAGMA table_info(sections)")}
    assert {"wait_capacity", "wait_count", "instructor", "meetings"} <= cols


def test_no_alerts_when_official_waitlist_has_people(conn):
    db.add_subscription(conn, "a@go.olemiss.edu", "202710", "5")
    [opening] = db.save_snapshot(conn, [parse_section(raw("5", "005", seats=1, wait_cap=10, wait_count=6))])
    assert notify_opening(conn, FakeMailer(), opening) == []


# ---------- trade matching (db level) ----------

def test_mutual_match(conn):
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "1")
    db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "2", True)
    db.set_enrollment(conn, "b@go.olemiss.edu", "202710", "2")
    assert db.find_trade_matches(conn, "a@go.olemiss.edu") == []      # b doesn't want 001 yet
    db.set_trade_want(conn, "b@go.olemiss.edu", "202710", "1", True)

    [m] = db.find_trade_matches(conn, "a@go.olemiss.edu")
    assert (m["my_section"], m["their_section"], m["partner"]) == ("001", "002", "b@go.olemiss.edu")
    [m2] = db.find_trade_matches(conn, "b@go.olemiss.edu")
    assert (m2["my_section"], m2["their_section"], m2["partner"]) == ("002", "001", "a@go.olemiss.edu")


def test_no_match_without_opt_in(conn):
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "1")
    db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "2", True)
    db.set_enrollment(conn, "b@go.olemiss.edu", "202710", "2", open_to_trade=False)
    db.set_trade_want(conn, "b@go.olemiss.edu", "202710", "1", True)
    assert db.find_trade_matches(conn, "a@go.olemiss.edu") == []
    assert db.find_trade_matches(conn, "b@go.olemiss.edu") == []


def test_one_sided_or_third_section_is_not_a_match(conn):
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "1")
    db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "2", True)
    db.set_enrollment(conn, "b@go.olemiss.edu", "202710", "2")
    db.set_trade_want(conn, "b@go.olemiss.edu", "202710", "3", True)   # b wants 003, not 001
    assert db.find_trade_matches(conn, "a@go.olemiss.edu") == []


def test_want_validation(conn):
    with pytest.raises(ValueError, match="pick the section"):
        db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "2", True)   # not enrolled yet
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "1")
    with pytest.raises(ValueError, match="already in"):
        db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "1", True)
    with pytest.raises(ValueError, match="pick the section"):
        db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "9", True)   # different course
    with pytest.raises(ValueError, match="not found"):
        db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "404", True)


def test_switching_into_wanted_section_drops_that_want(conn):
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "1")
    db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "2", True)
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "2")   # the swap happened
    assert db.trade_wants_for(conn, "a@go.olemiss.edu", "202710") == set()


def test_leaving_trades_clears_wants(conn):
    db.set_enrollment(conn, "a@go.olemiss.edu", "202710", "1")
    db.set_trade_want(conn, "a@go.olemiss.edu", "202710", "2", True)
    db.clear_enrollment(conn, "a@go.olemiss.edu", "202710", "MATH", "1150")
    assert db.get_enrollment(conn, "a@go.olemiss.edu", "202710", "MATH", "1150") is None
    assert db.trade_wants_for(conn, "a@go.olemiss.edu", "202710") == set()


# ---------- web ----------

@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "w.db")
    c = db.connect(path)
    db.save_snapshot(c, [parse_section(r) for r in SECTIONS])
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


def test_course_page_shows_waitlist_link_instead_of_switch(app):
    a = login(app, "a@go.olemiss.edu")
    html = a.get("/course/202710/MATH/1150").data.decode()
    assert "Waitlist 6/10" in html
    switches = re.findall(r'snatch-switch"[^>]*?data-crn="(\d+)"', html, re.S)
    assert "5" not in switches        # active waitlist -> link, not our alert switch
    assert "1" in switches            # full, no waitlist -> alert switch still offered
    r = a.post("/api/subscribe", json={"term": "202710", "crn": "5", "subscribe": True}, headers=API)
    assert r.status_code == 400 and "official waitlist" in r.get_json()["error"]


def test_trade_flow_end_to_end(app):
    mailer = app.extensions["mailer"]
    a = login(app, "a@go.olemiss.edu")
    b = login(app, "b@go.olemiss.edu")

    assert a.post("/api/enrollment", json={"term": "202710", "crn": "1"}, headers=API).status_code == 200
    r = a.post("/api/trade-want", json={"term": "202710", "crn": "2", "want": True}, headers=API)
    assert r.get_json()["matches"] == []
    b.post("/api/enrollment", json={"term": "202710", "crn": "2"}, headers=API)

    before = len(mailer.sent)
    r = b.post("/api/trade-want", json={"term": "202710", "crn": "1", "want": True}, headers=API)
    assert r.get_json()["matches"] == [{"give": "MATH 1150-002", "get": "MATH 1150-001",
                                        "partner": "a@go.olemiss.edu"}]
    emails = mailer.sent[before:]
    assert sorted(to for to, _, _ in emails) == ["a@go.olemiss.edu", "b@go.olemiss.edu"]
    to_a = next(body for to, _, body in emails if to == "a@go.olemiss.edu")
    assert "b@go.olemiss.edu" in to_a and "You give: MATH 1150-001" in to_a

    # Re-saving doesn't email again.
    b.post("/api/trade-want", json={"term": "202710", "crn": "1", "want": True}, headers=API)
    assert len(mailer.sent) == before + 2

    # Both dashboards show the partner; a stranger sees nothing.
    assert "b@go.olemiss.edu" in a.get("/dashboard").data.decode()
    assert "a@go.olemiss.edu" in b.get("/dashboard").data.decode()
    c = login(app, "c@go.olemiss.edu")
    page = c.get("/dashboard").data.decode()
    assert "a@go.olemiss.edu" not in page and "b@go.olemiss.edu" not in page

    # Pausing trades hides the match.
    b.post("/api/enrollment", json={"term": "202710", "crn": "2", "open_to_trade": False}, headers=API)
    assert "b@go.olemiss.edu" not in a.get("/dashboard").data.decode()


def test_trade_api_validation(app):
    a = login(app, "a@go.olemiss.edu")
    r = a.post("/api/trade-want", json={"term": "202710", "crn": "2", "want": True}, headers=API)
    assert r.status_code == 400
    r = a.post("/api/enrollment", json={"term": "202710", "crn": "404"}, headers=API)
    assert r.status_code == 400
    assert a.post("/api/enrollment", json={"term": "202710", "crn": "1"}).status_code == 400   # no fetch header


def test_course_page_trades_card(app):
    a = login(app, "a@go.olemiss.edu")
    assert "Join Trades" in a.get("/course/202710/MATH/1150").data.decode()
    a.post("/api/enrollment", json={"term": "202710", "crn": "1"}, headers=API)
    html = a.get("/course/202710/MATH/1150").data.decode()
    assert "You&#39;re here" in html or "You're here" in html
    assert 'class="form-check-input trade-want"' in html
    assert "Leave Trades" in html
