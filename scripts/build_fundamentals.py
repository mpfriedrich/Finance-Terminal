"""Phase 1: load SEC company facts into the local store and measure what we got."""
import time

from portfolio_lab.config import CACHE_DIR, MARKET_DB
from portfolio_lab.data import sec
from portfolio_lab.data.schema import open_market

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
print("\n--- filers per reporting currency, and foreign private issuers ---", flush=True)
print(con.execute(
    """
    SELECT COALESCE(currency, 'USD') AS currency, COUNT(DISTINCT cik) AS filers,
           COUNT(DISTINCT CASE WHEN form IN ('20-F', '40-F') THEN cik END) AS foreign
    FROM fundamentals WHERE metric <> 'shares_outstanding'
    GROUP BY 1 ORDER BY filers DESC LIMIT 25
    """).fetch_df().to_string(index=False), flush=True)
print("\nnext: scripts/build_fx.py (exchange rates), then "
      "scripts/build_listing_ratios.py (ADR ratios)", flush=True)
con.close()
print(f"\ntotal {time.time()-t0:.0f}s", flush=True)
