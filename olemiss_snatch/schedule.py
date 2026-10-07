"""Alert windows: only check seats during registration and add/drop periods.

Set POLL_WINDOWS to one or more ranges in Ole Miss (Central) time, separated by ';':

    POLL_WINDOWS=2026-11-02 07:00 to 2026-11-20 23:59; 2027-01-12 06:00 to 2027-01-26 23:59

If POLL_WINDOWS is empty, checking runs all the time.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
FORMAT = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class Status:
    active: bool
    current_end: datetime | None = None   # set when inside a window
    next_start: datetime | None = None    # set when outside, if a window is coming up
    configured: bool = True


def parse_windows(raw: str) -> list[tuple[datetime, datetime]]:
    windows = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            start_s, end_s = (x.strip() for x in part.split(" to "))
            start = datetime.strptime(start_s, FORMAT).replace(tzinfo=TZ)
            end = datetime.strptime(end_s, FORMAT).replace(tzinfo=TZ)
        except ValueError:
            raise ValueError(f"Bad POLL_WINDOWS entry {part!r}; use 'YYYY-MM-DD HH:MM to YYYY-MM-DD HH:MM'")
        if end <= start:
            raise ValueError(f"POLL_WINDOWS entry {part!r} ends before it starts")
        windows.append((start, end))
    return sorted(windows)


def status(now: datetime | None = None, raw: str | None = None) -> Status:
    raw = os.environ.get("POLL_WINDOWS", "") if raw is None else raw
    windows = parse_windows(raw)
    if not windows:
        return Status(active=True, configured=False)
    now = now or datetime.now(TZ)
    for start, end in windows:
        if start <= now < end:
            return Status(active=True, current_end=end)
    upcoming = [start for start, _ in windows if start > now]
    return Status(active=False, next_start=min(upcoming) if upcoming else None)


def describe(st: Status) -> str:
    fmt = lambda d: d.strftime("%b %-d, %-I:%M %p")
    if not st.configured:
        return "Seat checks run around the clock."
    if st.active:
        return f"Seat checks are on until {fmt(st.current_end)} (Central)."
    if st.next_start:
        return f"Seat checks are paused until {fmt(st.next_start)} (Central)."
    return "Seat checks are paused. No upcoming registration window is scheduled."
