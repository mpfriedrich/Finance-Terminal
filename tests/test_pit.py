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
import pandas as pd
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


@pytest.fixture()
def shifted():
    """Restatements that move period_start by a few days — the shape the independent
    review found in 32,463 groups of the real data (PLAN.md, Gegenprüfung 25.09.2026).
    Partitioning on the exact period_start put each version in its own partition, so the
    restatement survived as "the earliest filing of its own period"."""
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = [
        # --- CIK 8: FY2023 revenue 500, restated to 800 with a start two days earlier ---
        (8, "revenue", 500.0, D(2023, 1, 1), D(2023, 12, 31), D(2024, 2, 15), "h1", "Revenues", 0),
        (8, "revenue", 800.0, D(2022, 12, 30), D(2023, 12, 31), D(2024, 8, 20), "h2", "Revenues", 0),
        # --- CIK 9: four quarters; Q4 restated from 40 to 90 with a shifted start ---
        (9, "net_income", 10.0, D(2023, 1, 1), D(2023, 3, 31), D(2023, 5, 1), "i1", "NetIncomeLoss", 0),
        (9, "net_income", 20.0, D(2023, 4, 1), D(2023, 6, 30), D(2023, 8, 1), "i2", "NetIncomeLoss", 0),
        (9, "net_income", 30.0, D(2023, 7, 1), D(2023, 9, 30), D(2023, 11, 1), "i3", "NetIncomeLoss", 0),
        (9, "net_income", 40.0, D(2023, 10, 1), D(2023, 12, 31), D(2024, 2, 1), "i4", "NetIncomeLoss", 0),
        (9, "net_income", 90.0, D(2023, 9, 29), D(2023, 12, 31), D(2024, 9, 1), "i5", "NetIncomeLoss", 0),
        # --- CIK 10: a quarter and a nine-month YTD span ending on the same day are
        #     different periods, and must not be merged by the fix ---
        (10, "revenue", 30.0, D(2023, 7, 1), D(2023, 9, 30), D(2023, 11, 1), "j1", "Revenues", 0),
        (10, "revenue", 75.0, D(2023, 1, 1), D(2023, 9, 30), D(2023, 11, 1), "j1", "Revenues", 0),
    ]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return c


def test_restatement_with_shifted_start_does_not_displace_the_original(shifted):
    """R13: a restatement stays invisible as a replacement even when it moves the
    period's start date by a couple of days."""
    ttm = pit.ttm_values(shifted, D(2025, 1, 1))
    row = ttm[(ttm.cik == 8) & (ttm.metric == "revenue")]
    assert row.value.iloc[0] == pytest.approx(500.0), "shifted restatement leaked"


def test_restated_quarter_with_shifted_start_does_not_displace_the_original(shifted):
    ttm = pit.ttm_values(shifted, D(2025, 1, 1))
    row = ttm[(ttm.cik == 9) & (ttm.metric == "net_income")]
    assert row.value.iloc[0] == pytest.approx(100.0), "restated quarter leaked into TTM"


def test_spans_of_different_length_ending_together_stay_separate(shifted):
    vis = pit._visible(shifted, D(2025, 1, 1))
    assert sorted(vis[vis.cik == 10].value) == [30.0, 75.0]


def test_a_restatement_under_a_better_tag_does_not_displace_the_original():
    """R13 across tags. The original 10-K tags equity under the rank-1 fallback tag; the
    10-K/A half a year later restates it under the preferred rank-0 tag, with a new share
    count. Earliest-filing-per-TAG let the amendment win, because each tag had its own
    'first filing' and the better tag was preferred afterwards (independent review,
    24.09.2026)."""
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = [
        (11, "book_value", 900.0, None, D(2023, 12, 31), D(2024, 2, 15), "k1",
         "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", 1),
        (11, "book_value", 1400.0, None, D(2023, 12, 31), D(2024, 8, 20), "k2",
         "StockholdersEquity", 0),
        (11, "shares_outstanding", 100_000_000.0, None, D(2023, 12, 31), D(2024, 2, 15), "k1",
         "WeightedAverageNumberOfDilutedSharesOutstanding", 2),
        (11, "shares_outstanding", 50_000_000.0, None, D(2023, 12, 31), D(2024, 8, 20), "k2",
         "EntityCommonStockSharesOutstanding", 0),
    ]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    later = D(2024, 9, 1)
    inst = pit.instant_values(c, later)
    assert inst[inst.metric == "book_value"].value.iloc[0] == 900.0, "amendment leaked"
    shares = pit.share_counts(c, later)
    assert shares.value.iloc[0] == 100_000_000.0, "amended share count leaked"


def test_an_amendment_does_not_become_the_first_filing_once_the_original_ages_out():
    """R13 against the age limit. A delinquent filer's FY2023 original (filed Feb 2024)
    passes the 400-day limit; a 10-K/A for the same period arrives in May 2025. Picking
    the earliest filing AFTER the age filter made the amendment the 'first' one — with
    a fresh filing date that also made a stale filer look current (third independent
    review, 24.09.2026)."""
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = [
        (12, "book_value", 900.0, None, D(2023, 12, 31), D(2024, 2, 15), "m1",
         "StockholdersEquity", 0),
        (12, "shares_outstanding", 100_000_000.0, None, D(2023, 12, 31), D(2024, 2, 15),
         "m1", "EntityCommonStockSharesOutstanding", 0),
        (12, "book_value", 1400.0, None, D(2023, 12, 31), D(2025, 5, 20), "m2",
         "StockholdersEquity", 0),
        (12, "shares_outstanding", 50_000_000.0, None, D(2023, 12, 31), D(2025, 5, 20),
         "m2", "EntityCommonStockSharesOutstanding", 0),
    ]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    before = D(2025, 3, 1)
    assert pit.instant_values(c, before).value.iloc[0] == 900.0
    assert pit.share_counts(c, before).value.iloc[0] == 100_000_000.0
    after = D(2025, 6, 1)
    assert pit.instant_values(c, after).empty, "the amendment stood in for an aged-out original"
    assert pit.share_counts(c, after).empty, "the amended share count stood in"


