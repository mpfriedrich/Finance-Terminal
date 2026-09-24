"""Indicators, each a pure function of a price series.

Pure on purpose: every one of these takes a series and returns a series of the same
length, with no reference to today, no database and no state. That is what makes them
testable against a series whose answer can be worked out by hand — which is the whole
argument for rules-as-data over generated code.

One convention runs through all of them: a signal for day *t* may only use information
available at the close of day *t*. The engine then fills at the next open. Any indicator
that peeks at t+1 turns a backtest into a prophecy, and it is the easiest mistake to make
here — `rolling()` centred instead of trailing, a `shift` in the wrong direction, a
z-score computed over the whole series including its future.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def moving_average(prices: pd.Series, days: int) -> pd.Series:
    """Trailing simple average. NaN until `days` observations exist — not a shorter
    average, which would be a different and much jumpier statistic."""
    return prices.rolling(window=days, min_periods=days).mean()


def ma_cross(prices: pd.Series, fast: int, slow: int) -> pd.Series:
    """True while the fast average is above the slow one."""
    if fast >= slow:
        raise ValueError("the fast average has to be shorter than the slow one")
    return (moving_average(prices, fast) > moving_average(prices, slow)).fillna(False)


def momentum(prices: pd.Series, lookback: int, threshold: float = 0.0) -> pd.Series:
    """True while the trailing `lookback`-day return exceeds `threshold`."""
    past = prices.shift(lookback)
    return ((prices / past - 1.0) > threshold).fillna(False)


def zscore(prices: pd.Series, lookback: int) -> pd.Series:
    """How many trailing standard deviations the price sits from its trailing average.

    Trailing, never centred: a centred window would average tomorrow's prices into
    today's signal, which is the most comfortable way to build a strategy that cannot
    be traded.
    """
    mean = prices.rolling(lookback, min_periods=lookback).mean()
    sd = prices.rolling(lookback, min_periods=lookback).std(ddof=0)
    return (prices - mean) / sd.replace(0.0, np.nan)


def mean_reversion(prices: pd.Series, lookback: int, z_entry: float,
                   z_exit: float) -> pd.Series:
    """Enter below `z_entry`, stay in until the z-score climbs back above `z_exit`.

    Two thresholds rather than one, because a single line makes the position flicker in
    and out on every wobble around it — and every flicker costs a spread.
    """
    z = zscore(prices, lookback)
    holding = False
    out: list[bool] = []
    for value in z:
        if np.isnan(value):
            out.append(False)
            continue
        if not holding and value <= z_entry:
            holding = True
        elif holding and value >= z_exit:
            holding = False
        out.append(holding)
    return pd.Series(out, index=prices.index)


def apply_stop(held: pd.Series, prices: pd.Series, kind: str, pct: float,
               cooldown: int = 0) -> pd.Series:
    """Force an exit when a position falls `pct` below its entry, or below its high.

    The stop is checked on the same closes the signal uses, so it can only act on what
    was knowable.

    A stopped-out name comes back when the signal has turned off and on again — or after
    `cooldown` days, if one is set. One rule, two releases, and the default matters: with
    no cooldown a permanently-true signal would otherwise re-buy the next morning at the
    stopped-out price, which turns the stop into a toll booth that charges a spread on
    the way past instead of protecting anything.
    """
    if kind == "none" or pct <= 0:
        return held

    out: list[bool] = []
    entry: float | None = None
    peak: float | None = None
    blocked = 0
    awaiting_fresh_signal = False
    for flag, price in zip(held, prices):
        if not flag:
            # The signal itself let go: the stop's memory is cleared and a later entry
            # is a new decision rather than a re-entry into the position it just closed.
            awaiting_fresh_signal = False
            out.append(False)
            entry = peak = None
            continue
        if blocked > 0:
            blocked -= 1
            if blocked == 0 and cooldown > 0:
                awaiting_fresh_signal = False      # the time-based bar has been served
            out.append(False)
            continue
        if awaiting_fresh_signal:
            out.append(False)
            continue
        if entry is None:
            entry, peak = price, price
        peak = max(peak, price)
        reference = entry if kind == "stop_loss" else peak
        if price <= reference * (1.0 - pct):
            out.append(False)
            blocked = cooldown
            awaiting_fresh_signal = True
            entry = peak = None
        else:
            out.append(True)
    return pd.Series(out, index=held.index)


def _hysteresis(enter: pd.Series, leave: pd.Series) -> pd.Series:
    """In from the first `enter` day until the first `leave` day, then out again.

    Most rules below are two-threshold rules: a single line would flip the position on
    every wobble around it, and every flip costs a spread. Days where the inputs are
    not yet defined (warm-up) count as neither.
    """
    holding = False
    out: list[bool] = []
    for go_in, go_out in zip(enter.fillna(False), leave.fillna(False)):
        if not holding and go_in:
            holding = True
        elif holding and go_out:
            holding = False
        out.append(holding)
    return pd.Series(out, index=enter.index)


def bollinger_bands(prices: pd.Series, days: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Trailing average ± k population standard deviations — Bollinger's definition."""
    middle = prices.rolling(days, min_periods=days).mean()
    sd = prices.rolling(days, min_periods=days).std(ddof=0)
    return pd.DataFrame({"lower": middle - k * sd, "middle": middle,
                         "upper": middle + k * sd}, index=prices.index)


