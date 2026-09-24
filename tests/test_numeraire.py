"""Pricing in gold instead of dollars: the unit changes, the arithmetic must not."""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import backtest as bt
from portfolio_lab import numeraire as nm
from portfolio_lab.config import CostModel
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply
from tests.test_backtest import synthetic_market


def gold(values: dict) -> pd.Series:
    return pd.Series(values, dtype=float)


def test_a_series_is_divided_by_the_gold_price_of_the_same_day():
    dates = [dt.date(2024, 1, d) for d in (1, 2, 3)]
    usd = pd.Series([2000.0, 2200.0, 2400.0], index=dates)
    ounces = nm.convert(usd, gold(dict(zip(dates, [2000.0, 2000.0, 2000.0]))))
    assert list(ounces) == [1.0, 1.1, 1.2]


def test_gold_is_forward_filled_onto_the_series_own_calendar():
    """Metals and equities do not keep identical calendars, and a missing metal bar must
    not delete a stock's day."""
    dates = [dt.date(2024, 1, d) for d in (1, 2, 3)]
    usd = pd.Series([100.0, 100.0, 100.0], index=dates)
    sparse = gold({dates[0]: 2000.0, dates[2]: 2500.0})
    ounces = nm.convert(usd, sparse)
    assert ounces.iloc[1] == pytest.approx(0.05)    # carried from day 1, not dropped
    assert not ounces.isna().any()


def test_a_rise_that_only_matches_gold_is_no_gain_in_ounces():
    """The whole point of the unit: 20% in dollars while gold also rose 20% is flat."""
    dates = [dt.date(2024, 1, d) for d in (1, 2)]
    usd = pd.Series([100.0, 120.0], index=dates)
    ounces = nm.convert(usd, gold(dict(zip(dates, [2000.0, 2400.0]))))
    assert ounces.iloc[0] == pytest.approx(ounces.iloc[1])


def test_without_a_gold_series_the_values_come_back_untouched():
    usd = pd.Series([1.0, 2.0], index=[dt.date(2024, 1, 1), dt.date(2024, 1, 2)])
    assert list(nm.convert(usd, pd.Series(dtype=float))) == [1.0, 2.0]


def test_coverage_is_checked_before_a_window_is_drawn():
    g = gold({dt.date(2024, 1, 1): 2000.0, dt.date(2024, 6, 1): 2100.0})
    assert nm.covers(g, dt.date(2024, 2, 1), dt.date(2024, 5, 1))
    assert not nm.covers(g, dt.date(2023, 1, 1), dt.date(2024, 5, 1))
    assert not nm.covers(pd.Series(dtype=float), dt.date(2024, 1, 1), dt.date(2024, 2, 1))


def test_the_backtest_measures_everything_in_the_chosen_unit():
    """Portfolio and benchmark both converted, so the table cannot end up in dollars
    under a chart in ounces."""
    dates = pd.bdate_range(dt.date(2024, 1, 1), periods=40).date
    rows = [("AAA", d, 100.0 * (1 + 0.01 * i), 100.0 * (1 + 0.01 * i))
            for i, d in enumerate(dates)]
    rows += [("^SP500TR", d, 100.0, 100.0) for d in dates]
    con = synthetic_market(rows)

    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    # Gold rises exactly as fast as the stock: in ounces, nothing happened.
    metal = gold({d: 2000.0 * (1 + 0.01 * i) for i, d in enumerate(dates)})

    usd_run = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free)
    gold_run = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free, numeraire=metal)

    assert usd_run.metrics["total_return"] == pytest.approx(0.39, abs=1e-9)
    assert gold_run.metrics["total_return"] == pytest.approx(0.0, abs=1e-9)
    # The benchmark, flat in dollars, must LOSE in ounces while gold rises.
    assert gold_run.metrics["benchmark_total_return"] < -0.2


def test_the_note_names_what_was_not_converted():
    text = nm.note(nm.GOLD)
    assert "price-to-book" in text
    assert "floors stay in" in text or "eligibility floors" in text
    assert nm.note(nm.USD) == ""
