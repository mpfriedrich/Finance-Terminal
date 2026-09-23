"""Load the yield curve and the front fed funds contract."""
import datetime as dt, time
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.data import prices as px
from portfolio_lab.macro import RATE_SERIES, FED_FUNDS_FRONT

t0 = time.time()
con = open_market(MARKET_DB)
symbols = list(RATE_SERIES) + [FED_FUNDS_FRONT]
end = dt.date.today(); start = end - dt.timedelta(days=1100)
print(px.fetch_into_store(con, symbols, start, end, chunk=len(symbols)))
for s in symbols:
    r = con.execute("SELECT COUNT(*), MAX(date), MAX(close) FROM prices WHERE ticker=?", [s]).fetchone()
    print(f"  {s:8} {r[0]:5} bars, latest {r[1]}")
print(f"total {time.time()-t0:.0f}s")
con.close()
