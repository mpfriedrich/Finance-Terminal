"""Live tracking: the one measurement in this app that cannot be flattered after the fact."""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply
from portfolio_lab.track import performance

D = dt.date


@pytest.fixture()
def market():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = []
    dates = pd.bdate_range("2026-01-01", "2026-03-31").date
    # AAA doubles, BBB flat, the benchmark gains 10%
    for i, d in enumerate(dates):
        f = i / (len(dates) - 1)
        rows += [
            ("AAA", d, 10.0, 10 * (1 + f), 10 * (1 + f), 1000),
            ("BBB", d, 20.0, 20.0, 20.0, 1000),
            ("^SP500TR", d, 100.0, 100 * (1 + 0.10 * f), 100 * (1 + 0.10 * f), 0),
        ]
    c.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) VALUES (?,?,?,?,?,?)",
        rows,
    )
    return c


def _holdings(cost=0.0):
    return pd.DataFrame([
        {"ticker": "AAA", "name": "A", "weight": 0.5, "entry_price": 10.0,
         "entry_date": D(2026, 1, 1), "entry_cost": cost},
        {"ticker": "BBB", "name": "B", "weight": 0.5, "entry_price": 20.0,
         "entry_date": D(2026, 1, 1), "entry_cost": cost},
    ])


def test_equal_weight_return_is_the_average_of_the_positions(market):
    p = performance(market, _holdings(), D(2026, 1, 1))
    # one position doubles (+100%), one is flat (0%) -> +50%
    assert p["portfolio_return"] == pytest.approx(0.50, abs=1e-6)


def test_benchmark_is_measured_over_the_same_window(market):
    p = performance(market, _holdings(), D(2026, 1, 1))
    assert p["benchmark_return"] == pytest.approx(0.10, abs=1e-6)
    assert p["excess"] == pytest.approx(0.40, abs=1e-6)


def test_entry_costs_reduce_the_result(market):
    free = performance(market, _holdings(cost=0.0), D(2026, 1, 1))["portfolio_return"]
    costly = performance(market, _holdings(cost=0.01), D(2026, 1, 1))["portfolio_return"]
    assert costly < free
    # 1% charged once on entry, compounded by the portfolio's growth
    assert costly == pytest.approx((1 + free) * 0.99 - 1, abs=1e-6)


def test_per_position_contributions_add_up(market):
    p = performance(market, _holdings(), D(2026, 1, 1))
    assert p["detail"]["contribution"].sum() == pytest.approx(0.50, abs=1e-6)


def test_curve_starts_at_one_hundred(market):
    p = performance(market, _holdings(), D(2026, 1, 1))
    assert p["portfolio"][0] == pytest.approx(100.0, abs=1e-6)
    assert p["benchmark"][0] == pytest.approx(100.0, abs=1e-6)
