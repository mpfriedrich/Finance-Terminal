"""Intrinsic value is a model. These tests pin down what it refuses to claim."""
import numpy as np
import pandas as pd
import pytest

from portfolio_lab.screen.valuation import (
    Assumptions, add_valuation, discounted_cash_flow, graham_number,
)


def test_a_perpetuity_with_no_growth_is_cash_flow_over_the_discount_rate():
    """The textbook anchor: with zero growth everywhere, value = FCF / r.
    Checked analytically, so a wrong discounting loop cannot hide."""
    a = Assumptions(discount_rate=0.10, growth_rate=0.0, terminal_growth=0.0, years=200)
    v = discounted_cash_flow(pd.Series([100.0]), a)
    assert v.iloc[0] == pytest.approx(1000.0, rel=1e-6)


def test_a_higher_discount_rate_lowers_the_value():
    fcf = pd.Series([100.0])
    cheap = discounted_cash_flow(fcf, Assumptions(discount_rate=0.08)).iloc[0]
    dear = discounted_cash_flow(fcf, Assumptions(discount_rate=0.14)).iloc[0]
    assert dear < cheap


def test_cash_burners_get_no_value_rather_than_a_negative_one():
    """A negative value would sort to the very top of a 'cheapest first' ranking."""
    v = discounted_cash_flow(pd.Series([-50.0, 0.0, 100.0]))
    assert np.isnan(v.iloc[0]) and np.isnan(v.iloc[1])
    assert v.iloc[2] > 0


def test_terminal_growth_at_or_above_the_discount_rate_yields_nothing():
    """Otherwise the formula divides by zero and reports infinite worth."""
    a = Assumptions(discount_rate=0.08, terminal_growth=0.08)
    assert not a.valid()
    assert np.isnan(discounted_cash_flow(pd.Series([100.0]), a).iloc[0])


def test_graham_number_needs_both_profit_and_equity():
    g = graham_number(pd.Series([2.0, -1.0, 2.0]), pd.Series([10.0, 10.0, -5.0]))
    assert g.iloc[0] == pytest.approx(np.sqrt(22.5 * 2 * 10))
    assert np.isnan(g.iloc[1]) and np.isnan(g.iloc[2])


def test_free_cash_flow_subtracts_capex_and_records_when_it_was_missing():
    frame = pd.DataFrame({
        "price": [10.0, 10.0],
        "market_cap": [1000.0, 1000.0],
        "shares_outstanding": [100.0, 100.0],
        "book_value": [500.0, 500.0],
        "net_income_ttm": [50.0, 50.0],
        "operating_cash_flow_ttm": [200.0, 200.0],
        "capex_ttm": [80.0, np.nan],
    })
    out = add_valuation(frame)
    assert out.free_cash_flow_ttm.iloc[0] == 120.0
    assert out.free_cash_flow_ttm.iloc[1] == 200.0   # treated as zero capex...
    assert out.capex_reported.tolist() == [True, False]  # ...but flagged as unreported


def test_assumptions_are_stated_in_words():
    text = Assumptions().describe()
    for token in ("0% growth", "10 years", "2%", "10%"):
        assert token in text


def test_price_to_free_cash_flow_is_computed_after_capex_is_known():
    """Regression: this ratio was first written into derive_metrics, which runs before
    free cash flow exists — it silently produced NaN for every company."""
    frame = pd.DataFrame({
        "price": [10.0], "market_cap": [1200.0], "shares_outstanding": [120.0],
        "book_value": [500.0], "net_income_ttm": [50.0],
        "operating_cash_flow_ttm": [200.0], "capex_ttm": [50.0],
    })
    out = add_valuation(frame)
    assert out.free_cash_flow_ttm.iloc[0] == 150.0
    assert out.price_to_free_cash_flow.iloc[0] == pytest.approx(8.0)
