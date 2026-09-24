"""Bollinger bands and the candle precondition.

The bands are the only place in the app where a number is drawn on a chart without a
filing behind it, so the arithmetic is pinned down here rather than eyeballed.
"""
import numpy as np
import pandas as pd

from portfolio_lab import research as rs


def flat(n=40, value=100.0):
    return pd.DataFrame({"close": [value] * n})


def test_a_flat_price_has_no_envelope():
    """Zero volatility, zero width. The bands collapse onto the average."""
    b = rs.bollinger(flat())
    assert b["upper"].iloc[-1] == b["lower"].iloc[-1] == 100.0


def test_the_warm_up_is_empty_rather_than_narrow():
    """A 20-day band from 3 days is a different statistic, not an early reading."""
    b = rs.bollinger(flat(), window=20)
    assert b["middle"].head(19).isna().all()
    assert b["middle"].iloc[19] == 100.0


def test_the_deviation_is_the_population_one():
    """Bollinger's definition uses ddof=0; pandas defaults to ddof=1 and runs wider."""
    closes = pd.DataFrame({"close": np.arange(1, 21, dtype=float)})
    b = rs.bollinger(closes, window=20, num_std=2.0)
    expected = closes["close"].std(ddof=0) * 2
    assert np.isclose(b["upper"].iloc[-1] - b["middle"].iloc[-1], expected)


def test_percent_b_places_the_price_in_the_envelope():
    """0 at the lower band, 1 at the upper — and deliberately NOT clamped there.

    A jump that breaks out of the envelope reads above 1, which is the whole point of
    the measure: "outside its recent range" has to look different from "at the edge of
    it". Clamping would hide exactly the days worth looking at.
    """
    middling = pd.DataFrame({"close": [9.0, 11.0] * 10})
    assert 0.0 <= rs.bollinger(middling, window=20)["percent_b"].iloc[-1] <= 1.0

    breakout = pd.DataFrame({"close": [10.0] * 19 + [12.0]})
    assert rs.bollinger(breakout, window=20)["percent_b"].iloc[-1] > 1.0


def test_bands_follow_the_series_they_are_asked_for():
    """Adjusted and unadjusted closes are different series and must not be mixed."""
    px = pd.DataFrame({"close": [10.0] * 20, "close_adj": [5.0] * 20})
    assert rs.bollinger(px, column="close")["middle"].iloc[-1] == 10.0
    assert rs.bollinger(px, column="close_adj")["middle"].iloc[-1] == 5.0


def test_candles_are_refused_when_the_intraday_range_is_missing():
    """Bars stored before the high/low columns existed must fall back to a line."""
    old = pd.DataFrame({"open": [1.0, 2.0], "close": [1.5, 2.5]})
    assert rs.has_intraday_range(old) is False

    null_range = old.assign(high=[np.nan, np.nan], low=[np.nan, np.nan])
    assert rs.has_intraday_range(null_range) is False

    full = old.assign(high=[1.6, 2.6], low=[0.9, 1.9])
    assert rs.has_intraday_range(full) is True


def test_a_single_usable_bar_is_not_a_candle_chart():
    one = pd.DataFrame({"open": [1.0, 2.0], "close": [1.5, 2.5],
                        "high": [1.6, np.nan], "low": [0.9, np.nan]})
    assert rs.has_intraday_range(one) is False
