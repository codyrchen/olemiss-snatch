import pytest

from olemiss_snatch import db, notify, poll
from olemiss_snatch.banner import parse_section
from olemiss_snatch.web import create_app

EMAIL_VARS = ["RESEND_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM"]


@pytest.fixture
def clean_env(monkeypatch):
    for k in EMAIL_VARS + ["SNATCH_TERMS", "SNATCH_DB", "POLL_EVERY", "BASE_URL", "SECRET_KEY"]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(notify, "load_env", lambda path=".env": None)
    monkeypatch.setattr(poll, "load_env", lambda path=".env": None)
    return monkeypatch


class FakeResponse:
    def __init__(self, status=200, text="{}"):
        self.status_code, self.text = status, text


def test_resend_used_when_key_set(clean_env):
    clean_env.setenv("RESEND_API_KEY", "re_test")
    clean_env.setenv("MAIL_FROM", "RebelSnatch <alerts@example.com>")
    calls = []
    clean_env.setattr(notify.requests, "post",
                      lambda url, **kw: calls.append((url, kw)) or FakeResponse())

    m = notify.Mailer()
    assert m.configured
    m.send("a@go.olemiss.edu", "Seat open", "body")

    [(url, kw)] = calls
    assert url == "https://api.resend.com/emails"
    assert kw["headers"]["Authorization"] == "Bearer re_test"
    assert kw["json"] == {"from": "RebelSnatch <alerts@example.com>", "to": ["a@go.olemiss.edu"],
                          "subject": "Seat open", "text": "body"}


def test_resend_error_raises(clean_env):
    clean_env.setenv("RESEND_API_KEY", "re_test")
    clean_env.setattr(notify.requests, "post", lambda url, **kw: FakeResponse(422, "invalid from"))
    with pytest.raises(RuntimeError, match="422"):
        notify.Mailer().send("a@go.olemiss.edu", "s", "b")


def test_not_configured_is_dry_run(clean_env, capsys):
    m = notify.Mailer()
    assert not m.configured
    m.send("a@go.olemiss.edu", "Hello", "body")
    assert "dry run" in capsys.readouterr().out


class FakeClient:
    def __init__(self, *a, **kw):
        self.searched = []

    def get_subjects(self, term):
        return [{"code": "MATH"}]

    def search_subject(self, term, subject):
        self.searched.append((term, subject))
        return [parse_section({
            "term": term, "courseReferenceNumber": f"{term[-2:]}001", "subject": subject,
            "courseNumber": "1150", "sequenceNumber": "001", "courseTitle": "Stats",
            "seatsAvailable": 0, "maximumEnrollment": 40, "waitAvailable": 0})]


def test_poll_multiple_terms_from_env(clean_env, tmp_path):
    clean_env.setenv("SNATCH_TERMS", "202730 202710")
    clean_env.setenv("SNATCH_DB", str(tmp_path / "s.db"))
    client = FakeClient()
    clean_env.setattr(poll, "BannerClient", lambda **kw: client)

    poll.main([])

    assert client.searched == [("202730", "MATH"), ("202710", "MATH")]
    assert set(db.terms(db.connect(str(tmp_path / "s.db")))) == {"202730", "202710"}


def test_poll_survives_a_failing_term(clean_env, tmp_path, capsys):
    client = FakeClient()

    def boom(term, subject):
        raise ConnectionError("banner down")
    client.search_subject = boom
    clean_env.setattr(poll, "BannerClient", lambda **kw: client)

    poll.main(["--term", "202710", "--db", str(tmp_path / "s.db")])  # must not raise
    assert "banner down" in capsys.readouterr().err


def test_wal_enabled_for_file_db(tmp_path):
    conn = db.connect(str(tmp_path / "s.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_healthz(clean_env, tmp_path):
    app = create_app(db_path=str(tmp_path / "s.db"))
    r = app.test_client().get("/healthz")
    assert r.status_code == 200 and r.data == b"ok"


def test_https_requires_real_secret_key(clean_env, tmp_path):
    clean_env.setenv("BASE_URL", "https://rebelsnatch.example")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(db_path=str(tmp_path / "s.db"))
    clean_env.setenv("SECRET_KEY", "x" * 64)
    assert create_app(db_path=str(tmp_path / "s.db"))


def test_subject_list_retried_after_failure(clean_env, tmp_path):
    client = FakeClient()
    attempts = []
    real = client.get_subjects

    def flaky(term):
        attempts.append(term)
        if len(attempts) == 1:
            raise ConnectionError("banner down")
        return real(term)
    client.get_subjects = flaky
    clean_env.setattr(poll, "BannerClient", lambda **kw: client)
    sleeps = []

    def fake_sleep(n):
        sleeps.append(n)
        if len(sleeps) == 2:
            raise KeyboardInterrupt
    clean_env.setattr(poll.time, "sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        poll.main(["--term", "202710", "--db", str(tmp_path / "s.db"), "--every", "5"])
    assert attempts == ["202710", "202710"]          # retried on the next pass, not 6h later
    assert client.searched == [("202710", "MATH")]   # and polled once it worked


def test_concurrent_connections_while_writing(tmp_path):
    """The website opens a connection per request while the poller writes."""
    import threading

    path = str(tmp_path / "s.db")
    db.connect(path)
    errors, stop = [], threading.Event()

    def writer():
        conn = db.connect(path)
        while not stop.is_set():
            db.save_snapshot(conn, [parse_section({
                "term": "202710", "courseReferenceNumber": str(i), "subject": "MATH",
                "courseNumber": "1150", "sequenceNumber": "001", "courseTitle": "Stats",
                "seatsAvailable": i % 2, "maximumEnrollment": 40, "waitAvailable": 0}) for i in range(400)])

    def request(n):
        try:
            for i in range(30):
                conn = db.connect(path)
                db.add_subscription(conn, f"u{n}@go.olemiss.edu", "202710", str(i))
                db.list_subscriptions(conn, f"u{n}@go.olemiss.edu")
                conn.close()
        except Exception as e:
            errors.append(e)

    w = threading.Thread(target=writer)
    w.start()
    readers = [threading.Thread(target=request, args=(n,)) for n in range(6)]
    [t.start() for t in readers]
    [t.join() for t in readers]
    stop.set()
    w.join()
    assert errors == []
