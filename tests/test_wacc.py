"""Cost of capital and the DCF on it, worked out by hand.

One company: 100 shares at 20 (equity 2,000), debt 500 with interest 30 (6%), cash 100
(net debt 400), tax 25%; operating cash flow 300, capex 100.
"""
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import wacc as W

ROW = pd.Series(dict(market_cap=2000.0, total_debt=500.0, interest_expense_ttm=30.0,
                     tax_rate=0.25, tax_rate_source="effective", net_debt=400.0,
                     shares_outstanding=100.0, price=20.0,
                     operating_cash_flow_ttm=300.0, capex_ttm=100.0))
RAW = W.Inputs(risk_free=0.04, beta=1.2, equity_risk_premium=0.05, adjust_beta=False,
               growth=0.0, years=5, terminal_growth=0.0)


def test_the_wacc_build_up():
    b = W.build(ROW, RAW)
    assert b["cost_of_equity"] == pytest.approx(0.04 + 1.2 * 0.05)        # 10%
    assert b["cost_of_debt_pre_tax"] == pytest.approx(0.06)
    assert b["weight_equity"] == pytest.approx(0.8)
    # 0.8 × 10% + 0.2 × 6% × 0.75
    assert b["wacc"] == pytest.approx(0.08 + 0.009)


def test_blume_pulls_beta_towards_one():
    assert W.adjusted_beta(1.6) == pytest.approx(1.402)
    b = W.build(ROW, W.Inputs(risk_free=0.04, beta=1.6))
    assert b["beta"] == pytest.approx(1.402) and b["beta_raw"] == 1.6


def test_an_implausible_cost_of_debt_falls_back_to_a_named_spread():
    rate, source = W.cost_of_debt(1.0, 500.0, 0.04)          # 0.2%: below risk-free
    assert rate == pytest.approx(0.06) and "outside plausible bounds" in source
    rate, source = W.cost_of_debt(np.nan, 500.0, 0.04)
    assert rate == pytest.approx(0.06) and "no usable interest line" in source


def test_fcff_adds_back_after_tax_interest():
    # 300 − 100 + 30 × 0.75
    assert W.fcff(ROW) == pytest.approx(222.5)


def test_a_no_growth_dcf_is_a_perpetuity():
    """With zero growth everywhere, EV = FCFF / WACC exactly, whatever the horizon."""
    wacc = 0.089
    out = W.dcf(ROW, wacc, RAW)
    assert out["enterprise_value"] == pytest.approx(222.5 / wacc)
    assert out["equity_value"] == pytest.approx(222.5 / wacc - 400.0)
    assert out["per_share"] == pytest.approx((222.5 / wacc - 400.0) / 100.0)


def test_growth_at_or_above_the_wacc_is_refused():
    out = W.dcf(ROW, 0.02, W.Inputs(risk_free=0.04, beta=1.0, terminal_growth=0.02))
    assert "error" in out


def test_a_negative_cash_flow_is_refused_not_valued():
    out = W.dcf(ROW.copy().replace({300.0: 50.0}), 0.09, RAW)
    assert "error" in out


def test_the_sensitivity_grid_moves_the_right_way():
    grid = W.sensitivity(ROW, 0.089, RAW)
    assert grid.shape == (5, 3)
    # a higher WACC lowers the value; higher growth forever raises it
    assert grid.iloc[0, 1] > grid.iloc[-1, 1]
    assert grid.iloc[2, 2] > grid.iloc[2, 0]
    assert grid.iloc[2, 1] == pytest.approx(W.dcf(ROW, 0.089, RAW)["per_share"])
