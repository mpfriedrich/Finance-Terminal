"""World stage 3: download from WRDS and build data/world.duckdb.

The world store holds the US and every company Compustat Global lists in the chosen
countries, all priced in dollars. The US half comes from CRSP where the WRDS account
includes it — every US common stock since `--since`, including the ones that went
bankrupt or were taken over, with what holders received at the end — continued after
CRSP's last day with the free store's daily prices; the SEC filings stay the source of
fundamentals. Without CRSP (or with --us=free) the US half is the free store as is.
Afterwards set PORTFOLIO_LAB_SOURCE=world in .env.local and restart the app.

Needs a WRDS account: WRDS_USERNAME in .env.local, and WRDS_PASSWORD there or the
password in ~/.pgpass. If CRSP comes through a different university's account, add
WRDS_CRSP_USERNAME and WRDS_CRSP_PASSWORD as well; each account then connects on its own
(two Duo pushes). With two-factor authentication WRDS sends a Duo push when the
connection opens — approve it on the phone. Nothing here prints the password.

Run:
  uv run python scripts/build_wrds.py                   download + build (first time: long)
  uv run python scripts/build_wrds.py --countries=DEU,JPN,GBR --since=2015-01-01
  uv run python scripts/build_wrds.py --countries=none   the US from CRSP only
  uv run python scripts/build_wrds.py --us=free          no CRSP: the free US store
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
from portfolio_lab.data import crsp, wrds
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild


def arg(name: str) -> str | None:
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")),
                None)


t0 = time.time()


def step(message: str) -> None:
    """Progress between the long silent steps, with the time so far."""
    print(f"  [{time.time() - t0:5.0f}s] {message} ...", flush=True)


us_only = "--us-only" in sys.argv
from_dir = Path(arg("from-dir")) if arg("from-dir") else None
countries = (() if arg("countries") == "none" else
             tuple(arg("countries").split(",")) if arg("countries") else wrds.DEFAULT_COUNTRIES)
use_crsp = (arg("us") or "crsp") == "crsp"
since = dt.date.fromisoformat(arg("since")) if arg("since") else wrds.DEFAULT_SINCE
cache = from_dir or CACHE_DIR / "wrds"

if us_only and not WORLD_DB.exists():
    sys.exit("No world store yet: run scripts/build_wrds.py without --us-only first.")

def connect(user_key: str, password_key: str, what: str):
    user = require_secret(user_key)
    print(f"connecting to WRDS for {what} ({user_key}; approve the Duo push if asked) ...",
          flush=True)
    return wrds.connect(user, secret(password_key))


def download_crsp(server) -> bool:
    print(f"downloading CRSP (US, every listing since {since}) and Compustat North "
          "America ...", flush=True)
    try:
        print(f"downloaded: {crsp.download(server, cache, since)}", flush=True)
        return True
    except RuntimeError as exc:
        print(f"  {exc}\n  continuing with the free US store.", flush=True)
        return False


if not us_only and from_dir is None:
    # CRSP may come through a second account (WRDS_CRSP_USERNAME) when the first
    # university does not license it; then each account gets its own connection.
    separate_crsp = use_crsp and bool(secret(wrds.KEY_CRSP_USER))
    if countries or (use_crsp and not separate_crsp):
        server = connect(wrds.KEY_USER, wrds.KEY_PASSWORD, "Compustat Global"
                         if not use_crsp or separate_crsp else "Compustat Global and CRSP")
        try:
            if countries:
                print(f"downloading Compustat Global for {len(countries)} countries since "
                      f"{since} ...", flush=True)
                print(f"downloaded: {wrds.download(server, cache, countries, since)}",
                      flush=True)
            if use_crsp and not separate_crsp:
                use_crsp = download_crsp(server)
        finally:
            server.close()
    if separate_crsp:
        server = connect(wrds.KEY_CRSP_USER, wrds.KEY_CRSP_PASSWORD, "CRSP")
        try:
            use_crsp = download_crsp(server)
        finally:
            server.close()

WORLD_DB.parent.mkdir(parents=True, exist_ok=True)
step("opening the world store (a copy if the app holds it)")
con, work = open_market_for_rebuild(WORLD_DB)

crsp_on_disk = any(any((cache / folder).glob("*.parquet"))
                   for folder in ("crsp_dsf_v2", "crsp_dsf"))
if not us_only and use_crsp and crsp_on_disk:
    counts = crsp.ingest(con, cache, MARKET_DB if MARKET_DB.exists() else None, log=step)
    print(f"CRSP: {counts}", flush=True)
    print(f"  {counts['ended']:,} US listings ended before {counts['crsp_end']} — the "
          f"companies the free data cannot supply; {counts['with_delisting_return']:,} "
          "of them with the delisting return holders actually received.", flush=True)

if not us_only and countries:
    counts = wrds.ingest(con, cache, log=step)
    print(f"Compustat Global: {counts}", flush=True)
    print(f"  {counts['facts_on_estimated_date']:,} of {counts['facts']:,} facts carry an "
          "ESTIMATED publication date (period end + legal deadline).", flush=True)
    if counts["price_rows_without_rate"]:
        print(f"  {counts['price_rows_without_rate']:,} daily prices had no exchange rate "
              "within 10 days and were left out.", flush=True)
    print(wrds.country_counts(con).to_string(index=False), flush=True)

if MARKET_DB.exists():
    copied = wrds.copy_us(con, MARKET_DB, log=step)
    print(f"free US store copied in: {copied}", flush=True)
    if copied.get("crsp_lines_without_current_prices"):
        print(f"  {copied['crsp_lines_without_current_prices']:,} lines trading when CRSP "
              "ends have no bar in the free store to continue with; from 10 days after "
              "that they count as delisted. Usually a ticker the free store lacks — "
              "add it to the free universe and rerun build_prices.py.", flush=True)
else:
    print("no US store (data/market.duckdb): no benchmark, no risk-free series, and "
          "no prices after CRSP ends", flush=True)

print(f"total {time.time() - t0:.0f}s", flush=True)
if finish_rebuild(con, work, WORLD_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
