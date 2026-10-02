"""Store one count of the aircraft in the air, per region, from OpenSky. No key needed.

Run: uv run python scripts/build_air.py   (build_prices.py takes one snapshot per run)
"""
from portfolio_lab import aviation
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

con, work = open_market_for_rebuild(MARKET_DB)
total = aviation.snapshot(con)
latest = aviation.history(con).iloc[-1]
print(f"{total:,} aircraft airborne at {latest.name:%Y-%m-%d %H:%M} UTC", flush=True)
print(latest.astype(int).to_string(), flush=True)
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
