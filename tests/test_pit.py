"""The rules that decide whether a backtest is honest.

Each test here corresponds to a way results get silently inflated:
  - seeing a filing before it was published (look-ahead)
  - using a restated figure nobody had at the time
  - double-counting revenue by adding overlapping XBRL spans
  - letting a company that stopped reporting stay in the screen forever
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from portfolio_lab.data import pit
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

D = dt.date


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = [
        # --- CIK 1: restates its FY2023 equity upward in a later filing ---
        (1, "book_value", 900.0, None, D(2023, 12, 31), D(2024, 2, 15), "a1", "StockholdersEquity", 0),
        (1, "book_value", 1400.0, None, D(2023, 12, 31), D(2024, 8, 20), "a2", "StockholdersEquity", 0),
        (1, "shares_outstanding", 100.0, None, D(2023, 12, 31), D(2024, 2, 15), "a1", "EntityCommonStockSharesOutstanding", 0),
        # --- CIK 2: four clean quarters, no annual figure ---
        (2, "net_income", 10.0, D(2023, 1, 1), D(2023, 3, 31), D(2023, 5, 1), "b1", "NetIncomeLoss", 0),
        (2, "net_income", 20.0, D(2023, 4, 1), D(2023, 6, 30), D(2023, 8, 1), "b2", "NetIncomeLoss", 0),
        (2, "net_income", 30.0, D(2023, 7, 1), D(2023, 9, 30), D(2023, 11, 1), "b3", "NetIncomeLoss", 0),
        (2, "net_income", 40.0, D(2023, 10, 1), D(2023, 12, 31), D(2024, 2, 1), "b4", "NetIncomeLoss", 0),
        # --- CIK 3: reports BOTH a quarter and the full year in one filing.
        #     Naively summing these rows gives 500; the truth is 400. ---
        (3, "revenue", 100.0, D(2023, 10, 1), D(2023, 12, 31), D(2024, 2, 1), "c1", "Revenues", 0),
        (3, "revenue", 400.0, D(2023, 1, 1), D(2023, 12, 31), D(2024, 2, 1), "c1", "Revenues", 0),
        # --- CIK 4: zombie. Last filed early 2019, nothing since. ---
        (4, "book_value", 2000.0, None, D(2018, 12, 31), D(2019, 3, 1), "d1", "StockholdersEquity", 0),
        # --- CIK 5: tags a literal 1 as its share count (seen in real SEC data) ---
        (5, "shares_outstanding", 1.0, None, D(2023, 12, 31), D(2024, 2, 1), "e1", "CommonStockSharesOutstanding", 1),
        # --- CIK 6: no instant share count at all, only a weighted average (a
        #     duration fact) — the Simon Property Group shape ---
        (6, "shares_outstanding", 324_018_022.0, D(2023, 10, 1), D(2023, 12, 31), D(2024, 2, 1), "f1", "WeightedAverageNumberOfDilutedSharesOutstanding", 2),
        # --- CIK 7: a zero from the preferred tag, the truth in the fallback tag ---
        (7, "shares_outstanding", 0.0, None, D(2023, 12, 31), D(2024, 2, 1), "g1", "CommonStockSharesOutstanding", 1),
        (7, "shares_outstanding", 65_645_000.0, D(2023, 10, 1), D(2023, 12, 31), D(2024, 2, 1), "g1", "WeightedAverageNumberOfDilutedSharesOutstanding", 2),
    ]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return c


def test_nothing_visible_before_it_was_filed(con):
    """The restatement lands 2024-08-20 and must be invisible the day before."""
    inst = pit.instant_values(con, D(2024, 8, 19))
    bv = inst[(inst.cik == 1) & (inst.metric == "book_value")]
    assert bv.value.iloc[0] == 900.0


def test_earliest_filing_beats_later_restatement(con):
    """Even long after the restatement, a point-in-time screen uses what was known then."""
    inst = pit.instant_values(con, D(2025, 1, 1))
    bv = inst[(inst.cik == 1) & (inst.metric == "book_value")]
    assert len(bv) == 1
    assert bv.value.iloc[0] == 900.0, "restated figure leaked into the point-in-time view"


def test_ttm_sums_four_contiguous_quarters(con):
    ttm = pit.ttm_values(con, D(2024, 3, 1))
    row = ttm[(ttm.cik == 2) & (ttm.metric == "net_income")]
    assert row.value.iloc[0] == pytest.approx(100.0)
    assert row.method.iloc[0] == "quarters"


def test_ttm_does_not_double_count_overlapping_spans(con):
    """A quarter nested inside its own fiscal year must not be added to it."""
    ttm = pit.ttm_values(con, D(2024, 3, 1))
    row = ttm[(ttm.cik == 3) & (ttm.metric == "revenue")]
    assert row.value.iloc[0] == pytest.approx(400.0), "overlapping spans were summed"
    assert row.method.iloc[0] == "annual"


def test_incomplete_quarters_do_not_produce_a_ttm(con):
    """Only three quarters are visible in August; no TTM may be invented from them."""
    ttm = pit.ttm_values(con, D(2023, 8, 15))
    assert ttm[(ttm.cik == 2) & (ttm.metric == "net_income")].empty


def test_ancient_facts_are_dropped_entirely(con):
    """A 2019 balance sheet cannot describe a company in 2026. It leaves the
    point-in-time view; the screener then counts the filer as stale rather than
    multiplying a seven-year-old figure by today's price."""
    inst = pit.instant_values(con, D(2026, 1, 1))
    assert inst[inst.cik == 4].empty
    # ...but the same fact is visible while it is still current
    fresh = pit.instant_values(con, D(2019, 6, 1))
    assert not fresh[fresh.cik == 4].empty


def test_implausible_share_count_is_rejected(con):
    """A listed company does not have one share."""
    shares = pit.share_counts(con, D(2024, 3, 1))
    assert shares[shares.cik == 5].empty


def test_share_count_falls_back_to_a_duration_fact(con):
    """Some filers tag no point-in-time share count at all."""
    shares = pit.share_counts(con, D(2024, 3, 1))
    row = shares[shares.cik == 6]
    assert row.value.iloc[0] == 324_018_022.0


def test_a_zero_from_the_preferred_tag_does_not_win(con):
    """The better-ranked tag loses when its value is not usable."""
    shares = pit.share_counts(con, D(2024, 3, 1))
    row = shares[shares.cik == 7]
    assert row.value.iloc[0] == 65_645_000.0


def test_wide_view_carries_age_and_method(con):
    wide = pit.fundamentals_wide(con, D(2024, 3, 1))
    assert "data_age_days" in wide.columns
    assert "net_income_method" in wide.columns
    assert set(wide.cik) >= {1, 2, 3}
