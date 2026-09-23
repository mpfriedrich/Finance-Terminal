"""Phase 1: load SEC company facts into the local store and measure what we got."""
import time
from portfolio_lab.config import MARKET_DB, CACHE_DIR
from portfolio_lab.data.schema import open_market
from portfolio_lab.data import sec

t0 = time.time()
con = open_market(MARKET_DB)
counts = sec.ingest(CACHE_DIR / "companyfacts.zip", con)
print(f"ingest done in {time.time()-t0:.0f}s: {counts}", flush=True)

print("\n--- which tag actually carried each metric (checks A20) ---", flush=True)
print(sec.tag_usage(con).to_string(index=False), flush=True)

print("\n--- filers per metric per filing year (checks A11) ---", flush=True)
cov = sec.measure_coverage(con)
piv = cov.pivot(index="year", columns="metric", values="n_ciks").fillna(0).astype(int)
print(piv.to_string(), flush=True)
con.close()
print(f"\ntotal {time.time()-t0:.0f}s", flush=True)
