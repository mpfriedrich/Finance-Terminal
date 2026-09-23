"""Phase 1 proof: the interface holds, and the fixture's known traps behave."""
import datetime as dt

from portfolio_lab.data.base import DataSource
from portfolio_lab.data.fixture import FixtureSource


def test_fixture_satisfies_protocol():
    assert isinstance(FixtureSource(), DataSource)


def test_universe_excludes_delisted_after_its_last_day():
    src = FixtureSource()
    assert "DDD" in set(src.universe(dt.date(2023, 1, 31)).ticker)
    assert "DDD" not in set(src.universe(dt.date(2023, 12, 31)).ticker)


def test_no_fact_visible_before_it_was_filed():
    src = FixtureSource()
    f = src.fundamentals(dt.date(2024, 2, 1))
    assert (f.filed <= dt.date(2024, 2, 1)).all()
    assert 1001 not in set(f.cik)  # its first 2023 filing lands 2024-02-15


def test_earliest_filing_wins_over_later_restatement():
    src = FixtureSource()
    f = src.fundamentals(dt.date(2025, 1, 1))
    bv = f[(f.cik == 1001) & (f.metric == "book_value")]
    assert len(bv) == 1
    assert bv.value.iloc[0] == 900.0  # not the 1400 restatement


def test_open_and_close_differ_so_fill_price_is_testable():
    src = FixtureSource()
    p = src.prices(["AAA"], dt.date(2023, 1, 1), dt.date(2023, 1, 31))
    assert len(p) > 10
    assert (p.open != p.close).all()