# --- foreign currencies (24.09.2026) ---------------------------------------------------

@pytest.fixture()
def fx_store():
    """CIK 20 reports in euros, CIK 21 in a currency with no stored rate, CIK 22 in USD.
    EURUSD is 1.10 up to 1 March 2024 and 2.00 afterwards."""
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = [
        (20, "book_value", 1_000.0, None, D(2023, 12, 31), D(2024, 2, 15), "x", "Equity", 0, "EUR"),
        (20, "shares_outstanding", 100_000_000.0, None, D(2023, 12, 31), D(2024, 2, 15), "x",
         "EntityCommonStockSharesOutstanding", 0, None),
        (21, "book_value", 1_000.0, None, D(2023, 12, 31), D(2024, 2, 15), "y", "Equity", 0, "XYZ"),
        (22, "book_value", 1_000.0, None, D(2023, 12, 31), D(2024, 2, 15), "z",
         "StockholdersEquity", 0, "USD"),
    ]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank, currency) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    fx = [("EURUSD=X", d, 1.10 if d <= D(2024, 3, 1) else 2.00)
          for d in pd.bdate_range("2024-01-02", "2024-06-28").date]
    c.executemany("INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
                  "VALUES (?, ?, ?, ?, ?, 0)", [(t, d, r, r, r) for t, d, r in fx])
    return c


def test_euro_amounts_are_converted_at_the_rate_of_the_screen_date(fx_store):
    wide = pit.fundamentals_wide(fx_store, D(2024, 2, 20)).set_index("cik")
    assert wide.at[20, "book_value"] == pytest.approx(1_100.0)
    assert wide.at[20, "reporting_currency"] == "EUR"
    assert wide.at[22, "book_value"] == pytest.approx(1_000.0)
    assert wide.at[20, "shares_outstanding"] == 100_000_000.0, "shares are not money"


def test_a_later_exchange_rate_never_reaches_an_earlier_screen(fx_store):
    before = pit.fundamentals_wide(fx_store, D(2024, 3, 1)).set_index("cik")
    after = pit.fundamentals_wide(fx_store, D(2024, 3, 15)).set_index("cik")
    assert before.at[20, "book_value"] == pytest.approx(1_100.0)
    assert after.at[20, "book_value"] == pytest.approx(2_000.0)


def test_a_currency_without_a_rate_is_flagged_and_keeps_no_amounts(fx_store):
    wide = pit.fundamentals_wide(fx_store, D(2024, 2, 20)).set_index("cik")
    assert bool(wide.at[21, "fx_missing"]) and pd.isna(wide.at[21, "book_value"])
    assert not bool(wide.at[20, "fx_missing"])


def test_a_stale_rate_is_not_todays_rate(fx_store):
    """The stored rates end on 28 June; a screen in late July has no current rate."""
    wide = pit.fundamentals_wide(fx_store, D(2024, 7, 25)).set_index("cik")
    if 20 in wide.index:
        assert bool(wide.at[20, "fx_missing"])


def test_each_amount_converts_in_its_own_currency():
    """A filer switched to EUR for its balance sheet while its newest annual revenue is
    still the USD figure of the previous year. The revenue used to be multiplied by the
    euro rate because the filer's latest currency was applied to every amount (audit
    24.10.2026 F10)."""
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank, currency) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(30, "revenue", 500.0, D(2023, 1, 1), D(2023, 12, 31), D(2024, 2, 15), "u",
          "Revenues", 0, "USD"),
         (30, "book_value", 1_000.0, None, D(2024, 3, 31), D(2024, 5, 10), "e", "Equity",
          0, "EUR")])
    c.executemany("INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
                  "VALUES ('EURUSD=X', ?, 2.0, 2.0, 2.0, 0)",
                  [(d,) for d in pd.bdate_range("2024-06-03", "2024-06-28").date])
    wide = pit.fundamentals_wide(c, D(2024, 6, 28)).set_index("cik")
    assert wide.at[30, "revenue_ttm"] == pytest.approx(500.0)
    assert wide.at[30, "book_value"] == pytest.approx(2_000.0)
    assert wide.at[30, "reporting_currency"] == "EUR"
    assert not bool(wide.at[30, "fx_missing"])


def test_four_quarters_in_two_currencies_are_not_one_ttm_figure():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    quarters = [(D(2023, 1, 1), D(2023, 3, 31), "USD"), (D(2023, 4, 1), D(2023, 6, 30), "USD"),
                (D(2023, 7, 1), D(2023, 9, 30), "EUR"), (D(2023, 10, 1), D(2023, 12, 31), "EUR")]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank, currency) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(31, "revenue", 100.0, s, e, e + dt.timedelta(days=40), "q", "Revenues", 0, cur)
         for s, e, cur in quarters])
    assert pit.ttm_values(c, D(2024, 3, 1)).empty
