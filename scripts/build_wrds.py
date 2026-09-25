"""World stage 3: download Compustat Global from WRDS and build data/world.duckdb.

The world store is the free US store (SEC + Yahoo) plus every company Compustat Global
lists in the chosen countries, all priced in dollars. Afterwards set
PORTFOLIO_LAB_SOURCE=world in .env.local and restart the app.

Needs a WRDS account: WRDS_USERNAME in .env.local, and WRDS_PASSWORD there or the
password in ~/.pgpass. With two-factor authentication WRDS sends a Duo push when the
connection opens — approve it on the phone. Nothing here prints the password.

Run:
  uv run python scripts/build_wrds.py                   download + build (first time: long)
  uv run python scripts/build_wrds.py --countries=DEU,JPN,GBR --since=2015-01-01
  uv run python scripts/build_wrds.py --from-dir=DIR    build from parquet already on disk
  uv run python scripts/build_wrds.py --us-only         only bring in today's US store
                                                        (fast; build_prices.py does this)

A second full run only fetches the current year of daily prices; closed years stay
cached under data/cache/wrds/.
"""
import datetime as dt
import sys
import time
from pathlib import Path

from portfolio_lab.config import CACHE_DIR, MARKET_DB, WORLD_DB, require_secret, secret
from portfolio_lab.data import wrds
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild


def arg(name: str) -> str | None:
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")),
                None)


t0 = time.time()
us_only = "--us-only" in sys.argv
from_dir = Path(arg("from-dir")) if arg("from-dir") else None
countries = tuple(arg("countries").split(",")) if arg("countries") else wrds.DEFAULT_COUNTRIES
since = dt.date.fromisoformat(arg("since")) if arg("since") else wrds.DEFAULT_SINCE
cache = from_dir or CACHE_DIR / "wrds"

if us_only and not WORLD_DB.exists():
    sys.exit("No world store yet: run scripts/build_wrds.py without --us-only first.")

if not us_only and from_dir is None:
    user = require_secret(wrds.KEY_USER)
    print("connecting to WRDS as the configured user (approve the Duo push if asked) ...",
          flush=True)
    server = wrds.connect(user, secret(wrds.KEY_PASSWORD))
    try:
        print(f"downloading Compustat Global for {len(countries)} countries since {since} "
              "...", flush=True)
        fetched = wrds.download(server, cache, countries, since)
        print(f"downloaded: {fetched}", flush=True)
    finally:
        server.close()

WORLD_DB.parent.mkdir(parents=True, exist_ok=True)
con, work = open_market_for_rebuild(WORLD_DB)
if MARKET_DB.exists():
    print(f"copied the US store: {wrds.copy_us(con, MARKET_DB)}", flush=True)
else:
    print("no US store (data/market.duckdb): the world store has no US companies, "
          "benchmark or risk-free series", flush=True)

if not us_only:
    counts = wrds.ingest(con, cache)
    print(f"ingested: {counts}", flush=True)
    print(f"  {counts['facts_on_estimated_date']:,} of {counts['facts']:,} facts carry an "
          "ESTIMATED publication date (period end + legal deadline).", flush=True)
    if counts["price_rows_without_rate"]:
        print(f"  {counts['price_rows_without_rate']:,} daily prices had no exchange rate "
              "within 10 days and were left out.", flush=True)
    print(wrds.country_counts(con).to_string(index=False), flush=True)

print(f"total {time.time() - t0:.0f}s", flush=True)
if finish_rebuild(con, work, WORLD_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
