"""Ingest SEC XBRL company facts into the local store.

Reads members straight out of companyfacts.zip without unpacking it — the archive is
~1.4 GB compressed and several times that as loose JSON, and we need perhaps 0.1% of it.

The `filed` date is the point of this whole module. Every fact carries the date it became
public, which is what lets a backtest screen 2019 using only what a 2019 investor could
have known. Without it, every result is fiction.

Tag choices here come from experience, NOT from a verified specification (PLAN.md A20).
`measure_coverage()` exists precisely so that assumption gets tested against reality
rather than trusted: it reports, per metric and per year, how many filers we actually
resolved. A metric that comes back thin is a metric the UI must flag or drop.
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterator

import duckdb
import pandas as pd

# Periodic reports only. Facts also appear in 8-Ks and S-1s, where period conventions are
# inconsistent enough to do more harm than good.
ACCEPTED_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A"}

# metric -> ordered candidate (taxonomy, tag, unit). First hit per (cik, period) wins.
# Order matters: the more specific/modern tag comes first, the broader fallback after.
TAG_MAP: dict[str, list[tuple[str, str, str]]] = {
    "book_value": [
        ("us-gaap", "StockholdersEquity", "USD"),
        (
            "us-gaap",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "USD",
        ),
    ],
    "shares_outstanding": [
        ("dei", "EntityCommonStockSharesOutstanding", "shares"),
        ("us-gaap", "CommonStockSharesOutstanding", "shares"),
        ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
    ],
    # MEASURED 2026-09-21, not assumed: across 20,390 filers `Revenues` resolves for
    # 9,050 CIKs, the ASC-606 tag for only 4,867 (it did not exist before that standard),
    # `SalesRevenueNet` for 3,576. `Revenues` is also the TOTAL concept, where the ASC-606
    # tag covers only revenue from customer contracts — so for a price-to-sales ratio the
    # broad tag is both better covered and more correct. Ordered accordingly.
    "revenue": [
        ("us-gaap", "Revenues", "USD"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
        ("us-gaap", "SalesRevenueNet", "USD"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss", "USD"),
        ("us-gaap", "ProfitLoss", "USD"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"),
        (
            "us-gaap",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "USD",
        ),
    ],
    # --- balance sheet, added 2026-09-21 for debt, margin and cash metrics ---
    "total_assets": [("us-gaap", "Assets", "USD")],
    "total_liabilities": [("us-gaap", "Liabilities", "USD")],
    # Interest-bearing debt, long and short. Split because many filers tag only one side,
    # and summing what exists beats demanding both.
    "long_term_debt": [
        ("us-gaap", "LongTermDebtNoncurrent", "USD"),
        ("us-gaap", "LongTermDebt", "USD"),
    ],
    "current_debt": [
        ("us-gaap", "DebtCurrent", "USD"),
        ("us-gaap", "LongTermDebtCurrent", "USD"),
        ("us-gaap", "ShortTermBorrowings", "USD"),
    ],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue", "USD"),
        ("us-gaap",
         "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "USD"),
    ],
    "current_assets": [("us-gaap", "AssetsCurrent", "USD")],
    "current_liabilities": [("us-gaap", "LiabilitiesCurrent", "USD")],
    # Capital expenditure, so free cash flow is operating cash flow MINUS what the
    # business had to spend to stay standing. Valuing a company on operating cash flow
    # alone flatters anything capital-hungry — a steel mill and a software firm are not
    # comparable until capex is taken out.
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment", "USD"),
        ("us-gaap", "PaymentsToAcquireProductiveAssets", "USD"),
    ],
}

# Reverse index so one pass over a filer's facts can classify every tag it meets.
_TAG_LOOKUP: dict[tuple[str, str, str], tuple[str, int]] = {
    (tax, tag, unit): (metric, rank)
    for metric, candidates in TAG_MAP.items()
    for rank, (tax, tag, unit) in enumerate(candidates)
}

# Metrics reported over a span (income statement, cash flow) vs at an instant (balance
# sheet, share counts). Spans get summed to trailing-twelve-months at query time, which is
# why period_start has to survive ingestion.
DURATION_METRICS = {"revenue", "net_income", "operating_cash_flow", "capex"}


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def iter_company_facts(zip_path: Path, limit: int | None = None) -> Iterator[dict]:
    """Yield one parsed company-facts document at a time, straight from the archive."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json") and "CIK" in n]
        names.sort()
        if limit is not None:
            names = names[:limit]
        for name in names:
            try:
                with zf.open(name) as fh:
                    yield json.load(fh)
            except (json.JSONDecodeError, KeyError, zipfile.BadZipFile):
                continue


