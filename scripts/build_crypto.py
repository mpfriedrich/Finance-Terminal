"""Load crypto price series.

They live in the same price table as shares, but they are NOT in `securities` — that is
deliberate. `securities` is the equity universe the screener runs over, and a token has
no revenue, equity or earnings to screen on. Keeping them out means a crypto pair can
never drift into a price-to-book ranking, while every price-based tool still reaches it.
"""
import datetime as dt
import time

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data import prices as px
from portfolio_lab.data.schema import open_market

CRYPTO = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
    "XRP-USD": "XRP", "ADA-USD": "Cardano", "DOGE-USD": "Dogecoin",
    "LTC-USD": "Litecoin", "LINK-USD": "Chainlink",
}

t0 = time.time()
con = open_market(MARKET_DB)
end = dt.date.today()
start = end - dt.timedelta(days=2000)
stats = px.fetch_into_store(con, list(CRYPTO), start, end, chunk=len(CRYPTO))
print("stats:", stats)
for s, label in CRYPTO.items():
    row = con.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM prices WHERE ticker = ?", [s]
    ).fetchone()
    print(f"  {s:10} {label:12} {row[0]:5} bars  {row[1]} .. {row[2]}")
print(f"total {time.time()-t0:.0f}s")
con.close()
