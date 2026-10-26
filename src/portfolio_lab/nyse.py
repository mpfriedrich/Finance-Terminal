"""NYSE sessions with their hours: the exchange's holiday rules (filing_butterfly's
calendar) plus the special closures and the 13:00 early closes it publishes.

Audit 24.10.2026, low item 10: the calendar knew neither early closes nor special
closures, so the forward books took 16:00 as the close of 27.11.2026 and 24.12.2026 and
would have waited three hours longer than needed (harmless), and a special closure was
a "session" without bars. Only the dates below are known; a closure announced later must
be added here. Source: nyse.com/markets/hours-calendars (read 13.10.2026) for 2026 and
2027; earlier years from the exchange's notices.
"""

from __future__ import annotations

import datetime as dt

from .filing_butterfly import ET, nyse_holidays

__all__ = [
    "EARLY_CLOSES",
    "ET",
    "SPECIAL_CLOSURES",
    "close_hour",
    "is_session",
    "next_session",
    "prev_session",
    "sessions_from",
]

# Closed for the whole day outside the regular holiday rules.
SPECIAL_CLOSURES = {
    dt.date(2012, 10, 29), dt.date(2012, 10, 30),     # Hurricane Sandy
    dt.date(2018, 12, 5),                             # national day of mourning, G. H. W. Bush
    dt.date(2025, 1, 9),                              # national day of mourning, J. Carter
}
# The regular session ends at 13:00 New York time.
EARLY_CLOSES = {
    dt.date(2024, 7, 3), dt.date(2024, 11, 29), dt.date(2024, 12, 24),
    dt.date(2025, 7, 3), dt.date(2025, 11, 28), dt.date(2025, 12, 24),
    dt.date(2026, 11, 27), dt.date(2026, 12, 24),
    dt.date(2027, 11, 26),
}


def is_session(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year) and d not in SPECIAL_CLOSURES


def close_hour(d: dt.date) -> int:
    """The hour (New York) the regular session of `d` ends."""
    return 13 if d in EARLY_CLOSES else 16


def next_session(d: dt.date) -> dt.date:
    """The first session strictly after `d`."""
    d += dt.timedelta(days=1)
    while not is_session(d):
        d += dt.timedelta(days=1)
    return d


def prev_session(d: dt.date) -> dt.date:
    """The last session strictly before `d`."""
    d -= dt.timedelta(days=1)
    while not is_session(d):
        d -= dt.timedelta(days=1)
    return d


def sessions_from(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start if is_session(start) else next_session(start)
    while len(out) < n:
        out.append(d)
        d = next_session(d)
    return out
