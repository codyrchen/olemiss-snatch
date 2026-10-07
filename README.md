# olemiss-snatch

Get notified when a seat opens in a full Ole Miss class. 

It reads public seat counts from Ole Miss's Banner class search (the same data as "Browse Classes"). It never logs in as a student and never registers anyone. It only watches and alerts.

## Status

- [x] Banner client: terms, subjects, sections with seat counts
- [x] SQLite snapshots + detection of full → open seats
- [x] Subscriptions + email alerts (waitlist order, CLI-managed)
- [x] Website: email sign-in, course search, subscribe switches, waitlist position
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

## Email alerts

### 1. Set up sending (Gmail)

1. Turn on 2-Step Verification for the Gmail account at https://myaccount.google.com/security.
2. Create an App Password at https://myaccount.google.com/apppasswords. Name it `olemiss-snatch` and copy the 16-character password.
3. Copy the example settings file and fill it in:
   ```bash
   cp .env.example .env
   open -e .env
   ```
4. Send yourself a test email:
   ```bash
   python -m olemiss_snatch.poll --test-email you@go.olemiss.edu
   ```

Without a `.env` file, alerts are printed in the terminal instead of being emailed.

### 2. Subscribe to sections

Run the poller once for the term first, so the CRNs are in the database. Then:

```bash
python -m olemiss_snatch.subscribe add you@go.olemiss.edu 10343 --term 202710
python -m olemiss_snatch.subscribe list
python -m olemiss_snatch.subscribe remove you@go.olemiss.edu 10343 --term 202710
```

When a watched section goes from full to open, the poller emails subscribers in the order they subscribed. It sends to 3 people per open seat, so 1 seat means the first 3 in line get the email. The same opening never emails anyone twice.

### 3. Test an alert without waiting for registration

Pretend a section you're subscribed to is full, then poll its subject again:

```bash
sqlite3 snatch.db "UPDATE sections SET seats_available = 0 WHERE crn = '10343'"
python -m olemiss_snatch.poll --term 202710 --subjects MATH
```

If the real section has open seats, this looks like an opening and you'll get the email.

## Website

A TigerSnatch-style site where students:
- sign in with an Ole Miss email using a one-time link (no passwords)
- search courses by code, title or instructor
- flip a switch on a full section to get in line
- see their place in line on the dashboard

Every alert email includes a one-click unsubscribe link.

### Run it locally

```bash
pip install -r requirements.txt
python -c "import secrets; print(secrets.token_hex(32))"   # paste into .env as SECRET_KEY
python -m flask --app olemiss_snatch.web run --debug
```

Open http://127.0.0.1:5000. (On a Mac, `localhost:5000` can hit AirPlay Receiver and show 403 Forbidden.) Keep the poller running in another Terminal tab so seat counts stay fresh. Both use the same `snatch.db`.

Without SMTP settings, the sign-in link is printed in the Terminal running Flask instead of being emailed.

## Notes on the data

- Term codes look like `YYYYTT`: `10` is Fall, `20` is Winter, `30` is Spring and `50` is Summer. For example, `202730` is Spring 2027.
- `seatsAvailable` can be negative when a section is over-enrolled through overrides. Any value at or below 0 counts as full.
- `maximumEnrollment` of 0 usually means the section is cancelled or closed to normal registration. Those sections never trigger alerts.
- Requests are spaced `--delay` seconds apart (default 1s). Keep it polite.

## Tests

```bash
python -m pytest -q
```
