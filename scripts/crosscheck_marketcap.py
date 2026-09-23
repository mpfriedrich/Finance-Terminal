"""Phase 1 (b): our market cap vs an independent source, on a random sample.

Circular checks are worthless: comparing SEC data against SEC data proves nothing about
whether we joined the right share count to the right price. Yahoo computes market cap
from its own share data, so agreement is real evidence and disagreement is a real bug.
"""
import warnings, random
import pandas as pd, yfinance as yf
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.data.free import FreeSource
from portfolio_lab.screen.engine import derive_metrics

warnings.simplefilter("ignore")
# Read-only: this script never writes, so it can run while the app is open.
con = open_market(MARKET_DB, read_only=True)
src = FreeSource(con)
asof = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
frame = derive_metrics(src.screening_frame(asof))
have = frame[frame.market_cap.notna() & (frame.market_cap > 0)]
random.seed(7)
sample = have.sample(n=40, random_state=7)

rows = []
for t in sample.itertuples():
    try:
        info = yf.Ticker(t.ticker).get_info()
        ref = info.get("marketCap")
    except Exception:
        ref = None
    if not ref:
        continue
    rows.append({"ticker": t.ticker, "ours": t.market_cap, "yahoo": float(ref),
                 "ratio": float(ref) / t.market_cap})

df = pd.DataFrame(rows)
print(f"verglichen: {len(df)} von 40\n")
def bucket(r):
    if 0.9 <= r <= 1.1: return "stimmt (±10%)"
    if 900 <= r <= 1100: return "Faktor ~1000 (Tausender)"
    if 0.0009 <= r <= 0.0011: return "Faktor ~1/1000"
    return "anders daneben"
df["urteil"] = df.ratio.map(bucket)
print(df.urteil.value_counts().to_string())
print("\nBeispiele je Gruppe:")
for name, grp in df.groupby("urteil"):
    print(f"\n-- {name}")
    print(grp[["ticker","ours","yahoo","ratio"]].head(4).to_string(index=False))
con.close()
