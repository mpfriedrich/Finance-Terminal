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
from collections.abc import Iterator
from pathlib import Path

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

# IFRS equivalents, for the foreign private issuers that file 20-F under IFRS (added
# 2026-09-24). Appended AFTER the US-GAAP candidates: a filer reports in one taxonomy,
# so the ranks never compete. Chosen from the IFRS taxonomy by concept, NOT yet measured
# against the archive — `measure_coverage()` is the check, per metric and per year.
IFRS_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "book_value": [('ifrs-full', 'EquityAttributableToOwnersOfParent'), ('ifrs-full', 'Equity')],
    "revenue": [('ifrs-full', 'Revenue'), ('ifrs-full', 'RevenueFromContractsWithCustomers')],
    "net_income": [('ifrs-full', 'ProfitLossAttributableToOwnersOfParent'), ('ifrs-full', 'ProfitLoss')],
    "operating_cash_flow": [('ifrs-full', 'CashFlowsFromUsedInOperatingActivities')],
    "total_assets": [('ifrs-full', 'Assets')],
    "total_liabilities": [('ifrs-full', 'Liabilities')],
    "long_term_debt": [('ifrs-full', 'NoncurrentPortionOfNoncurrentBorrowings'), ('ifrs-full', 'LongtermBorrowings')],
    "current_debt": [('ifrs-full', 'CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings'), ('ifrs-full', 'CurrentPortionOfLongtermBorrowings'), ('ifrs-full', 'ShorttermBorrowings')],
    "cash": [('ifrs-full', 'CashAndCashEquivalents')],
    "current_assets": [('ifrs-full', 'CurrentAssets')],
    "current_liabilities": [('ifrs-full', 'CurrentLiabilities')],
    "capex": [('ifrs-full', 'PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'), ('ifrs-full', 'PurchaseOfPropertyPlantAndEquipment')],
}
for _metric, _candidates in IFRS_CANDIDATES.items():
    TAG_MAP[_metric] = TAG_MAP[_metric] + [(tax, tag, "USD") for tax, tag in _candidates]

# In TAG_MAP, "USD" means "an amount of money": the unit actually accepted is any ISO
# currency code. Until 2026-09-24 only USD was read, so every filer reporting in euros,
# yen or rupees had no fundamentals at all, US-GAAP 20-F filers included. The amounts
# stay in the reporting currency here; pit.py converts them at the rate of the screen
# date, which is the only rate an investor on that date could have used.
MONEY = "USD"


def _unit_kind(unit: str) -> str | None:
    """'shares', 'money' (any three-letter currency code), or None (per-share etc.)."""
    if unit == "shares":
        return "shares"
    if len(unit) == 3 and unit.isalpha() and unit.isupper():
        return "money"
    return None


# Income-statement lines for enterprise-value multiples, returns on capital and the cost
# of debt (added 2026-09-24): EBIT and D&A make EBITDA; tax and pre-tax income give the
# effective tax rate; interest expense gives the cost of debt. Same caveat as above —
# chosen by concept, measured by measure_coverage(), not assumed complete.
_INCOME_LINES: dict[str, list[tuple[str, str]]] = {
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ],
    "depreciation_amortization": [
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "DepreciationAndAmortization"),
        ("us-gaap", "DepreciationAmortizationAndAccretionNet"),
        ("ifrs-full", "DepreciationAndAmortisationExpense"),
        ("ifrs-full", "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLoss"
                      "RecognisedInProfitOrLoss"),
    ],
    "interest_expense": [
        ("us-gaap", "InterestExpense"),
        ("us-gaap", "InterestExpenseNonoperating"),
        ("us-gaap", "InterestExpenseDebt"),
        ("ifrs-full", "FinanceCosts"),
        ("ifrs-full", "InterestExpense"),
    ],
    "income_tax": [
        ("us-gaap", "IncomeTaxExpenseBenefit"),
        ("ifrs-full", "IncomeTaxExpenseContinuingOperations"),
    ],
    "pretax_income": [
        ("us-gaap", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItems"
                    "NoncontrollingInterest"),
        ("us-gaap", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterest"
                    "AndIncomeLossFromEquityMethodInvestments"),
        ("ifrs-full", "ProfitLossBeforeTax"),
    ],
    "gross_profit": [
        ("us-gaap", "GrossProfit"),
        ("ifrs-full", "GrossProfit"),
    ],
}
for _metric, _candidates in _INCOME_LINES.items():
    TAG_MAP[_metric] = [(tax, tag, MONEY) for tax, tag in _candidates]

