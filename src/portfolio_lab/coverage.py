"""Which fields are thin, and which filters therefore stand on thin ice.

Phase 1 measured how often each XBRL field is actually reported and wrote the answer to
the `coverage` table. That was half the criterion: a number in a table nobody reads
protects nobody. This module is the other half — it maps a filter back to the raw fields
it needs and says when one of them is missing for a large part of the universe.

Why it matters in practice: a filter on revenue does not quietly ignore companies whose
revenue is unknown. It FAILS them. Screening for "price to sales under 1" on a field
covered for two thirds of the universe silently removes the other third, and the result
looks like a screen over everything.
"""

from __future__ import annotations

import duckdb
import pandas as pd

THRESHOLD = 0.80

# Which stored fields each filterable metric is computed from. Only metrics that depend
# on a reported figure appear here; price and volume come from the price source, which is
# either there or the row is dropped by the floors.
INPUTS: dict[str, tuple[str, ...]] = {
    "market_cap": ("shares_outstanding",),
    "price_to_book": ("book_value", "shares_outstanding"),
    "price_to_earnings": ("net_income", "shares_outstanding"),
    "price_to_sales": ("revenue", "shares_outstanding"),
    "price_to_cash_flow": ("operating_cash_flow", "shares_outstanding"),
    "price_to_free_cash_flow": ("operating_cash_flow", "capex"),
    "price_to_cash": ("cash",),
    "price_to_graham": ("net_income", "book_value"),
    "price_to_intrinsic": ("operating_cash_flow", "capex"),
    "net_margin": ("net_income", "revenue"),
    "operating_margin_proxy": ("operating_cash_flow", "revenue"),
    "return_on_equity": ("net_income", "book_value"),
    "debt_to_equity": ("long_term_debt", "current_debt", "book_value"),
    "liabilities_to_equity": ("total_liabilities", "book_value"),
    "current_ratio": ("current_assets", "current_liabilities"),
    "book_value": ("book_value",),
    "net_income_ttm": ("net_income",),
    "revenue_ttm": ("revenue",),
    "operating_cash_flow_ttm": ("operating_cash_flow",),
    "free_cash_flow_ttm": ("operating_cash_flow", "capex"),
    "cash": ("cash",),
    "total_assets": ("total_assets",),
    "total_liabilities": ("total_liabilities",),
}


def latest(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Measured coverage per field, as a fraction, for the most recent year measured."""
    frame = con.execute(
        "SELECT metric, pct_of_universe FROM coverage "
        "WHERE year = (SELECT MAX(year) FROM coverage)"
    ).fetch_df()
    if frame.empty:
        return {}
    return {r.metric: float(r.pct_of_universe) / 100.0 for r in frame.itertuples()}


def thin_for(metrics, con: duckdb.DuckDBPyConnection,
             threshold: float = THRESHOLD) -> pd.DataFrame:
    """The under-covered fields that the given filters depend on.

    One row per (filter, field, coverage), worst first — so the caption can name the
    filter the person actually typed rather than an XBRL tag they have never seen.
    """
    measured = latest(con)
    if not measured:
        return pd.DataFrame(columns=["metric", "field", "coverage"])
    rows = []
    for metric in dict.fromkeys(metrics):
        for field in INPUTS.get(metric, ()):
            pct = measured.get(field)
            if pct is not None and pct < threshold:
                rows.append({"metric": metric, "field": field, "coverage": pct})
    return pd.DataFrame(rows).sort_values("coverage") if rows else pd.DataFrame(
        columns=["metric", "field", "coverage"])


def sentence(thin: pd.DataFrame) -> str:
    """One line for the screen, naming the filter and what it costs."""
    if thin.empty:
        return ""
    worst = thin.iloc[0]
    others = len(thin) - 1
    more = f" and {others} more field(s)" if others else ""
    return (
        f"Thin data: `{worst.field}` is reported by only {worst.coverage:.0%} of the "
        f"universe, and your filter on {worst.metric.replace('_', ' ')} needs it{more}. "
        "A company that never reported the field FAILS the filter rather than being "
        "ignored, so the screen is running over a smaller universe than it looks."
    )
