"""Rates, the curve, and what a company actually did on the day.

Two separate questions live here, and they need different tools.

**What does the market expect?** The honest instrument is the yield curve. Short yields
price the next few months of policy; long yields price the view beyond that. A move in
the three-month rate IS a change in expectations — no model needed to read it. What is
NOT here is a month-by-month path of rate decisions: that requires the strip of dated
fed funds futures, which this data source does not carry. Building one out of the front
contract would be extrapolation dressed as measurement.

**What did an event actually do?** That is an event study: strip out the market's own
move on the day and see what is left. The leftover — the abnormal return — is the part
attributable to the event rather than to the tide. Its cumulative version over a window
is the standard measure in the academic literature, and it is the only defensible way to
say a company "reacted" to something.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

# The curve, short to long, plus the front fed funds contract.
RATE_SERIES = {
    "^IRX": ("3 months", 0.25),
    "^FVX": ("5 years", 5.0),
    "^TNX": ("10 years", 10.0),
    "^TYX": ("30 years", 30.0),
}
FED_FUNDS_FRONT = "ZQ=F"


def rate_snapshot(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Where the curve is now and how far it has moved.

    Changes are in basis points because that is how rates are discussed and because a
    move from 4.96 to 5.10 is a 14bp move, not a 2.8% one — the percentage framing of a
    percentage is where rate conversations usually go wrong.
    """
    rows = []
    for symbol, (label, years) in RATE_SERIES.items():
        hist = con.execute(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date", [symbol]
        ).fetch_df()
        if hist.empty:
            continue
        hist["date"] = pd.to_datetime(hist["date"])
        s = hist.set_index("date")["close"].astype(float)
        row = {"symbol": symbol, "tenor": label, "years": years,
               "rate": float(s.iloc[-1]), "as_of": s.index[-1].date()}
        for label_, days in (("1w", 5), ("1m", 21), ("3m", 63), ("1y", 252)):
            row[f"change_{label_}"] = (
                float(s.iloc[-1] - s.iloc[-days]) * 100 if len(s) > days else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows).sort_values("years") if rows else pd.DataFrame()


def curve_shape(snapshot: pd.DataFrame) -> dict:
    """Slope and inversion.

    An inverted curve — short rates above long — has preceded most US recessions of the
    last fifty years. It is a well-documented regularity, not a mechanism, and it has
    been early by a year or more and occasionally wrong. Reported as what it is.
    """
    if snapshot.empty or len(snapshot) < 2:
        return {}
    short = snapshot.iloc[0]
    long = snapshot.iloc[-1]
    ten = snapshot[snapshot.symbol == "^TNX"]
    ten_rate = float(ten.iloc[0].rate) if len(ten) else float(long.rate)
    slope = ten_rate - float(short.rate)
    return {
        "short_tenor": short.tenor, "short_rate": float(short.rate),
        "long_tenor": "10 years", "long_rate": ten_rate,
        "slope_bp": slope * 100,
        "inverted": slope < 0,
        "as_of": short.as_of,
    }


