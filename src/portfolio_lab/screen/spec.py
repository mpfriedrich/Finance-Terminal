"""The portfolio specification — the only language between the chat and the screener.

This object is deliberately small and closed. The LLM's tool schema is generated from it,
which means:

  * there is no field in which a model could name a stock, so it cannot;
  * a criterion that does not map to a field here cannot be silently absorbed — it lands
    in `unsupported` and the UI has to say so (PLAN.md R3).

Everything the screener does is derivable from this object plus the data. Same spec, same
data, same portfolio — every time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Literal

# Fields a filter may reference. Mirrors the metric vocabulary in PLAN.md.
FILTERABLE: tuple[str, ...] = (
    # size and price
    "market_cap",
    "price",
    "median_dollar_volume_60d",
    # valuation multiples — lower is cheaper
    "price_to_book",
    "price_to_earnings",
    "price_to_sales",
    "price_to_cash_flow",
    "price_to_free_cash_flow",
    "price_to_cash",
    # model-based value — see screen/valuation.py; these depend on assumptions
    "price_to_intrinsic",
    "price_to_graham",
    # quality and leverage — higher margin is better, lower debt is safer
    "net_margin",
    "operating_margin_proxy",
    "return_on_equity",
    "debt_to_equity",
    "liabilities_to_equity",
    "current_ratio",
    # raw magnitudes
    "book_value",
    "net_income_ttm",
    "revenue_ttm",
    "operating_cash_flow_ttm",
    "free_cash_flow_ttm",
    "cash",
    "total_assets",
    "total_liabilities",
    # categorical
    "industry_group",  # the coarse, typeable one: "Technology", "Health Care", ...
    "sector",          # the SEC's own wording: "Services-Prepackaged Software", ...
    "exchange",
)

Op = Literal["<=", ">=", "<", ">", "==", "!=", "in", "contains", "not_contains"]

# Lower is cheaper for these, so an ascending sort means "most attractive first".
LOWER_IS_BETTER = {
    "price_to_book",
    "price_to_earnings",
    "price_to_sales",
    "price_to_cash_flow",
    "price_to_free_cash_flow",
    "price_to_cash",
    "price_to_intrinsic",
    "price_to_graham",
}


@dataclass(frozen=True, slots=True)
class Filter:
    metric: str
    op: Op
    value: float | str | list[str]

    def describe(self) -> str:
        words = {"<=": "at most", ">=": "at least", "<": "below", ">": "above",
                 "==": "is", "!=": "is not", "in": "one of",
                 "contains": "contains", "not_contains": "does not contain"}
        return f"{self.metric.replace('_', ' ')} {words.get(self.op, self.op)} {self.value}"


@dataclass(frozen=True, slots=True)
class PortfolioSpec:
    size: int = 10
    filters: tuple[Filter, ...] = ()
    rank_by: str | None = None
    rank_ascending: bool = True
    weighting: Literal["equal", "market_cap"] = "equal"
    rebalance: Literal["buy_and_hold", "quarterly", "annual"] = "quarterly"
    # Eligibility floors. Defaults exist because the typical screen here targets small,
    # cheap companies — exactly where untradeable names produce fake backtest returns.
    min_price: float = 5.0
    min_dollar_volume: float = 1_000_000.0
    # How OFTEN it trades, not how much. Turnover measures the size of a day's trading
    # and says nothing about the days there are none: three names in the universe traded
    # on four days in a year and cleared a million dollars on each, so a turnover floor
    # alone waved them through (measured 2026-09-24).
    min_trading_days_60: int = 40
    max_filing_age_months: int = 6
    # Anything the translator could not map. Never empty silently — the UI prints it.
    unsupported: tuple[str, ...] = ()
    # Hand-picked holdings. When this is set the screen is bypassed entirely: the
    # portfolio IS these companies.
    #
    # This field is deliberately absent from the language model's tool schema. The person
    # types tickers into a form; the model still has no way to name a security. That
    # asymmetry is the point — you may pick stocks, it may not.
    tickers: tuple[str, ...] = ()
    # Which of the passing names the person actually wants to hold. Empty means "the
    # screen has not been curated yet", and the top `size` are proposed as a starting
    # point — not as a limit. Selecting is what turns a ranked list into a portfolio.
    #
    # Absent from the model's schema for the same reason as `tickers`: it holds ticker
    # symbols, so a model that could write to it could name stocks through the back door.
    selected: tuple[str, ...] = ()

    def effective_rank(self) -> tuple[str, bool]:
        """Which metric decides who makes the cut when too many names qualify.

        If the user did not say, use the strictest valuation filter they DID set — that is
        the closest thing to their stated intent. Shown in the UI and overridable, because
        an invisible tiebreak that changes the portfolio is not acceptable (PLAN.md R6).
        """
        if self.rank_by:
            return self.rank_by, self.rank_ascending
        for f in self.filters:
            if f.metric in LOWER_IS_BETTER:
                return f.metric, True
        return "market_cap", False

    def with_filter(self, new: Filter) -> "PortfolioSpec":
        """Add or replace a filter on the same metric+direction, keeping the rest.

        This is what makes a follow-up message refine instead of restart: "cap not above
        2bn" must not wipe the price-to-book rule set two messages earlier (R1).
        """
        same_side = lambda a, b: (a.op in ("<", "<=")) == (b.op in ("<", "<="))
        kept = tuple(
            f for f in self.filters
            if not (f.metric == new.metric and same_side(f, new))
        )
        return replace(self, filters=kept + (new,))

    @property
    def is_hand_picked(self) -> bool:
        return bool(self.tickers)

    def describe(self) -> list[str]:
        """Plain-language rendering shown after every message (R2)."""
        if self.tickers:
            return [
                f"hand-picked: {', '.join(self.tickers)}",
                f"{len(self.tickers)} positions, weighting: {self.weighting}",
                "filters are not applied to a hand-picked list",
            ]
        lines = [f.describe() for f in self.filters]
        rank, asc = self.effective_rank()
        lines.append(f"rank every passing name by {rank.replace('_', ' ')}"
                     f" ({'lowest' if asc else 'highest'} first)")
        lines.append(
            f"holding {len(self.selected)} of them, chosen by hand"
            if self.selected else
            f"nothing chosen yet — the top {self.size} are proposed"
        )
        lines.append(f"weighting: {self.weighting}; rebalance: {self.rebalance}")
        lines.append(
            f"eligibility: price ≥ {self.min_price:g}, "
            f"60-day median turnover ≥ {self.min_dollar_volume:,.0f}, "
            f"traded on ≥ {self.min_trading_days_60} of the last 60 sessions, "
            f"filing not older than {self.max_filing_age_months} months"
        )
        return lines

    def fingerprint(self) -> str:
        """Stable identity, used to count how often this exact screen has been tested
        against the same data (PLAN.md R21 — the overfitting counter)."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def spec_to_json(spec: PortfolioSpec) -> str:
    return json.dumps(asdict(spec), sort_keys=True)


def spec_from_json(blob: str) -> PortfolioSpec:
    """Rebuild a spec from storage.

    Every sequence field has to be coerced back to a tuple: JSON has no tuples, so a
    round-trip silently turns them into lists and the spec stops comparing equal to
    itself — which would also change its fingerprint and reset the backtest counter.
    """
    raw = json.loads(blob)
    filters = tuple(Filter(**f) for f in raw.pop("filters", []))
    unsupported = tuple(raw.pop("unsupported", ()))
    tickers = tuple(raw.pop("tickers", ()))
    selected = tuple(raw.pop("selected", ()))
    return PortfolioSpec(
        filters=filters, unsupported=unsupported, tickers=tickers,
        selected=selected, **raw
    )
