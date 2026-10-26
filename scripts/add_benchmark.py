"""Store the S&P 500 series the benchmark and its audit depend on.

Three symbols, and the reason for each:

  ^SP500TR  the benchmark itself — TOTAL return. The portfolio's returns include
            dividends, so a price-only benchmark would hand it ~2 points a year of
            outperformance it never earned (R11).
  ^GSPC     the same index WITHOUT dividends. Only useful as a control: the gap between
            the two is the dividend contribution, and if it is ever zero then what we are
            calling a total-return series is not one.
  SPY       an independent implementation of the same index, built from traded prices and
            actual distributions rather than from an index calculation. It should track
            ^SP500TR to within its fee. Two different data paths agreeing is evidence;
            one path agreeing with itself is not.

Run: uv run python scripts/add_benchmark.py
"""
import datetime as dt
import sys

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data import prices as px
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

SYMBOLS = px.BENCHMARK_SERIES

con, work = open_market_for_rebuild(MARKET_DB)
if work is not None:
    print("the app is holding the store; building on a copy and swapping it in", flush=True)

start = dt.date(2010, 1, 1)
end = dt.date.today()
for symbol in sys.argv[1:] or SYMBOLS:
    bars = px._download([symbol], start, end)
    if bars.empty:
        print(f"{symbol}: nothing returned", flush=True)
        continue
    con.execute("DELETE FROM prices WHERE ticker = ?", [symbol])
    con.register("_b", bars)
    con.execute(
        "INSERT INTO prices (ticker, date, open, high, low, close, close_adj, volume) "
        "SELECT ticker, date, open, high, low, close, close_adj, volume FROM _b"
    )
    con.unregister("_b")
    print(f"{symbol}: {len(bars)} bars, {bars.date.min()} .. {bars.date.max()}", flush=True)

if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