def fed_funds_expectation(con: duckdb.DuckDBPyConnection) -> dict:
    """What the front fed funds contract implies for the current month's average rate.

    Price convention: 100 minus the contract price is the rate. Compared against the
    three-month bill, a lower implied rate means easing is priced in, a higher one
    tightening — for THIS month only. One contract cannot describe a path, and this does
    not pretend to.
    """
    ff = con.execute(
        "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 1",
        [FED_FUNDS_FRONT],
    ).fetchone()
    bill = con.execute(
        "SELECT close FROM prices WHERE ticker = '^IRX' ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not ff:
        return {}
    implied = 100.0 - float(ff[1])
    out = {"implied_rate": implied, "as_of": ff[0], "contract": FED_FUNDS_FRONT}
    if bill:
        out["bill_rate"] = float(bill[0])
        out["gap_bp"] = (implied - float(bill[0])) * 100
        out["direction"] = ("easing priced in" if out["gap_bp"] < -5
                            else "tightening priced in" if out["gap_bp"] > 5
                            else "no change priced in")
    return out


def event_study(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    event_date: dt.date,
    benchmark: str = "^SP500TR",
    estimation_days: int = 120,
    gap_days: int = 10,
    window: tuple[int, int] = (-5, 5),
) -> dict:
    """What this share did around an event, net of the market.

    The market model: fit `stock = alpha + beta x market` on a quiet estimation period
    BEFORE the event, then use it to predict what the share should have done each day of
    the event window. The gap between actual and predicted is the abnormal return, and
    its running total across the window is the cumulative abnormal return.

    Two details decide whether this measures anything:
      * The estimation period stops `gap_days` before the event, so anticipation and
        leakage do not contaminate the yardstick being used to judge the event.
      * The window is symmetric by default. Choosing it after seeing the result — moving
        the boundary until the number looks good — is the classic way this method gets
        abused, so the window is stated up front and reported with the answer.
    """
    px = con.execute(
        "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) ORDER BY date",
        [ticker],
    ).fetch_df()
    bm = con.execute(
        "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date", [benchmark]
    ).fetch_df()
    if px.empty or bm.empty:
        return {}
    for frame in (px, bm):
        frame["date"] = pd.to_datetime(frame["date"])

    joined = (
        px.set_index("date")["close_adj"].rename("stock")
        .to_frame()
        .join(bm.set_index("date")["close_adj"].rename("market"), how="inner")
        .astype(float)
        .pct_change()
        .dropna()
    )
    if joined.empty:
        return {}

    event = pd.Timestamp(event_date)
    trading = joined.index
    if event < trading[0] or event > trading[-1]:
        return {"error": f"{event_date} falls outside the stored price history."}
    # Nearest trading day at or after the event.
    after = trading[trading >= event]
    anchor = after[0] if len(after) else trading[-1]
    pos = int(trading.get_loc(anchor))

    est_end = pos - gap_days
    est_start = est_end - estimation_days
    if est_start < 0:
        return {"error": "Not enough history before the event to fit a market model."}

    est = joined.iloc[est_start:est_end]
    if len(est) < 30 or est.market.var() == 0:
        return {"error": "Estimation period too short or the market did not move."}
    beta = float(est.stock.cov(est.market) / est.market.var())
    alpha = float(est.stock.mean() - beta * est.market.mean())

    lo = max(pos + window[0], 0)
    hi = min(pos + window[1] + 1, len(joined))
    win = joined.iloc[lo:hi].copy()
    win["expected"] = alpha + beta * win.market
    win["abnormal"] = win.stock - win.expected
    win["cumulative"] = win.abnormal.cumsum()
    win["offset"] = range(lo - pos, hi - pos)

    resid = est.stock - (alpha + beta * est.market)
    sigma = float(resid.std())
    car = float(win.cumulative.iloc[-1])
    # t-statistic of the cumulative abnormal return against estimation-period noise.
    t_stat = car / (sigma * np.sqrt(len(win))) if sigma > 0 else np.nan

    return {
        "ticker": ticker, "event_date": anchor.date(),
        "alpha": alpha, "beta": beta,
        "estimation_days": len(est),
        "window": window,
        "daily": win,
        "car": car,
        "t_stat": float(t_stat),
        "significant": bool(abs(t_stat) > 1.96) if not np.isnan(t_stat) else False,
        "benchmark": benchmark,
    }


def rate_sensitivity(
    con: duckdb.DuckDBPyConnection, ticker: str, min_days: int = 120
) -> pd.DataFrame:
    """How the share has moved on days when rates moved.

    Regressed on the CHANGE in each yield, not its level: a share does not care that the
    ten-year is at 5%, it cares that it went from 4.9 to 5.0 today. Levels would produce
    a spurious relationship from two series that merely trend together.
    """
    px = con.execute(
        "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) ORDER BY date",
        [ticker],
    ).fetch_df()
    if len(px) < min_days:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"])
    stock = px.set_index("date")["close_adj"].astype(float).pct_change().dropna()

    rows = []
    for symbol, (label, _) in RATE_SERIES.items():
        r = con.execute(
            "SELECT date, close FROM prices WHERE ticker = ? ORDER BY date", [symbol]
        ).fetch_df()
        if len(r) < min_days:
            continue
        r["date"] = pd.to_datetime(r["date"])
        # Yield changes in percentage points, so beta reads as "% move per 1pp of rate".
        moves = r.set_index("date")["close"].astype(float).diff().dropna()
        joined = pd.concat([stock.rename("a"), moves.rename("b")], axis=1)
        joined = joined.replace([np.inf, -np.inf], np.nan).dropna()
        if len(joined) < min_days or joined.b.var() == 0:
            continue
        rows.append({
            "tenor": label, "symbol": symbol,
            "correlation": float(joined.a.corr(joined.b)),
            "beta_per_1pp": float(joined.a.cov(joined.b) / joined.b.var()),
            "days": len(joined),
        })
    return pd.DataFrame(rows)
