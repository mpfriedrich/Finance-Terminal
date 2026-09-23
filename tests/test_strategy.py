"""Contribution strategies. The cash account is what makes the comparison honest."""
import numpy as np
import pandas as pd
import pytest

from portfolio_lab.strategy import Plan, compare, simulate


def _rising(days=800, rate=0.0003):
    idx = pd.bdate_range("2022-01-03", periods=days)
    return pd.Series(100 * (1 + rate) ** np.arange(days), index=idx)


def _flat(days=800):
    idx = pd.bdate_range("2022-01-03", periods=days)
    return pd.Series(100.0, index=idx)


def test_investing_on_arrival_leaves_no_idle_cash():
    r = simulate(_rising(), Plan(rule="immediately", cash_rate=0.0))
    assert r["idle_cash_share"] == pytest.approx(0.0, abs=1e-9)
    assert r["n_purchases"] > 20


def test_a_flat_market_returns_what_was_put_in_minus_costs():
    r = simulate(_flat(), Plan(rule="immediately", cash_rate=0.0, cost=0.0))
    assert r["final_value"] == pytest.approx(r["invested"], rel=1e-9)


def test_costs_reduce_the_result():
    free = simulate(_flat(), Plan(cost=0.0, cash_rate=0.0))["final_value"]
    charged = simulate(_flat(), Plan(cost=0.01, cash_rate=0.0))["final_value"]
    assert charged < free


def test_waiting_for_a_dip_that_never_comes_leaves_money_in_cash():
    """A price that only rises never falls below its running high, so the rule never
    fires and the whole balance stays in cash. The test that the cash account exists."""
    r = simulate(_rising(), Plan(rule="on_drawdown", drawdown_trigger=0.05, cash_rate=0.0))
    assert r["n_purchases"] == 0
    assert r["final_value"] == pytest.approx(r["invested"], rel=1e-9)
    assert r["idle_cash_share"] > 0.9


def test_idle_cash_earns_the_stated_rate():
    """Without this, every dip strategy is penalised for money that in reality sits in
    an account paying something."""
    dead = simulate(_rising(), Plan(rule="on_drawdown", drawdown_trigger=0.5, cash_rate=0.0))
    paid = simulate(_rising(), Plan(rule="on_drawdown", drawdown_trigger=0.5, cash_rate=0.05))
    assert paid["final_value"] > dead["final_value"]


def test_a_dip_rule_does_buy_when_the_price_actually_falls():
    idx = pd.bdate_range("2022-01-03", periods=600)
    s = pd.Series(100.0, index=idx)
    s.iloc[300:320] = 80.0          # a 20% fall from the running high
    r = simulate(s, Plan(rule="on_drawdown", drawdown_trigger=0.05, cash_rate=0.0))
    assert r["n_purchases"] > 0
    assert r["average_cost"] == pytest.approx(80.0, rel=1e-6)


def test_dip_buying_beats_steady_buying_when_the_dip_is_real():
    idx = pd.bdate_range("2022-01-03", periods=600)
    s = pd.Series(100.0, index=idx)
    s.iloc[200:400] = 50.0
    s.iloc[400:] = 120.0
    steady = simulate(s, Plan(rule="immediately", cash_rate=0.0))
    dip = simulate(s, Plan(rule="on_drawdown", drawdown_trigger=0.10, cash_rate=0.0))
    assert dip["average_cost"] < steady["average_cost"]


def test_lump_sum_invests_everything_at_the_start():
    r = simulate(_rising(), Plan(rule="lump_sum", cash_rate=0.0))
    assert r["n_purchases"] == 1
    assert r["purchases"].iloc[0]["date"] == _rising().index[0]


def test_money_weighted_return_is_positive_in_a_rising_market():
    r = simulate(_rising(), Plan(rule="immediately", cash_rate=0.0, cost=0.0))
    assert r["money_weighted_return"] > 0


def test_compare_lines_the_strategies_up():
    out = compare(_rising(), [Plan(rule="immediately"), Plan(rule="lump_sum"),
                              Plan(rule="on_drawdown", drawdown_trigger=0.03)])
    assert len(out) == 3
    assert {"strategy", "invested", "final value", "return p.a."} <= set(out.columns)
