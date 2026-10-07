"""Poll Banner for seat counts and report sections that just opened up.

Examples:
    python -m olemiss_snatch.poll --list-terms
    python -m olemiss_snatch.poll --term 202710 --subjects MATH CSCI
    python -m olemiss_snatch.poll --term 202710 --every 300      # all subjects, every 5 min
"""

import argparse
import sys
import time

from . import db
from .banner import BannerClient


def poll_once(client: BannerClient, conn, term: str, subjects: list[str]) -> int:
    total_sections = total_openings = 0
    for subject in subjects:
        try:
            sections = client.search_subject(term, subject)
        except Exception as e:  # one bad subject shouldn't kill the whole poll
            print(f"  ! {subject}: {e}", file=sys.stderr)
            continue
        openings = db.save_snapshot(conn, sections)
        total_sections += len(sections)
        total_openings += len(openings)
        for o in openings:
            s = o.section
            print(f"  OPENED  {s.crn}  {s.label}  {s.title}  "
                  f"({o.seats_before} -> {s.seats_available} of {s.max_enrollment})")
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
    args = p.parse_args(argv)

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
        poll_once(client, conn, args.term, [s.upper() for s in subjects])
        if not args.every:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()
