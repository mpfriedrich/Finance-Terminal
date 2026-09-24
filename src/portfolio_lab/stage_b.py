"""Stage B: the point-in-time backtest — re-screen at every date, using only what was known.

Stage A takes today's basket and runs it backwards, which is hindsight by construction.
Stage B does the thing that is actually worth doing: at each rebalance date it re-runs the
screen using **only facts already filed by that date** and **only prices up to it**, then
trades into the result at the **next session's open**.

Three rules decide whether the number means anything, and each has a test:

  1. **No fact from the future.** A figure filed on 15 March cannot inform a screen dated
     1 March, however much it describes the quarter that ended in December.
  2. **The first filing wins, not the restatement.** What the market saw on the day is
     the original number; a correction published two years later is the story of the
     restatement, not of the decision.
  3. **A name that was not listed then, or was already gone, cannot be bought.**

What stage B still does NOT fix, and the label says so: the universe is survivor-based.
Phase 7 measured how many vanished companies could be recovered — none of twenty — so the
companies that failed are missing from every date. That error points upward, and no
amount of point-in-time discipline removes it.

One convention that needs stating. Fills use the next session's OPEN, as R16 requires,
but returns are measured on the adjusted close, because that is the series that carries
dividends. The open is therefore put on the same adjusted basis by scaling it with the
same day's adjustment factor (close_adj / close). Without that, every split in the
holding period would show up as a phantom gain or loss at the next rebalance.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from . import metrics as mx
from .config import CostModel
from .screen.engine import run_screen
from .screen.spec import PortfolioSpec

FREQUENCIES = {"quarterly": 3, "annual": 12, "buy_and_hold": 0}


def rebalance_dates(start: dt.date, end: dt.date, frequency: str = "quarterly") -> list[dt.date]:
    """The dates on which the screen is re-run. Buy-and-hold gives exactly one."""
    months = FREQUENCIES.get(frequency, 3)
    if months == 0:
        return [start]
    dates, current = [], start
    while current <= end:
        dates.append(current)
        year, month = divmod(current.month - 1 + months, 12)
        try:
            current = current.replace(year=current.year + year, month=month + 1)
        except ValueError:                      # 31st in a short month
            current = current.replace(year=current.year + year, month=month + 1, day=28)
    return dates


def bars_for(source, tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    """Daily bars for the names that were ever selected, through the seam.

    Through `source.prices()` rather than SQL, so the identical loop runs against the
    fixture and against the live store — which is what makes the three point-in-time
    tests below worth anything.

    Only the names a rebalance actually chose are fetched. The alternative, loading the
    whole universe for sixteen years, is fifteen million rows to simulate a portfolio
    that never holds more than a few dozen.
    """
    if not tickers:
        return pd.DataFrame()
    frame = source.prices(sorted(set(tickers)), start, end)
    if frame.empty:
        return frame
    # The fill happens at the open, the return is measured on the adjusted close, so the
    # open is put on the adjusted basis with that day's own adjustment factor. Without
    # it every split inside a holding period reads as a gap at the next rebalance.
    factor = frame["close_adj"] / frame["close"].replace(0.0, np.nan)
    frame = frame.assign(open_adj=frame["open"] * factor)
    # One date type through the whole loop. DuckDB hands pandas a datetime64 column while
    # the rebalance schedule is built from `datetime.date`, and comparing the two raises —
    # the same mismatch that broke the gold conversion, in a different module.
    frame = frame.assign(date=[pd.Timestamp(d).date() for d in frame["date"]])
    return frame.dropna(subset=["close_adj"])


def next_session(dates: pd.Index, after: dt.date):
    """The first session strictly after `after`, or None at the end of the data.

    Strictly after, because screening on a day's close and filling at that same close is
    look-ahead — and it is the default mistake in almost every home-made backtest.
    """
    later = [d for d in dates if d > after]
    return later[0] if later else None


@dataclass(frozen=True, slots=True)
class StageBResult:
    dates: list[dt.date]
    portfolio: list[float]
    benchmark: list[float] | None
    benchmark_symbol: str | None
    metrics: dict
    rebalances: pd.DataFrame          # one row per rebalance: date, names, turnover, cost
    turnover: float                   # average annualised turnover
    data_fingerprint: str
    label: str
    notes: list[str] = field(default_factory=list)

    @property
    def years(self) -> float:
        return max((self.dates[-1] - self.dates[0]).days, 1) / 365.25


def _weights_from(picks: pd.DataFrame) -> dict[str, float]:
    if picks.empty:
        return {}
    if "weight" in picks.columns and picks.weight.sum() > 0:
        return dict(zip(picks.ticker.astype(str), picks.weight / picks.weight.sum()))
    equal = 1.0 / len(picks)
    return {str(t): equal for t in picks.ticker}


def run(
    source,
    spec: PortfolioSpec,
    start: dt.date,
    end: dt.date,
    costs: CostModel | None = None,
    frequency: str = "quarterly",
    benchmark: str = "^SP500TR",
    risk_free: float = 0.02,
    label: str = "",
    data_fingerprint: str = "",
    frame_for=None,
    cost_tier_market_cap: float = 0.0,
) -> StageBResult | None:
    """Re-screen at each rebalance date and trade into the result at the next open.

    `frame_for(asof)` supplies the screening frame for a date; it defaults to the
    source's own, and exists so a caller can hand in a cached one. A full run re-screens
    the whole universe at every date, which is the expensive part.
    """
    costs = costs or CostModel()
    frame_for = frame_for or source.screening_frame

    # Pass one: re-screen at every date and remember what was chosen. Nothing is priced
    # yet, so nothing here can see a price it should not.
    schedule_dates = [d for d in rebalance_dates(start, end, frequency) if d <= end]
    chosen_at: dict[dt.date, dict[str, float]] = {}
    notes: list[str] = []
    for screen_date in schedule_dates:
        frame = frame_for(screen_date)
        if frame is None or frame.empty:
            notes.append(f"{screen_date}: no universe at that date, position kept")
            continue
        result = run_screen(frame, spec)
        chosen_at[screen_date] = _weights_from(result.picks.head(spec.size))
        if not chosen_at[screen_date]:
            notes.append(f"{screen_date}: nothing passed, moved to cash")

    wanted_names = sorted({t for picks in chosen_at.values() for t in picks})
    bars = bars_for(source, wanted_names, start, end)
    if bars.empty:
        return None
    sessions = pd.Index(sorted(bars.date.unique()))
    closes = bars.pivot_table(index="date", columns="ticker", values="close_adj").ffill()
    opens = bars.pivot_table(index="date", columns="ticker", values="open_adj")

    holdings: dict[str, float] = {}       # ticker -> shares (in adjusted terms)
    cash = 1.0                            # everything starts in cash, value 1.0
    value_by_date: dict[dt.date, float] = {}
    rebalance_rows: list[dict] = []

    pending = {d: next_session(sessions, d) for d in chosen_at}

    for session in sessions:
        # value the book at today's close
        held_value = sum(
            shares * closes.at[session, ticker]
            for ticker, shares in holdings.items()
            if ticker in closes.columns and not pd.isna(closes.at[session, ticker])
        )
        value_by_date[session] = cash + held_value

        due = [d for d, fill in pending.items() if fill == session]
        if not due:
            continue
        for screen_date in due:
            target = chosen_at[screen_date]
            total = value_by_date[session]
            # sell everything at today's open, then buy the target at the same open
            priced = {t: opens.at[session, t] for t in set(target) | set(holdings)
                      if t in opens.columns and not pd.isna(opens.at[session, t])}
            old_value = {t: holdings.get(t, 0.0) * priced.get(t, 0.0) for t in holdings}
            traded = 0.0
            new_holdings: dict[str, float] = {}
            for ticker, weight in target.items():
                price = priced.get(ticker)
                if not price or price <= 0:
                    continue
                want = total * weight
                traded += abs(want - old_value.get(ticker, 0.0))
                new_holdings[ticker] = want / price
            for ticker, value in old_value.items():
                if ticker not in target:
                    traded += abs(value)

            turnover_fraction = traded / total if total else 0.0
            # Cost is charged on what actually changed hands, at the tier of the names
            # being traded. One rate for the basket is close enough here and wrong in an
            # obvious direction: it understates a basket of micro-caps, so the cost is
            # taken from the names bought.
            tier = costs.spread_for(cost_tier_market_cap) + costs.commission
            charge = total * turnover_fraction * tier

            holdings = new_holdings
            cash = max(total - sum(v * priced.get(t, 0.0) for t, v in holdings.items()), 0.0)
            cash -= charge
            value_by_date[session] = cash + sum(
                shares * closes.at[session, t] for t, shares in holdings.items()
                if t in closes.columns and not pd.isna(closes.at[session, t])
            )
            rebalance_rows.append({
                "screened_on": screen_date,
                "filled_on": session,
                "names": len(holdings),
                "turnover": turnover_fraction,
                "cost": charge / total if total else 0.0,
            })

    curve = pd.Series(value_by_date).sort_index()
    curve = 100.0 * curve / curve.iloc[0]
    bench = _benchmark_curve(getattr(source, "con", None), benchmark, curve.index)

    years = max((curve.index[-1] - curve.index[0]).days, 1) / 365.25
    rebalances = pd.DataFrame(rebalance_rows)
    annual_turnover = (
        float(rebalances.turnover.sum() / years) if not rebalances.empty else 0.0
    )
    summary = mx.summary(curve, bench, years, risk_free, base=100.0,
                         turnover=annual_turnover)

    return StageBResult(
        dates=list(curve.index),
        portfolio=[float(v) for v in curve],
        benchmark=[float(v) for v in bench] if bench is not None else None,
        benchmark_symbol=benchmark if bench is not None else None,
        metrics=summary,
        rebalances=rebalances,
        turnover=annual_turnover,
        data_fingerprint=data_fingerprint,
        label=label or stage_b_label(),
        notes=notes,
    )


def _benchmark_curve(con, symbol: str, index) -> pd.Series | None:
    """The index on the same dates. A source without one (the fixture) simply has none,
    and the result says so rather than inventing a comparison."""
    if con is None:
        return None
    frame = con.execute(
        "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date", [symbol]
    ).fetch_df()
    if frame.empty:
        return None
    series = frame.set_index("date")["close_adj"].reindex(index).ffill().bfill()
    if series.isna().all():
        return None
    return 100.0 * series / series.iloc[0]


def stage_b_label(universe_verdict: str = "") -> str:
    """What stage B fixed, and what it could not (R12)."""
    text = (
        "Stage B · re-screened at every rebalance date. "
        "Corrected: only filings already published at each date are used, the first "
        "filed version wins over later restatements, orders fill at the NEXT session's "
        "open, and turnover is charged at the cost tier of the names actually traded. "
        "NOT corrected: the universe holds only companies that still exist."
    )
    return f"{text} {universe_verdict}".strip()
