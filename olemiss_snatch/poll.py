"""Poll Banner for seat counts and report sections that just opened up.

Examples:
    python -m olemiss_snatch.poll --list-terms
    python -m olemiss_snatch.poll --term 202710 --subjects MATH CSCI
    python -m olemiss_snatch.poll --term 202710 --every 300      # all subjects, every 5 min
    python -m olemiss_snatch.poll --test-email you@go.olemiss.edu
"""

import argparse
import sys
import time

from . import db
from .banner import BannerClient
from .notify import Mailer, load_env, notify_opening


def _progress(msg: str):
    """Overwrite one status line in a terminal; plain lines when piped to a log."""
    if sys.stdout.isatty():
        print(f"\r{msg:<60}", end="", flush=True)
    else:
        print(msg)


def _clear_progress():
    if sys.stdout.isatty():
        print(f"\r{'':<60}\r", end="", flush=True)


def poll_once(client: BannerClient, conn, term: str, subjects: list[str], mailer: Mailer) -> int:
    total_sections = total_openings = 0
    for i, subject in enumerate(subjects, 1):
        try:
            sections = client.search_subject(term, subject)
        except Exception as e:  # one bad subject shouldn't kill the whole poll
            _clear_progress()
            print(f"  ! {subject}: {e}", file=sys.stderr)
            continue
        _progress(f"  [{i}/{len(subjects)}] {subject}: {len(sections)} sections")
        openings = db.save_snapshot(conn, sections)
        total_sections += len(sections)
        total_openings += len(openings)
        for o in openings:
            s = o.section
            _clear_progress()
            print(f"  OPENED  {s.crn}  {s.label}  {s.title}  "
                  f"({o.seats_before} -> {s.seats_available} of {s.max_enrollment})")
            emailed = notify_opening(conn, mailer, o)
            if emailed:
                print(f"          emailed {len(emailed)}: {', '.join(emailed)}")
    _clear_progress()
    print(f"checked {total_sections} sections in {len(subjects)} subjects, "
          f"{total_openings} opening(s)")
    return total_openings


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--term", help="term code, e.g. 202710 (see --list-terms)")
    p.add_argument("--subjects", nargs="+", help="subject codes; default is every subject in the term")
    p.add_argument("--db", default="snatch.db", help="SQLite file (default: snatch.db)")
    p.add_argument("--every", type=int, help="repeat every N seconds instead of running once")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between Banner requests")
    p.add_argument("--list-terms", action="store_true", help="print available terms and exit")
    p.add_argument("--test-email", metavar="ADDRESS", help="send a test email and exit")
    args = p.parse_args(argv)

    load_env()
    mailer = Mailer()

    if args.test_email:
        mailer.send(args.test_email, "olemiss-snatch test email",
                    "If you're reading this, seat alerts can reach you.\n")
        if mailer.configured:
            print(f"sent test email to {args.test_email}")
        return

    if not mailer.configured:
        print("note: SMTP not configured (.env missing?); alerts will be printed, not emailed")

    client = BannerClient(delay=args.delay)

    if args.list_terms:
        for t in client.get_terms():
            print(t["code"], t["description"])
        return
    if not args.term:
        p.error("--term is required (use --list-terms to see codes)")

    subjects = args.subjects or [s["code"] for s in client.get_subjects(args.term)]
    conn = db.connect(args.db)

    while True:
        print(time.strftime("[%Y-%m-%d %H:%M:%S]"), f"polling term {args.term}")
        poll_once(client, conn, args.term, [s.upper() for s in subjects], mailer)
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
