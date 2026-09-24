"""Run a StrategySpec over daily bars and report what it would have cost and earned.

The execution model is the part worth reading, because it is where backtests lie:

  * a signal is computed from the CLOSE of day t;
  * the trade happens at the OPEN of day t+1;
  * the day is therefore split in two — the overnight move from close(t) to open(t+1)
    belongs to yesterday's position, and the move from open(t+1) to close(t+1) belongs to
    the new one.

Screening on a close and filling at that same close is the default mistake in home-made
backtests, and it is worth roughly the average overnight move per trade — in the same
direction as the signal, every single time, which is what makes it so flattering.

Costs are charged on what actually changes hands, at the tier of the names traded. A rule
that flips in and out weekly pays that spread weekly, and for a basket of small companies
that alone decides whether the strategy is worth anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import metrics as mx
from ..config import CostModel
from . import signals as sg
from .spec import StrategySpec


@dataclass(frozen=True, slots=True)
class LabResult:
    dates: list
    portfolio: list[float]
    benchmark: list[float] | None
    benchmark_symbol: str | None
    metrics: dict
    exposure: list[float]            # share of capital invested, per day
    trades: int
    turnover: float                  # annualised, one-way
    label: str
    notes: list[str] = field(default_factory=list)
    # One row per entry or exit: date, ticker, side ("buy"/"sell"), price (the open it
    # filled at, on the adjusted basis). Resizing a held position is not listed.
    fills: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def years(self) -> float:
        return max((self.dates[-1] - self.dates[0]).days, 1) / 365.25


def held_series(prices: pd.Series, spec: StrategySpec) -> pd.Series:
    """The raw in/out decision for one name, before sizing and before the stop."""
    if spec.signal == "always_in":
        held = pd.Series(True, index=prices.index)
    elif spec.signal == "ma_cross":
        held = sg.ma_cross(prices, spec.fast_days, spec.slow_days)
    elif spec.signal == "momentum":
        held = sg.momentum(prices, spec.lookback_days, spec.threshold)
    elif spec.signal == "mean_reversion":
        held = sg.mean_reversion(prices, spec.lookback_days, spec.z_entry, spec.z_exit)
    elif spec.signal == "bollinger_reversion":
        held = sg.bollinger_reversion(prices, spec.band_days, spec.band_k)
    elif spec.signal == "bollinger_breakout":
        held = sg.bollinger_breakout(prices, spec.band_days, spec.band_k)
    elif spec.signal == "rsi":
        held = sg.rsi_signal(prices, spec.rsi_days, spec.rsi_entry, spec.rsi_exit)
    elif spec.signal == "channel_breakout":
        held = sg.channel_breakout(prices, spec.breakout_days, spec.exit_days)
    elif spec.signal == "macd":
        held = sg.macd_signal(prices, spec.macd_fast, spec.macd_slow, spec.macd_signal)
    else:
        raise ValueError(f"unknown signal: {spec.signal}")
    held = sg.trend_filter(held, prices, spec.trend_filter_days)
    return sg.apply_stop(held, prices, spec.stop, spec.stop_pct, spec.cooldown_days)


def target_weights(held: pd.DataFrame, closes: pd.DataFrame,
                   spec: StrategySpec) -> pd.DataFrame:
    """Turn in/out flags into weights, one row per day.

    Volatility weighting gives every position the same expected wobble rather than the
    same money, which stops one jumpy name from deciding the result. It uses a trailing
    60-day deviation, so it too knows nothing about tomorrow.
    """
    if spec.weighting == "volatility":
        vol = closes.pct_change().rolling(60, min_periods=20).std()
        raw = held.astype(float) / vol.replace(0.0, np.nan)
        raw = raw.fillna(0.0)
    else:
        raw = held.astype(float)

    total = raw.sum(axis=1)
    weights = raw.div(total.replace(0.0, np.nan), axis=0).fillna(0.0)
    if spec.max_weight < 1.0:
        weights = weights.clip(upper=spec.max_weight)
        # Capping leaves the row summing to less than one; the remainder stays in cash,
        # which is the honest reading of "no position above x%".
    if not spec.cash_when_flat:
        # Spread everything over whatever is held; a day with nothing held is still cash,
        # because there is nothing to spread over.
        pass
    return weights


def run(
    bars: pd.DataFrame,
    spec: StrategySpec,
    costs: CostModel | None = None,
    benchmark: pd.Series | None = None,
    benchmark_symbol: str | None = None,
    risk_free: float = 0.02,
    cost_tier_market_cap: float = 0.0,
) -> LabResult | None:
    """`bars`: long frame with ticker, date, open_adj, close_adj (adjusted basis)."""
    if bars.empty:
        return None
    costs = costs or CostModel()
    closes = bars.pivot_table(index="date", columns="ticker", values="close_adj").sort_index()
    opens = bars.pivot_table(index="date", columns="ticker", values="open_adj").sort_index()

    # A price of zero is not a price. The free source carries stretches of zero adjusted
    # closes for some names — Diversified Energy has 732 of them — and dividing by one
    # produces an infinite return that propagates through the whole curve. Found exactly
    # that way: a rule reporting +inf% a year.
    notes: list[str] = []
    bad = {t: int((closes[t] <= 0).sum()) for t in closes.columns}
    broken = {t: n for t, n in bad.items() if n}
    if broken:
        notes.append(
            "ignored days with a price of zero or less, which the data source carries for "
            + ", ".join(f"{t} ({n} days)" for t, n in sorted(
                broken.items(), key=lambda kv: -kv[1])[:5])
            + ". Those days are treated as no price at all rather than as a collapse."
        )
    closes = closes.where(closes > 0).ffill()
    opens = opens.where(opens > 0).ffill()
    if len(closes) < 2:
        return None

    held = pd.DataFrame(
        {ticker: held_series(closes[ticker].dropna(), spec).reindex(closes.index).fillna(False)
         for ticker in closes.columns},
        index=closes.index,
    )
    wanted = target_weights(held, closes, spec)

    rate = costs.spread_for(cost_tier_market_cap) + costs.commission
    value = 1.0
    current = pd.Series(0.0, index=closes.columns)
    values: list[float] = [value]
    exposure: list[float] = [0.0]
    dates = list(closes.index)
    trades = 0
    traded_total = 0.0
    fill_rows: list[dict] = []

    for i in range(1, len(dates)):
        today, yesterday = dates[i], dates[i - 1]
        # 1. overnight, still on yesterday's position
        overnight = _finite(opens.loc[today] / closes.loc[yesterday] - 1.0)
        value *= 1.0 + float((current * overnight).sum())

        # 2. the trade, at the open, on the signal from yesterday's close
        target = wanted.loc[yesterday]
        change = float((target - current).abs().sum())
        if change > 1e-12:
            trades += 1
            traded_total += change
            value *= 1.0 - change * rate
            for ticker in target.index:
                was, now = current[ticker] > 1e-12, target[ticker] > 1e-12
                if was != now:
                    fill_rows.append({"date": today, "ticker": ticker,
                                      "side": "buy" if now else "sell",
                                      "price": float(opens.at[today, ticker])})
            current = target

        # 3. the rest of the day, on the new position
        intraday = _finite(closes.loc[today] / opens.loc[today] - 1.0)
        value *= 1.0 + float((current * intraday).sum())

        values.append(value)
        exposure.append(float(current.sum()))

    curve = pd.Series(values, index=dates) * 100.0
    years = max((dates[-1] - dates[0]).days, 1) / 365.25
    bench = None
    if benchmark is not None and not benchmark.empty:
        # Forward only: a back-fill hands the first days the index's first LATER value
        # when the index starts after the run (the same defect the stage B review found).
        wanted = pd.Index(curve.index)
        bench = benchmark.reindex(benchmark.index.union(wanted)).ffill().reindex(wanted)
        bench = None if bench.isna().any() else 100.0 * bench / bench.iloc[0]

    summary = mx.summary(curve, bench, years, risk_free, base=100.0,
                         turnover=traded_total / years)
    summary["average_exposure"] = float(np.mean(exposure))

    return LabResult(
        dates=dates,
        portfolio=[float(v) for v in curve],
        benchmark=[float(v) for v in bench] if bench is not None else None,
        benchmark_symbol=benchmark_symbol if bench is not None else None,
        metrics=summary,
        exposure=exposure,
        trades=trades,
        turnover=traded_total / years,
        label=lab_label(),
        notes=notes,
        fills=pd.DataFrame(fill_rows, columns=["date", "ticker", "side", "price"]),
    )


def _finite(returns: pd.Series) -> pd.Series:
    """Anything that is not a real number is a missing observation, not a move.

    A single infinity here does not spoil one day: it multiplies into the running value
    and every figure after it, which is how a broken price two years ago turns a whole
    backtest into +inf% a year.
    """
    return returns.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def lab_label() -> str:
    """What the lab did and did not account for (R12, and the honest version of it)."""
    return (
        "Rule test · signals read at the close, filled at the NEXT open, costs charged on "
        "what changed hands at the tier of the names traded. "
        "NOT corrected: the basket comes from today's screen, so the names are survivors "
        "chosen with hindsight; and a rule tested until it looks good is a rule fitted to "
        "this data. The attempt counter next to the result is there for that reason."
    )