def extract_rows(doc: dict) -> list[tuple]:
    """Pull the facts we care about out of one company document.

    Collapses the comparative re-reporting problem: the same figure reappears in every
    later filing, so we keep, per (metric, period, value), only the EARLIEST filing that
    disclosed it. A genuine restatement has a different value and survives as its own row,
    which is exactly what the point-in-time rule needs to distinguish.
    """
    cik = doc.get("cik")
    facts = doc.get("facts") or {}
    if cik is None or not facts:
        return []

    # (metric, period_start, period_end, value) -> (filed, accession, tag, rank)
    best: dict[tuple, tuple] = {}

    for taxonomy, tags in facts.items():
        for tag, payload in tags.items():
            for unit, entries in (payload.get("units") or {}).items():
                hit = _TAG_LOOKUP.get((taxonomy, tag, unit))
                if hit is None:
                    continue
                metric, rank = hit
                for e in entries:
                    if e.get("form") not in ACCEPTED_FORMS:
                        continue
                    filed = _parse_date(e.get("filed"))
                    end = _parse_date(e.get("end"))
                    if filed is None or end is None:
                        continue
                    val = e.get("val")
                    if val is None:
                        continue
                    start = _parse_date(e.get("start"))
                    if metric in DURATION_METRICS and start is None:
                        continue
                    key = (metric, start, end, float(val))
                    prev = best.get(key)
                    if prev is None or filed < prev[0]:
                        best[key] = (filed, e.get("accn") or "", tag, rank)

    # Where two candidate tags produced the same period, keep the higher-priority tag.
    by_period: dict[tuple, tuple] = {}
    for (metric, start, end, val), (filed, accn, tag, rank) in best.items():
        k = (metric, start, end, filed)
        prev = by_period.get(k)
        if prev is None or rank < prev[-1]:
            by_period[k] = (val, accn, tag, rank)

    return [
        (int(cik), metric, val, start, end, filed, accn, tag, rank)
        for (metric, start, end, filed), (val, accn, tag, rank) in by_period.items()
    ]


def ingest(
    zip_path: Path,
    con: duckdb.DuckDBPyConnection,
    limit: int | None = None,
    batch_size: int = 300_000,
    progress_every: int = 2000,
) -> dict[str, int]:
    """Load the archive into `fundamentals`. Returns simple counters."""
    cols = [
        "cik",
        "metric",
        "value",
        "period_start",
        "period_end",
        "filed",
        "accession",
        "xbrl_tag",
        "tag_rank",
    ]
    con.execute("DELETE FROM fundamentals;")

    buffer: list[tuple] = []
    counts = {"companies": 0, "rows": 0, "skipped": 0}

    def flush() -> None:
        if not buffer:
            return
        frame = pd.DataFrame(buffer, columns=cols)
        con.register("_incoming", frame)
        con.execute(
            "INSERT INTO fundamentals "
            "(cik, metric, value, period_start, period_end, filed, accession, xbrl_tag, tag_rank) "
            "SELECT cik, metric, value, period_start, period_end, filed, accession, xbrl_tag, tag_rank "
            "FROM _incoming"
        )
        con.unregister("_incoming")
        buffer.clear()

    for doc in iter_company_facts(zip_path, limit=limit):
        rows = extract_rows(doc)
        counts["companies"] += 1
        if not rows:
            counts["skipped"] += 1
        buffer.extend(rows)
        counts["rows"] += len(rows)
        if len(buffer) >= batch_size:
            flush()
        if progress_every and counts["companies"] % progress_every == 0:
            print(
                f"  {counts['companies']:>6} companies, {counts['rows']:>9} rows",
                flush=True,
            )

    flush()
    return counts


def measure_coverage(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """How many filers actually resolve each metric, per year.

    This is the evidence for PLAN.md Phase 1(d)/(e): it turns "XBRL starts around 2009-2011"
    and "these are probably the right tags" from claims into measurements.
    """
    df = con.execute(
        """
        SELECT metric,
               CAST(EXTRACT(year FROM filed) AS INTEGER) AS year,
               COUNT(DISTINCT cik) AS n_ciks
        FROM fundamentals
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetch_df()
    if df.empty:
        return df
    totals = (
        con.execute(
            """
            SELECT CAST(EXTRACT(year FROM filed) AS INTEGER) AS year,
                   COUNT(DISTINCT cik) AS total_ciks
            FROM fundamentals GROUP BY 1
            """
        )
        .fetch_df()
        .set_index("year")["total_ciks"]
    )
    df["pct_of_universe"] = df.apply(
        lambda r: 100.0 * r.n_ciks / totals.get(r.year, 1), axis=1
    ).round(1)

    con.execute("DELETE FROM coverage;")
    con.register("_cov", df[["metric", "year", "n_ciks", "pct_of_universe"]])
    con.execute("INSERT INTO coverage SELECT metric, year, n_ciks, pct_of_universe FROM _cov")
    con.unregister("_cov")
    return df


def tag_usage(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Which of the candidate tags actually carried the data — checks A20 directly."""
    return con.execute(
        """
        SELECT metric, xbrl_tag, COUNT(DISTINCT cik) AS n_ciks, COUNT(*) AS n_rows
        FROM fundamentals
        GROUP BY 1, 2
        ORDER BY metric, n_ciks DESC
        """
    ).fetch_df()
