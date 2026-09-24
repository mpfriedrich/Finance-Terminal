"""Criterion 1(e), second half: a measurement that stays in a table protects nobody."""
import duckdb
import pandas as pd
import pytest

from portfolio_lab import coverage as cov
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    c.executemany(
        "INSERT INTO coverage (metric, year, n_ciks, pct_of_universe) VALUES (?,?,?,?)",
        [("book_value", 2026, 4000, 90.6),
         ("net_income", 2026, 4100, 93.0),
         ("revenue", 2026, 3000, 67.2),
         ("capex", 2026, 2700, 61.8),
         ("shares_outstanding", 2026, 4200, 94.5),
         # an older year that must not win
         ("revenue", 2020, 100, 5.0)],
    )
    return c


def test_only_the_most_recent_measurement_counts(con):
    assert cov.latest(con)["revenue"] == pytest.approx(0.672)


def test_a_filter_on_a_thin_field_is_flagged(con):
    thin = cov.thin_for(["price_to_sales"], con)
    assert list(thin.field) == ["revenue"]
    assert thin.coverage.iloc[0] == pytest.approx(0.672)


def test_a_filter_on_a_well_covered_field_is_not(con):
    assert cov.thin_for(["price_to_book"], con).empty


def test_a_filter_is_judged_by_every_field_it_needs(con):
    """Free cash flow needs operating cash flow AND capex; the thin one decides."""
    thin = cov.thin_for(["price_to_free_cash_flow"], con)
    assert "capex" in set(thin.field)


def test_the_worst_field_is_named_first(con):
    thin = cov.thin_for(["price_to_sales", "price_to_free_cash_flow"], con)
    assert thin.iloc[0].field == "capex"      # 61.8% is worse than 67.2%


def test_the_sentence_says_what_it_costs(con):
    text = cov.sentence(cov.thin_for(["price_to_sales"], con))
    assert "revenue" in text and "67%" in text
    assert "FAILS" in text        # the part people get wrong: not ignored, failed
    assert cov.sentence(pd.DataFrame()) == ""


def test_without_a_measurement_nothing_is_claimed(con):
    empty = duckdb.connect(":memory:")
    _apply(empty, MARKET_MIGRATIONS)
    assert cov.latest(empty) == {}
    assert cov.thin_for(["price_to_sales"], empty).empty


def test_every_mapped_input_is_a_field_the_ingest_measures():
    """A typo in the mapping would silently mean 'never thin'."""
    from portfolio_lab.data.sec import measure_coverage  # noqa: F401
    known = {
        "book_value", "net_income", "revenue", "operating_cash_flow", "capex", "cash",
        "total_assets", "total_liabilities", "current_assets", "current_liabilities",
        "long_term_debt", "current_debt", "shares_outstanding",
    }
    used = {f for fields in cov.INPUTS.values() for f in fields}
    assert used <= known, f"unknown fields in the mapping: {sorted(used - known)}"
