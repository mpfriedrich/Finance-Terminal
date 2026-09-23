"""The data seam.

Every market fact the app ever uses passes through `DataSource`. Nothing above this
module knows whether the numbers came from the SEC, from Yahoo, from a paid vendor or
from a test fixture. That is what makes the paid-source upgrade a config change instead
of a rewrite (PLAN.md, Q5).

Two rules that look like details and are not:

1. `fundamentals()` takes an `asof` date and MUST NOT return a fact whose `filed` date is
   after it. A backtest that sees a balance sheet before it was published invents money.
2. Valuation ratios are computed from the UNADJUSTED close; returns are computed from the
   ADJUSTED close. Split/dividend adjustment rewrites history backwards, so an adjusted
   price times a historical share count is not a market cap that ever existed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Identifies the exact data build a result was produced from.

    Free price sources silently restate adjusted closes, so a backtest run in March is not
    reproducible in May unless we record which build it read (PLAN.md R22).
    """

    built_at: dt.datetime
    source: str
    row_counts: dict[str, int]

    def fingerprint(self) -> str:
        parts = [self.source, self.built_at.isoformat()]
        parts += [f"{k}={v}" for k, v in sorted(self.row_counts.items())]
        return "|".join(parts)


# Canonical metric names. The LLM tool schema is generated from this tuple, so a metric
# that is not listed here cannot be requested, cannot be silently dropped, and must be
# explicitly rejected (PLAN.md R3).
METRICS: tuple[str, ...] = (
    "market_cap",
    "shares_outstanding",
    "book_value",
    "price_to_book",
    "revenue_ttm",
    "price_to_sales",
    "operating_cash_flow_ttm",
    "price_to_cash_flow",
    "net_income_ttm",
    "price_to_earnings",
    "sector",
    "exchange",
    "price",
    "median_dollar_volume_60d",
    "data_age_days",
)


@runtime_checkable
class DataSource(Protocol):
    """What every source must provide. Implemented by FreeSource, FixtureSource, PaidSource."""

    name: str

    def snapshot(self) -> Snapshot:
        """Identity of the current data build."""
        ...

    def universe(self, asof: dt.date) -> pd.DataFrame:
        """Securities tradable on `asof`.

        Columns: ticker, cik, name, sector, exchange.

        MUST reflect listings as of `asof`, not today's survivors. A source that cannot do
        this sets `survivorship_free = False` so the UI can say so instead of implying a
        correction that never happened (PLAN.md Phase 7).
        """
        ...

    def fundamentals(self, asof: dt.date) -> pd.DataFrame:
        """Point-in-time fundamentals known on `asof`.

        Columns: cik, metric, value, period_end, filed.

        Contract: no row with `filed > asof`. Where a period was later restated, the
        EARLIEST filed version wins — that is what the market actually saw.
        """
        ...

    def prices(
        self, tickers: list[str], start: dt.date, end: dt.date
    ) -> pd.DataFrame:
        """Daily bars.

        Columns: ticker, date, open, close, close_adj, volume.
        `close` is unadjusted (for valuation ratios), `close_adj` is total-return adjusted
        (for performance). `open` is the next-day fill price used by the rebalance loop.
        """
        ...

    @property
    def survivorship_free(self) -> bool:
        """True only if delisted securities are present with prices.

        The free stack will answer False. The UI prints that verdict rather than a
        reassuring silence (PLAN.md Phase 7, <50% branch).
        """
        ...
