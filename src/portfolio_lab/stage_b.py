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

import calendar
import datetime as dt
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

from . import metrics as mx
from .config import CostModel
from .screen.engine import run_screen, weighted
from .screen.spec import PortfolioSpec

FREQUENCIES = {"quarterly": 3, "annual": 12, "buy_and_hold": 0}


def rebalance_dates(start: dt.date, end: dt.date, frequency: str = "quarterly") -> list[dt.date]:
    """The dates on which the screen is re-run. Buy-and-hold gives exactly one.

    Every date is counted from `start`, not from the previous date: stepping from the
    previous one clamped 31 January to 28 April and then stayed on the 28th for the rest
    of the run.
    """
    months = FREQUENCIES.get(frequency, 3)
    if months == 0:
        return [start]
    dates: list[dt.date] = []
    step = 0
    while True:
        index = start.month - 1 + step * months
        year, month = start.year + index // 12, index % 12 + 1
        current = dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
        if current > end:
            return dates
        dates.append(current)
        step += 1


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
    ever_held: list[str]              # every name the rule ever bought
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
    delisting_return: float = -0.30,
    label: str = "",
    data_fingerprint: str = "",
    frame_for=None,
    cost_tier_market_cap: float = 0.0,
    exclude: set[str] | None = None,
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
    if getattr(source, "universe_basis", "ticker_history") == "securities":
        notes.append(
            "ticker_history is empty or older than the newest prices, so listings came "
            "from the securities master's observed price ranges instead of the Phase 7 "
            "table. Rebuild it (scripts/build_ticker_history.py, or any price update) so "
            "the run uses the universe the plan asks for."
        )
    # R19: who left the universe between two screens. Counted from the frames the screen
    # actually saw, so it reflects whatever the source knows about listings.
    previous: set[str] | None = None
    departed: set[str] = set()
    priced_when_last_seen: dict[str, bool] = {}
    for screen_date in schedule_dates:
        frame = frame_for(screen_date)
        if frame is None or frame.empty:
            notes.append(f"{screen_date}: no universe at that date, position kept")
            continue
        members = set(frame.ticker.astype(str))
        if "price" in frame.columns:
            priced_when_last_seen.update(
                zip(frame.ticker.astype(str), frame.price.notna()))
        if previous is not None:
            departed |= previous - members
        previous = members
        result = run_screen(frame, spec)
        picks = result.picks
        if exclude and not spec.selected:
            # Used by the sensitivity check (R20): drop one name and see whether the
            # whole result was standing on it. Excluded from the RANKING, then cut to
            # size, so the position it would have taken goes to the next name down —
            # which is what would actually have happened. Filtering the picks instead,
            # already cut to `size`, just shrank the portfolio by one (independent
            # review, 24.09.2026).
            keep = ~result.ranked.ticker.astype(str).str.upper().isin(
                {t.upper() for t in exclude})
            picks = weighted(result.ranked[keep].head(spec.size), spec.weighting)
        elif exclude:
            # Hand-picked names have no "next one down"; the excluded one just goes.
            picks = weighted(picks[~picks.ticker.astype(str).str.upper().isin(
                {t.upper() for t in exclude})], spec.weighting)
        chosen_at[screen_date] = _weights_from(picks.head(spec.size))
        if not chosen_at[screen_date]:
            notes.append(f"{screen_date}: nothing passed, moved to cash")

    wanted_names = sorted({t for picks in chosen_at.values() for t in picks})
    bars = bars_for(source, wanted_names, start, end)
    if bars.empty:
        return None
    sessions = pd.Index(sorted(bars.date.unique()))
    raw_closes = bars.pivot_table(index="date", columns="ticker", values="close_adj")
    # The last real bar has to be found BEFORE the forward fill, or every column runs to
    # the end of the window and nothing ever looks delisted. Stage A gets this right; the
    # first version here did not, and the R18 test caught it.
    last_bar = {t: raw_closes[t].last_valid_index() for t in raw_closes.columns}
    closes = raw_closes.ffill()
    opens = bars.pivot_table(index="date", columns="ticker", values="open_adj")

    # A name whose bars stop before the window ends did not hold its value there (R18).
    # Its last close is not a price anyone could have sold at; the stated delisting loss
    # is, and the proceeds return to cash at that point rather than sitting in a position
    # that cannot be traded.
    window_end = sessions[-1]
    gone_after = {t: d for t, d in last_bar.items()
                  if d is not None and (window_end - d).days > 10}

    holdings: dict[str, float] = {}       # ticker -> shares (in adjusted terms)
    cash = 1.0                            # everything starts in cash, value 1.0
    value_by_date: dict[dt.date, float] = {}
    rebalance_rows: list[dict] = []
    delisted: list[str] = []
    stuck_events = 0

    pending = {d: next_session(sessions, d) for d in chosen_at}

    def priced_at(table, session, ticker):
        if ticker not in table.columns:
            return None
        value = table.at[session, ticker]
        return None if pd.isna(value) or value <= 0 else float(value)

    for session in sessions:
        # 1. Names that have just gone dark are realised at the stated delisting return.
        for ticker in [t for t in holdings if t in gone_after and session > gone_after[t]]:
            final = closes.at[gone_after[ticker], ticker] * (1.0 + delisting_return)
            cash += holdings.pop(ticker) * final
            delisted.append(ticker)

        due = [d for d, fill in pending.items() if fill == session]
        for screen_date in due:
            target = chosen_at[screen_date]
            # 2. The book is valued AT THE OPEN, because that is the moment the money is
            # deployed. Sizing it at the close of the same session — as this loop did
            # until an independent review caught it — re-levers the portfolio by
            # close/open at every rebalance. On a flat market with one 30% spike on a
            # fill day, a position nobody traded at that price turned into a 30% gain.
            tradable = {t: priced_at(opens, session, t)
                        for t in set(target) | set(holdings)}
            tradable = {t: p for t, p in tradable.items() if p}
            # A holding without an open today cannot be sold today. It keeps its shares
            # rather than being liquidated for free, and its value stays out of the money
            # being redeployed.
            stuck = {t: s for t, s in holdings.items() if t not in tradable}
            if stuck:
                stuck_events += 1
            sellable = sum(shares * tradable[t] for t, shares in holdings.items()
                           if t in tradable)
            total = cash + sellable

            traded = 0.0
            new_holdings: dict[str, float] = dict(stuck)
            for ticker, weight in target.items():
                price = tradable.get(ticker)
                if not price:
                    continue
                want = total * weight
                held_now = holdings.get(ticker, 0.0) * price if ticker in tradable else 0.0
                traded += abs(want - held_now)
                new_holdings[ticker] = want / price
            for ticker, shares in holdings.items():
                if ticker in tradable and ticker not in target:
                    traded += abs(shares * tradable[ticker])

            turnover_fraction = traded / total if total else 0.0
            tier = costs.spread_for(cost_tier_market_cap) + costs.commission
            charge = total * turnover_fraction * tier

            # The cost is paid out of the money being invested, not borrowed at 0%. The
            # first version bought the full pre-cost amount and let cash go negative by
            # the charge, so the whole book earned a return on money already spent on
            # trading — small per quarter, a full holding period long for buy-and-hold
            # (independent review, 24.09.2026).
            investable = (total - charge) / total if total else 0.0
            for ticker in target:
                if ticker in new_holdings and ticker not in stuck:
                    new_holdings[ticker] *= investable
            spent = sum(shares * tradable[t] for t, shares in new_holdings.items()
                        if t in tradable)
            cash = max(total - charge - spent, 0.0)
            holdings = new_holdings

        # 3. The curve is marked at the close, after whatever happened during the day.
        value_by_date[session] = cash + sum(
            shares * closes.at[session, ticker]
            for ticker, shares in holdings.items()
            if ticker in closes.columns and not pd.isna(closes.at[session, ticker])
        )
        for screen_date in due:
            rebalance_rows.append({
                "screened_on": screen_date,
                "filled_on": session,
                "names": len(holdings),
                "turnover": turnover_fraction,
                "cost": charge / total if total else 0.0,
            })

    if delisted:
        notes.append(
            f"{len(delisted)} position(s) stopped trading during the run "
            f"({', '.join(sorted(set(delisted))[:8])}). Each was realised at "
            f"{delisting_return:.0%} from its last close rather than held at it (R18); "
            "the assumption is a setting and it moves the result."
        )
    # R19 asks for both numbers in every report, zero included: a missing sentence reads
    # as "nothing to see", which is not the same as "counted, and it was nothing".
    unpriced = sorted(t for t in departed if not priced_when_last_seen.get(t, False))
    listed = f" ({', '.join(sorted(departed)[:8])})" if departed else ""
    notes.append(
        f"{len(departed)} name(s) left the universe between rebalance dates{listed}, "
        f"{len(unpriced)} of them without a price when last screened; from then on none "
        "of them could be chosen (R19). Companies that vanished without a symbol never "
        "entered the universe at all and cannot be counted here — the universe verdict "
        "above says how many of those the data is missing."
    )
    if stuck_events:
        notes.append(
            f"{stuck_events} rebalance(s) met a holding with no opening price that day. "
            "It was kept rather than sold, because a position you cannot price is one you "
            "cannot trade — selling it into the gap would have been a free exit."
        )

    curve = pd.Series(value_by_date).sort_index()
    curve = 100.0 * curve / curve.iloc[0]
    bench = _benchmark_curve(getattr(source, "con", None), benchmark, curve.index)

    years = max((curve.index[-1] - curve.index[0]).days, 1) / 365.25
    rebalances = pd.DataFrame(rebalance_rows)
    ever_held = sorted({name for picks in chosen_at.values() for name in picks})
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
        ever_held=ever_held,
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
    # Forward only. A back-fill handed the first days of a run the index's FIRST later
    # value when the series started after the backtest — a price from the future.
    stored = pd.Series(frame["close_adj"].to_numpy(),
                       index=pd.to_datetime(frame["date"]))
    wanted = pd.to_datetime(pd.Index(index))
    series = stored.reindex(stored.index.union(wanted)).ffill().reindex(wanted)
    if series.isna().any():
        return None
    series.index = index
    return 100.0 * series / series.iloc[0]


def stage_b_label(universe_verdict: str = "") -> str:
    """What stage B fixed, and what it could not (R12)."""
    text = (
        "Stage B · re-screened at every rebalance date. "
        "Corrected: only filings already published at each date are used, the first filed "
        "version wins over later restatements, orders are both PRICED AND SIZED at the "
        "next session's open, a position that stops trading takes the stated delisting "
        "loss, and one that cannot be priced on a rebalance day is kept rather than sold "
        "into the gap. "
        "Costs are charged on what changed hands at the most expensive tier, which "
        "overstates them for large companies — deliberately, since the alternative is to "
        "understate them for small ones. "
        "NOT corrected: the universe holds only companies that still exist."
    )
    return f"{text} {universe_verdict}".strip()
