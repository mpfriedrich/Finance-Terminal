"""When to invest, rather than what to buy.

The screener picks securities. This answers a different question: given money arriving
over time, does it pay to deploy it immediately, or to hold it back and wait for a fall?

**The comparison only means something with a cash account.** The usual version of this
test quietly cheats: it compares "buy every dip" against "buy every month" while ignoring
where the money sat while it waited. Cash that is waiting is not invested, and cash that
is invested is not waiting. Both strategies here receive exactly the same contributions on
exactly the same dates into a cash balance; the only difference is the rule that decides
when that balance is spent. Idle cash earns a stated rate, which the reader can set to
zero to see how much of the answer depends on it.

What comes out is a money-weighted return — the return on the money you actually had in
the market, weighted by how long it was there. That is the number that describes your
outcome. A time-weighted return would describe the asset's outcome, which is the same for
every strategy here and therefore says nothing about the choice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Frequency = Literal["weekly", "fortnightly", "monthly", "quarterly"]
Rule = Literal["immediately", "on_drawdown", "below_average", "lump_sum"]

PERIOD_DAYS = {"weekly": 7, "fortnightly": 14, "monthly": 30, "quarterly": 91}


@dataclass(frozen=True, slots=True)
class Plan:
    amount: float = 500.0
    frequency: Frequency = "monthly"
    rule: Rule = "immediately"
    drawdown_trigger: float = 0.02
    """For `on_drawdown`: how far below the running high before cash is deployed."""
    average_days: int = 200
    """For `below_average`: the moving average the price must be under."""
    deploy_fraction: float = 1.0
    """How much of the waiting balance to spend when the rule fires."""
    cash_rate: float = 0.02
    """Annual return on money waiting to be invested."""
    cost: float = 0.0005
    """Charged on each purchase."""

    def describe(self) -> str:
        base = f"{self.amount:,.0f} {self.frequency}"
        if self.rule == "immediately":
            return f"{base}, invested on arrival"
        if self.rule == "lump_sum":
            return "everything invested at the start"
        if self.rule == "on_drawdown":
            return (f"{base}, held as cash until the price is "
                    f"{self.drawdown_trigger:.0%} below its running high")
        return f"{base}, held as cash until the price is under its {self.average_days}-day average"


def _contribution_dates(index: pd.DatetimeIndex, frequency: Frequency) -> set:
    """Contribution days: the first trading day on or after each period boundary."""
    step = PERIOD_DAYS[frequency]
    out, nxt = set(), index[0]
    while nxt <= index[-1]:
        candidates = index[index >= nxt]
        if len(candidates) == 0:
            break
        out.add(candidates[0])
        nxt = nxt + pd.Timedelta(days=step)
    return out


def simulate(prices: pd.Series, plan: Plan) -> dict:
    """Run one plan over one price series.

    `prices` must be total-return adjusted, so reinvested dividends are already in it.
    """
    s = prices.dropna().astype(float)
    if len(s) < 30:
        return {}
    s.index = pd.to_datetime(s.index)

    contributions = _contribution_dates(s.index, plan.frequency)
    running_high = s.cummax()
    drawdown = s / running_high - 1.0
    moving_avg = s.rolling(plan.average_days, min_periods=plan.average_days // 2).mean()

    daily_cash_rate = (1 + plan.cash_rate) ** (1 / 365) - 1
    total_periods = max(len(contributions), 1)

    cash = 0.0
    units = 0.0
    invested = 0.0
    purchases: list[dict] = []
    values, cash_line = [], []
    prev_date = s.index[0]

    if plan.rule == "lump_sum":
        # Everything the plan would ever contribute, deployed on day one. The honest
        # yardstick: it is what you would do if you already had the money.
        cash = plan.amount * total_periods

    for date, price in s.items():
        days = max((date - prev_date).days, 0)
        if cash > 0 and days:
            cash *= (1 + daily_cash_rate) ** days
        prev_date = date

        if plan.rule != "lump_sum" and date in contributions:
            cash += plan.amount
            invested += plan.amount

        fires = False
        if plan.rule in ("immediately", "lump_sum"):
            fires = cash > 0
        elif plan.rule == "on_drawdown":
            fires = cash > 0 and drawdown.loc[date] <= -abs(plan.drawdown_trigger)
        elif plan.rule == "below_average":
            ma = moving_avg.loc[date]
            fires = cash > 0 and not np.isnan(ma) and price < ma

        if fires:
            spend = cash * plan.deploy_fraction
            net = spend * (1 - plan.cost)
            units += net / price
            cash -= spend
            purchases.append({"date": date, "price": price, "amount": spend})

        values.append(units * price + cash)
        cash_line.append(cash)

    if plan.rule == "lump_sum":
        invested = plan.amount * total_periods

    curve = pd.Series(values, index=s.index, name="value")
    final = float(curve.iloc[-1])
    buys = pd.DataFrame(purchases)

    years = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    # Money-weighted: solve for the rate that makes the contributions grow into the
    # final value. Bisection again — the function is monotonic in the rate.
    flows = [(d, plan.amount) for d in sorted(contributions)] if plan.rule != "lump_sum" \
        else [(s.index[0], plan.amount * total_periods)]

    def future_value(rate: float) -> float:
        return sum(
            amount * (1 + rate) ** ((s.index[-1] - d).days / 365.25) for d, amount in flows
        )

    lo, hi = -0.99, 2.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if future_value(mid) < final:
            lo = mid
        else:
            hi = mid
    money_weighted = (lo + hi) / 2

    return {
        "curve": curve,
        "cash": pd.Series(cash_line, index=s.index, name="cash"),
        "final_value": final,
        "invested": invested,
        "profit": final - invested,
        "multiple": final / invested if invested else np.nan,
        "money_weighted_return": money_weighted,
        "purchases": buys,
        "n_purchases": len(buys),
        "average_cost": float(buys.amount.sum() / (buys.amount / buys.price).sum())
                        if len(buys) else np.nan,
        "average_price": float(s.mean()),
        "idle_cash_share": float(np.mean(np.array(cash_line) / np.maximum(curve.to_numpy(), 1e-9))),
        "years": years,
        "plan": plan,
    }


def compare(prices: pd.Series, plans: list[Plan]) -> pd.DataFrame:
    """Run several plans over the same series and line the outcomes up."""
    rows = []
    for plan in plans:
        r = simulate(prices, plan)
        if not r:
            continue
        rows.append({
            "strategy": plan.describe(),
            "invested": r["invested"],
            "final value": r["final_value"],
            "profit": r["profit"],
            "return p.a.": r["money_weighted_return"],
            "purchases": r["n_purchases"],
            "avg cost": r["average_cost"],
            "idle cash": r["idle_cash_share"],
        })
    return pd.DataFrame(rows)
