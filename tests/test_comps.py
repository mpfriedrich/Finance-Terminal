"""Trading comps: the table, implied value and the company against its own history.

Four companies with round numbers, so every figure below can be checked by hand.
"""
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import comps


@pytest.fixture()
def frame():
    return pd.DataFrame([
        # subject: EBITDA 100, net debt 200, 50 shares at 30 → cap 1500, EV 1700
        dict(ticker="SUB", name="Subject", price=30.0, shares_outstanding=50.0,
             market_cap=1500.0, enterprise_value=1700.0, ev_to_ebitda=17.0,
             ebitda_ttm=100.0, net_debt=200.0, net_income_ttm=60.0,
             price_to_earnings=25.0, operating_income_ttm=80.0, ev_to_ebit=21.25,
             revenue_ttm=500.0, ev_to_sales=3.4),
        dict(ticker="P1", name="Peer 1", ev_to_ebitda=8.0, price_to_earnings=12.0,
             ev_to_ebit=10.0, ev_to_sales=1.0),
        dict(ticker="P2", name="Peer 2", ev_to_ebitda=10.0, price_to_earnings=15.0,
             ev_to_ebit=12.0, ev_to_sales=2.0),
        dict(ticker="P3", name="Peer 3", ev_to_ebitda=12.0, price_to_earnings=18.0,
             ev_to_ebit=14.0, ev_to_sales=3.0),
    ])


def test_the_sheet_puts_the_company_first_and_summarises_only_the_peers(frame):
    s = comps.sheet(frame, "SUB", ["P1", "P2", "P3"])
    assert list(s["rows"].ticker) == ["SUB", "P1", "P2", "P3"]
    assert s["stats"].at["median", "ev_to_ebitda"] == 10.0, "the company is not its own peer"
    assert s["stats"].at["n", "ev_to_ebitda"] == 3
    assert s["vs_median"]["ev_to_ebitda"] == pytest.approx(0.70)


def test_an_ev_multiple_is_bridged_to_equity_through_net_debt(frame):
    s = comps.sheet(frame, "SUB", ["P1", "P2", "P3"])
    imp = comps.implied(frame, "SUB", s["stats"]).set_index("multiple")
    # 10 × 100 = 1000 EV, − 200 net debt = 800 equity, / 50 shares = 16
    assert imp.at["EV/EBITDA", "implied equity value"] == pytest.approx(800.0)
    assert imp.at["EV/EBITDA", "implied price"] == pytest.approx(16.0)
    assert imp.at["EV/EBITDA", "vs price"] == pytest.approx(16 / 30 - 1)


def test_an_equity_multiple_is_not_bridged(frame):
    s = comps.sheet(frame, "SUB", ["P1", "P2", "P3"])
    imp = comps.implied(frame, "SUB", s["stats"]).set_index("multiple")
    # 15 × 60 = 900 equity, / 50 = 18 — no net debt taken off
    assert imp.at["P/E", "implied price"] == pytest.approx(18.0)


def test_a_multiple_is_not_applied_to_a_loss(frame):
    frame.loc[0, "net_income_ttm"] = -10.0
    s = comps.sheet(frame, "SUB", ["P1", "P2", "P3"])
    imp = comps.implied(frame, "SUB", s["stats"])
    assert "P/E" not in set(imp.multiple)


def test_without_net_debt_there_is_no_bridge_from_ev(frame):
    frame.loc[0, "net_debt"] = np.nan
    s = comps.sheet(frame, "SUB", ["P1", "P2", "P3"])
    imp = comps.implied(frame, "SUB", s["stats"])
    assert "EV/EBITDA" not in set(imp.multiple) and "P/E" in set(imp.multiple)


def test_against_its_own_history():
    hist = pd.DataFrame({"year": [2021, 2022, 2023], "price_to_earnings": [10.0, 12.0, 14.0]})
    z = comps.against_history(hist, pd.Series({"price_to_earnings": 16.0}))
    row = z.set_index("multiple").loc["P/E"]
    assert row["own average"] == 12.0 and row["z-score"] == pytest.approx(2.0)


def test_two_years_are_not_a_history():
    hist = pd.DataFrame({"year": [2022, 2023], "price_to_earnings": [10.0, 12.0]})
    assert comps.against_history(hist, pd.Series({"price_to_earnings": 16.0})).empty


def test_a_margin_is_compared_in_points_not_as_a_percentage_of_itself(frame):
    frame["ebitda_margin"] = [0.28, 0.02, 0.03, 0.025]
    s = comps.sheet(frame, "SUB", ["P1", "P2", "P3"])
    assert s["vs_median"]["ebitda_margin"] == pytest.approx(0.255)
