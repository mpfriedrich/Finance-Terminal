"""Update the six forward paper books A-F (src/portfolio_lab/forward), then print them.

One pass: refresh SPY, EUR/USD and today's FRED vintage; train or retrain A/C when due;
book every session since the last run (fills at the open, marks at the close); create
the orders for the next open — only while that open still lies ahead. Repeating it books
nothing twice. Paper only: it cannot place an order anywhere.

The morning job (scripts/morning_briefing.py) runs it after the price update and puts
the result in the brief. By hand:
  uv run python scripts/forward_books.py            (update + report)
  uv run python scripts/forward_books.py --report   (report only, no network)
"""

import datetime as dt
import sys

from portfolio_lab.config import FORWARD_DB, MARKET_DB, Settings
from portfolio_lab.data.schema import open_market
from portfolio_lab.forward import engine as en
from portfolio_lab.forward import ledger as lg
from portfolio_lab.forward import report as rp

Settings()                                   # loads .env.local
print(f"[{dt.datetime.now():%Y-%m-%d %H:%M}] Forward-Paper-Bücher A–F", flush=True)
if "--report" not in sys.argv:
    market = open_market(MARKET_DB, read_only=True)
    try:
        s = en.run(FORWARD_DB, market, log=lambda m: print(f"  {m}", flush=True))
    finally:
        market.close()
    for code, status in s["status"].items():
        print(f"  {code}: {status}")
con = lg.connect(FORWARD_DB, read_only=True)
try:
    print(rp.brief_section(con))
finally:
    con.close()
