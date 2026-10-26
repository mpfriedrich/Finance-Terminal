"""The strategy lab: indicators against hand-computable series, and an execution model
that cannot see tomorrow.

The single most valuable test in this file is the look-ahead one. A backtest that fills
at the close it screened on earns the average overnight move on every trade, in the
direction of the signal, every time — and it looks like skill.
"""
import json

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


# --- the translator: a closed vocabulary, and an honest gap where it ends -------------

from portfolio_lab.lab import translate as LT


def test_the_schema_cannot_hold_a_company():
    """The model translates a rule; it does not pick stocks. There is no field to do it
    with, which is stronger than telling it not to."""
    blob = json.dumps(LT.TOOL).lower()
    for word in ("ticker", "symbol", "company", "cik", "stock_name"):
        assert word not in blob


def test_the_signals_are_a_closed_enum():
    assert set(LT.TOOL["input_schema"]["properties"]["signal"]["enum"]) == set(
        ("always_in", "ma_cross", "momentum", "mean_reversion", "bollinger_reversion",
         "bollinger_breakout", "rsi", "channel_breakout", "macd"))
    assert LT.TOOL["strict"] is True


def test_a_golden_cross_becomes_the_rule_it_names():
    spec = LT.payload_to_spec(
        {"signal": "ma_cross", "fast_days": 50, "slow_days": 200, "stop": "none",
         "weighting": "equal", "unsupported": []}, StrategySpec())
    assert spec.signal == "ma_cross" and spec.fast_days == 50 and spec.slow_days == 200


def test_a_second_message_refines_rather_than_restarts():
    first = LT.payload_to_spec(
        {"signal": "ma_cross", "fast_days": 20, "slow_days": 100, "stop": "none",
         "weighting": "equal", "unsupported": []}, StrategySpec())
    second = LT.payload_to_spec(
        {"signal": "ma_cross", "stop": "trailing_stop", "stop_pct": 0.15,
         "weighting": "equal", "unsupported": []}, first)
    assert second.fast_days == 20 and second.slow_days == 100   # kept
    assert second.stop == "trailing_stop" and second.stop_pct == 0.15


def test_what_cannot_be_expressed_is_reported_not_approximated():
    """An RSI rule has no home in this vocabulary. Turning it into the nearest available
    indicator would hand the person a result they would read as their own idea."""
    spec = LT.payload_to_spec(
        {"signal": "always_in", "stop": "none", "weighting": "equal",
         "unsupported": ["buy when the RSI drops below 30"]}, StrategySpec())
    assert spec.unsupported == ("buy when the RSI drops below 30",)
    assert spec.signal == "always_in"


def test_a_swapped_pair_of_averages_is_refused_rather_than_reordered():
    """The engine cannot compute a fast average longer than the slow one, and silently
    swapping them would change the person's rule into a different one."""
    previous = StrategySpec(signal="ma_cross", fast_days=50, slow_days=200)
    spec = LT.payload_to_spec(
        {"signal": "ma_cross", "fast_days": 200, "slow_days": 50, "stop": "none",
         "weighting": "equal", "unsupported": []}, previous)
    assert (spec.fast_days, spec.slow_days) == (50, 200)
    assert any("shorter than" in u for u in spec.unsupported)


def test_the_diff_says_what_changed():
    before = StrategySpec(signal="always_in")
    after = StrategySpec(signal="momentum", lookback_days=252)
    changed = " ".join(LT.diff_summary(before, after))
    assert "signal" in changed and "lookback_days" in changed


# --- prices that are not prices --------------------------------------------------------

def test_a_zero_price_is_treated_as_missing_rather_than_as_a_collapse():
    """The data source carries stretches of zero adjusted closes — 732 days of them for
    one name in this universe. Dividing by zero gives an infinity that multiplies into
    every later day, and the whole run reports +inf% a year. Found exactly that way."""
    frame = bars([100, 0, 0, 120])
    r = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    assert np.isfinite(r.metrics["total_return"])
    assert np.isfinite(r.metrics["annual_return"])
    assert any("zero or less" in n for n in r.notes)


def test_the_broken_days_are_named_not_just_survived():
    frame = bars([100, 0, 100, 100])
    r = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    assert any("AAA" in n for n in r.notes)


def test_a_negative_price_is_refused_too():
    frame = bars([100, -5, 100, 110])
    r = lab.run(frame, StrategySpec(signal="always_in"), costs=FREE)
    assert np.isfinite(r.metrics["total_return"])


# --- the single-stock rules: Bollinger, RSI, breakout, MACD, trend filter -------------

