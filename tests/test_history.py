"""Phase 7: the historical universe, and the honesty of what it cannot recover.

The measurement matters more than the machinery here. A ticker_history that quietly
returns today's symbols for every company would make stage B look point-in-time while it
still ran on survivors — which is the exact failure this phase exists to prevent.
"""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab.data import history
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    # Two filers that stopped in 2015-2016 and one that is still going.
    facts = [
        (10, "book_value", 1.0, dt.date(2014, 3, 1)),
        (10, "book_value", 1.0, dt.date(2016, 3, 1)),
        (20, "book_value", 1.0, dt.date(2014, 4, 1)),
        (20, "book_value", 1.0, dt.date(2015, 4, 1)),
        (30, "book_value", 1.0, dt.date(2014, 5, 1)),
        (30, "book_value", 1.0, dt.date(2026, 5, 1)),
    ]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, filed, accession, xbrl_tag) "
        "VALUES (?,?,?,?,'acc','tag')", facts,
    )
    c.executemany(
        "INSERT INTO ticker_history (cik, ticker, valid_from, valid_to, source) "
        "VALUES (?,?,?,?,?)",
        [
            (10, "OLD", dt.date(2010, 1, 1), dt.date(2016, 6, 1), history.SOURCE_FILINGS),
            (30, "LIVE", dt.date(2010, 1, 1), None, history.SOURCE_FILINGS),
        ],
    )
    c.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES (?,?,?,?,?,1000)",
        [("OLD", dt.date(2015, 6, 1), 5.0, 5.0, 5.0),
         ("LIVE", dt.date(2026, 5, 1), 5.0, 5.0, 5.0)],
    )
    return c


def test_a_symbol_is_only_returned_inside_its_validity_window(con):
    assert history.symbol_at(con, 10, dt.date(2015, 1, 1)) == "OLD"
    assert history.symbol_at(con, 10, dt.date(2020, 1, 1)) is None


def test_an_open_ended_window_still_applies_today(con):
    assert history.symbol_at(con, 30, dt.date(2026, 1, 1)) == "LIVE"


def test_a_filer_with_no_recorded_symbol_returns_nothing_rather_than_a_guess(con):
    """CIK 20 filed and vanished. The honest answer is 'unknown', not today's nearest
    match — a guessed symbol would silently put a survivor in a dead company's place."""
    assert history.symbol_at(con, 20, dt.date(2015, 1, 1)) is None


def test_the_sample_follows_the_plans_rule(con):
    """Filed around 2014, nothing after 2019 — and the still-active filer is excluded."""
    sample = history.vanished_sample(con, size=10)
    assert set(sample.cik) == {10, 20}


def test_the_sample_is_the_same_every_run(con):
    """Drawn by a hash of the CIK, so nobody can be accused of picking the convenient
    companies, and a later run can be compared with an earlier one."""
    first = history.vanished_sample(con, size=10)
    second = history.vanished_sample(con, size=10)
    assert list(first.cik) == list(second.cik)


def test_the_recovery_rate_counts_symbols_and_prices_separately(con):
    """A symbol without prices cannot be valued, so it cannot enter a backtest either.
    The two fractions have to be reported apart."""
    rec = history.recovery_rate(con, size=10)
    assert rec["n"] == 2
    assert rec["with_ticker"] == 1      # OLD is recorded, CIK 20 is not
    assert rec["with_prices"] == 1
    assert rec["ticker_rate"] == 0.5


def test_below_half_the_universe_is_declared_survivor_based():
    """The plan's decision point. Under 50% the app says stage B runs on survivors
    instead of implying a correction that did not happen."""
    assert history.survivor_based({"price_rate": 0.0})
    assert history.survivor_based({"price_rate": 0.49})
    assert not history.survivor_based({"price_rate": 0.51})
    assert history.survivor_based({"price_rate": None})


def test_an_empty_sample_is_not_reported_as_a_perfect_score():
    empty = duckdb.connect(":memory:")
    _apply(empty, MARKET_MIGRATIONS)
    rec = history.recovery_rate(empty)
    assert rec["n"] == 0
    assert rec["ticker_rate"] is None
    assert history.survivor_based(rec)
