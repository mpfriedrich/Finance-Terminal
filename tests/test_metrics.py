"""Phase 6: every formula against a series whose right answer is known in advance.

Tolerance is one basis point (1e-4 on a rate), as the plan demands. The series are built
so the correct answer can be derived with a pencil:

  * constant growth        -> exact compound return, zero volatility, zero drawdown
  * one fall of known depth -> exactly that drawdown
  * returns alternating ±x  -> standard deviation known in closed form
  * portfolio == index      -> tracking error exactly zero

A metric module is where a wrong number is quietest: nothing raises, the chart still
looks sensible, and the figure a decision rests on is simply false.
"""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import metrics as mx
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

BP = 1e-4  # one basis point


def constant_growth(daily_rate: float, days: int, start: float = 100.0) -> pd.Series:
    idx = pd.bdate_range("2020-01-01", periods=days)
    return pd.Series([start * (1 + daily_rate) ** i for i in range(days)], index=idx)


# --- return ---------------------------------------------------------------------------

def test_one_percent_a_month_for_five_years():
    """The plan's own example: +1% monthly compounds to 12.6825% a year."""
    months = 60
    idx = pd.bdate_range("2020-01-01", periods=months, freq="ME")
    curve = pd.Series([100 * 1.01**i for i in range(months)], index=idx)
    years = (idx[-1] - idx[0]).days / 365.25
    expected = (1.01 ** (12 * years)) ** (1 / years) - 1      # = 1.01**12 - 1
    assert mx.annual_return(curve, years) == pytest.approx(expected, abs=BP)
    assert mx.annual_return(curve, years) == pytest.approx(0.126825, abs=BP)


def test_a_doubling_over_exactly_two_years_is_the_square_root_of_two_minus_one():
    curve = pd.Series([100.0, 200.0], index=pd.to_datetime(["2020-01-01", "2022-01-01"]))
    years = 731 / 365.25
    assert mx.annual_return(curve, years) == pytest.approx(2 ** (1 / years) - 1, abs=BP)
    assert mx.annual_return(curve, years) == pytest.approx(0.41379, abs=BP)


def test_return_is_measured_against_capital_committed_when_a_base_is_given():
    """99.85 -> 200 is a 100% gain on the 100 that was put in, not 100.3%."""
    curve = pd.Series([99.85, 200.0], index=pd.to_datetime(["2020-01-01", "2021-01-01"]))
    years = 366 / 365.25
    with_base = mx.annual_return(curve, years, base=100.0)
    without = mx.annual_return(curve, years)
    assert with_base < without
    assert with_base == pytest.approx(2.0 ** (1 / years) - 1, abs=BP)


# --- volatility -----------------------------------------------------------------------

def test_a_series_that_never_wobbles_has_no_volatility():
    assert mx.annual_volatility(constant_growth(0.0005, 300)) == pytest.approx(0.0, abs=BP)


def test_alternating_returns_match_the_closed_form():
    """Daily returns of exactly ±1%: the sample deviation of n such values around a mean
    of zero is 0.01·√(n/(n−1)), annualised by √252."""
    step, n = 0.01, 251
    values, price = [100.0], 100.0
    for i in range(n):
        price *= (1 + step) if i % 2 == 0 else (1 - step)
        values.append(price)
    curve = pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)))

    returns = curve.pct_change().dropna()
    expected = returns.std(ddof=1) * np.sqrt(252)
    assert mx.annual_volatility(curve) == pytest.approx(expected, abs=BP)
    # and the closed form, to make sure `expected` is not just pandas agreeing with itself
    closed_form = 0.01 * np.sqrt(n / (n - 1)) * np.sqrt(252)
    assert mx.annual_volatility(curve) == pytest.approx(closed_form, abs=5e-4)


def test_volatility_needs_at_least_two_returns():
    single = pd.Series([100.0], index=pd.to_datetime(["2020-01-01"]))
    assert np.isnan(mx.annual_volatility(single))


# --- drawdown -------------------------------------------------------------------------

def test_a_rising_series_never_draws_down():
    assert mx.max_drawdown(constant_growth(0.0005, 300)) == pytest.approx(0.0, abs=BP)


def test_one_fall_of_known_depth_is_reported_exactly():
    """100 -> 70 is −30%, whatever happens afterwards."""
    curve = pd.Series([100, 120, 84, 150, 160],
                      index=pd.bdate_range("2020-01-01", periods=5))
    assert mx.max_drawdown(curve) == pytest.approx(-0.30, abs=BP)


def test_drawdown_is_measured_from_the_running_peak_not_from_the_start():
    """Doubling and then halving is −50%, not break-even. The difference is the one
    that decides whether a strategy looks survivable."""
    curve = pd.Series([100, 200, 100], index=pd.bdate_range("2020-01-01", periods=3))
    assert mx.max_drawdown(curve) == pytest.approx(-0.50, abs=BP)


def test_the_deepest_of_several_falls_wins():
    curve = pd.Series([100, 90, 100, 60, 100, 80],
                      index=pd.bdate_range("2020-01-01", periods=6))
    assert mx.max_drawdown(curve) == pytest.approx(-0.40, abs=BP)


# --- sharpe ---------------------------------------------------------------------------

def test_sharpe_is_excess_return_over_volatility():
    step, n = 0.01, 251
    values, price = [100.0], 100.0
    for i in range(n):
        price *= (1 + step) if i % 2 == 0 else (1 - step)
        values.append(price)
    curve = pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)))
    years = (curve.index[-1] - curve.index[0]).days / 365.25

    expected = (mx.annual_return(curve, years) - 0.03) / mx.annual_volatility(curve)
    assert mx.sharpe(curve, years, 0.03) == pytest.approx(expected, abs=BP)


