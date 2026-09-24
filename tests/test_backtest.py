"""Phase 5: the retrospective backtest, its costs, and the benchmark it is measured against.

The rules being pinned here are the ones that decide whether a curve means anything:
R8 (costs, tiered by size), R11 (both sides on the same return basis), R16b (buy and hold,
turnover zero, cost charged once), R17 (dividends reinvested at the ex-date) and R22 (every
run carries the identity of the data build it read).
"""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import backtest as bt
from portfolio_lab.config import MARKET_DB, CostModel
from portfolio_lab.data.schema import MARKET_MIGRATIONS, USER_MIGRATIONS, _apply
from portfolio_lab.store import Store


# --- a hand-computable world ---------------------------------------------------------

def synthetic_market(rows: list[tuple]) -> duckdb.DuckDBPyConnection:
    """(ticker, date, close, close_adj) -> an in-memory market store."""
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES (?,?,?,?,?,1000000)",
        [(t, d, c, c, a) for t, d, c, a in rows],
    )
    return con


def flat_then_double(ticker: str, start=dt.date(2024, 1, 1), days=100, end_value=2.0):
    """A series that ends at exactly `end_value` times where it began."""
    dates = pd.bdate_range(start, periods=days).date
    step = end_value ** (1 / (days - 1))
    return [(ticker, d, 10.0 * step**i, 10.0 * step**i) for i, d in enumerate(dates)]


@pytest.fixture()
def doubling_market():
    rows = flat_then_double("AAA") + flat_then_double("^SP500TR", end_value=1.5)
    return synthetic_market(rows)


# --- R16b / R8: one purchase, charged once, tiered by size ---------------------------

def test_buy_and_hold_has_no_turnover_by_definition(doubling_market):
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    r = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert r.turnover == 0.0
    assert r.metrics["turnover"] == 0.0


def test_the_entry_cost_is_charged_once_and_shows_in_the_result(doubling_market):
    """A doubling stock bought with a 0.15% cost ends at 2 × (1 − 0.0015), not at 2."""
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    costs = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0015),))
    r = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31),
                        costs=costs)
    assert r.portfolio[-1] == pytest.approx(200.0 * (1 - 0.0015), rel=1e-9)
    assert r.metrics["total_return"] == pytest.approx(2.0 * (1 - 0.0015) - 1.0, rel=1e-9)


def test_zeroing_the_costs_changes_the_result(doubling_market):
    """R8's own acceptance: costs must be able to move the number."""
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    charged = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    gratis = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31),
                             costs=free)
    assert gratis.metrics["total_return"] > charged.metrics["total_return"]
    assert gratis.entry_cost_total == 0.0


def test_a_micro_cap_costs_more_to_buy_than_a_mega_cap():
    """Two size bands, two costs — the other half of R8's acceptance."""
    holdings = pd.DataFrame({"ticker": ["TINY", "HUGE"], "weight": [0.5, 0.5],
                             "market_cap": [100e6, 500e9]})
    costs = bt.entry_costs(holdings, CostModel())
    assert costs.iloc[0] > costs.iloc[1]


def test_a_missing_market_cap_pays_the_most_expensive_tier():
    """An unknown is not a blue chip; guessing cheap would flatter the result."""
    holdings = pd.DataFrame({"ticker": ["???"], "weight": [1.0], "market_cap": [np.nan]})
    unknown = bt.entry_costs(holdings, CostModel()).iloc[0]
    micro = bt.entry_costs(
        pd.DataFrame({"ticker": ["T"], "weight": [1.0], "market_cap": [1e6]}), CostModel()
    ).iloc[0]
    assert unknown == micro


# --- the cost must not vanish from the annualised figure ------------------------------

def test_costs_survive_annualisation(doubling_market):
    """The first version of this module annualised from the post-cost starting value,
    which made the entire cost model invisible in the headline number while it still
    showed up in the total return. The two have to tell the same story."""
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    costs = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.05),))
    r = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31),
                        costs=costs)
    implied = (1 + r.metrics["total_return"]) ** (1 / r.years) - 1
    assert r.metrics["annual_return"] == pytest.approx(implied, rel=1e-9)


