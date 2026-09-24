"""Intrinsic value — a model, not a measurement.

Everything else in this screener is read off a filing: book value is a number a company
reported. Intrinsic value is not. It is the output of assumptions about the future, and
whoever picks the assumptions picks the answer. Change the discount rate by two points
and a "40% undervalued" company becomes fairly priced.

So this module does three things deliberately:

1. **The assumptions are explicit and adjustable**, never buried. They travel with the
   result so a number can always be traced back to what produced it.
2. **The defaults assume no growth.** Growth is the assumption that does the most damage
   when wrong, and it is the one people are most optimistic about. A company that keeps
   earning what it earns today is a floor, not a forecast.
3. **Two independent models**, not one. When a discounted cash flow and Graham's formula
   disagree wildly about a company, that disagreement is information — usually that the
   company's cash flow and its book value tell different stories.

Neither output is a price target and neither is advice. They are what a stated set of
assumptions implies, and they are only as good as those assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Assumptions:
    """The knobs. Shown in the UI; every intrinsic value carries these."""

    discount_rate: float = 0.10
    """Required annual return. Higher means you demand more, so values fall."""

    growth_rate: float = 0.00
    """Assumed annual growth of free cash flow over the projection. Default: none."""

    terminal_growth: float = 0.02
    """Growth assumed forever after the projection. Must stay below the discount rate."""

    years: int = 10
    """Length of the explicit projection before the terminal value takes over."""

    def valid(self) -> bool:
        return self.terminal_growth < self.discount_rate and self.years >= 1

    def describe(self) -> str:
        return (
            f"DCF on free cash flow: {self.growth_rate:.0%} growth for {self.years} years, "
            f"then {self.terminal_growth:.0%} forever, discounted at "
            f"{self.discount_rate:.0%}"
        )


def discounted_cash_flow(
    free_cash_flow: pd.Series, a: Assumptions = Assumptions()
) -> pd.Series:
    """Present value of a growing cash flow stream, per company.

    A negative or missing free cash flow yields no value rather than a negative one: a
    company burning cash is not worth less than nothing to an equity holder, it is simply
    outside what this model can say anything about. Returning NaN makes it drop out of
    the screen and be counted, instead of ranking as spectacularly cheap.
    """
    if not a.valid():
        return pd.Series(np.nan, index=free_cash_flow.index)

    fcf = pd.to_numeric(free_cash_flow, errors="coerce")
    usable = fcf > 0

    # Explicit projection: sum of FCF_t / (1+r)^t for t = 1..years
    factor = 0.0
    for t in range(1, a.years + 1):
        factor += ((1 + a.growth_rate) ** t) / ((1 + a.discount_rate) ** t)

    # Terminal value at year N, discounted back. Gordon growth on the final year's flow.
    final = (1 + a.growth_rate) ** a.years
    terminal = (final * (1 + a.terminal_growth)) / (a.discount_rate - a.terminal_growth)
    factor += terminal / ((1 + a.discount_rate) ** a.years)

    return pd.Series(np.where(usable, fcf * factor, np.nan), index=fcf.index)


def graham_number(eps: pd.Series, book_value_per_share: pd.Series) -> pd.Series:
    """Graham's rule of thumb: sqrt(22.5 × EPS × book value per share).

    Kept alongside the DCF because it leans on entirely different inputs — earnings and
    balance sheet rather than cash flow and a discount rate. Where the two agree there is
    some substance; where they diverge, one of the two stories is wrong.

    The 22.5 is Graham's own product of a 15 price-to-earnings and a 1.5 price-to-book
    ceiling. It is a convention from 1949, not a law, and it says nothing about companies
    with losses or negative equity — those return NaN.
    """
    e = pd.to_numeric(eps, errors="coerce")
    b = pd.to_numeric(book_value_per_share, errors="coerce")
    ok = (e > 0) & (b > 0)
    # The product is masked BEFORE the root, not after. np.where evaluates both branches,
    # so passing the raw product took the square root of every loss-making company's
    # negative number first and discarded it afterwards — harmless in the result, but it
    # printed a RuntimeWarning on every screen and every test run, which teaches everyone
    # to ignore warnings.
    product = (22.5 * e * b).where(ok)
    return pd.Series(np.sqrt(product), index=e.index)


def add_valuation(frame: pd.DataFrame, a: Assumptions = Assumptions()) -> pd.DataFrame:
    """Attach intrinsic value columns to a screening frame."""
    out = frame.copy()

    def col(name: str) -> pd.Series:
        if name not in out:
            return pd.Series(np.nan, index=out.index, dtype="float64")
        return pd.to_numeric(out[name], errors="coerce")

    ocf = col("operating_cash_flow_ttm")
    capex = col("capex_ttm")
    # Capex is reported as a positive outflow, so subtract its magnitude. Where a filer
    # tagged no capex, treat it as zero rather than discarding the company — but this is
    # the optimistic reading, so the column records it.
    out["free_cash_flow_ttm"] = ocf - capex.abs().fillna(0.0)
    out["capex_reported"] = capex.notna()

    out["price_to_free_cash_flow"] = pd.Series(
        np.where(out["free_cash_flow_ttm"] > 0,
                 col("market_cap") / out["free_cash_flow_ttm"], np.nan),
        index=out.index, dtype="float64",
    )

    out["intrinsic_value"] = discounted_cash_flow(out["free_cash_flow_ttm"], a)
    # Below 1 means the market pays less than this model's value. It is not a buy signal;
    # it usually means the market disagrees with the assumptions above.
    out["price_to_intrinsic"] = np.where(
        out["intrinsic_value"] > 0, out["market_cap"] / out["intrinsic_value"], np.nan
    )

    shares = col("shares_outstanding")
    eps = col("net_income_ttm") / shares
    bvps = col("book_value") / shares
    out["graham_number"] = graham_number(eps, bvps)
    price = col("price")
    out["price_to_graham"] = np.where(
        out["graham_number"] > 0, price / out["graham_number"], np.nan
    )
    return out
