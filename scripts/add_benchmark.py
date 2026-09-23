"""Store the S&P 500 TOTAL RETURN series alongside the stocks.

Total return, not the plain index: the portfolio's returns include dividends, so a
price-only benchmark would hand it ~2 points a year of outperformance it never earned.
"""
import datetime as dt
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.data import prices as px

con = open_market(MARKET_DB)
symbol, bars = px.fetch_benchmark(dt.date(2010, 1, 1), dt.date.today())
con.execute("DELETE FROM prices WHERE ticker = ?", [symbol])
con.register("_b", bars)
con.execute("INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
            "SELECT ticker, date, open, close, close_adj, volume FROM _b")
con.unregister("_b")
print(f"{symbol}: {len(bars)} Balken, {bars.date.min()} .. {bars.date.max()}")
con.close()