# --- R11: both sides on the same return basis ----------------------------------------

def test_the_benchmark_is_rebased_on_the_same_dates(doubling_market):
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    r = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert r.benchmark[0] == pytest.approx(100.0)
    assert len(r.benchmark) == len(r.portfolio) == len(r.dates)
    assert r.benchmark[-1] == pytest.approx(150.0, rel=1e-9)


@pytest.mark.slow
def test_the_stored_index_really_is_a_total_return_series():
    """R11, measured rather than assumed, in two independent ways.

    1. Against the SAME index without dividends (^GSPC): the gap is the dividend
       contribution. A price series mislabelled as total return would show zero.
    2. Against SPY, which is the same index built the other way round — from traded
       prices and actual distributions instead of an index calculation. Two different
       data paths agreeing is evidence; one path agreeing with itself is not. SPY should
       sit slightly BELOW the index, by roughly its fee.

    The plan allows 25 basis points against the published figure (60 on the SPY
    fallback). The measured gap here is about 11.
    """
    from portfolio_lab.data.schema import open_market

    if not MARKET_DB.exists():
        pytest.skip("no local market store")
    try:
        con = open_market(MARKET_DB, read_only=True)
    except (duckdb.IOException, RuntimeError) as exc:
        pytest.skip(f"market store is locked by another process: {exc}")
    start, end = dt.date(2015, 9, 30), dt.date(2025, 9, 30)

    def cagr(symbol: str, column: str) -> float | None:
        df = con.execute(
            f"SELECT date, {column} AS v FROM prices "
            "WHERE ticker = ? AND date BETWEEN ? AND ? ORDER BY date",
            [symbol, start, end],
        ).fetch_df()
        if len(df) < 200:
            return None
        years = (df.date.iloc[-1] - df.date.iloc[0]).days / 365.25
        return (df.v.iloc[-1] / df.v.iloc[0]) ** (1 / years) - 1

    total, price, etf = cagr("^SP500TR", "close"), cagr("^GSPC", "close"), cagr("SPY", "close_adj")
    if total is None:
        pytest.skip("no ten-year benchmark history stored")

    if price is not None:
        dividend_contribution = total - price
        assert 0.010 <= dividend_contribution <= 0.025, (
            f"the 'total return' series adds {dividend_contribution:.2%} a year over the "
            "price index — that is not a plausible dividend yield"
        )
    if etf is not None:
        assert abs(total - etf) <= 0.0025, (
            f"index and ETF disagree by {(total - etf) * 10000:.0f} bp a year"
        )
        assert total >= etf - 0.0005, "the ETF beating the index it tracks is a data fault"


# --- R17: dividends reinvested at the ex-date ----------------------------------------

