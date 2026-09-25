"""Exchange rates for every currency the SEC filers report in.

Stage 1 of the world-market plan (PLAN.md): filers that report in euros, yen or rupees
are read since 24.09.2026, and pit.py converts their amounts at the rate of the screen
date. This stores those rates — USD per unit, Yahoo's "{CUR}USD=X" — from 2009 on.
Run it once after build_fundamentals.py; build_prices.py keeps them current afterwards.

Run: uv run python scripts/build_fx.py
"""
import datetime as dt

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data import prices as px
from portfolio_lab.data.pit import fx_symbol
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

con, work = open_market_for_rebuild(MARKET_DB)
currencies = [r[0] for r in con.execute(
    "SELECT currency, COUNT(DISTINCT cik) n FROM fundamentals "
    "WHERE currency IS NOT NULL AND currency <> 'USD' GROUP BY 1 ORDER BY n DESC"
).fetchall()]
print(f"{len(currencies)} reporting currencies besides USD: {', '.join(currencies)}",
      flush=True)

missing = []
for cur in currencies:
    symbol = fx_symbol(cur)
    bars = px._download([symbol], dt.date(2009, 1, 1), dt.date.today())
    if bars.empty:
        missing.append(cur)
        continue
    con.execute("DELETE FROM prices WHERE ticker = ?", [symbol])
    con.register("_fx", bars)
    con.execute(
        "INSERT INTO prices (ticker, date, open, high, low, close, close_adj, volume) "
        "SELECT ticker, date, open, high, low, close, close_adj, volume FROM _fx")
    con.unregister("_fx")
    print(f"  {symbol}: {len(bars)} days, {bars.date.min()} .. {bars.date.max()}",
          flush=True)

if missing:
    n = con.execute(
        "SELECT COUNT(DISTINCT cik) FROM fundamentals WHERE currency IN (SELECT UNNEST(?))",
        [missing]).fetchone()[0]
    print(f"\nno rate found for {', '.join(missing)} — {n} filers stay excluded as "
          "'no exchange rate for the reporting currency'", flush=True)
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
