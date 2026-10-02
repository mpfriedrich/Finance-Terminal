"""Performance of a live-tracked paper portfolio.

This is the honest half of the app. A backtest has to argue its way past survivorship,
look-ahead and the fact that you kept adjusting filters until the curve looked good. A
portfolio frozen today has none of those problems, because the picks existed before the
outcome did. The price is patience: it only tells you something after time passes.

What is modelled:
  * entry costs, charged once at the start (commission + a spread that scales with size)
  * dividends, via the adjusted close — so this is total return, same basis as the
    benchmark, which is the comparison people most often get wrong
  * positions that stop trading — held at their last observed price and flagged, never
    silently dropped

What is NOT modelled: taxes, currency, ongoing fees, and any rebalancing. A tracked
portfolio is buy-and-hold from its start date.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

BENCHMARK_TICKER = "^SP500TR"


def _series(con: duckdb.DuckDBPyConnection, tickers: list[str], start: dt.date) -> pd.DataFrame:
    return con.execute(
        "SELECT ticker, date, close_adj, close FROM prices "
        "WHERE ticker IN (SELECT UNNEST(?)) AND date >= ? ORDER BY date",
        [tickers, start],
    ).fetch_df()


def performance(
    market: duckdb.DuckDBPyConnection,
    holdings: pd.DataFrame,
    started_on: dt.date,
    benchmark: str = BENCHMARK_TICKER,
) -> dict:
    """Value the frozen portfolio forward from its start date.

    Returns a dict with a daily curve (portfolio and benchmark, both rebased to 100),
    per-position detail, and the headline numbers.
    """
    if holdings.empty:
        return {"empty": True}

    tickers = holdings.ticker.tolist()
    px = _series(market, tickers + [benchmark], started_on)
    if px.empty:
        return {"empty": True}

    bench = px[px.ticker == benchmark].set_index("date")["close_adj"].sort_index()
    stocks = px[px.ticker != benchmark]

    # Total return per name, from the adjusted close, net of the entry cost.
    wide = stocks.pivot_table(index="date", columns="ticker", values="close_adj").sort_index()
    wide = wide.ffill()
    if wide.empty:
        return {"empty": True}

    first = wide.iloc[0]
    growth = wide.divide(first, axis=1)

    weights = holdings.set_index("ticker")["weight"].reindex(wide.columns).fillna(0.0)
    costs = holdings.set_index("ticker")["entry_cost"].reindex(wide.columns).fillna(0.0)
    # One-off entry cost: you start below par by exactly what the trade cost you.
    net_weights = weights * (1.0 - costs)

    curve = (growth * net_weights).sum(axis=1)
    curve = 100.0 * curve / weights.sum()

    out = {"empty": False, "dates": curve.index.tolist(), "portfolio": curve.tolist()}

    if not bench.empty:
        b = bench.reindex(curve.index).ffill()
        out["benchmark"] = (100.0 * b / b.iloc[0]).tolist()
        out["benchmark_symbol"] = benchmark
    else:
        out["benchmark"] = None
        out["benchmark_symbol"] = None

    days = max((curve.index[-1] - curve.index[0]).days, 1)
    out["days"] = days
    out["portfolio_return"] = curve.iloc[-1] / 100.0 - 1.0
    out["benchmark_return"] = (
        (out["benchmark"][-1] / 100.0 - 1.0) if out["benchmark"] else None
    )
    out["excess"] = (
        out["portfolio_return"] - out["benchmark_return"]
        if out["benchmark_return"] is not None
        else None
    )

    last = wide.iloc[-1]
    detail = holdings.copy()
    detail["last_price"] = detail.ticker.map(last.to_dict())
    detail["return"] = detail.ticker.map((last / first - 1.0).to_dict())
    detail["contribution"] = detail["return"] * detail["weight"]
    # A name whose last observed bar is well before everyone else's has stopped trading.
    last_seen = stocks.groupby("ticker")["date"].max()
    newest = last_seen.max()
    detail["stopped_trading"] = detail.ticker.map(
        ((newest - last_seen).dt.days > 5).to_dict()
    ).fillna(True)
    out["detail"] = detail

    return out
