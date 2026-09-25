"""PaidSource: Sharadar (via Nasdaq Data Link) behind the same seam as FreeSource.

Phase 10. What the paid data buys is exactly the part the free stack cannot supply
(PLAN.md Phase 7 measured 0/20): companies that were delisted, with their prices, and
fundamentals stamped with the date they were filed. The screener, the point-in-time
assembly and the backtest do not change at all — which is the whole point of the seam.

How it is wired, and why:

  * The three Sharadar tables (TICKERS, SF1, SEP) are downloaded ONCE by
    `scripts/build_paid.py` and translated into the SAME schema the free store uses,
    in a file of its own (`data/paid.duckdb`). `PaidSource` is then `FreeSource` pointed
    at that file. No second set of queries exists that could drift from the first: the
    point-in-time rules in pit.py and the universe from ticker_history apply unchanged.
  * Only the AS-REPORTED dimensions (ARQ, ARY) are loaded. Sharadar's MR* dimensions are
    "most recent", i.e. restated — the look-ahead R13 forbids. `datekey` is the filing
    date and becomes `filed`.
  * Delisted names are kept. Sharadar suffixes a reused symbol (a dead "ABC" becomes
    "ABC1"), so a ticker names one company, and `permaticker` stands in for the CIK as
    the join key.

Written against the published Sharadar table layout, WITHOUT access to a subscription:
nothing here has met the real service yet. The translation is tested on hand-made files
in that layout; the download is not tested at all. PLAN.md records both.
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import duckdb
import requests

from . import history
from .free import FreeSource

KEY_NAME = "NASDAQ_DATA_LINK_API_KEY"
API = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/{table}.json"
TABLES = ("TICKERS", "SF1", "SEP")

# Stored metric -> Sharadar SF1 column. Duration metrics carry a span; instant metrics are
# balance-sheet values at the period end.
DURATION = {
    "revenue": "revenue",
    "net_income": "netinc",
    "operating_cash_flow": "ncfo",
    # Sharadar reports capex as a negative cash flow; the SEC tag stores the payment as a
    # positive number. Stored positive so both sources mean the same thing.
    "capex": "capex",
    # For EV multiples, returns on capital and the cost of debt, as in sec.py.
    "operating_income": "opinc",
    "depreciation_amortization": "depamor",
    "interest_expense": "intexp",
    "income_tax": "taxexp",
    "pretax_income": "ebt",
    "gross_profit": "gp",
}
INSTANT = {
    "book_value": "equity",
    "total_assets": "assets",
    "total_liabilities": "liabilities",
    "long_term_debt": "debtnc",
    "current_debt": "debtc",
    "cash": "cashneq",
    "current_assets": "assetsc",
    "current_liabilities": "liabilitiesc",
}
SPAN_MONTHS = {"ARQ": 3, "ARY": 12}


class MissingKey(RuntimeError):
    """The paid source was chosen but no key is configured. The message is the fix."""


def missing_key_message() -> str:
    return (
        f"The paid data source is selected, but {KEY_NAME} is not set. Add a line "
        f"'{KEY_NAME}=...' to .env.local (the value is never shown), then run "
        "scripts/build_paid.py once to download the data. Or switch back with "
        "PORTFOLIO_LAB_SOURCE=free."
    )


# -- download ----------------------------------------------------------------------------

def download_table(table: str, key: str, dest: Path, timeout: int = 120,
                   poll_seconds: int = 20, max_polls: int = 45) -> Path:
    """Fetch one table through the bulk export and return the path of its CSV.

    The export is asynchronous: the first call asks for a file, later calls report
    whether it is ready. Errors are re-raised WITHOUT the request URL, because the URL
    carries the key and a traceback on screen would print it.
    """
    dest.mkdir(parents=True, exist_ok=True)
    params = {"qopts.export": "true", "api_key": key}
    if table in ("SF1",):
        params["dimension"] = ",".join(SPAN_MONTHS)
    link = None
    for _ in range(max_polls):
        try:
            response = requests.get(API.format(table=table), params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise RuntimeError(f"{table}: request failed ({type(exc).__name__})") from None
        if response.status_code != 200:
            raise RuntimeError(f"{table}: HTTP {response.status_code} from Nasdaq Data Link")
        bulk = response.json().get("datatable_bulk_download", {}).get("file", {})
        if bulk.get("status", "").lower() == "fresh" and bulk.get("link"):
            link = bulk["link"]
            break
        time.sleep(poll_seconds)
    if link is None:
        raise RuntimeError(f"{table}: the export was not ready after {max_polls} polls")

    archive = dest / f"{table}.zip"
    try:
        with requests.get(link, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with archive.open("wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
    except requests.RequestException as exc:
        raise RuntimeError(f"{table}: download failed ({type(exc).__name__})") from None
    with zipfile.ZipFile(archive) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        z.extract(name, dest)
    return dest / name


# -- translation -------------------------------------------------------------------------

def ingest(con: duckdb.DuckDBPyConnection, tickers_csv: Path, sf1_csv: Path,
           sep_csv: Path) -> dict[str, int]:
    """Translate the three Sharadar files into the market schema. Replaces what is there.

    `con` must already carry MARKET_MIGRATIONS. Runs inside one transaction, so an
    interrupted build leaves the previous contents rather than half of each.
    """
    read = "read_csv_auto(?, header=true, sample_size=-1)"
    con.execute("BEGIN")
    try:
        for table in ("securities", "fundamentals", "prices", "ticker_history"):
            con.execute(f"DELETE FROM {table}")

        # Common stock only: the free universe is companies, not funds, warrants or
        # preferred shares. Sharadar's category says which is which.
        con.execute(
            f"""
            INSERT INTO securities (cik, ticker, name, sector, sic, exchange,
                                    first_seen, last_seen)
            SELECT DISTINCT permaticker, upper(ticker), name, sicindustry,
                   CAST(siccode AS VARCHAR), exchange,
                   TRY_CAST(firstpricedate AS DATE), TRY_CAST(lastpricedate AS DATE)
            FROM {read}
            WHERE "table" = 'SEP' AND category LIKE 'Domestic Common Stock%'
            """,
            [str(tickers_csv)],
        )

        con.execute(f"CREATE OR REPLACE TEMP TABLE _sf1 AS SELECT * FROM {read}",
                    [str(sf1_csv)])
        # permaticker comes from the securities just loaded; a ticker SF1 knows but the
        # price table does not is a company that cannot be traded here, so it is left out.
        common = """
            FROM _sf1 f JOIN securities s ON s.ticker = upper(f.ticker)
            WHERE f.dimension IN ('ARQ', 'ARY') AND TRY_CAST(f.datekey AS DATE) IS NOT NULL
        """
        # A column the export does not carry (older layouts, narrower subscriptions) is
        # skipped rather than failing the whole build; the metric is then simply absent.
        present = {r[0].lower() for r in con.execute("DESCRIBE _sf1").fetchall()}
        for metric, column in DURATION.items():
            if column not in present:
                continue
            sign = "-" if metric == "capex" else ""
            con.execute(
                f"""
                INSERT INTO fundamentals (cik, metric, value, period_start, period_end,
                                          filed, accession, xbrl_tag, tag_rank)
                SELECT s.cik, '{metric}', {sign}CAST(f.{column} AS DOUBLE),
                       CAST(CAST(f.reportperiod AS DATE)
                            - CASE f.dimension WHEN 'ARQ' THEN INTERVAL 3 MONTH
                                               ELSE INTERVAL 12 MONTH END
                            + INTERVAL 1 DAY AS DATE),
                       CAST(f.reportperiod AS DATE), CAST(f.datekey AS DATE),
                       'sharadar:' || f.ticker || ':' || f.dimension || ':' || f.reportperiod,
                       'sharadar:{column}', 0
                {common} AND f.{column} IS NOT NULL
                """
            )
        for metric, column in INSTANT.items():
            if column not in present:
                continue
            con.execute(
                f"""
                INSERT INTO fundamentals (cik, metric, value, period_start, period_end,
                                          filed, accession, xbrl_tag, tag_rank)
                SELECT s.cik, '{metric}', CAST(f.{column} AS DOUBLE), NULL,
                       CAST(f.reportperiod AS DATE), CAST(f.datekey AS DATE),
                       'sharadar:' || f.ticker || ':' || f.dimension || ':' || f.reportperiod,
                       'sharadar:{column}', 0
                {common} AND f.{column} IS NOT NULL
                """
            )
        # sharesbas is reported in the filer's own scale; sharefactor restores it.
        con.execute(
            f"""
            INSERT INTO fundamentals (cik, metric, value, period_start, period_end,
                                      filed, accession, xbrl_tag, tag_rank)
            SELECT s.cik, 'shares_outstanding',
                   CAST(f.sharesbas AS DOUBLE) * COALESCE(CAST(f.sharefactor AS DOUBLE), 1),
                   NULL, CAST(f.reportperiod AS DATE), CAST(f.datekey AS DATE),
                   'sharadar:' || f.ticker || ':' || f.dimension || ':' || f.reportperiod,
                   'sharadar:sharesbas', 0
            {common} AND f.sharesbas IS NOT NULL
            """
        )
        # The same period can arrive in both ARQ and ARY (a year-end balance sheet). One
        # copy per filing is enough; the point-in-time rules pick among filings.
        con.execute(
            """
            DELETE FROM fundamentals WHERE rowid IN (
                SELECT rowid FROM (
                    SELECT rowid, ROW_NUMBER() OVER (
                        PARTITION BY cik, metric, period_start, period_end, filed
                        ORDER BY accession) AS rn
                    FROM fundamentals) WHERE rn > 1)
            """
        )

        # SEP: open/high/low/close are split-adjusted, closeunadj is what traded,
        # closeadj adds dividends. That is exactly the free store's three bases.
        con.execute(
            f"""
            INSERT INTO prices (ticker, date, open, high, low, close, close_raw,
                                close_adj, volume)
            SELECT upper(p.ticker), CAST(p.date AS DATE), p.open, p.high, p.low, p.close,
                   p.closeunadj, p.closeadj, CAST(p.volume AS BIGINT)
            FROM {read} p
            WHERE upper(p.ticker) IN (SELECT ticker FROM securities)
            """,
            [str(sep_csv)],
        )
        history.refresh_price_windows(con, own_transaction=False)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return {
        table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("securities", "fundamentals", "prices", "ticker_history")
    }


# -- the source --------------------------------------------------------------------------

class PaidSource(FreeSource):
    """FreeSource's queries over the translated Sharadar store."""

    name = "paid (Sharadar via Nasdaq Data Link)"

    @property
    def survivorship_free(self) -> bool:
        """True when the store really holds companies that stopped trading, with prices.

        Measured rather than asserted: a store built from a partial export, or from the
        wrong table, would otherwise claim a correction it does not contain.
        """
        row = self.con.execute(
            "SELECT COUNT(*) FROM securities s WHERE s.last_seen < "
            "(SELECT MAX(date) FROM prices) - INTERVAL 30 DAY "
            "AND EXISTS (SELECT 1 FROM prices p WHERE p.ticker = s.ticker)"
        ).fetchone()
        return bool(row and row[0])


def delisted_count(con: duckdb.DuckDBPyConnection) -> int:
    """How many priced companies stopped trading before the newest bar — the number the
    paid source exists for, printed by the build script."""
    return con.execute(
        "SELECT COUNT(*) FROM securities WHERE last_seen < "
        "(SELECT MAX(date) FROM prices) - INTERVAL 30 DAY"
    ).fetchone()[0]