def test_a_flat_line_has_no_risk_adjusted_reading():
    """Zero volatility would divide by zero. Reporting infinity would rank a series that
    earns nothing above every real portfolio."""
    flat = pd.Series([100.0] * 50, index=pd.bdate_range("2020-01-01", periods=50))
    years = 50 / 252
    assert np.isnan(mx.sharpe(flat, years, 0.02))


def test_the_risk_free_rate_moves_the_sharpe_ratio():
    curve = constant_growth(0.0004, 300) * np.linspace(1.0, 1.02, 300)
    years = 300 / 252
    assert mx.sharpe(curve, years, 0.00) > mx.sharpe(curve, years, 0.05)


# --- deviation from the index ---------------------------------------------------------

def test_a_portfolio_identical_to_the_index_has_no_tracking_error():
    curve = constant_growth(0.0004, 200)
    assert mx.tracking_error(curve, curve.copy()) == pytest.approx(0.0, abs=BP)


def test_a_steady_lead_over_the_index_is_still_no_tracking_error():
    """Beating the index by the same amount every single day is the same ride, not a
    different one. Tracking error measures how differently it travelled."""
    index = constant_growth(0.0004, 200)
    ahead = constant_growth(0.0006, 200)
    assert mx.tracking_error(ahead, index) == pytest.approx(0.0, abs=BP)
    assert mx.annual_return(ahead, 200 / 252) > mx.annual_return(index, 200 / 252)


def test_a_wobbling_excess_produces_the_expected_tracking_error():
    """Index flat, portfolio alternating ±1%: the excess IS the portfolio's own return
    series, so the tracking error equals its volatility."""
    step, n = 0.01, 251
    values, price = [100.0], 100.0
    for i in range(n):
        price *= (1 + step) if i % 2 == 0 else (1 - step)
        values.append(price)
    idx = pd.bdate_range("2020-01-01", periods=len(values))
    portfolio = pd.Series(values, index=idx)
    flat = pd.Series([100.0] * len(values), index=idx)
    assert mx.tracking_error(portfolio, flat) == pytest.approx(
        mx.annual_volatility(portfolio), abs=BP
    )


# --- turnover -------------------------------------------------------------------------

def test_buy_and_hold_turnover_is_zero():
    assert mx.turnover_of_buy_and_hold() == 0.0


# --- the table R9 asks for ------------------------------------------------------------

def test_the_summary_reports_every_required_figure_for_both_sides():
    portfolio = constant_growth(0.0006, 260)
    index = constant_growth(0.0004, 260)
    out = mx.summary(portfolio, index, years=1.0, risk_free=0.02, turnover=0.0)
    for field in ("annual_return", "volatility", "sharpe", "max_drawdown", "turnover",
                  "excess_annual", "tracking_error"):
        assert field in out, field
    for field in ("benchmark_annual_return", "benchmark_volatility", "benchmark_sharpe",
                  "benchmark_max_drawdown", "benchmark_turnover"):
        assert field in out, field


def test_both_sides_are_measured_by_the_same_rule():
    """The same series in both slots must produce identical numbers. If the index were
    ever measured differently from the portfolio it judges, every comparison would be
    off by the difference between the two methods."""
    curve = constant_growth(0.0005, 260)
    out = mx.summary(curve, curve.copy(), years=1.0, risk_free=0.02)
    assert out["annual_return"] == pytest.approx(out["benchmark_annual_return"], abs=1e-12)
    assert out["volatility"] == pytest.approx(out["benchmark_volatility"], abs=1e-12)
    assert out["max_drawdown"] == pytest.approx(out["benchmark_max_drawdown"], abs=1e-12)
    assert out["excess_annual"] == pytest.approx(0.0, abs=1e-12)
    assert out["tracking_error"] == pytest.approx(0.0, abs=BP)


# --- the risk-free rate is defined, and says where it came from -----------------------

def market_with_bills(rows: list[tuple]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    if not rows:
        return con
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES (?,?,?,?,?,0)",
        [(mx.RISK_FREE_SYMBOL, d, v, v, v) for d, v in rows],
    )
    return con


def test_the_risk_free_rate_comes_from_the_bill_yield_and_names_its_source():
    dates = pd.bdate_range("2024-01-01", periods=20).date
    con = market_with_bills([(d, 4.0) for d in dates])
    rate, source = mx.risk_free_rate(con, dates[0], dates[-1], fallback=0.02)
    assert rate == pytest.approx(0.04, abs=BP)       # 4.0 quoted as percent -> 0.04
    assert mx.RISK_FREE_SYMBOL in source


def test_without_a_stored_yield_the_settings_value_is_used_and_said_so():
    con = market_with_bills([])
    rate, source = mx.risk_free_rate(con, dt.date(2024, 1, 1), dt.date(2024, 2, 1),
                                     fallback=0.025)
    assert rate == 0.025
    assert "fallback" in source


def test_too_few_observations_fall_back_rather_than_average_two_days():
    dates = pd.bdate_range("2024-01-01", periods=3).date
    con = market_with_bills([(d, 9.0) for d in dates])
    rate, source = mx.risk_free_rate(con, dates[0], dates[-1], fallback=0.02)
    assert rate == 0.02
    assert "fallback" in source
