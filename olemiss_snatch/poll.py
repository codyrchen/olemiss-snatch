"""Poll Banner for seat counts and report sections that just opened up.

Examples:
    python -m olemiss_snatch.poll --list-terms
    python -m olemiss_snatch.poll --term 202710 --subjects MATH CSCI
    python -m olemiss_snatch.poll --term 202710 --every 300      # all subjects, every 5 min
    python -m olemiss_snatch.poll --term 202730 202710 --every 300   # Spring and Fall
    python -m olemiss_snatch.poll --test-email you@go.olemiss.edu

On a server, settings come from SNATCH_TERMS, SNATCH_DB and POLL_EVERY instead of flags.
"""

import argparse
import os
import sys
import time

from . import db
from .banner import BannerClient
from . import schedule
from .notify import Mailer, Texter, load_env, notify_opening


def _progress(msg: str):
    """Overwrite one status line in a terminal; stay quiet in server logs."""
    if sys.stdout.isatty():
        print(f"\r{msg:<60}", end="", flush=True)


def _clear_progress():
    if sys.stdout.isatty():
        print(f"\r{'':<60}\r", end="", flush=True)


def poll_once(client: BannerClient, conn, term: str, subjects: list[str], mailer: Mailer,
              texter: Texter | None = None) -> int:
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
            emailed = notify_opening(conn, mailer, o, texter)
            if emailed:
                print(f"          emailed {len(emailed)}: {', '.join(emailed)}")
    _clear_progress()
    print(f"checked {total_sections} sections in {len(subjects)} subjects, "
          f"{total_openings} opening(s)")
    return total_openings


SUBJECT_REFRESH_SECONDS = 6 * 3600


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--term", nargs="+", default=os.environ.get("SNATCH_TERMS", "").split() or None,
                   help="term code(s), e.g. 202730 202710 (default: $SNATCH_TERMS; see --list-terms)")
    p.add_argument("--subjects", nargs="+", help="subject codes; default is every subject in the term")
    p.add_argument("--db", default=os.environ.get("SNATCH_DB", "snatch.db"),
                   help="SQLite file (default: $SNATCH_DB or snatch.db)")
    p.add_argument("--every", type=int, default=int(os.environ.get("POLL_EVERY", 0)) or None,
                   help="repeat every N seconds instead of running once (default: $POLL_EVERY)")
    p.add_argument("--delay", type=float, default=1.0, help="seconds between Banner requests")
    p.add_argument("--list-terms", action="store_true", help="print available terms and exit")
    p.add_argument("--test-email", metavar="ADDRESS", help="send a test email and exit")
    args = p.parse_args(argv)

    mailer = Mailer()
    texter = Texter()

    if args.test_email:
        mailer.send(args.test_email, "olemiss-snatch test email",
                    "If you're reading this, seat alerts can reach you.\n")
        if mailer.configured:
            print(f"sent test email to {args.test_email}")
        return

    if not mailer.configured:
        print("note: email not configured (.env missing?); alerts will be printed, not emailed")

    client = BannerClient(delay=args.delay)

    if args.list_terms:
        for t in client.get_terms():
            print(t["code"], t["description"])
        return
    if not args.term:
        p.error("--term is required (or set SNATCH_TERMS; use --list-terms to see codes)")

    conn = db.connect(args.db)
    subjects: dict[str, list[str]] = {}
    subjects_fetched = 0.0

    paused_note = None
    while True:
        if args.every:
            st = schedule.status()
            if not st.active:
                note = schedule.describe(st)
                if note != paused_note:  # say it once, not every minute
                    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), note, flush=True)
                    paused_note = note
                time.sleep(min(args.every, 300))
                continue
            paused_note = None
        stale = time.time() - subjects_fetched > SUBJECT_REFRESH_SECONDS
        if not args.subjects and (stale or any(t not in subjects for t in args.term)):
            for term in args.term:
                try:
                    subjects[term] = [s["code"] for s in client.get_subjects(term)]
                except Exception as e:  # retried next pass
                    print(f"  ! could not load subjects for {term}: {e}", file=sys.stderr)
            if all(t in subjects for t in args.term):
                subjects_fetched = time.time()
        for term in args.term:
            term_subjects = [s.upper() for s in args.subjects] if args.subjects else subjects.get(term)
            if not term_subjects:
                continue
            print(time.strftime("[%Y-%m-%d %H:%M:%S]"), f"polling term {term}", flush=True)
            try:
                poll_once(client, conn, term, term_subjects, mailer, texter)
            except Exception as e:  # keep the loop alive if Banner or the network hiccups
                print(f"  ! poll of {term} failed: {e}", file=sys.stderr)
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