def bollinger_reversion(prices: pd.Series, days: int = 20, k: float = 2.0) -> pd.Series:
    """Buy a close below the lower band, sell once it is back at the average."""
    b = bollinger_bands(prices, days, k)
    return _hysteresis(prices < b.lower, prices >= b.middle)


def bollinger_breakout(prices: pd.Series, days: int = 20, k: float = 2.0) -> pd.Series:
    """Buy a close above the upper band, sell once it falls back under the average."""
    b = bollinger_bands(prices, days, k)
    return _hysteresis(prices > b.upper, prices < b.middle)


def rsi(prices: pd.Series, days: int = 14) -> pd.Series:
    """Wilder's relative strength index, 0–100, with his smoothing (alpha = 1/days).

    NaN until `days` changes exist, like every other indicator here.
    """
    change = prices.diff()
    gain = change.clip(lower=0.0).ewm(alpha=1.0 / days, adjust=False,
                                      min_periods=days).mean()
    loss = (-change).clip(lower=0.0).ewm(alpha=1.0 / days, adjust=False,
                                         min_periods=days).mean()
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # No losses at all in the window is RSI 100, not undefined.
    return out.where(~(loss.eq(0.0) & gain.notna()), 100.0)


def rsi_signal(prices: pd.Series, days: int = 14, entry: float = 30.0,
               exit: float = 70.0) -> pd.Series:
    """Buy when the RSI drops to `entry` (oversold), sell when it reaches `exit`."""
    r = rsi(prices, days)
    return _hysteresis(r <= entry, r >= exit)


def channel_breakout(prices: pd.Series, entry_days: int = 55,
                     exit_days: int = 20) -> pd.Series:
    """Donchian / turtle rule: buy a close above the highest close of the previous
    `entry_days`, sell a close below the lowest of the previous `exit_days`.

    `shift(1)` before the rolling window, so today's close is compared with the channel
    of the days BEFORE it — including today would make a new high impossible to beat.
    """
    high = prices.shift(1).rolling(entry_days, min_periods=entry_days).max()
    low = prices.shift(1).rolling(exit_days, min_periods=exit_days).min()
    return _hysteresis(prices > high, prices < low)


def macd(prices: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD line (fast EMA − slow EMA) and its signal line (EMA of the MACD)."""
    if fast >= slow:
        raise ValueError("the fast average has to be shorter than the slow one")
    line = (prices.ewm(span=fast, adjust=False, min_periods=fast).mean()
            - prices.ewm(span=slow, adjust=False, min_periods=slow).mean())
    return pd.DataFrame({"macd": line,
                         "signal": line.ewm(span=signal, adjust=False,
                                            min_periods=signal).mean()},
                        index=prices.index)


def macd_signal(prices: pd.Series, fast: int = 12, slow: int = 26,
                signal: int = 9) -> pd.Series:
    """Hold while the MACD line is above its signal line."""
    m = macd(prices, fast, slow, signal)
    return (m.macd > m.signal).fillna(False)


def trend_filter(held: pd.Series, prices: pd.Series, days: int) -> pd.Series:
    """Only hold while the price is above its own `days`-day average.

    The classic way to combine rules: a dip-buying or breakout signal, allowed only in
    an uptrend. 0 switches it off.
    """
    if days <= 0:
        return held
    return held & (prices > moving_average(prices, days)).fillna(False)
