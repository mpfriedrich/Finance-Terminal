"""Pricing things in gold instead of dollars.

A dollar is not a fixed yardstick. A portfolio that gained 20% in a year when the dollar
lost 15% of its purchasing power against gold did something rather different from what
the chart says, and the only way to see that is to change the unit and look again.

So this module converts a price series into ounces: divide by the gold price on the same
day. What comes out is the same asset measured against a different thing, and both
readings are legitimate — which is why this is a toggle and not a setting.

Deliberately limited, and the boundary is the interesting part:

  * **Time series only.** Curves, charts and the returns computed from them.
  * **Ratios are untouched**, because they have no unit. Price-to-book is a price divided
    by a price; converting both sides changes nothing, and a "gold-adjusted P/E" would
    be a number pretending to say something.
  * **The eligibility floors stay in dollars.** A minimum price of $5 and a minimum
    turnover of $1m measure whether a stock can actually be traded, and a broker answers
    that question in dollars. Converting them would mean the same screen returned
    different companies depending on which unit you happened to be looking at.

The gold series is `GC=F`, the front-month futures contract — the most liquid quote and
the one stored here already. It is a futures price, so it carries a small financing
component relative to spot bullion; over a year that is a fraction of a percent, well
inside the error of anything this is used for, but it is a futures price and not a coin
in your hand.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

GOLD_SYMBOL = "GC=F"
USD = "USD"
GOLD = "gold"


def gold_series(
    con: duckdb.DuckDBPyConnection, start: dt.date | None = None,
    end: dt.date | None = None,
) -> pd.Series:
    """Dollars per ounce, by date. Empty when nothing is stored."""
    sql = "SELECT date, close FROM prices WHERE ticker = ? AND close IS NOT NULL"
    params: list = [GOLD_SYMBOL]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    frame = con.execute(sql + " ORDER BY date", params).fetch_df()
    if frame.empty:
        return pd.Series(dtype=float)
    return frame.set_index("date")["close"].astype(float)


def convert(values: pd.Series | pd.DataFrame, gold: pd.Series):
    """Divide a dollar series (or every column of a frame) by the gold price per day.

    Gold is forward-filled onto the value's own dates: metals and equities do not keep
    identical calendars, and a missing gold bar must not delete a stock's day. Dates with
    no gold price at all — before the stored history begins — drop out, which is why the
    caller checks `covers()` first and says so rather than showing a shortened chart with
    no explanation.
    """
    if gold.empty:
        return values
    # Both sides are put on a datetime index before anything is aligned. One side
    # arriving as `datetime.date` and the other as `Timestamp` produces a union that
    # contains each day twice, and pandas then refuses to reindex at all — which is how
    # this failed the first time it met real data next to a hand-built series.
    metal = gold.copy()
    metal.index = pd.to_datetime(pd.Index(metal.index))
    metal = metal[~metal.index.duplicated(keep="last")].sort_index()

    wanted = pd.to_datetime(pd.Index(values.index))
    ounces = metal.reindex(wanted.union(metal.index)).ffill().reindex(wanted)
    ounces.index = values.index

    if isinstance(values, pd.DataFrame):
        return values.div(ounces, axis=0)
    return values.div(ounces)


def covers(gold: pd.Series, start: dt.date, end: dt.date) -> bool:
    """True when the stored gold history spans the window that is about to be drawn."""
    if gold.empty:
        return False
    first, last = min(gold.index), max(gold.index)
    return first <= start and last >= end


def label(unit: str) -> str:
    return "ounces of gold" if unit == GOLD else "US dollars"


def note(unit: str) -> str:
    """The sentence that travels with a converted chart."""
    if unit != GOLD:
        return ""
    return (
        f"Priced in {label(GOLD)} ({GOLD_SYMBOL}, front-month futures): every value is "
        "divided by the gold price of the same day. Ratios such as price-to-book are "
        "unchanged, because they have no unit, and the eligibility floors stay in "
        "dollars, because whether a stock can be traded is a question a broker answers "
        "in dollars."
    )
