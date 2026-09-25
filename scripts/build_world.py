"""Store the market monitor's series: world indices, currencies, US yields, commodities,
crypto (world.MONITOR). Run once; build_prices.py keeps them current afterwards.

Run: uv run python scripts/build_world.py
"""
import datetime as dt

from portfolio_lab import world
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data import prices as px
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

con, work = open_market_for_rebuild(MARKET_DB)
start, end = dt.date(2010, 1, 1), dt.date.today()
missing = []
for symbol in world.stored_symbols():
    bars = px._download([symbol], start, end)
    if bars.empty:
        missing.append(symbol)
        continue
    con.execute("DELETE FROM prices WHERE ticker = ?", [symbol])
    con.register("_w", bars)
    con.execute(
        "INSERT INTO prices (ticker, date, open, high, low, close, close_adj, volume) "
        "SELECT ticker, date, open, high, low, close, close_adj, volume FROM _w")
    con.unregister("_w")
    print(f"  {symbol:<11} {len(bars):>5} days, {bars.date.min()} .. {bars.date.max()}",
          flush=True)
if missing:
    print(f"\nnothing returned for {', '.join(missing)} — shown empty on the monitor")
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
