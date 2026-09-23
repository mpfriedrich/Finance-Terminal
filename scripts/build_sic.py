"""Classify the universe by the SEC's own industry code, and drop what is not a company."""
import time
from portfolio_lab.config import MARKET_DB, CACHE_DIR
from portfolio_lab.data.schema import open_market
from portfolio_lab.data import sic

t0 = time.time()
con = open_market(MARKET_DB)
counts = sic.load_sic(CACHE_DIR / "submissions.zip", con)
print(f"parsed in {time.time()-t0:.0f}s: {counts}", flush=True)

print("\n--- classified in our universe ---")
print(con.execute(
    "SELECT COUNT(*) total, COUNT(sic) with_sic FROM securities"
).fetch_df().to_string(index=False))

print("\n--- entities the SEC does not class as operating companies ---")
print(sic.non_operating(con).to_string(index=False))

print("\n--- REITs, deliberately kept ---")
print(con.execute(
    "SELECT COUNT(*) FROM securities WHERE sic = '6798'"
).fetchone()[0], "real estate investment trusts")

removed = sic.drop_non_operating(con)
print(f"\nremoved: {removed}")
print("universe now:", con.execute("SELECT COUNT(*) FROM securities").fetchone()[0])

print("\n--- top sectors remaining ---")
print(con.execute(
    "SELECT sector, COUNT(*) n FROM securities WHERE sector IS NOT NULL "
    "GROUP BY 1 ORDER BY n DESC LIMIT 12"
).fetch_df().to_string(index=False))
con.close()
