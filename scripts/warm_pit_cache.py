"""Fill the point-in-time frame cache so a stage B backtest fits its time budget.

One frame costs 6-10 seconds against the live store, almost all of it the point-in-time
fundamentals assembly. A quarterly run from 2011 needs 63 of them. Computing them once
here turns every later backtest — with any strategy — into seconds.

Run: uv run python scripts/warm_pit_cache.py [start-year]
"""
import datetime as dt
import sys

from portfolio_lab import stage_b
from portfolio_lab.config import CACHE_DIR, MARKET_DB
from portfolio_lab.data.free import FreeSource
from portfolio_lab.data.schema import open_market
from portfolio_lab.pit_cache import FrameCache

start_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2011
src = FreeSource(open_market(MARKET_DB, read_only=True))
asof = src.latest_equity_date()
dates = stage_b.rebalance_dates(dt.date(start_year, 1, 3), asof, "quarterly")

cache = FrameCache(src, CACHE_DIR / "pit", src.snapshot().fingerprint())
print(f"{len(dates)} rebalance dates, {dates[0]} .. {dates[-1]}", flush=True)
print(f"cache: {cache.root}", flush=True)

stats = cache.warm(dates, progress=lambda i, n, d: print(
    f"  {i:>3}/{n}  {d}", flush=True) if i % 5 == 0 or i == n else None)
print(f"\n{stats}", flush=True)
