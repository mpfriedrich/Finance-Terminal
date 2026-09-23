"""Fetch daily bars.

Two modes:
  full         first build — a long window for the whole universe (slow, ~8 minutes)
  incremental  default — only the days since the last stored bar (fast)

Incremental re-fetches a few days of overlap on purpose. Free price sources revise
recent bars (late corrections, split adjustments), and overwriting the overlap picks
those up instead of freezing the first version we happened to see.
"""
import datetime as dt
import sys
import time

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild
from portfolio_lab.data import prices as px, universe

FULL_WINDOW_DAYS = 460
OVERLAP_DAYS = 5

full = "--full" in sys.argv
t0 = time.time()
con, work = open_market_for_rebuild(MARKET_DB)
if work is not None:
    print('the app is holding the store; building on a copy and swapping it in',
          flush=True)

tickers = [r[0] for r in con.execute(
    "SELECT ticker FROM securities ORDER BY ticker").fetchall()]
last = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
end = dt.date.today()

if full or last is None:
    start = end - dt.timedelta(days=FULL_WINDOW_DAYS)
    print(f"full build: {len(tickers)} tickers, {start} .. {end}", flush=True)
else:
    start = last - dt.timedelta(days=OVERLAP_DAYS)
    print(f"incremental: {len(tickers)} tickers, {start} .. {end} "
          f"(last stored {last})", flush=True)
    if start >= end:
        print("already current; nothing to do")
        finish_rebuild(con, work, MARKET_DB)
        raise SystemExit(0)

stats = px.fetch_into_store(con, tickers, start, end)
universe.observed_ranges(con)
new_last = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
rows = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]

print(f"\nstats: {stats}", flush=True)
print(f"latest bar: {last} -> {new_last}", flush=True)
print(f"rows in store: {rows:,}", flush=True)
print(f"total {time.time()-t0:.0f}s", flush=True)
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it",
          flush=True)
