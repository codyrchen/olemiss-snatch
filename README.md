# olemiss-snatch

[RebelSnatch](https://rebelsnatch.com) helps Ole Miss students get notified when a seat opens in a full class. 

It reads public seat counts from Ole Miss's Banner class search (the same data as "Browse Classes"). It never logs in as a student and never registers anyone. It only watches and alerts.

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

## Deploy (Railway + Resend + your domain)

Costs: Railway Hobby about $5/month (it starts with a free trial), a domain about $10/year, and Resend free (3,000 emails/month).

Railway's Hobby plan blocks SMTP, so on the server the app sends email through Resend's API instead of Gmail. Locally, Gmail SMTP keeps working.

### 1. Buy a domain

Buy one at [Cloudflare Registrar](https://dash.cloudflare.com) (sold at cost, and its DNS works well with Railway). Example: `rebelsnatch.com`.

### 2. Set up Resend

1. Sign up at https://resend.com, go to **Domains → Add Domain** and enter your domain.
2. Resend shows a few DNS records (TXT and MX). In Cloudflare, go to **your domain → DNS → Records** and add each one exactly as shown.
3. Back in Resend, click **Verify**. It can take a few minutes.
4. Go to **API Keys → Create API Key** (sending access) and copy the key, which starts with `re_`.

### 3. Create the Railway service

1. Sign up at https://railway.com with GitHub.
2. Click **New Project → Deploy from GitHub repo** and pick `olemiss-snatch`. Give Railway access to the repo if it asks.
3. Add a **volume** so the database survives redeploys: on the project canvas, right-click (or press `Cmd + K`), choose **Volume**, attach it to the service, and set the mount path to `/data`.
4. Open the service's **Variables** tab, then **Raw Editor**, and paste the following, filling in your values:
   ```
   SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
   BASE_URL=https://rebelsnatch.com
   RESEND_API_KEY=re_...
   MAIL_FROM=RebelSnatch <alerts@rebelsnatch.com>
   SNATCH_DB=/data/snatch.db
   SNATCH_TERMS=202730 202710
   POLL_EVERY=300
   ```
5. Railway redeploys. `railway.json` tells it to run `start.sh`, which starts the poller and the website. It also tells Railway to health-check `/healthz`.

### 4. Point your domain at Railway

1. In the service, go to **Settings → Networking → Custom Domain** and enter `rebelsnatch.com`.
2. Railway shows a **CNAME** record (and sometimes a TXT record). Add it in Cloudflare DNS with the proxy set to **DNS only** (grey cloud).
3. Wait for Railway to show the domain as active. It issues the HTTPS certificate automatically.

### 5. Check it

- **Deploy logs:** after a few minutes you should see `checked 8xxx sections in 1xx subjects` for each term.
- **Sign in:** open https://rebelsnatch.com and sign in with your Ole Miss email. That also tests that Resend is sending.
- **Changing terms:** when registration moves to a new term, edit `SNATCH_TERMS`. Railway restarts the service with the new value.

## Waitlists and Trades

**Official waitlists come first.** Ole Miss's Experience/Banner waitlist holds a seat for the next student.
- Sections with an active waitlist (`waitCapacity > 0` and open spots or people on it) show **"Waitlist N/M · join in Experience"** instead of an alert switch.
- Alerts are never sent for sections that have people on the official waitlist.
- How Banner reports these fields after waitlists close (the week before classes) is unconfirmed. The rule lives in one function, `banner.has_active_waitlist`.

**Trades.** On a course page, a student picks the section they're in ("Join Trades") and checks **Trade** on full sections they'd switch to.
- When two students each want the other's section, both get an email with the partner's address and swap steps.
- The match also appears on both dashboards.
- Emails are shown only to matched students, and only while both are "Open to trades".

## Sign in with Google

Ole Miss mail can quarantine emails from new domains, so students can sign in with their **@go.olemiss.edu Google account** instead of an emailed link.
- The app only accepts accounts whose Google Workspace domain (`hd`) and email are in `ALLOWED_EMAIL_DOMAINS`, so personal Gmail accounts are rejected.
- Students can also send alerts to a **personal email**. They confirm it from that inbox first, on the dashboard under "Where alerts go".

Setup (about 10 minutes):
1. Go to https://console.cloud.google.com, create a project called `RebelSnatch`, and open **APIs & Services**.
2. Open **OAuth consent screen**:
   - choose **External**
   - app name `RebelSnatch`, with your email as the support and developer contact
   - scopes: just `openid` and `email`
   - **Publish app** (set to "In production"). Basic sign-in needs no Google review.
3. Go to **Credentials → Create credentials → OAuth client ID → Web application**.
   - Authorized redirect URI: `https://rebelsnatch.com/auth/google/callback`
4. Copy the client ID and secret into Railway as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

If Ole Miss blocks third-party apps for student accounts, Google shows an "access blocked" page. Then ask IT to allow the app.

## Limits, schedule, stats, admin and texts

All of these are set as Railway **Variables**:

| Variable | What it does | Example |
|---|---|---|
| `MAX_SUBSCRIPTIONS` | Max sections one student can watch per term (default 15) | `15` |
| `POLL_WINDOWS` | Only check seats in these windows, Central time, `;`-separated. Unset = always | `2026-11-02 07:00 to 2026-11-20 23:59; 2027-01-12 06:00 to 2027-01-26 23:59` |
| `ADMIN_EMAILS` | Who can open `/admin` (stats, block students, clear a section) | `you@go.olemiss.edu` |
| `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM` | Turn on text alerts. Students add a phone number on the dashboard | from twilio.com |

- `/stats` is public and shows only totals and counts, never anyone's email.
- `/admin` shows everything and lets you block a student. Blocking signs them out, removes their subscriptions and trades, and stops future sign-ins.
- **Twilio note:** texting US numbers needs a verified toll-free number or A2P 10DLC registration in Twilio. Approval can take a few days, so start early.

## Notes on the data

- Term codes look like `YYYYTT`: `10` is Fall, `20` is Winter, `30` is Spring and `50` is Summer. For example, `202730` is Spring 2027.
- `seatsAvailable` can be negative when a section is over-enrolled through overrides. Any value at or below 0 counts as full.
- `maximumEnrollment` of 0 usually means the section is cancelled or closed to normal registration. Those sections never trigger alerts.
- Requests are spaced `--delay` seconds apart (default 1s). Keep it polite.

## Tests

```bash
python -m pytest -q
```
