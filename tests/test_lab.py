"""The strategy lab: indicators against hand-computable series, and an execution model
that cannot see tomorrow.

The single most valuable test in this file is the look-ahead one. A backtest that fills
at the close it screened on earns the average overnight move on every trade, in the
direction of the signal, every time — and it looks like skill.
"""
import numpy as np
import pandas as pd
import pytest

from portfolio_lab.config import CostModel
from portfolio_lab.lab import engine as lab
from portfolio_lab.lab import signals as sg
from portfolio_lab.lab.spec import StrategySpec, spec_from_json, spec_to_json

FREE = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))


def series(values):
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values)),
                     dtype=float)


# --- indicators -----------------------------------------------------------------------

def test_a_moving_average_is_empty_until_it_has_enough_days():
    ma = sg.moving_average(series([1, 2, 3, 4]), 3)
    assert ma.isna().tolist() == [True, True, False, False]
    assert ma.iloc[2] == pytest.approx(2.0)


def test_the_cross_turns_on_when_the_fast_average_overtakes_the_slow_one():
    prices = series([10, 10, 10, 10, 11, 12, 13, 14])
    flags = sg.ma_cross(prices, 2, 4)
    assert not flags.iloc[3]
    assert flags.iloc[-1]


def test_a_fast_average_longer_than_the_slow_one_is_refused():
    with pytest.raises(ValueError):
        sg.ma_cross(series([1, 2, 3]), 10, 5)


def test_momentum_compares_against_the_price_that_many_days_ago():
    prices = series([100, 100, 100, 110])
    assert sg.momentum(prices, 3, 0.05).tolist() == [False, False, False, True]
    assert sg.momentum(prices, 3, 0.20).tolist() == [False, False, False, False]


def test_the_zscore_looks_backwards_only():
    """A centred window would average tomorrow into today — the most comfortable way to
    build a strategy nobody could have traded."""
    prices = series(list(range(1, 21)))
    z = sg.zscore(prices, 5)
    assert z.head(4).isna().all()
    # A steadily rising series is always ABOVE its trailing mean, never below.
    assert (z.dropna() > 0).all()


def test_mean_reversion_holds_until_the_exit_level_not_at_every_wobble():
    prices = series([10] * 20 + [7] + [8, 9, 10, 11] + [10] * 5)
    flags = sg.mean_reversion(prices, 20, z_entry=-1.0, z_exit=0.0)
    assert flags.iloc[20]            # entered on the drop
    assert flags.iloc[21]            # still in while it recovers
    assert not flags.iloc[-1]        # out once it is back above its average


def test_a_trailing_stop_fires_on_the_fall_from_the_high_not_from_the_entry():
    prices = series([10, 12, 14, 11, 11])
    kept = sg.apply_stop(pd.Series([True] * 5, index=prices.index), prices,
                         "trailing_stop", 0.20)
    assert kept.tolist() == [True, True, True, False, False]


def test_a_stop_loss_ignores_the_high_and_watches_the_entry():
    prices = series([10, 12, 14, 11, 7.9])
    kept = sg.apply_stop(pd.Series([True] * 5, index=prices.index), prices,
                         "stop_loss", 0.20)
    assert kept.tolist() == [True, True, True, True, False]


def test_after_a_stop_a_cooldown_releases_the_name_again():
    prices = series([10, 7, 10, 10, 10])
    kept = sg.apply_stop(pd.Series([True] * 5, index=prices.index), prices,
                         "stop_loss", 0.20, cooldown=2)
    assert kept.tolist() == [True, False, False, False, True]


def test_without_a_cooldown_a_stopped_name_needs_a_fresh_signal():
    """A rule that re-buys the next morning at the price that just stopped it out is not
    a stop, it is a toll booth. With no cooldown the signal has to let go first."""
    prices = series([10, 7, 10, 10, 10])
    always = pd.Series([True] * 5, index=prices.index)
    assert sg.apply_stop(always, prices, "stop_loss", 0.20).tolist() == [
        True, False, False, False, False]

    # the same prices, but the signal goes off in between: re-entry is then a new decision
    flickering = pd.Series([True, True, False, True, True], index=prices.index)
    assert sg.apply_stop(flickering, prices, "stop_loss", 0.20).tolist() == [
        True, False, False, True, True]


