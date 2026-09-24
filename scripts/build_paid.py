"""Phase 10: download Sharadar (Nasdaq Data Link) and build data/paid.duckdb.

Needs NASDAQ_DATA_LINK_API_KEY in .env.local and a Sharadar subscription covering
TICKERS, SF1 and SEP. The key is read, never printed. Afterwards set
PORTFOLIO_LAB_SOURCE=paid in .env.local and restart the app.

The index series (^SP500TR, ^IRX) are not part of Sharadar; they are copied over from
the free store when it exists, so the benchmark and the risk-free rate still work.

Run: uv run python scripts/build_paid.py [--from-dir=DIR]
     --from-dir uses TICKERS.csv, SF1.csv and SEP.csv already in DIR instead of downloading.
"""
import sys
import time
from pathlib import Path

from portfolio_lab.config import CACHE_DIR, MARKET_DB, PAID_DB, require_secret
from portfolio_lab.data import paid
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

t0 = time.time()
from_dir = next((Path(a.split("=", 1)[1]) for a in sys.argv[1:]
                 if a.startswith("--from-dir=")), None)

if from_dir is None:
    key = require_secret(paid.KEY_NAME)
    dest = CACHE_DIR / "sharadar"
    files = {}
    for table in paid.TABLES:
        print(f"downloading {table} ...", flush=True)
        files[table] = paid.download_table(table, key, dest)
else:
    files = {t: from_dir / f"{t}.csv" for t in paid.TABLES}

PAID_DB.parent.mkdir(parents=True, exist_ok=True)
con, work = open_market_for_rebuild(PAID_DB)
counts = paid.ingest(con, files["TICKERS"], files["SF1"], files["SEP"])
print(f"ingested: {counts}", flush=True)

if MARKET_DB.exists():
    con.execute(f"ATTACH '{MARKET_DB}' AS free (READ_ONLY)")
    con.execute(
        "INSERT OR REPLACE INTO prices BY NAME SELECT * FROM free.prices "
        "WHERE ticker IN ('^SP500TR', '^IRX')"
    )
    con.execute("DETACH free")
    print("copied ^SP500TR and ^IRX from the free store", flush=True)
else:
    print("no free store: the benchmark and risk-free series are missing", flush=True)

print(f"companies that stopped trading, with prices: {paid.delisted_count(con)}")
print(f"total {time.time() - t0:.0f}s", flush=True)
if finish_rebuild(con, work, PAID_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
