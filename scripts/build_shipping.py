"""Download the daily chokepoint transits from IMF PortWatch and store them. No key needed.

Run: uv run python scripts/build_shipping.py   (build_prices.py keeps them current)
The first run takes the whole history since 2019 (~80 requests, about a minute).
"""
from portfolio_lab import shipping
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

con, work = open_market_for_rebuild(MARKET_DB)
n = shipping.update(con)
first, last, points = con.execute(
    "SELECT MIN(date), MAX(date), COUNT(DISTINCT chokepoint) FROM chokepoint_transits"
).fetchone()
print(f"{n:,} chokepoint-days stored; {points} chokepoints, {first} .. {last}", flush=True)
board = shipping.dashboard(con)
if not board.empty:
    print(board.head(8).to_string(index=False), flush=True)
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