# --- execution ------------------------------------------------------------------------

def bars(closes, opens=None, ticker="AAA"):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    opens = opens if opens is not None else closes
    return pd.DataFrame({"ticker": ticker, "date": idx.date,
                         "open_adj": opens, "close_adj": closes})


def test_buy_and_hold_earns_the_move_from_the_first_fill_onwards():
    """Not the whole series: the run starts flat and the first signal, read at the close
    of day one, can only be filled at the open of day two. Counting day one's move would
    be the look-ahead this engine exists to avoid — so 110 -> 121, not 100 -> 121."""
    frame = bars([100, 110, 121])
    r = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    assert r.metrics["total_return"] == pytest.approx(121 / 110 - 1, rel=1e-9)


def test_the_signal_is_filled_at_the_next_open_not_at_the_close_it_was_read_from():
    """The whole point. The jump between close(t) and open(t+1) belongs to whoever held
    the position overnight, and a signal read at close(t) did not."""
    closes = [100, 100, 100, 100]
    opens = [100, 100, 100, 130]        # a gap up on the morning of the fill
    frame = bars(closes, opens)
    spec = StrategySpec(signal="momentum", lookback_days=1, threshold=-1.0)  # always true
    r = lab.run(frame, spec, costs=FREE)
    # If the engine filled at the close it read, it would capture the +30% gap.
    assert r.metrics["total_return"] < 0.01


def test_costs_are_charged_on_what_changed_hands():
    frame = bars([100, 100, 100, 100])
    charged = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.01),))
    flat = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    paid = lab.run(frame, StrategySpec(signal="always_in"), costs=charged)
    assert paid.metrics["total_return"] < flat.metrics["total_return"]
    assert paid.trades >= 1


def test_a_rule_that_never_enters_stays_flat_and_says_so():
    frame = bars([100, 90, 80, 70])
    spec = StrategySpec(signal="momentum", lookback_days=1, threshold=10.0)
    r = lab.run(frame, spec, costs=FREE)
    assert r.metrics["total_return"] == pytest.approx(0.0, abs=1e-12)
    assert r.metrics["average_exposure"] == pytest.approx(0.0)
    assert r.trades == 0


def test_a_stop_takes_the_position_out_and_the_loss_stops_there():
    closes = [100, 100, 70, 40, 20]
    frame = bars(closes)
    spec = StrategySpec(signal="always_in", stop="stop_loss", stop_pct=0.2)
    r = lab.run(frame, spec, costs=FREE)
    held = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    assert r.metrics["total_return"] > held.metrics["total_return"]


def test_two_names_are_weighted_equally_by_default():
    """One doubles, one goes nowhere; half in each is +50% — measured from the fill."""
    frame = pd.concat([bars([100, 100, 200], ticker="AAA"),
                       bars([100, 100, 100], ticker="BBB")], ignore_index=True)
    r = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    assert r.metrics["total_return"] == pytest.approx(0.5, rel=1e-9)


def test_the_position_cap_leaves_the_rest_in_cash():
    """Two names capped at 25% each means half the money never goes to work, so a
    doubling of both lifts the portfolio by 50%, not by 100%."""
    frame = pd.concat([bars([100, 100, 200], ticker="AAA"),
                       bars([100, 100, 200], ticker="BBB")], ignore_index=True)
    capped = lab.run(frame, StrategySpec(signal="always_in", max_weight=0.25), costs=FREE)
    assert capped.metrics["average_exposure"] <= 0.5 + 1e-9
    assert capped.metrics["total_return"] == pytest.approx(0.5, rel=1e-9)


# --- the spec itself ------------------------------------------------------------------

def test_the_rule_reads_back_in_plain_language():
    lines = " ".join(StrategySpec(signal="ma_cross", fast_days=20, slow_days=100,
                                  stop="trailing_stop", stop_pct=0.15).describe())
    assert "20-day average is above the 100-day" in lines
    assert "15% below its high" in lines
    assert "next open" in lines


def test_the_fingerprint_survives_a_round_trip_and_separates_rules():
    a = StrategySpec(signal="ma_cross", fast_days=50)
    b = StrategySpec(signal="ma_cross", fast_days=51)
    assert a.fingerprint() != b.fingerprint()
    assert spec_from_json(spec_to_json(a)).fingerprint() == a.fingerprint()
