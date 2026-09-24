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
