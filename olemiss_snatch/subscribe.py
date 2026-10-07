"""Manage seat-opening subscriptions.

Examples:
    python -m olemiss_snatch.subscribe add you@go.olemiss.edu 10343 --term 202710
    python -m olemiss_snatch.subscribe remove you@go.olemiss.edu 10343 --term 202710
    python -m olemiss_snatch.subscribe list
    python -m olemiss_snatch.subscribe list --email you@go.olemiss.edu
"""

import argparse
import sys

from . import db


def add(conn, email: str, term: str, crn: str) -> int:
    sec = db.get_section(conn, term, crn)
    if sec is None:
        print(f"CRN {crn} not found for term {term}. Run the poller for that term first:\n"
              f"  python -m olemiss_snatch.poll --term {term}", file=sys.stderr)
        return 1
    if sec["max_enrollment"] <= 0:
        print(f"CRN {crn} has capacity 0 (cancelled or closed); not subscribing.", file=sys.stderr)
        return 1
    label = f'{sec["subject"]} {sec["course_number"]}-{sec["section"]} {sec["title"]}'
    if not db.add_subscription(conn, email, term, crn):
        print(f"{email} is already watching {label}.")
        return 0
    position = len(db.subscribers_for(conn, term, crn))
    print(f"{email} is now watching {label} (CRN {crn}). Position in line: #{position}.")
    if sec["seats_available"] > 0:
        print(f"Note: it has {sec['seats_available']} open seat(s) right now; you'll be "
              f"emailed the next time it goes from full to open.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="snatch.db", help="SQLite file (default: snatch.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("add", "remove"):
        sp = sub.add_parser(name)
        sp.add_argument("email")
        sp.add_argument("crn")
        sp.add_argument("--term", required=True, help="term code, e.g. 202710")
    ls = sub.add_parser("list")
    ls.add_argument("--email")
    args = p.parse_args(argv)

    conn = db.connect(args.db)

    if args.cmd == "add":
        return add(conn, args.email, args.term, args.crn)
    if args.cmd == "remove":
        if db.remove_subscription(conn, args.email, args.term, args.crn):
            print(f"Removed {args.email} from CRN {args.crn}.")
            return 0
        print(f"{args.email} wasn't watching CRN {args.crn}.", file=sys.stderr)
        return 1
    rows = db.list_subscriptions(conn, args.email)
    if not rows:
        print("No subscriptions.")
    for r in rows:
        course = f'{r["subject"]} {r["course_number"]}-{r["section"]} {r["title"]}' if r["subject"] else "?"
        seats = f'{r["seats_available"]}/{r["max_enrollment"]}' if r["subject"] else "?"
        print(f'{r["term"]}  {r["crn"]}  {seats:>7}  {course[:45]:45}  {r["email"]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
