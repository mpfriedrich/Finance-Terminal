"""Build the securities master.

Phase 1 builds TODAY's universe from SEC's current ticker file. That file is a snapshot:
companies that were acquired or went bankrupt are simply absent from it. That is fine for
screening today and fatal for backtesting the past, which is why Phase 7 builds
`ticker_history` separately and measures how much of the vanished world it can recover.
Nothing here should ever be used to decide what was listed in 2015.

Sector is deliberately NOT loaded here. Getting an industry code for every filer means
another multi-gigabyte bulk file or tens of thousands of requests, and sector is only
needed for the few dozen names that survive a screen. It is fetched lazily for those.
"""

from __future__ import annotations

import datetime as dt
import json

import duckdb
import pandas as pd
import requests

from ..config import SEC_USER_AGENT

TICKER_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

# Common stock only. Everything here is a fund, trust, ADR wrapper or similar that has no
# business in an equity screen driven by book value and earnings.
EXCLUDED_EXCHANGES = {"OTC", ""}

# STOPGAP, not the real fix. Exchange-traded funds and commodity trusts file with the SEC
# and carry a book value, so a cheapness screen happily ranks them alongside operating
# companies — abrdn's Platinum, Silver and Palladium trusts turned up in a price-to-book
# screen on 2026-09-21. These name patterns are unambiguous (all 57 "ETF" matches are
# genuinely funds).
#
# Deliberately NOT matched: "Trust" on its own. Digital Realty Trust, Camden Property
# Trust and Federal Realty Investment Trust are real operating companies; excluding them
# to catch Sprott Physical Gold Trust would cost more than it saves. The principled fix is
# the SEC's own industry classification (SIC), which distinguishes 6726 investment offices
# and 6221 commodity contracts from operating businesses — that needs the submissions
# bulk file.
FUND_NAME_PATTERN = (
    r"(?i)\b(ETF|ETN|ETV)\b"
    r"|(?i)\bFund\b"
    r"|(?i)\bPhysical (Gold|Silver|Platinum|Palladium|Copper) Trust\b"
    r"|(?i)\bIndex Trust\b"
)


def fetch_ticker_file(timeout: int = 60) -> pd.DataFrame:
    resp = requests.get(
        TICKER_URL, headers={"User-Agent": SEC_USER_AGENT}, timeout=timeout
    )
    resp.raise_for_status()
    payload = json.loads(resp.text)
    frame = pd.DataFrame(payload["data"], columns=payload["fields"])
    frame = frame.rename(columns={"cik": "cik", "name": "name", "ticker": "ticker"})
    frame["exchange"] = frame["exchange"].fillna("").astype(str).str.upper()
    return frame


def build_securities(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Populate `securities` from the current SEC ticker file."""
    frame = fetch_ticker_file()
    before = len(frame)

    frame = frame[~frame.exchange.isin(EXCLUDED_EXCHANGES)]
    # A ticker with a dot or dash is usually a share class or a unit/warrant line. Keeping
    # them would double-count companies in an equal-weight portfolio.
    frame = frame[~frame.ticker.astype(str).str.contains(r"[.\-]", regex=True)]
    frame = frame[~frame.name.astype(str).str.contains(FUND_NAME_PATTERN, regex=True, na=False)]
    frame = frame.drop_duplicates(subset=["cik"], keep="first")

    out = pd.DataFrame(
        {
            "cik": frame.cik.astype("int64"),
            "ticker": frame.ticker.astype(str).str.upper(),
            "name": frame.name.astype(str),
            "sector": None,
            "sic": None,
            "exchange": frame.exchange.astype(str),
            "first_seen": None,
            "last_seen": None,
        }
    )

    con.execute("DELETE FROM securities;")
    con.register("_sec_in", out)
    con.execute(
        "INSERT INTO securities (cik, ticker, name, sector, sic, exchange, first_seen, last_seen) "
        "SELECT cik, ticker, name, sector, sic, exchange, first_seen, last_seen FROM _sec_in"
    )
    con.unregister("_sec_in")
    stats = {"fetched": before, "kept": len(out)}

    # Rebuilding the universe wipes sic/sector, because the ticker file does not carry
    # them. Re-applying the classification here stops a rebuild from silently letting
    # 290 SPACs and the commodity trusts back in — a regression nobody would notice until
    # a blank-cheque shell turned up at the top of a value screen again.
    from ..config import CACHE_DIR
    from . import sic as sic_module

    archive = CACHE_DIR / "submissions.zip"
    if archive.exists():
        sic_module.load_sic(archive, con)
        stats["reclassified"] = True
        stats["non_operating_removed"] = sic_module.drop_non_operating(con)
    else:
        stats["reclassified"] = False

    return stats


def observed_ranges(con: duckdb.DuckDBPyConnection) -> int:
    """Fill first_seen/last_seen from the prices we actually hold.

    These are observations, not exchange records: `last_seen` means "our price source
    stopped giving us bars", which for a free source usually means the ticker was dropped,
    not necessarily that trading ceased on that date.
    """
    return con.execute(
        """
        UPDATE securities AS s
        SET first_seen = p.lo, last_seen = p.hi
        FROM (SELECT ticker, MIN(date) AS lo, MAX(date) AS hi FROM prices GROUP BY 1) AS p
        WHERE p.ticker = s.ticker
        """
    ).fetchall() and con.execute(
        "SELECT COUNT(*) FROM securities WHERE first_seen IS NOT NULL"
    ).fetchone()[0]


def latest_trading_day(con: duckdb.DuckDBPyConnection) -> dt.date | None:
    row = con.execute("SELECT MAX(date) FROM prices").fetchone()
    return row[0] if row else None
