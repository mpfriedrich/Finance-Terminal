"""Enterprise-value multiples, returns on capital and growth — each worked out by hand.

One company: price 10, 100 shares (cap 1,000); debt 300 long + 100 short, cash 200
(EV 1,200, net debt 200); EBIT 150, D&A 50 (EBITDA 200); revenue 1,000, a year ago 800;
gross profit 400; net income 90; interest 30; pre-tax 120, tax 30 (25%); equity 600;
assets 2,000.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_lab.screen import translate as T
from portfolio_lab.screen.engine import derive_metrics
from portfolio_lab.screen.spec import FILTERABLE, LOWER_IS_BETTER

BASE = dict(price=10.0, shares_outstanding=100.0, long_term_debt=300.0,
            current_debt=100.0, cash=200.0, operating_income_ttm=150.0,
            depreciation_amortization_ttm=50.0, revenue_ttm=1000.0,
            revenue_ttm_prior=800.0, gross_profit_ttm=400.0, net_income_ttm=90.0,
            interest_expense_ttm=30.0, pretax_income_ttm=120.0, income_tax_ttm=30.0,
            book_value=600.0, total_assets=2000.0)


def one(**overrides):
    return derive_metrics(pd.DataFrame([{**BASE, **overrides}])).iloc[0]


def test_enterprise_value_and_its_multiples():
    r = one()
    assert r.enterprise_value == pytest.approx(1200.0)
    assert r.ebitda_ttm == pytest.approx(200.0)
    assert r.ev_to_ebitda == pytest.approx(6.0)
    assert r.ev_to_ebit == pytest.approx(8.0)
    assert r.ev_to_sales == pytest.approx(1.2)


def test_leverage_and_cover():
    r = one()
    assert r.net_debt == pytest.approx(200.0)
    assert r.net_debt_to_ebitda == pytest.approx(1.0)
    assert r.interest_coverage == pytest.approx(5.0)


def test_margins_returns_and_growth():
    r = one()
    assert r.gross_margin == pytest.approx(0.40)
    assert r.operating_margin == pytest.approx(0.15)
    assert r.ebitda_margin == pytest.approx(0.20)
    assert r.return_on_assets == pytest.approx(0.045)
    assert r.tax_rate == pytest.approx(0.25) and r.tax_rate_source == "effective"
    # 150 × (1 − 25%) / (600 + 400 − 200)
    assert r.return_on_invested_capital == pytest.approx(112.5 / 800)
    assert r.revenue_growth == pytest.approx(0.25)


def test_net_cash_gives_a_negative_leverage_multiple_not_a_missing_one():
    r = one(cash=900.0)
    assert r.net_debt_to_ebitda == pytest.approx(-2.5)


def test_a_negative_enterprise_value_has_no_multiples():
    r = one(cash=5000.0)
    assert r.enterprise_value < 0
    assert np.isnan(r.ev_to_ebitda) and np.isnan(r.ev_to_sales)


def test_unknown_debt_leaves_enterprise_value_unknown_rather_than_flattering():
    r = one(long_term_debt=np.nan, current_debt=np.nan)
    assert np.isnan(r.enterprise_value) and np.isnan(r.ev_to_ebitda)


def test_without_a_usable_tax_rate_roic_uses_the_statutory_rate_and_says_so():
    r = one(income_tax_ttm=np.nan)
    assert r.tax_rate == pytest.approx(0.21) and r.tax_rate_source == "statutory 21%"
    assert r.return_on_invested_capital == pytest.approx(150 * 0.79 / 800)


def test_missing_d_and_a_leaves_ebitda_missing_rather_than_equal_to_ebit():
    r = one(depreciation_amortization_ttm=np.nan)
    assert np.isnan(r.ebitda_ttm) and np.isnan(r.ev_to_ebitda)
    assert r.ev_to_ebit == pytest.approx(8.0)


def test_the_new_metrics_can_be_filtered_and_asked_for():
    new = {"ev_to_ebitda", "ev_to_ebit", "ev_to_sales", "net_debt_to_ebitda",
           "return_on_invested_capital", "return_on_assets", "operating_margin",
           "gross_margin", "ebitda_margin", "revenue_growth", "interest_coverage"}
    assert new <= set(FILTERABLE)
    schema = T.TOOL["input_schema"]["properties"]["filters"]["items"]["properties"]
    assert new <= set(schema["metric"]["enum"])
    assert {"ev_to_ebitda", "net_debt_to_ebitda"} <= LOWER_IS_BETTER


def test_the_paid_source_carries_the_inputs_through_to_the_frame(tmp_path):
    from tests.paid_fixture import END, build_store

    d = derive_metrics(build_store(tmp_path).screening_frame(END)).set_index("ticker")
    # AAA: quarterly EBIT 45 (1.5 × 30), D&A 25 (0.1 × 250) → EBITDA TTM 280
    assert d.at["AAA", "ebitda_ttm"] == pytest.approx(4 * (45e6 + 25e6))
    assert d.at["AAA", "ev_to_ebitda"] > 0
