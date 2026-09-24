"""Bollinger bands and the candle precondition.

The bands are the only place in the app where a number is drawn on a chart without a
filing behind it, so the arithmetic is pinned down here rather than eyeballed.
"""
import numpy as np
import pandas as pd
import pytest

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


# --- the two-panel layout: price on top, volume below ---------------------------------

def _bars(n=30):
    dates = pd.bdate_range("2026-01-05", periods=n)
    close = np.linspace(10, 12, n)
    close[5] = 9.0                                        # one down day
    return pd.DataFrame({"date": dates, "open": close - 0.1, "high": close + 0.2,
                         "low": close - 0.2, "close": close,
                         "volume": np.arange(1, n + 1) * 1000.0})


def test_price_sits_on_top_and_volume_underneath_on_one_time_axis():
    from portfolio_lab import charts

    fig = charts.price_figure(_bars(), "AAA")
    where = {(t.type, t.name): (t.xaxis, t.yaxis) for t in fig.data}
    assert where[("scatter", "AAA")] == ("x", "y"), "the price line is the top panel"
    assert where[("bar", "volume")] == ("x2", "y2"), "volume is the bottom panel"
    assert fig.layout.xaxis.matches == "x2" or fig.layout.xaxis2.matches == "x"


def test_candles_replace_the_line_on_top_and_volume_stays_below():
    from portfolio_lab import charts

    fig = charts.price_figure(_bars(), "AAA", candles=True)
    kinds = {t.type: t.yaxis for t in fig.data}
    assert kinds["candlestick"] == "y" and kinds["bar"] == "y2"
    assert not any(t.type == "scatter" and t.name == "AAA" for t in fig.data)


def test_volume_bars_take_the_colour_of_the_day_and_carry_their_average():
    from portfolio_lab import charts, ui

    fig = charts.price_figure(_bars(), "AAA")
    bars = next(t for t in fig.data if t.type == "bar")
    assert bars.marker.color[5] == ui.DOWN and bars.marker.color[6] == ui.UP
    avg = next(t for t in fig.data if t.name.endswith("avg volume"))
    assert pd.isna(avg.y[13]) and avg.y[14] == pytest.approx(8_000.0)


def test_without_volume_there_is_one_panel():
    from portfolio_lab import charts

    fig = charts.price_figure(_bars().drop(columns="volume"), "AAA")
    assert {t.type for t in fig.data} == {"scatter"}
    assert "xaxis2" not in fig.layout.to_plotly_json()


def test_trades_are_marked_on_the_price():
    from portfolio_lab import charts

    bars = _bars()
    trades = pd.DataFrame({"date": [bars.date[3], bars.date[20]],
                           "price": [10.3, 11.4], "side": ["buy", "sell"]})
    fig = charts.price_figure(bars, "AAA", trades=trades)
    marks = {t.name: t.yaxis for t in fig.data if t.name in ("buy", "sell")}
    assert marks == {"buy": "y", "sell": "y"}


def test_a_store_without_index_series_still_has_the_lookup_columns():
    """The research tab filters this frame by `symbol` for every ticker typed; without
    any index or commodity series stored it used to come back with no columns."""
    import duckdb

    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    frame = rs.price_only_symbols(con)
    assert frame.empty and "symbol" in frame.columns
