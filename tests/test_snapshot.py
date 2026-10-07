from olemiss_snatch import db
from olemiss_snatch.banner import parse_section


def raw(crn="10343", seats=0, cap=49, title="Elementary Statistics"):
    return {
        "term": "202710", "courseReferenceNumber": crn, "subject": "MATH",
        "courseNumber": "1150", "sequenceNumber": "007", "courseTitle": title,
        "seatsAvailable": seats, "maximumEnrollment": cap, "waitAvailable": 0,
    }


def snap(conn, *raws):
    return db.save_snapshot(conn, [parse_section(r) for r in raws])


def test_parse_unescapes_title():
    s = parse_section(raw(title="Calculus for Business, Econ., &amp; Life Sci"))
    assert s.title == "Calculus for Business, Econ., & Life Sci"
    assert s.label == "MATH 1150-007"


def test_full_and_closed_flags():
    assert parse_section(raw(seats=0, cap=49)).is_full
    assert parse_section(raw(seats=-1, cap=34)).is_full  # over-enrolled
    closed = parse_section(raw(seats=0, cap=0))
    assert closed.is_closed and not closed.is_full


def test_first_snapshot_reports_nothing():
    conn = db.connect(":memory:")
    assert snap(conn, raw(seats=5)) == []


def test_full_to_open_is_an_opening():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    openings = snap(conn, raw(seats=2))
    assert [(o.section.crn, o.seats_before, o.section.seats_available) for o in openings] == [("10343", 0, 2)]
    assert conn.execute("SELECT COUNT(*) FROM openings").fetchone()[0] == 1


def test_overenrolled_to_open_is_an_opening():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=-1))
    assert len(snap(conn, raw(seats=1))) == 1


def test_open_to_more_open_is_not_an_opening():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=1))
    assert snap(conn, raw(seats=3)) == []


def test_still_full_is_not_an_opening():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    assert snap(conn, raw(seats=0)) == []


def test_closed_section_is_never_an_opening():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0, cap=0))
    assert snap(conn, raw(seats=0, cap=0)) == []


def test_latest_counts_are_stored():
    conn = db.connect(":memory:")
    snap(conn, raw(seats=0))
    snap(conn, raw(seats=4))
    row = conn.execute("SELECT seats_available FROM sections WHERE crn = '10343'").fetchone()
    assert row[0] == 4