# Reverse index so one pass over a filer's facts can classify every tag it meets.
_TAG_LOOKUP: dict[tuple[str, str, str], tuple[str, int]] = {
    (tax, tag, "money" if unit == MONEY else unit): (metric, rank)
    for metric, candidates in TAG_MAP.items()
    for rank, (tax, tag, unit) in enumerate(candidates)
}

# Metrics reported over a span (income statement, cash flow) vs at an instant (balance
# sheet, share counts). Spans get summed to trailing-twelve-months at query time, which is
# why period_start has to survive ingestion.
DURATION_METRICS = {"revenue", "net_income", "operating_cash_flow", "capex",
                    "operating_income", "depreciation_amortization", "interest_expense",
                    "income_tax", "pretax_income", "gross_profit"}


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

    # The currency is chosen PER FILING, not over the filer's whole history (audit
    # 24.10.2026 F10). The whole-history majority dropped every fact in any other
    # currency, so a filer that later switched from USD to EUR lost its earlier USD
    # facts and a 2020 screen saw less than was known in 2020. Measured on the SEC
    # archive of 21.09.2026: 154 of 16,978 filers switch currency between filings, and
    # 17,466 monetary entries were lost that way. Within one filing the majority still
    # decides, which drops convenience translations (31,450 entries).
    currencies = filing_currencies(facts)

    # (metric, period_start, period_end, value, currency)
    #   -> (filed, accession, tag, rank, form)
    best: dict[tuple, tuple] = {}

    for taxonomy, tags in facts.items():
        for tag, payload in tags.items():
            for unit, entries in (payload.get("units") or {}).items():
                kind = _unit_kind(unit)
                hit = _TAG_LOOKUP.get((taxonomy, tag, kind)) if kind else None
                if hit is None:
                    continue
                metric, rank = hit
                for e in entries:
                    if e.get("form") not in ACCEPTED_FORMS:
                        continue
                    # One currency per filing. Some foreign filers also tag convenience
                    # translations of the same figure in USD; keeping both would put two
                    # values for one period side by side, in different units.
                    if kind == "money" and unit != currencies.get(e.get("accn") or ""):
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
                    key = (metric, start, end, float(val),
                           None if kind != "money" else unit)
                    prev = best.get(key)
                    if prev is None or filed < prev[0]:
                        best[key] = (filed, e.get("accn") or "", tag, rank,
                                     e.get("form"))

    # Where two candidate tags produced the same period, keep the higher-priority tag.
    by_period: dict[tuple, tuple] = {}
    for (metric, start, end, val, unit), (filed, accn, tag, rank, form) in best.items():
        k = (metric, start, end, filed)
        prev = by_period.get(k)
        if prev is None or rank < prev[3]:
            by_period[k] = (val, accn, tag, rank, form, unit)

    return [
        (int(cik), metric, val, start, end, filed, accn, tag, rank, unit, form)
        for (metric, start, end, filed), (val, accn, tag, rank, form, unit)
        in by_period.items()
    ]


def filing_currencies(facts: dict) -> dict[str, str]:
    """accession -> the currency most of that filing's tracked monetary facts use
    (ties broken alphabetically, so the choice is stable)."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for taxonomy, tags in facts.items():
        for tag, payload in tags.items():
            for unit, entries in (payload.get("units") or {}).items():
                if _unit_kind(unit) != "money" or (taxonomy, tag, "money") not in _TAG_LOOKUP:
                    continue
                for e in entries:
                    if e.get("form") in ACCEPTED_FORMS:
                        counts[e.get("accn") or ""][unit] += 1
    return {accn: max(sorted(c), key=lambda u: c[u]) for accn, c in counts.items()}


def reporting_currency(facts: dict) -> str | None:
    """The currency a filer reports its amounts in: the one most of its tracked
    monetary facts use. None when it tags no monetary fact we read."""
    counts: dict[str, int] = defaultdict(int)
    for taxonomy, tags in facts.items():
        for tag, payload in tags.items():
            for unit, entries in (payload.get("units") or {}).items():
                if _unit_kind(unit) == "money" and (taxonomy, tag, "money") in _TAG_LOOKUP:
                    counts[unit] += len(entries)
    if not counts:
        return None
    return max(sorted(counts), key=lambda c: counts[c])


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
        "currency",
        "form",
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
            "(cik, metric, value, period_start, period_end, filed, accession, xbrl_tag, "
            "tag_rank, currency, form) "
            "SELECT cik, metric, value, period_start, period_end, filed, accession, "
            "xbrl_tag, tag_rank, currency, form FROM _incoming"
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
