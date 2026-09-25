"""Download the macro series in fred.SERIES from FRED and store them. No key needed.

Run: uv run python scripts/build_fred.py   (build_prices.py refreshes them too)
"""
from portfolio_lab import fred
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

con, work = open_market_for_rebuild(MARKET_DB)
failed = []
for sid, (label, *_rest) in fred.SERIES.items():
    try:
        n = fred.store(con, fred.download(sid))
        print(f"  {sid:<20} {n:>6} observations  {label}", flush=True)
    except Exception as exc:                                        # noqa: BLE001
        failed.append(sid)
        print(f"  {sid:<20} failed ({type(exc).__name__})", flush=True)
if failed:
    print(f"\n{len(failed)} series not refreshed: {', '.join(failed)}")
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