def test_bollinger_reversion_buys_under_the_band_and_sells_at_the_average():
    """Flat at 100 for 25 days, then 90 (far under a band of zero width), then 100 again
    (above the average, which the 90 has pulled to 99.5)."""
    held = sg.bollinger_reversion(series([100.0] * 25 + [90.0, 100.0, 100.0]))
    assert not held.iloc[24]
    assert held.iloc[25], "a close under the lower band is a buy"
    assert not held.iloc[26], "back at the average is a sell"


def test_bollinger_breakout_buys_over_the_band_and_sells_under_the_average():
    held = sg.bollinger_breakout(series([100.0] * 25 + [110.0, 109.0, 95.0]))
    assert held.iloc[25] and held.iloc[26]
    assert not held.iloc[27]


def test_rsi_is_100_when_it_only_rises_and_0_when_it_only_falls():
    assert sg.rsi(series(range(1, 40))).iloc[-1] == pytest.approx(100.0)
    assert sg.rsi(series(range(40, 1, -1))).iloc[-1] == pytest.approx(0.0)


def test_rsi_is_50_when_gains_and_losses_match():
    r = sg.rsi(series([100.0, 101.0] * 40))
    assert 45.0 < r.iloc[-1] < 55.0


def test_rsi_is_empty_during_its_warm_up():
    assert sg.rsi(series(range(1, 40)), days=14).iloc[:13].isna().all()


def test_the_rsi_rule_buys_oversold_and_sells_overbought():
    falling, rising = list(range(100, 70, -1)), list(range(71, 130))
    held = sg.rsi_signal(series(falling + rising))
    assert held.iloc[len(falling) - 1], "RSI near 0 after a straight fall is a buy"
    assert not held.iloc[-1], "RSI near 100 after a straight rise is a sell"


def test_a_breakout_needs_a_close_above_the_previous_days_not_including_today():
    """Rising every day: from day 55 on, each close beats the 55 closes before it."""
    held = sg.channel_breakout(series(range(1, 80)), entry_days=55, exit_days=20)
    assert not held.iloc[54]
    assert held.iloc[55]


def test_the_breakout_exit_is_a_close_under_the_recent_low():
    values = list(range(1, 70)) + [30.0]
    held = sg.channel_breakout(series(values), entry_days=55, exit_days=20)
    assert held.iloc[-2] and not held.iloc[-1]


def test_macd_holds_an_accelerating_rise_and_drops_an_accelerating_fall():
    up = series([100 * 1.01 ** i for i in range(80)])
    # Not 0.99**i: a constant percentage fall slows down in dollars, which lifts the
    # MACD. A fall that speeds up in dollars is what the indicator calls a downtrend.
    down = series([200 - 0.02 * i ** 2 for i in range(80)])
    assert sg.macd_signal(up).iloc[-1]
    assert not sg.macd_signal(down).iloc[-1]


def test_the_trend_filter_blocks_a_signal_below_the_average():
    prices = series([100.0] * 10 + [90.0] * 5)
    held = sg.trend_filter(pd.Series(True, index=prices.index), prices, days=5)
    assert not held.iloc[12], "below its own average: filtered out"


@pytest.mark.parametrize("spec", [
    StrategySpec(signal="bollinger_reversion"), StrategySpec(signal="bollinger_breakout"),
    StrategySpec(signal="rsi"), StrategySpec(signal="channel_breakout"),
    StrategySpec(signal="macd"), StrategySpec(signal="ma_cross", trend_filter_days=50),
])
def test_no_signal_reads_the_future(spec):
    """Rewrite everything after day 150: nothing on or before day 150 may change."""
    rng = np.random.default_rng(7)
    path = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    other = path.copy()
    other[151:] = other[151:] * np.exp(np.cumsum(rng.normal(0.01, 0.05, 149)))
    a = lab.held_series(series(path), spec)
    b = lab.held_series(series(other), spec)
    assert a.iloc[:151].equals(b.iloc[:151])


def test_fills_are_listed_at_the_next_open():
    """Bollinger reversion on one stock: the dip on day 25 is read at that close and
    bought at day 26's open; the recovery signal of day 26 is sold at day 27's open."""
    closes = [100.0] * 25 + [90.0, 100.0, 100.0, 100.0]
    opens = [100.0] * 25 + [95.0, 92.0, 99.0, 100.0]
    r = lab.run(bars(closes, opens), StrategySpec(signal="bollinger_reversion"), costs=FREE)
    days = pd.bdate_range("2024-01-01", periods=len(closes)).date
    assert list(r.fills.side) == ["buy", "sell"]
    assert list(r.fills.date) == [days[26], days[27]]
    assert list(r.fills.price) == [92.0, 99.0]


def test_a_rule_saved_before_the_new_fields_still_loads():
    old = json.dumps({"signal": "ma_cross", "fast_days": 20, "slow_days": 100,
                      "unsupported": []})
    spec = spec_from_json(old)
    assert spec.fast_days == 20 and spec.band_days == 20 and spec.trend_filter_days == 0
