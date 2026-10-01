"""Fill the point-in-time frame cache so a stage B backtest fits its time budget.

One frame costs 6-10 seconds against the live store, almost all of it the point-in-time
fundamentals assembly. A quarterly run from 2011 needs 63 of them. Computing them once
here turns every later backtest — with any strategy — into seconds.

Frames are cached per data source (the fingerprint is part of the cache key), so the
script warms the source the app would run on: PORTFOLIO_LAB_SOURCE from .env.local,
or the one named with --source=. The free store is the default, as before.

With --prune, cached builds whose newest frame is older than seven days are deleted
afterwards (the morning job passes it: every daily update orphans the previous build).

Run: uv run python scripts/warm_pit_cache.py [start-year] [--source=free|paid|world] [--prune]
"""
import datetime as dt
import sys

from portfolio_lab import stage_b
from portfolio_lab.config import CACHE_DIR, Settings
from portfolio_lab.data.sources import open_selected
from portfolio_lab.pit_cache import FrameCache, prune

args = sys.argv[1:]
start_year = next((int(a) for a in args if a.isdigit()), 2011)
source_name = next((a.split("=", 1)[1] for a in args if a.startswith("--source=")), None)
settings = Settings(data_source=source_name) if source_name else Settings()

src, store = open_selected(settings=settings)
asof = src.latest_equity_date()
dates = stage_b.rebalance_dates(dt.date(start_year, 1, 3), asof, "quarterly")

cache = FrameCache(src, CACHE_DIR / "pit", src.snapshot().fingerprint())
print(f"{len(dates)} rebalance dates, {dates[0]} .. {dates[-1]}", flush=True)
print(f"source: {settings.data_source} ({store})", flush=True)
print(f"cache: {cache.root}", flush=True)

stats = cache.warm(dates, progress=lambda i, n, d: print(
    f"  {i:>3}/{n}  {d}", flush=True) if i % 5 == 0 or i == n else None)
print(f"\n{stats}", flush=True)
if "--prune" in args:
    print(f"pruned {prune(CACHE_DIR / 'pit', cache.root)} orphaned build(s)", flush=True)
