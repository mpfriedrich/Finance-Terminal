"""Measure the ADR ratio of every foreign filer's US listing (listing.py).

For each ticker whose filer files 20-F or 40-F: ratio = ordinary shares from the SEC
filing / shares Yahoo counts for the US line. A ratio is stored only if it lands within
6% of one depositary programmes actually use (1:1, 1:2, 1:10, …); anything else is
recorded as unresolved, and that listing stays excluded rather than priced 1:1.

Yahoo's share count is not a primary source. Look at the list this prints, and fix any
ratio you know to be wrong by hand:

    uv run python scripts/build_listing_ratios.py --set TM=10 --set BHP=2

Run: uv run python scripts/build_listing_ratios.py
"""
import datetime as dt
import sys
import time

import yfinance as yf

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.listing import FOREIGN_FORMS, snap_ratio
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

con, work = open_market_for_rebuild(MARKET_DB)
today = dt.date.today()


def store(ticker, ratio, source, detail):
    con.execute("DELETE FROM listing_ratios WHERE ticker = ?", [ticker])
    con.execute("INSERT INTO listing_ratios VALUES (?,?,?,?,?)",
                [ticker, ratio, source, today, detail])


manual = [a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--set=")] + [
    sys.argv[i + 1] for i, a in enumerate(sys.argv[:-1]) if a == "--set"]
if manual:
    for item in manual:
        ticker, value = item.split("=", 1)
        store(ticker.upper(), float(value), "manual", "set by hand")
        print(f"{ticker.upper()}: set to {float(value):g} by hand")
    finish_rebuild(con, work, MARKET_DB)
    raise SystemExit(0)

foreign = con.execute(
    """
    WITH latest AS (
        SELECT cik, arg_max(form, filed) AS form FROM fundamentals
        WHERE form IS NOT NULL GROUP BY cik
    ),
    shares AS (
        SELECT cik, arg_max(value, filed) AS ordinary FROM fundamentals
        WHERE metric = 'shares_outstanding' AND value >= 1000 GROUP BY cik
    )
    SELECT s.ticker, sh.ordinary
    FROM securities s JOIN latest l USING (cik) LEFT JOIN shares sh USING (cik)
    WHERE l.form IN (SELECT UNNEST(?))
      AND s.ticker NOT IN (SELECT ticker FROM listing_ratios WHERE source = 'manual')
    ORDER BY s.ticker
    """,
    [list(FOREIGN_FORMS)],
).fetchall()
print(f"{len(foreign)} US listings of foreign filers to measure", flush=True)

resolved = unresolved = 0
for i, (ticker, ordinary) in enumerate(foreign, 1):
    try:
        listed = yf.Ticker(ticker).get_info().get("sharesOutstanding")
    except Exception as exc:                                        # noqa: BLE001
        listed, why = None, type(exc).__name__
    else:
        why = "no share count on Yahoo"
    if ordinary and listed:
        raw = float(ordinary) / float(listed)
        ratio = snap_ratio(raw)
        detail = f"SEC {ordinary:,.0f} / Yahoo {listed:,.0f} = {raw:.3f}"
    else:
        ratio, detail = None, why if not listed else "no SEC share count"
    store(ticker, ratio, "yahoo", detail)
    if ratio:
        resolved += 1
    else:
        unresolved += 1
        print(f"  unresolved {ticker:<8} {detail}", flush=True)
    if i % 50 == 0:
        print(f"  {i}/{len(foreign)} · {resolved} resolved", flush=True)
    time.sleep(0.3)                     # Yahoo throttles bursts

not_one = con.execute(
    "SELECT ticker, ratio, detail FROM listing_ratios WHERE ratio > 0 AND ratio <> 1 "
    "ORDER BY ticker").fetchall()
print(f"\n{resolved} resolved ({len(not_one)} of them not 1:1), {unresolved} unresolved "
      "and excluded from the screen until set by hand.")
for ticker, ratio, detail in not_one:
    print(f"  {ticker:<8} 1 listed = {ratio:g} ordinary   ({detail})")
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
