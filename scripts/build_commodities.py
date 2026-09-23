"""Load commodity futures and macro series so sensitivities can be measured."""
import datetime as dt, time
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.data import prices as px
from portfolio_lab.research import COMMODITIES

t0 = time.time()
con = open_market(MARKET_DB)
end = dt.date.today()
start = end - dt.timedelta(days=1100)
symbols = list(COMMODITIES)
stats = px.fetch_into_store(con, symbols, start, end, chunk=len(symbols))
print("stats:", stats)
for s, label in COMMODITIES.items():
    n = con.execute("SELECT COUNT(*) FROM prices WHERE ticker = ?", [s]).fetchone()[0]
    print(f"  {s:10} {label:22} {n:5} bars")
print(f"total {time.time()-t0:.0f}s")
con.close()