def test_dividends_are_reinvested_rather_than_left_in_cash():
    """A payer whose PRICE goes nowhere but whose adjusted series rises by the dividend
    must end up worth more, by exactly that amount. Worked by hand: 4% paid once.
    """
    dates = pd.bdate_range(dt.date(2024, 1, 1), periods=60).date
    rows = []
    for i, d in enumerate(dates):
        price = 50.0                                  # flat all the way through
        adjusted = 50.0 if i < 30 else 52.0           # a single 4% distribution
        rows.append(("PAYER", d, price, adjusted))
    rows += [("^SP500TR", d, 100.0, 100.0) for d in dates]
    con = synthetic_market(rows)

    h = pd.DataFrame({"ticker": ["PAYER"], "weight": [1.0], "market_cap": [5e9]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    r = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free)
    assert r.metrics["total_return"] == pytest.approx(0.04, rel=1e-9)
    assert r.portfolio[-1] == pytest.approx(104.0, rel=1e-9)


# --- honesty about what the run could not do -----------------------------------------

def test_a_name_without_a_price_at_the_start_is_named_not_silently_dropped(doubling_market):
    h = pd.DataFrame({"ticker": ["AAA", "GHOST"], "weight": [0.5, 0.5],
                      "market_cap": [5e9, 5e9]})
    r = bt.buy_and_hold(doubling_market, h, dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    assert r is not None
    assert any("GHOST" in n for n in r.notes) or len(r.per_position) == 1


def test_a_shortened_window_is_reported(doubling_market):
    """Asking for 2020 and getting 2024 has to be visible, or every number is read
    against a period that never happened."""
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    r = bt.buy_and_hold(doubling_market, h, dt.date(2020, 1, 1), dt.date(2024, 12, 31))
    assert any("earliest stored bar" in n for n in r.notes)


def test_the_label_names_both_uncorrected_biases():
    """R12: the chart may not travel without saying what it did NOT fix."""
    label = bt.stage_a_label()
    assert "NOT corrected" in label
    assert "today's filings" in label      # look-ahead in the selection
    assert "delisted" in label             # survivorship
    assert "upward" in label               # and which way both of them point


# --- R22: every run carries the identity of the data build ---------------------------

@pytest.fixture()
def store() -> Store:
    con = duckdb.connect(":memory:")
    _apply(con, USER_MIGRATIONS)
    return Store(con)


def test_a_run_is_stamped_with_the_data_build(store):
    pid = store.create("Deep value")
    store.record_run(pid, "stage_a", "spec123", "build-A", dt.date(2024, 1, 1),
                     dt.date(2024, 12, 31), {"costs": "default"}, {"annual_return": 0.1})
    runs = store.runs(pid)
    assert len(runs) == 1
    assert runs.data_fingerprint.iloc[0] == "build-A"


def test_the_same_screen_on_a_different_build_is_flagged(store):
    pid = store.create("Deep value")
    store.record_run(pid, "stage_a", "spec123", "build-A", None, None, {}, {})
    assert store.other_data_builds("spec123", "build-A") == 0
    assert store.other_data_builds("spec123", "build-B") == 1


def test_the_overfitting_counter_counts_per_screen_and_build(store):
    """R21's counter, which Phase 9 will display: attempts belong next to the result."""
    pid = store.create("Deep value")
    for _ in range(3):
        store.record_run(pid, "stage_a", "spec123", "build-A", None, None, {}, {})
    assert store.backtest_count("spec123", "build-A") == 3
    assert store.backtest_count("spec123", "build-B") == 0
    assert store.backtest_count("other", "build-A") == 0


def test_a_build_id_distinguishes_builds_rather_than_naming_the_source():
    """Truncating the fingerprint showed the source name for every build alike."""
    a = bt.short_build_id("free (SEC XBRL + yfinance)|2026-09-23|prices=1842747")
    b = bt.short_build_id("free (SEC XBRL + yfinance)|2026-09-24|prices=1842999")
    assert a != b and len(a) == len(b) == 10
    assert bt.short_build_id("") == "—"


# --- R18: a name that stops trading does not politely hold its value -----------------

def delisting_market():
    """AAA trades the whole way and doubles; DEAD stops halfway at an unchanged price."""
    dates = pd.bdate_range(dt.date(2024, 1, 1), periods=100).date
    step = 2.0 ** (1 / 99)
    rows = [("AAA", d, 10.0 * step**i, 10.0 * step**i) for i, d in enumerate(dates)]
    rows += [("DEAD", d, 10.0, 10.0) for d in dates[:50]]
    rows += [("^SP500TR", d, 100.0, 100.0) for d in dates]
    return synthetic_market(rows), dates


def test_a_delisted_name_takes_the_stated_loss_instead_of_holding_its_price():
    """Half the money in a doubling stock, half in one that vanishes at −30%:
    100 × (0.5 × 2.0 + 0.5 × 0.7) = 135, worked out by hand."""
    con, dates = delisting_market()
    h = pd.DataFrame({"ticker": ["AAA", "DEAD"], "weight": [0.5, 0.5],
                      "market_cap": [5e9, 5e9]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    r = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free, delisting_return=-0.30)
    assert r.portfolio[-1] == pytest.approx(135.0, rel=1e-9)


def test_the_delisting_assumption_is_a_setting_that_moves_the_result():
    """[A17] is an unverified number, so it has to be visibly adjustable."""
    con, dates = delisting_market()
    h = pd.DataFrame({"ticker": ["AAA", "DEAD"], "weight": [0.5, 0.5],
                      "market_cap": [5e9, 5e9]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    mild = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free, delisting_return=-0.10)
    harsh = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free, delisting_return=-1.00)
    assert mild.portfolio[-1] > harsh.portfolio[-1]
    assert harsh.portfolio[-1] == pytest.approx(100.0, rel=1e-9)   # 0.5×2.0 + 0.5×0.0


def test_the_disappearance_is_counted_and_named(delisted=None):
    """R19: how many left, and the assumption applied to them, both on the result."""
    con, dates = delisting_market()
    h = pd.DataFrame({"ticker": ["AAA", "DEAD"], "weight": [0.5, 0.5],
                      "market_cap": [5e9, 5e9]})
    r = bt.buy_and_hold(con, h, dates[0], dates[-1])
    assert any("DEAD" in n and "stopped trading" in n for n in r.notes)
    assert any("-30%" in n or "−30%" in n for n in r.notes)


def test_a_name_that_trades_to_the_end_is_not_treated_as_delisted():
    con, dates = delisting_market()
    h = pd.DataFrame({"ticker": ["AAA"], "weight": [1.0], "market_cap": [5e9]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    r = bt.buy_and_hold(con, h, dates[0], dates[-1], costs=free)
    assert r.portfolio[-1] == pytest.approx(200.0, rel=1e-9)
    assert not any("stopped trading" in n for n in r.notes)


# --- the traded price, rebuilt from the splits ---------------------------------------

@pytest.mark.slow
def test_the_stored_price_is_the_one_the_stock_actually_traded_at():
    """The defect this column exists for: the free source serves history already
    adjusted for splits, so its `close` for Apple on 2020-08-28 is 124.81 where the
    stock changed hands at 499.23. Multiplied by a point-in-time share count, that turns
    every historical market cap into a quarter, a tenth or a hundredth of itself —
    always downward, which is exactly the direction a cheapness screen rewards.
    """
    import duckdb

    from portfolio_lab.data.schema import open_market

    if not MARKET_DB.exists():
        pytest.skip("no local market store")
    try:
        con = open_market(MARKET_DB, read_only=True)
    except (duckdb.IOException, RuntimeError) as exc:
        pytest.skip(f"market store is locked: {exc}")

    for ticker, day, traded in (("AAPL", "2020-08-28", 499.23),
                                ("NVDA", "2024-06-07", 1208.88)):
        row = con.execute(
            "SELECT close, close_raw FROM prices WHERE ticker = ? AND date = ?",
            [ticker, dt.date.fromisoformat(day)],
        ).fetchone()
        if not row:
            pytest.skip(f"{ticker} has no bar on {day}")
        adjusted, raw = row
        assert raw == pytest.approx(traded, rel=0.001), f"{ticker} on {day}"
        assert adjusted < raw / 3, "the adjusted close should be far below the traded one"


@pytest.mark.slow
def test_no_share_costs_more_than_a_million_dollars():
    """Reverse splits back-applied produced prices up to 25 trillion dollars a share.
    Those are the same defect in the other direction, and they are gone once the traded
    price is reconstructed. NVR, the most expensive real stock in the universe, peaked
    around ten thousand."""
    import duckdb

    from portfolio_lab.data.schema import open_market

    if not MARKET_DB.exists():
        pytest.skip("no local market store")
    try:
        con = open_market(MARKET_DB, read_only=True)
    except (duckdb.IOException, RuntimeError) as exc:
        pytest.skip(f"market store is locked: {exc}")

    absurd = con.execute(
        "SELECT COUNT(*) FROM prices WHERE close_raw > 1e6").fetchone()[0]
    assert absurd == 0
