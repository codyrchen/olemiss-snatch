from olemiss_snatch import db, subscribe
from olemiss_snatch.banner import parse_section
from olemiss_snatch.notify import BATCH_MULTIPLIER, notify_opening


class FakeMailer:
    configured = True

    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def send(self, to, subject, body):
        if to in self.fail_for:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, body))


def raw(crn="10343", seats=0, cap=49):
    return {
        "term": "202710", "courseReferenceNumber": crn, "subject": "MATH",
        "courseNumber": "1150", "sequenceNumber": "007", "courseTitle": "Elementary Statistics",
        "seatsAvailable": seats, "maximumEnrollment": cap, "waitAvailable": 0,
    }


def snap(conn, *raws):
    return db.save_snapshot(conn, [parse_section(r) for r in raws])


def open_section(conn, seats=1):
    """Snapshot full, then open; returns the single Opening."""
    snap(conn, raw(seats=0))
    [opening] = snap(conn, raw(seats=seats))
    return opening


def test_subscribers_emailed_in_order():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    for email in ["a@x.edu", "b@x.edu", "c@x.edu"]:
        db.add_subscription(conn, email, "202710", "10343")
    [opening] = snap(conn, raw(seats=1))

    mailer = FakeMailer()
    sent = notify_opening(conn, mailer, opening)

    assert sent == ["a@x.edu", "b@x.edu", "c@x.edu"]
    assert "CRN 10343" in mailer.sent[0][1]
    assert "MATH 1150-007" in mailer.sent[0][2]


def test_batch_size_scales_with_seats():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    emails = [f"s{i}@x.edu" for i in range(10)]
    for e in emails:
        db.add_subscription(conn, e, "202710", "10343")
    [opening] = snap(conn, raw(seats=2))

    sent = notify_opening(conn, FakeMailer(), opening)
    assert sent == emails[: 2 * BATCH_MULTIPLIER]


def test_same_opening_not_notified_twice():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    db.add_subscription(conn, "a@x.edu", "202710", "10343")
    [opening] = snap(conn, raw(seats=1))

    assert notify_opening(conn, FakeMailer(), opening) == ["a@x.edu"]
    assert notify_opening(conn, FakeMailer(), opening) == []


def test_new_opening_notifies_again():
    conn = db.connect(":memory:")
    first = open_section(conn)
    db.add_subscription(conn, "a@x.edu", "202710", "10343")
    notify_opening(conn, FakeMailer(), first)

    snap(conn, raw(seats=0))
    [second] = snap(conn, raw(seats=1))
    assert notify_opening(conn, FakeMailer(), second) == ["a@x.edu"]


def test_failed_send_is_retried_next_time():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    db.add_subscription(conn, "a@x.edu", "202710", "10343")
    db.add_subscription(conn, "b@x.edu", "202710", "10343")
    [opening] = snap(conn, raw(seats=1))

    assert notify_opening(conn, FakeMailer(fail_for={"a@x.edu"}), opening) == ["b@x.edu"]
    assert notify_opening(conn, FakeMailer(), opening) == ["a@x.edu"]


def test_other_sections_subscribers_not_emailed():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0), raw(crn="99999", seats=0))
    db.add_subscription(conn, "other@x.edu", "202710", "99999")
    [opening] = snap(conn, raw(seats=1))
    assert notify_opening(conn, FakeMailer(), opening) == []


def test_duplicate_subscription_ignored_and_email_case_insensitive():
    conn = db.connect(":memory:")
    assert db.add_subscription(conn, "A@X.edu", "202710", "10343")
    assert not db.add_subscription(conn, "a@x.edu", "202710", "10343")
    assert db.remove_subscription(conn, "A@x.EDU", "202710", "10343")


def test_subscribe_add_rejects_unknown_crn(capsys):
    conn = db.connect(":memory:")
    assert subscribe.add(conn, "a@x.edu", "202710", "10343") == 1
    assert "not found" in capsys.readouterr().err


def test_subscribe_add_rejects_closed_section(capsys):
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0, cap=0))
    assert subscribe.add(conn, "a@x.edu", "202710", "10343") == 1
    assert db.subscribers_for(conn, "202710", "10343") == []


def test_subscribe_add_reports_position(capsys):
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    subscribe.add(conn, "a@x.edu", "202710", "10343")
    subscribe.add(conn, "b@x.edu", "202710", "10343")
    assert "Position in line: #2" in capsys.readouterr().out
