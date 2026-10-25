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

from . import metrics as mx
from . import numeraire as nm
from .config import CostModel
from .data import history

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

# The formulas live in metrics.py, where each one is pinned to a series whose answer can
# be worked out by hand (Phase 6). Re-exported here so existing callers keep working and,
# more importantly, so there is exactly one implementation of "annual return" in the app.
annualised_return = mx.annual_return
annualised_volatility = mx.annual_volatility
max_drawdown = mx.max_drawdown
sharpe = mx.sharpe


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
    delisting_return: float = -0.30,
    data_fingerprint: str = "",
    universe_verdict: str = "",
    numeraire: pd.Series | None = None,
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

    # Changing the unit BEFORE anything is computed, so the curve, the metrics and the
    # benchmark all end up in the same one. Converting only the chart would leave a
    # picture in ounces above a table in dollars.
    if numeraire is not None and not numeraire.empty:
        wide = nm.convert(wide, numeraire).dropna(how="all")
        if not bench.empty:
            bench = nm.convert(bench, numeraire).dropna()
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

    # A name whose bars stop before the window ends did not politely hold its value
    # there. Carrying the last close forward to the end is the single most flattering
    # assumption a backtest can make, because the companies that stop trading are rarely
    # the ones that did well. So the stated delisting return is applied once, at the last
    # bar, and the position is held at that level afterwards (R18).
    #
    # The calendar is the MARKET's (the benchmark's), not the holdings' (audit 24.10.2026
    # F8). Built from the held names alone it ended with them: when every holding stopped
    # trading early the window ended on their last bar, nothing looked delisted, and the
    # stated loss was never taken. Stage B fixed the same hole with `source.sessions`.
    # Without a benchmark series the holdings' own dates are all there is, so that case
    # still cannot see a basket that vanished entirely.
    last_bar = wide.apply(lambda col: col.last_valid_index())
    if not bench.empty:
        later = bench.index[bench.index > wide.index[0]]
        wide = wide.reindex(wide.index.union(later))
    window_end = wide.index[-1]
    gone = [c for c in wide.columns
            if last_bar[c] is not None and (window_end - last_bar[c]).days > 10]

    wide = wide.ffill()
    # CRSP records what holders actually got for some of these; that beats any assumption.
    known_exit = history.delisting_returns(con, gone)
    for column in gone:
        stopped = last_bar[column]
        final = wide.loc[stopped, column] * (1.0 + known_exit.get(column, delisting_return))
        wide.loc[wide.index > stopped, column] = final
    if gone:
        actual = sorted(set(gone) & set(known_exit))
        notes.append(
            f"{len(gone)} position(s) stopped trading before the end of the window "
            f"({', '.join(sorted(gone))}). A delisting return of {delisting_return:.0%} "
            "was applied at the last bar instead of carrying the last price forward — "
            "the assumption is a setting, and it moves the result (R18, [A17])."
            + (f" For {len(actual)} of them CRSP records the actual delisting return, "
               "which was used instead: " + ", ".join(
                   f"{t} {known_exit[t]:+.0%}" for t in actual[:6]) + "." if actual else "")
        )

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
    metrics = mx.summary(
        curve, bench_curve, years, risk_free,
        base=INITIAL, turnover=mx.turnover_of_buy_and_hold(),
    )

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
        label=stage_a_label(benchmark, universe_verdict),
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


def stage_a_label(benchmark: str = BENCHMARK, universe_verdict: str = "") -> str:
    """The warning that travels with every Stage A chart (R12).

    Written out rather than abbreviated, because the two uncorrected biases both push the
    result the same way — upward — and a reader who skims needs to meet that fact.
    """
    text = (
        "Stage A · buy and hold, no rebalancing. "
        f"Corrected: dividends are reinvested on both sides ({benchmark} is a total-return "
        "series), trading costs are charged once at entry tiered by company size, and a "
        "position that stops trading takes the stated delisting loss rather than holding "
        "its last price. "
        "NOT corrected: the companies were chosen using today's filings, so the basket is "
        "hindsight; and delisted companies are absent from the universe, so survivors "
        "flatter the result. Both errors point upward."
    )
    return f"{text} {universe_verdict}".strip()
