# olemiss-snatch

Get notified when a seat opens in a full Ole Miss class. Inspired by Princeton's [TigerSnatch](https://github.com/TigerSnatch/TigerSnatch).

It reads public seat counts from Ole Miss's Banner class search (the same data as "Browse Classes"). It never logs in as a student and never registers anyone. It only watches and alerts.

## Status

- [x] Banner client: terms, subjects, sections with seat counts
- [x] SQLite snapshots + detection of full → open seats
- [ ] Subscriptions + email alerts
- [ ] Website (search, subscribe, waitlist position)
- [ ] Hosting / scheduled polling

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

## Usage

```bash
# see term codes
python -m olemiss_snatch.poll --list-terms

# one pass over a few subjects
python -m olemiss_snatch.poll --term 202710 --subjects MATH CSCI

# every subject in the term, repeating every 5 minutes
python -m olemiss_snatch.poll --term 202710 --every 300
```

The first run only records the current counts. Later runs print a line like the one below for each section that went from full to open:

```
  OPENED  10343  MATH 1150-007  Elementary Statistics  (0 -> 2 of 49)
```

Data goes into `snatch.db` (SQLite). There are two tables: `sections` holds the latest counts and `openings` holds the history of seat openings.

## Notes on the data

- Term codes look like `YYYYTT`: `10` is Fall, `20` is Winter, `30` is Spring and `50` is Summer. For example, `202730` is Spring 2027.
- `seatsAvailable` can be negative when a section is over-enrolled through overrides. Any value at or below 0 counts as full.
- `maximumEnrollment` of 0 usually means the section is cancelled or closed to normal registration. Those sections never trigger alerts.
- Requests are spaced `--delay` seconds apart (default 1s). Keep it polite.

## Tests

```bash
python -m pytest -q
```
