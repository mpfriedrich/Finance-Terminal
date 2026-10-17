"""Update the SEC Filing Butterfly paper books (filing_butterfly.py), then print them.

One pass: read EDGAR's daily index since the last scan, decide the candidates whose
entry window is open (only during the US session; outside it nothing is decided),
close or mark the open positions, store the day's book values and SPY. Repeating it
books nothing twice. It never places an order anywhere: paper only.

The bookings live in data/paper_options.duckdb, which the PORTFOLIOS tab reads. The app
can stay open while this runs.

Run: uv run python scripts/butterfly_tracker.py            (update + report)
     uv run python scripts/butterfly_tracker.py --report   (report only, no network)
Scheduled during US hours by scripts/install_butterfly_tracker.sh.
"""

import datetime as dt
import os
import sys

from portfolio_lab import filing_butterfly as fb
from portfolio_lab.config import PAPER_OPTIONS_DB, SEC_USER_AGENT, Settings
from portfolio_lab.data.sources import open_selected

settings = Settings()                       # loads .env.local
print(f"[{dt.datetime.now():%Y-%m-%d %H:%M}] SEC Filing Butterfly", flush=True)
if "--report" not in sys.argv:
    src, _store = open_selected(settings)   # read-only: the app may hold the stores
    live = fb.Live(os.environ.get("SEC_USER_AGENT", SEC_USER_AGENT))
    s = fb.run(PAPER_OPTIONS_DB, src.con, live)
    print(f"US-Handelszeit: {'ja' if s['session_open'] else 'nein (keine Entscheidungen)'}"
          f" · neue Ereignisse {s['new_events']} · eröffnet {s['opened']} · "
          f"geschlossen {s['closed']} · abgelehnt {s['rejected']} · EUR/USD "
          f"{s.get('fx') or '—'}")
    for note in s["notes"]:
        print(f"  Hinweis: {note}")

con = fb.connect(PAPER_OPTIONS_DB, read_only=True)
try:
    at, last = fb.last_run(con)
    fx = last.get("fx")
    for b in fb.BOOKS:
        st = fb.state(con, b["id"], fx)
        cur = b["currency"]
        print(f"\n{b['name']}")
        print(f"  Kontowert {st['equity']:,.2f} {cur} · Cash {st['cash']:,.2f} · "
              f"reserviert {st['reserved']:,.2f} · offene Positionen {len(st['open'])}"
              + (f" · {st['uncertain']} mit unsicherer Bewertung" if st["uncertain"] else ""))
        for p in st["open"].itertuples():
            print(f"    {p.ticker} ×{p.units} Verfall {p.expiry:%Y-%m-%d}, Schließung "
                  f"geplant {p.close_on:%Y-%m-%d}, Bewertung {p.mark_value:,.2f} USD")
    months, _reasons = fb.monthly(con)
    if not months.empty:
        print("\nje Monat:")
        print(months.to_string(index=False))
    print(f"\nletzte Aktualisierung: {at or 'noch keine'} (UTC)")
finally:
    con.close()
