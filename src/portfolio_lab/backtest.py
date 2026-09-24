"""Stage A: the retrospective backtest — buy once, hold, compare against the index.

What this answers: *if I had bought this basket on that date and done nothing, what would
have happened, net of what the trades cost?*

What it does NOT answer, and the label says so on screen (R12):

  * **The names were chosen with today's data.** The screen that produced them ran on
    today's filings and today's prices. Running that basket backwards is look-ahead of
    the purest kind — the companies are known survivors that pass a filter built on
    figures nobody had at the start date. Stage B (Phase 8) removes this by re-screening
    at each historical date; Stage A cannot, and pretending otherwise is the failure mode
    this whole plan exists to avoid.
  * **The universe holds no delisted companies.** Anything that went to zero is missing
    (Phase 7). The result is flattered by survivors, always upward.

So Stage A is useful for one thing: seeing how a basket behaved and what costs did to it.
It is not evidence that the selection rule works.

Two conventions that decide the numbers:

  Returns come from `close_adj`, the split- and dividend-adjusted close. That IS dividend
  reinvestment at the ex-date into the same position, with fractions allowed (R17) — the
  adjusted series is built exactly that way, so no separate dividend loop can disagree
  with it.

  The benchmark is a TOTAL-return series, on the same basis (R11). Comparing a
  dividend-paying portfolio against a price-only index manufactures about two points a
  year of outperformance; `tests/test_backtest.py` measures that gap rather than
  assuming it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from .config import CostModel

BENCHMARK = "^SP500TR"
TRADING_DAYS = 252


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """A run, its inputs and the identity of the data it was computed from."""

    dates: list[dt.date]
    portfolio: list[float]          # rebased to 100
    benchmark: list[float] | None   # rebased to 100, same dates
    benchmark_symbol: str | None
    metrics: dict[str, float | None]
    per_position: pd.DataFrame
    entry_cost_total: float         # fraction of starting capital paid to trade in
    turnover: float                 # 0.0 for buy-and-hold, by definition (R16b)
    data_fingerprint: str
    label: str
    notes: list[str] = field(default_factory=list)

    @property
    def years(self) -> float:
        span = (self.dates[-1] - self.dates[0]).days
        return max(span, 1) / 365.25


def _bars(con: duckdb.DuckDBPyConnection, tickers: list[str],
          start: dt.date, end: dt.date) -> pd.DataFrame:
    return con.execute(
        "SELECT ticker, date, close_adj FROM prices "
        "WHERE ticker IN (SELECT UNNEST(?)) AND date BETWEEN ? AND ? ORDER BY date",
        [tickers, start, end],
    ).fetch_df()


INITIAL = 100.0


def annualised_return(curve: pd.Series, years: float, base: float | None = None) -> float:
    """Compound annual growth, measured against the CAPITAL COMMITTED.

    `base` matters more than it looks. The portfolio curve starts a little below 100,
    because the entry cost is paid before anything can grow. Annualising from that lower
    first value would quietly measure the return on what survived the trade instead of on
    what was put in — and the tiered cost model, the whole point of R8, would disappear
    from the headline number while still showing up in the total return. It did exactly
    that in the first run of this module.
    """
    if years <= 0 or curve.empty:
        return float("nan")
    start = curve.iloc[0] if base is None else base
    if start <= 0:
        return float("nan")
    return float((curve.iloc[-1] / start) ** (1.0 / years) - 1.0)


def annualised_volatility(curve: pd.Series) -> float:
    """Standard deviation of daily returns, scaled by √252.

    Daily, not monthly: a drawdown that matters happens inside a month, and the plan asks
    for the daily figure explicitly (R9).
    """
    returns = curve.pct_change().dropna()
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS))


def max_drawdown(curve: pd.Series) -> float:
    """Worst peak-to-trough fall, as a negative fraction, on daily closes."""
    if curve.empty:
        return float("nan")
    peak = curve.cummax()
    return float((curve / peak - 1.0).min())


def sharpe(curve: pd.Series, years: float, risk_free: float,
           base: float | None = None) -> float:
    vol = annualised_volatility(curve)
    if not np.isfinite(vol) or vol == 0:
        return float("nan")
    return float((annualised_return(curve, years, base) - risk_free) / vol)


def entry_costs(holdings: pd.DataFrame, costs: CostModel) -> pd.Series:
    """One-way cost per position, as a fraction of that position (R8).

    Tiered by market cap, because the screen this app is built for lands on small
    companies — where a large-cap spread assumption understates the real cost by an order
    of magnitude and turns a losing strategy into a winning chart.

    A position with no market cap on file pays the most expensive tier rather than the
    cheapest: an unknown is not a blue chip.
    """
    caps = pd.to_numeric(holdings.get("market_cap"), errors="coerce")
    if caps is None:
        caps = pd.Series(np.nan, index=holdings.index)
    return caps.fillna(0.0).map(lambda c: costs.spread_for(c) + costs.commission)


def buy_and_hold(
    con: duckdb.DuckDBPyConnection,
    holdings: pd.DataFrame,
    start: dt.date,
    end: dt.date | None = None,
    costs: CostModel | None = None,
    benchmark: str = BENCHMARK,
    risk_free: float = 0.02,
    data_fingerprint: str = "",
) -> BacktestResult | None:
    """Buy `holdings` at their weights on `start`, hold to `end`, never rebalance (R16b).

    `holdings` needs: ticker, weight, and market_cap where known (for the cost tier).
    Weights are renormalised over the names that actually have a price on the start date;
    a name with no history then is reported in `notes` instead of silently diluting the
    rest into cash.
    """
    if holdings.empty:
        return None
    costs = costs or CostModel()
    end = end or dt.date.today()
    tickers = [str(t) for t in holdings.ticker]

    px = _bars(con, tickers + [benchmark], start, end)
    if px.empty:
        return None

    bench = px[px.ticker == benchmark].set_index("date")["close_adj"].sort_index()
    wide = (px[px.ticker != benchmark]
            .pivot_table(index="date", columns="ticker", values="close_adj")
            .sort_index())
    if wide.empty:
        return None

    notes: list[str] = []
    # A name must be priced on the FIRST day of the window; one that lists later cannot be
    # bought at the start, and carrying it as if it could be is free hindsight.
    tradable = wide.columns[wide.iloc[0].notna()]
    missing = sorted(set(wide.columns) - set(tradable))
    if missing:
        notes.append(
            f"no price on {wide.index[0].date() if hasattr(wide.index[0], 'date') else wide.index[0]}"
            f", so left out of the run: {', '.join(missing)}. The remaining weights were "
            "scaled up to 100%."
        )
    wide = wide[tradable]
    if wide.empty:
        return None

    # Forward-fill AFTER the entry check: a halted name holds its last price, it does not
    # borrow a future one.
    wide = wide.ffill()

    held = holdings[holdings.ticker.isin(tradable)].copy()
    weights = held.set_index("ticker")["weight"].reindex(wide.columns).fillna(0.0)
    if weights.sum() <= 0:
        return None
    weights = weights / weights.sum()

    cost_fraction = entry_costs(held, costs)
    cost_fraction.index = held.ticker
    cost_fraction = cost_fraction.reindex(wide.columns).fillna(0.0)

    growth = wide.divide(wide.iloc[0], axis=1)
    # The cost is paid once, on the way in (R16b): you own slightly less than you paid for
    # from the first day, and everything compounds from there.
    invested = weights * (1.0 - cost_fraction)
    curve = 100.0 * (growth * invested).sum(axis=1)

    bench_curve = None
    if not bench.empty:
        b = bench.reindex(curve.index).ffill().bfill()
        bench_curve = 100.0 * b / b.iloc[0]

    actual_start = curve.index[0]
    actual_start = actual_start.date() if hasattr(actual_start, "date") else actual_start
    if (actual_start - start).days > 5:
        notes.append(
            f"asked for {start}, but the earliest stored bar for this basket is "
            f"{actual_start}, so the run covers {actual_start} onwards. The price store "
            "holds a rolling window; a longer history needs a deeper download."
        )

    years = max((curve.index[-1] - curve.index[0]).days, 1) / 365.25
    metrics: dict[str, float | None] = {
        "total_return": float(curve.iloc[-1] / INITIAL - 1.0),
        "annual_return": annualised_return(curve, years, base=INITIAL),
        "volatility": annualised_volatility(curve),
        "sharpe": sharpe(curve, years, risk_free, base=INITIAL),
        "max_drawdown": max_drawdown(curve),
        "turnover": 0.0,
    }
    if bench_curve is not None:
        metrics |= {
            "benchmark_total_return": float(bench_curve.iloc[-1] / INITIAL - 1.0),
            "benchmark_annual_return": annualised_return(bench_curve, years, base=INITIAL),
            "benchmark_volatility": annualised_volatility(bench_curve),
            "benchmark_sharpe": sharpe(bench_curve, years, risk_free, base=INITIAL),
            "benchmark_max_drawdown": max_drawdown(bench_curve),
            "excess_annual": annualised_return(curve, years, base=INITIAL)
            - annualised_return(bench_curve, years, base=INITIAL),
        }

    last = wide.iloc[-1]
    detail = held.copy()
    detail["entry_cost"] = cost_fraction.reindex(detail.ticker).to_numpy()
    detail["weight_used"] = weights.reindex(detail.ticker).to_numpy()
    detail["gross_return"] = detail.ticker.map((last / wide.iloc[0] - 1.0).to_dict())
    detail["contribution"] = detail["weight_used"] * (
        (1.0 - detail["entry_cost"]) * (1.0 + detail["gross_return"]) - 1.0
    )

    return BacktestResult(
        dates=[d.date() if hasattr(d, "date") else d for d in curve.index],
        portfolio=[float(v) for v in curve],
        benchmark=[float(v) for v in bench_curve] if bench_curve is not None else None,
        benchmark_symbol=benchmark if bench_curve is not None else None,
        metrics=metrics,
        per_position=detail,
        entry_cost_total=float((weights * cost_fraction).sum()),
        turnover=0.0,
        data_fingerprint=data_fingerprint,
        label=stage_a_label(benchmark),
        notes=notes,
    )


def short_build_id(fingerprint: str) -> str:
    """A short, stable name for a data build.

    The fingerprint itself starts with the source name, so truncating it for display
    shows "free (SEC XBRL + yfinance..." for every build ever made — which is exactly
    the information the reader does not need. A digest of the whole string changes
    whenever any part of it does, which is the whole point (R22).
    """
    import hashlib

    return hashlib.sha1(fingerprint.encode()).hexdigest()[:10] if fingerprint else "—"


def stage_a_label(benchmark: str = BENCHMARK) -> str:
    """The warning that travels with every Stage A chart (R12).

    Written out rather than abbreviated, because the two uncorrected biases both push the
    result the same way — upward — and a reader who skims needs to meet that fact.
    """
    return (
        "Stage A · buy and hold, no rebalancing. "
        f"Corrected: dividends are reinvested on both sides ({benchmark} is a total-return "
        "series), and trading costs are charged once at entry, tiered by company size. "
        "NOT corrected: the companies were chosen using today's filings, so the basket is "
        "hindsight; and delisted companies are absent from the universe, so survivors "
        "flatter the result. Both errors point upward."
    )
