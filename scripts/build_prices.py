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

from portfolio_lab.config import MARKET_DB, WORLD_DB
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild
from portfolio_lab import fred, world
from portfolio_lab.data import history, prices as px, universe, wrds

FULL_WINDOW_DAYS = 460
OVERLAP_DAYS = 5

full = "--full" in sys.argv
# --since YYYY-MM-DD downloads a deep history instead of the rolling window. Needed
# before any point-in-time backtest: four rebalance dates is not a backtest.
since = None
for arg in sys.argv[1:]:
    if arg.startswith("--since="):
        since = dt.date.fromisoformat(arg.split("=", 1)[1])
        full = True
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
    start = since or (end - dt.timedelta(days=FULL_WINDOW_DAYS))
    print(f"full build: {len(tickers)} tickers, {start} .. {end}", flush=True)
else:
    start = last - dt.timedelta(days=OVERLAP_DAYS)
    print(f"incremental: {len(tickers)} tickers, {start} .. {end} "
          f"(last stored {last})", flush=True)
    if start >= end:
        # Prices are current, but the windows may not be: a run that failed after the
        # download (the ticker_history key clash, 24.09.2026) left them behind.
        history.refresh_price_windows(con)
        print("already current; price windows refreshed")
        finish_rebuild(con, work, MARKET_DB)
        start = None


def refresh_world_store() -> None:
    """The world store (stage 3) carries a copy of this store's US half; bring it up to
    date so the world screen does not run on yesterday's US prices."""
    if not WORLD_DB.exists():
        return
    wcon, wwork = open_market_for_rebuild(WORLD_DB)
    print(f"world store: US half refreshed {wrds.copy_us(wcon, MARKET_DB)}", flush=True)
    finish_rebuild(wcon, wwork, WORLD_DB)


if start is None or start >= end:
    refresh_world_store()
    raise SystemExit(0)

stats = px.fetch_into_store(con, tickers, start, end)
# Exchange rates for the foreign filers (scripts/build_fx.py) and the market monitor's
# series (scripts/build_world.py) are stored once and kept current here — or every
# screen after ten days would lose its rates as stale, and the monitor would freeze.
extra = sorted({r[0] for r in con.execute(
    "SELECT DISTINCT ticker FROM prices WHERE ticker LIKE '%USD=X' "
    "OR ticker IN (SELECT UNNEST(?))", [world.stored_symbols()]).fetchall()})
if extra:
    px.fetch_into_store(con, extra, start, end)
# Macro series (scripts/build_fred.py), refreshed only if they were stored once. A FRED
# outage must not fail the price build, so each series is tried on its own.
if con.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0]:
    for sid in fred.SERIES:
        try:
            fred.store(con, fred.download(sid))
        except Exception:                                            # noqa: BLE001
            print(f"  FRED {sid}: not refreshed", flush=True)
universe.observed_ranges(con)
# ticker_history's price windows are what the universe is read from; keep them current.
history.refresh_price_windows(con)
new_last = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
rows = con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]

print(f"\nstats: {stats}", flush=True)
print(f"latest bar: {last} -> {new_last}", flush=True)
print(f"rows in store: {rows:,}", flush=True)
filled = px.rebuild_raw_closes(con)
print(f"traded prices rebuilt from splits: {filled:,} rows", flush=True)

odd = px.suspicious_series(con)
print(f"\nseries worth a look ({len(odd)} of them): a reassigned ticker splices two "
      f"companies into one, and the join usually shows up as a huge move or a long gap",
      flush=True)
print(odd.head(25).to_string(index=False), flush=True)

zeros = px.non_prices(con)
if len(zeros):
    print(f"\nbars priced at zero or less ({len(zeros)} tickers) — an absent price, not "
          "a collapse; anything dividing by one of these produces an infinity", flush=True)
    print(zeros.head(10).to_string(index=False), flush=True)

print(f"\ntotal {time.time()-t0:.0f}s", flush=True)
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it",
          flush=True)
refresh_world_store()
