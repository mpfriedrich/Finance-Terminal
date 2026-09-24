"""Paper books: simulated purchases from every tab, frozen when opened, valued live.

The numbers are chosen so each answer can be worked out by hand.
"""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import paper
from portfolio_lab.config import CostModel
from portfolio_lab.data.schema import MARKET_MIGRATIONS, USER_MIGRATIONS, _apply
from portfolio_lab.lab.spec import StrategySpec
from portfolio_lab.strategy import Plan

# A rule book with no market cap given pays the most expensive tier, like stage B.
RATE = CostModel().spread_for(0.0) + CostModel().commission
DAYS = pd.bdate_range("2025-01-01", "2026-06-30").date
START = dt.date(2026, 1, 2)


def market(paths: dict) -> duckdb.DuckDBPyConnection:
    """paths: ticker -> function(day) -> price. Open = close, adjusted = raw."""
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    rows = [(t, d, f(d), f(d), f(d), 1000) for t, f in paths.items() for d in DAYS]
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES (?,?,?,?,?,?)", rows)
    return con


@pytest.fixture()
def user():
    con = duckdb.connect(":memory:")
    _apply(con, USER_MIGRATIONS)
    return con


def doubles_after_start(d):
    return 10.0 if d <= START else 20.0


def flat(d):
    return 10.0


def test_a_hold_book_grows_with_what_it_holds(user):
    """Half in a name that doubles, half in a flat one, 1% entry cost:
    10,000 × (0.5 × 2 + 0.5 × 1) × 0.99 = 14,850."""
    m = market({"AAA": doubles_after_start, "BBB": flat, "^SP500TR": flat})
    book = paper.open_hold(user, "test", "SCREEN", pd.DataFrame({
        "ticker": ["AAA", "BBB"], "weight": [1, 1], "price": [10.0, 10.0],
        "cost": [0.01, 0.01]}), START, 10_000)
    out = paper.value_book(m, user, paper.books(user).iloc[0].to_dict())
    assert out["value_now"] == pytest.approx(14_850.0)
    assert out["invested_now"] == 10_000
    assert out["value"].iloc[0] == pytest.approx(9_900.0), "starts below par by the cost"
    assert out["benchmark_return"] == pytest.approx(0.0)
    assert paper.positions(user, book).weight.sum() == pytest.approx(1.0)


def test_a_book_sees_nothing_before_it_was_opened(user):
    m = market({"AAA": lambda d: 5.0 if d < START else 10.0})
    paper.open_hold(user, "t", "RESEARCH", pd.DataFrame(
        {"ticker": ["AAA"], "weight": [1], "price": [10.0]}), START, 1_000)
    out = paper.value_book(m, user, paper.books(user).iloc[0].to_dict())
    assert out["value"].index[0].date() >= START
    assert out["value_now"] == pytest.approx(1_000.0)


def test_a_rule_book_trades_only_from_the_open_after_it_was_opened(user):
    """Always-in on a name that doubles the day after the start: the first fill is the
    next open, already at 20, so the book earns nothing — the jump happened overnight,
    before it could buy. Before the start it held nothing, whatever the signal said."""
    m = market({"AAA": doubles_after_start})
    paper.open_rule(user, "rule", ["AAA"], StrategySpec(signal="always_in"), START, 1_000)
    out = paper.value_book(m, user, paper.books(user).iloc[0].to_dict())
    assert out["value"].index[0].date() == START
    assert out["value_now"] == pytest.approx(1_000.0 * (1 - RATE), rel=1e-9)
    assert list(out["fills"].side) == ["buy"]
    assert pd.Timestamp(out["fills"].date.iloc[0]).date() > START


def test_a_rule_book_earns_what_comes_after_its_first_fill(user):
    m = market({"AAA": lambda d: 10.0 if d <= dt.date(2026, 3, 2) else 15.0})
    paper.open_rule(user, "rule", ["AAA"], StrategySpec(signal="always_in"), START, 1_000)
    out = paper.value_book(m, user, paper.books(user).iloc[0].to_dict())
    assert out["value_now"] == pytest.approx(1_500.0 * (1 - RATE), rel=1e-9)


def test_a_plan_book_puts_money_in_on_its_schedule(user):
    """500 a month into a flat price from 2 January to 30 June: six contributions."""
    m = market({"AAA": flat})
    paper.open_plan(user, "plan", "AAA", Plan(amount=500, frequency="monthly",
                                              rule="immediately", cost=0.0), START)
    out = paper.value_book(m, user, paper.books(user).iloc[0].to_dict())
    assert out["invested_now"] == pytest.approx(3_000.0)
    assert out["value_now"] == pytest.approx(3_000.0)
    assert out["invested"].iloc[0] == pytest.approx(500.0)


def test_a_lump_sum_plan_is_refused_because_its_size_would_grow_with_time(user):
    with pytest.raises(ValueError, match="hold book"):
        paper.open_plan(user, "p", "AAA", Plan(rule="lump_sum"), START)


def test_closing_a_book_removes_it_and_its_positions(user):
    book = paper.open_hold(user, "t", "SCREEN", pd.DataFrame(
        {"ticker": ["AAA"], "weight": [1], "price": [10.0]}), START, 1_000)
    paper.close_book(user, book)
    assert paper.books(user).empty and paper.positions(user, book).empty


def test_a_book_needs_money(user):
    with pytest.raises(ValueError):
        paper.open_hold(user, "t", "SCREEN", pd.DataFrame(
            {"ticker": ["AAA"], "weight": [1], "price": [10.0]}), START, 0)


def test_a_double_run_of_one_click_opens_one_book(user):
    """Streamlit can run a button handler twice; that must not buy twice."""
    frame = pd.DataFrame({"ticker": ["AAA"], "weight": [1], "price": [10.0]})
    a = paper.open_hold(user, "same", "RESEARCH", frame, START, 1_000)
    b = paper.open_hold(user, "same", "RESEARCH", frame, START, 1_000)
    assert a == b
    assert len(paper.books(user)) == 1 and len(paper.positions(user, a)) == 1


def test_a_different_amount_is_a_different_book(user):
    frame = pd.DataFrame({"ticker": ["AAA"], "weight": [1], "price": [10.0]})
    paper.open_hold(user, "same", "RESEARCH", frame, START, 1_000)
    paper.open_hold(user, "same", "RESEARCH", frame, START, 2_000)
    assert len(paper.books(user)) == 2
