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
        `close` is the price the stock ACTUALLY TRADED at — the only one that may meet a
        share count. The free source delivers history already split-adjusted, so its own
        close column is not that price; `FreeSource` hands over the reconstructed one
        (see prices.py). `close_adj` is total-return adjusted, for performance. `open` is
        the next-day fill price used by the rebalance loop.
        """
        ...

    @property
    def survivorship_free(self) -> bool:
        """True only if delisted securities are present with prices.

        The free stack will answer False. The UI prints that verdict rather than a
        reassuring silence (PLAN.md Phase 7, <50% branch).
        """
        ...

    def screening_frame(self, asof: dt.date) -> pd.DataFrame:
        """One row per security with everything a screen needs.

        Columns: ticker, cik, name, sector, industry_group, exchange, price,
        median_dollar_volume_60d, data_age_days, plus whatever fundamental fields the
        source can supply (book_value, net_income_ttm, shares_outstanding, ...).

        `build_screening_frame` below assembles exactly this from the three primitives
        above, so a source gets it for free. A source may override it with a faster
        query, in which case the equivalence is a test, not a hope.
        """
        ...


def wide_from_long(facts: pd.DataFrame, asof: dt.date) -> pd.DataFrame:
    """Long point-in-time facts -> one row per filer, plus the age of its latest filing.

    MIN age, not max: the question the freshness floor asks is "has this company reported
    recently", so the most recent filing decides. Taking the oldest metric's age instead
    marks a company stale because one slow-moving annual figure lags (PLAN.md R14).
    """
    if facts.empty:
        return pd.DataFrame(columns=["cik", "data_age_days"])
    wide = facts.pivot_table(index="cik", columns="metric", values="value", aggfunc="last")
    age = (pd.to_datetime(asof) - pd.to_datetime(facts["filed"])).dt.days
    ages = age.groupby(facts["cik"]).min().rename("data_age_days")
    return wide.join(ages).reset_index()


def build_screening_frame(
    source: "DataSource",
    asof: dt.date,
    volume_sessions: int = 60,
    lookback_days: int = 1825,
) -> pd.DataFrame:
    """The reference screening frame, built only from the seam's own primitives.

    This is what makes "the data layer is exchangeable" a fact rather than a claim: the
    screener can be driven by ANY source that implements the protocol, including the
    fixture, and the identical test series runs against both (PLAN.md Phase 2).
    """
    from . import industry

    uni = source.universe(asof)
    if uni.empty:
        return uni
    facts = source.fundamentals(asof)
    # A source may already answer wide (one row per filer). Both shapes are accepted
    # because the pivot is the only difference, and refusing one would mean two code
    # paths in every caller instead of one here.
    wide = wide_from_long(facts, asof) if "metric" in facts.columns else facts

    # The lookback is deliberately long. Liquidity is the median of the last
    # `volume_sessions` SESSIONS, not of the sessions inside some calendar window: a name
    # that trades four days a year would otherwise be measured over four bars here and
    # over sixty there, and the two builders would disagree about who is tradable.
    # (That such a name passes a liquidity floor at all is a separate problem, recorded
    # in PLAN.md rather than quietly fixed by a windowing choice.)
    bars = source.prices(
        list(uni.ticker.astype(str)),
        asof - dt.timedelta(days=lookback_days),
        asof,
    )
    if bars.empty:
        last_px = pd.DataFrame(columns=["ticker", "price"])
        liquidity = pd.DataFrame(columns=["ticker", "median_dollar_volume_60d"])
    else:
        bars = bars[pd.to_datetime(bars.date) <= pd.to_datetime(asof)].sort_values("date")
        last_px = (bars.groupby("ticker", as_index=False)
                       .last()[["ticker", "close"]]
                       .rename(columns={"close": "price"}))
        recent = bars.groupby("ticker").tail(volume_sessions).copy()
        recent["dollar_volume"] = recent["close"] * recent["volume"]
        liquidity = (recent.groupby("ticker")["dollar_volume"].median()
                           .rename("median_dollar_volume_60d").reset_index())
        # Sessions are the last `volume_sessions` dates on which ANY name in this
        # universe traded — the same definition the live source uses in SQL, so the two
        # cannot drift apart on who counts as regularly traded.
        sessions = sorted(bars.date.unique())[-volume_sessions:]
        traded = (bars[bars.date.isin(sessions)]
                  .groupby("ticker").size().rename("trading_days_60").reset_index())

    out = uni
    if not wide.empty:
        out = out.merge(wide, on="cik", how="left")
    out = out.merge(last_px, on="ticker", how="left")
    out = out.merge(liquidity, on="ticker", how="left")
    if not bars.empty:
        out = out.merge(traded, on="ticker", how="left")
    if "trading_days_60" not in out.columns:
        out["trading_days_60"] = float("nan")
    if "data_age_days" not in out.columns:
        out["data_age_days"] = float("nan")
    return industry.add_industry_group(out)
