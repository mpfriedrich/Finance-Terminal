"""Risk of a portfolio of positions: how much it moves, how badly it can lose, what with.

All figures are historical: the positions' own daily returns over a lookback window,
held at their stated weights. That is the plain, checkable version of each measure —
no factor model, no distribution fitted to the tails:

  volatility   annualised standard deviation of the daily portfolio return
  beta         against the S&P 500 total return, same days
  VaR          the daily loss exceeded on 5% (1%) of days in the window
  CVaR         the average loss on those days — what "a bad day" costs on average
  stress       the return over a real past crisis, had these weights been held then

Stress windows are limited by the stored history (2010 onwards), so 2008 is not one.
A name that did not trade during a window is left out and the coverage is reported,
rather than filled with a guess.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

BENCHMARK = "^SP500TR"

# (name, start, end) — peak to trough of the S&P 500 in each episode.
SCENARIOS: tuple[tuple[str, dt.date, dt.date], ...] = (
    ("US downgrade 2011", dt.date(2011, 7, 22), dt.date(2011, 10, 3)),
    ("China & oil 2015–16", dt.date(2015, 11, 3), dt.date(2016, 2, 11)),
    ("Q4 2018 sell-off", dt.date(2018, 9, 20), dt.date(2018, 12, 24)),
    ("Covid crash 2020", dt.date(2020, 2, 19), dt.date(2020, 3, 23)),
    ("Rates bear market 2022", dt.date(2022, 1, 3), dt.date(2022, 10, 12)),
    ("Regional banks 2023", dt.date(2023, 3, 8), dt.date(2023, 3, 17)),
)


def _closes(con, tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    frame = con.execute(
        "SELECT ticker, date, close_adj FROM prices WHERE ticker IN (SELECT UNNEST(?)) "
        "AND date BETWEEN ? AND ? AND close_adj > 0 ORDER BY date",
        [tickers, start, end],
    ).fetch_df()
    if frame.empty:
        return frame
    wide = frame.pivot_table(index="date", columns="ticker", values="close_adj")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _weights(positions: pd.DataFrame) -> pd.Series:
    w = positions.set_index(positions["ticker"].astype(str).str.upper())["weight"].astype(float)
    return w / w.sum()


def portfolio_returns(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    """Daily return at fixed weights, renormalised each day over the names that have a
    return that day — a name with a missing day neither counts as flat nor as a loss."""
    w = weights.reindex(returns.columns).fillna(0.0)
    present = returns.notna()
    day_weight = present.mul(w, axis=1).sum(axis=1)
    return returns.fillna(0.0).mul(w, axis=1).sum(axis=1) / day_weight.replace(0, np.nan)


def measures(con: duckdb.DuckDBPyConnection, positions: pd.DataFrame, asof: dt.date,
             years: int = 3) -> dict:
    """Volatility, beta, VaR, CVaR, drawdown, correlation and concentration."""
    weights = _weights(positions)
    start = asof - dt.timedelta(days=int(365.25 * years))
    closes = _closes(con, list(weights.index) + [BENCHMARK], start, asof)
    if closes.empty:
        return {}
    bench = closes.pop(BENCHMARK) if BENCHMARK in closes else None
    returns = closes.pct_change().iloc[1:]
    port = portfolio_returns(returns, weights).dropna()
    if len(port) < 60:
        return {"error": "fewer than 60 days of shared history"}
    out = {"days": int(len(port)), "from": port.index[0].date(), "to": port.index[-1].date()}
    out["volatility"] = float(port.std() * np.sqrt(252))
    out["var_95"] = float(-np.quantile(port, 0.05))
    out["var_99"] = float(-np.quantile(port, 0.01))
    tail = port[port <= np.quantile(port, 0.05)]
    out["cvar_95"] = float(-tail.mean())
    growth = (1 + port).cumprod()
    out["max_drawdown"] = float((growth / growth.cummax() - 1).min())
    if bench is not None:
        b = bench.pct_change().reindex(port.index)
        both = pd.concat([port, b], axis=1).dropna()
        if len(both) > 60 and both.iloc[:, 1].var() > 0:
            out["beta"] = float(both.iloc[:, 0].cov(both.iloc[:, 1]) / both.iloc[:, 1].var())
            # A portfolio that does not move has no correlation with anything.
            if both.iloc[:, 0].std() > 1e-12:
                out["correlation_to_index"] = float(both.iloc[:, 0].corr(both.iloc[:, 1]))
    out["correlation"] = returns.corr(min_periods=60)
    out["largest_weight"] = float(weights.max())
    out["effective_names"] = float(1.0 / (weights ** 2).sum())
    covered = [t for t in weights.index if t in returns.columns]
    out["coverage"] = float(weights.reindex(covered).sum())
    return out


def exposure(con: duckdb.DuckDBPyConnection, positions: pd.DataFrame,
             column: str = "sector") -> pd.DataFrame:
    """Weight by sector (the SEC's industry wording) — or any securities column."""
    weights = _weights(positions)
    meta = con.execute(
        f"SELECT upper(ticker) AS ticker, {column} AS bucket FROM securities "
        "WHERE upper(ticker) IN (SELECT UNNEST(?))", [list(weights.index)]).fetch_df()
    bucket = meta.drop_duplicates("ticker").set_index("ticker")["bucket"]
    frame = pd.DataFrame({"weight": weights,
                          "bucket": bucket.reindex(weights.index).fillna("unknown")})
    return (frame.groupby("bucket")["weight"].sum().sort_values(ascending=False)
            .rename_axis(column).reset_index())


def stress(con: duckdb.DuckDBPyConnection, positions: pd.DataFrame) -> pd.DataFrame:
    """Each scenario: the portfolio's return over the window at these weights, the
    index's, and the share of the weight that had prices then."""
    weights = _weights(positions)
    rows = []
    for name, start, end in SCENARIOS:
        closes = _closes(con, list(weights.index) + [BENCHMARK],
                         start - dt.timedelta(days=7), end)
        if closes.empty:
            rows.append({"scenario": name, "from": start, "to": end,
                         "portfolio": np.nan, "index": np.nan, "coverage": 0.0})
            continue
        first = closes[closes.index <= pd.Timestamp(start)].ffill().iloc[-1:] \
            if (closes.index <= pd.Timestamp(start)).any() else closes.iloc[:1]
        last = closes.ffill().iloc[-1:]
        move = (last.iloc[0] / first.iloc[0] - 1.0)
        names = [t for t in weights.index if t in move.index and pd.notna(move[t])]
        covered = float(weights.reindex(names).sum()) if names else 0.0
        port = float((move[names] * weights[names]).sum() / covered) if covered else np.nan
        rows.append({"scenario": name, "from": start, "to": end, "portfolio": port,
                     "index": float(move.get(BENCHMARK, np.nan)), "coverage": covered})
    return pd.DataFrame(rows)
