"""The numbers a decision about real money would rest on (R9).

Every formula here is pinned by a test against a series whose right answer can be worked
out with a pencil — constant growth, one drawdown of known depth, returns that alternate
by a fixed amount — to a tolerance of one basis point (PLAN.md Phase 6). Metric code is
the quietest place in a system like this for an error to live: nothing crashes, the chart
still looks plausible, and the number is simply wrong.

Conventions, all of which change the answer and none of which are obvious:

  * **Daily, not monthly.** Volatility and drawdown are computed on daily closes. A
    monthly series hides the worst of a drawdown almost by construction, and the plan
    asks for the daily figure by name.
  * **Sample standard deviation (ddof=1).** The daily returns are a sample of a process,
    not the whole population. On a decade of data the difference is invisible; on a
    three-month run it is not, and consistency matters more than either choice.
  * **√252 to annualise.** The conventional count of trading days in a US year.
  * **Return is compound, measured against capital committed.** Not the mean of daily
    returns, which is higher than what was actually earned whenever the series moves.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

TRADING_DAYS = 252

# The 13-week US Treasury bill yield, as the market quotes it. PLAN.md names FRED's
# DGS3MO; this is the same instrument from the price source already in use, which
# removes the open question in [A16] — no second provider and no key. Stored as a
# percentage (4.25 means 4.25%), like every yield series from that source.
RISK_FREE_SYMBOL = "^IRX"


def annual_return(curve: pd.Series, years: float, base: float | None = None) -> float:
    """Compound annual growth, measured against the capital committed.

    `base` exists because a portfolio curve starts below its starting capital once the
    entry cost is paid. Annualising from the curve's own first value would measure the
    return on what survived the trade rather than on what was put in, and the trading
    costs would silently drop out of the headline figure.
    """
    if years <= 0 or curve.empty:
        return float("nan")
    start = curve.iloc[0] if base is None else base
    if start <= 0:
        return float("nan")
    return float((curve.iloc[-1] / start) ** (1.0 / years) - 1.0)


def annual_volatility(curve: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    returns = curve.pct_change().dropna()
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def max_drawdown(curve: pd.Series) -> float:
    """Worst peak-to-trough fall as a negative fraction.

    Measured from every running peak, not from the start: a portfolio that doubles and
    then halves has lost 50% of what it was worth, and calling that break-even is how a
    strategy that ruins someone looks survivable on paper.
    """
    if curve.empty:
        return float("nan")
    peak = curve.cummax()
    return float((curve / peak - 1.0).min())


def sharpe(curve: pd.Series, years: float, risk_free: float,
           base: float | None = None) -> float:
    """Excess return over the risk-free rate, per unit of volatility.

    Undefined, not infinite, when the series never moves: a return with no variation has
    no risk-adjusted reading, and reporting a huge number for it would rank a flat line
    above everything else.
    """
    vol = annual_volatility(curve)
    if not np.isfinite(vol) or vol == 0:
        return float("nan")
    return float((annual_return(curve, years, base) - risk_free) / vol)


def tracking_error(portfolio: pd.Series, benchmark: pd.Series,
                   periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised standard deviation of the DAILY difference to the index (R9).

    This is "deviation from the index" in the sense that matters: not how far behind the
    portfolio ended, but how differently it travelled. A portfolio that beats the index
    by a steady amount every day has a tracking error of zero — it is the same ride.
    """
    a = portfolio.pct_change().dropna()
    b = benchmark.pct_change().dropna()
    common = a.index.intersection(b.index)
    if len(common) < 2:
        return float("nan")
    excess = a.loc[common] - b.loc[common]
    return float(excess.std(ddof=1) * np.sqrt(periods_per_year))


def turnover_of_buy_and_hold() -> float:
    """Zero, by definition, after the initial purchase (R16b).

    A function rather than a literal so the table has one place to read it from, and so
    the day a rebalancing loop arrives there is an obvious thing to replace.
    """
    return 0.0


def risk_free_rate(
    con: duckdb.DuckDBPyConnection,
    start: dt.date,
    end: dt.date,
    fallback: float = 0.02,
) -> tuple[float, str]:
    """The average 13-week T-bill yield over the window, as a fraction, plus its source.

    Returns the source string so the screen can say where the number came from. A Sharpe
    ratio computed against an undocumented risk-free rate is not comparable with anyone
    else's, including your own from last month ([A16]).
    """
    row = con.execute(
        "SELECT AVG(close), COUNT(*) FROM prices WHERE ticker = ? AND date BETWEEN ? AND ?",
        [RISK_FREE_SYMBOL, start, end],
    ).fetchone()
    if row and row[0] is not None and row[1] >= 5:
        return float(row[0]) / 100.0, f"{RISK_FREE_SYMBOL} average over the window"
    return fallback, "settings fallback (no bill yield stored for this window)"


def summary(
    portfolio: pd.Series,
    benchmark: pd.Series | None,
    years: float,
    risk_free: float,
    base: float | None = None,
    turnover: float = 0.0,
) -> dict[str, float | None]:
    """Both columns of the table R9 asks for, computed the same way for both sides.

    One function, so the index can never be measured by a different rule than the
    portfolio it is there to judge.
    """
    out: dict[str, float | None] = {
        "total_return": float(portfolio.iloc[-1] / (base or portfolio.iloc[0]) - 1.0),
        "annual_return": annual_return(portfolio, years, base),
        "volatility": annual_volatility(portfolio),
        "sharpe": sharpe(portfolio, years, risk_free, base),
        "max_drawdown": max_drawdown(portfolio),
        "turnover": turnover,
        "risk_free": risk_free,
    }
    if benchmark is None or benchmark.empty:
        return out
    out |= {
        "benchmark_total_return": float(
            benchmark.iloc[-1] / (base or benchmark.iloc[0]) - 1.0
        ),
        "benchmark_annual_return": annual_return(benchmark, years, base),
        "benchmark_volatility": annual_volatility(benchmark),
        "benchmark_sharpe": sharpe(benchmark, years, risk_free, base),
        "benchmark_max_drawdown": max_drawdown(benchmark),
        "benchmark_turnover": 0.0,   # the index is not traded by this portfolio
        "excess_annual": annual_return(portfolio, years, base)
        - annual_return(benchmark, years, base),
        "tracking_error": tracking_error(portfolio, benchmark),
    }
    return out


def current_risk_free(
    con: duckdb.DuckDBPyConnection, fallback: float = 0.02
) -> tuple[float, str]:
    """Today's 13-week bill yield, as a fraction, plus where it came from and when.

    An option price depends on the rate, so leaving it as a slider invites the user to
    tune an input the market has already decided — and a slider that changes a fair value
    is a knob for making the number you wanted. The bill yield is downloaded with
    everything else; there is nothing to set.
    """
    row = con.execute(
        "SELECT close, date FROM prices WHERE ticker = ? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        [RISK_FREE_SYMBOL],
    ).fetchone()
    if row and row[0] is not None:
        return float(row[0]) / 100.0, f"{RISK_FREE_SYMBOL} close of {row[1]}"
    return fallback, "settings fallback (no bill yield stored)"
