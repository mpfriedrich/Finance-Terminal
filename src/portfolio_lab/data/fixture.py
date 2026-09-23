"""A tiny, fully deterministic DataSource.

This is not a mock for convenience. It is the proof that the seam is real: the Phase 2
screener test suite runs against FixtureSource AND FreeSource unchanged. An interface
that only ever had one implementation is an assumption, not an abstraction.

It also gives the point-in-time tests something with known answers:
  - CIK 1001 restates its 2023 book value upward in a later filing. The screener must use
    the FIRST filed value, because that is what was visible at the time.
  - CIK 1003 stops filing after 2021 — a "zombie". It must fall out on the freshness rule.
  - CIK 1004 delists mid-2023. It must be absent from the universe afterwards.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .base import Snapshot

_D = dt.date

_SECURITIES = pd.DataFrame(
    [
        # cik, ticker, name, sector, exchange, first_seen, last_seen
        (1001, "AAA", "Alpha Industries", "Industrials", "NYSE", _D(2018, 1, 2), None),
        (1002, "BBB", "Beta Materials", "Materials", "NASDAQ", _D(2018, 1, 2), None),
        (1003, "CCC", "Gamma Holdings", "Financials", "NASDAQ", _D(2018, 1, 2), None),
        (1004, "DDD", "Delta Mining", "Materials", "NYSE", _D(2018, 1, 2), _D(2023, 6, 30)),
        (1005, "EEE", "Epsilon Foods", "Staples", "NYSE", _D(2018, 1, 2), None),
    ],
    columns=["cik", "ticker", "name", "sector", "exchange", "first_seen", "last_seen"],
)

# (cik, metric, value, period_end, filed, accession)
# Note CIK 1001 twice for FY2023 book value: 900 filed first, 1400 as a later restatement.
_FUNDAMENTALS = pd.DataFrame(
    [
        (1001, "book_value", 900.0, _D(2023, 12, 31), _D(2024, 2, 15), "0001-24-000001"),
        (1001, "book_value", 1400.0, _D(2023, 12, 31), _D(2024, 8, 20), "0001-24-000009"),
        (1001, "net_income_ttm", 300.0, _D(2023, 12, 31), _D(2024, 2, 15), "0001-24-000001"),
        (1001, "shares_outstanding", 100.0, _D(2023, 12, 31), _D(2024, 2, 15), "0001-24-000001"),
        (1002, "book_value", 5000.0, _D(2023, 12, 31), _D(2024, 3, 1), "0002-24-000001"),
        (1002, "net_income_ttm", 50.0, _D(2023, 12, 31), _D(2024, 3, 1), "0002-24-000001"),
        (1002, "shares_outstanding", 400.0, _D(2023, 12, 31), _D(2024, 3, 1), "0002-24-000001"),
        # Zombie: nothing after early 2022.
        (1003, "book_value", 2000.0, _D(2021, 12, 31), _D(2022, 2, 20), "0003-22-000001"),
        (1003, "net_income_ttm", 10.0, _D(2021, 12, 31), _D(2022, 2, 20), "0003-22-000001"),
        (1003, "shares_outstanding", 200.0, _D(2021, 12, 31), _D(2022, 2, 20), "0003-22-000001"),
        (1004, "book_value", 800.0, _D(2022, 12, 31), _D(2023, 3, 10), "0004-23-000001"),
        (1004, "net_income_ttm", 120.0, _D(2022, 12, 31), _D(2023, 3, 10), "0004-23-000001"),
        (1004, "shares_outstanding", 150.0, _D(2022, 12, 31), _D(2023, 3, 10), "0004-23-000001"),
        (1005, "book_value", 3000.0, _D(2023, 12, 31), _D(2024, 2, 28), "0005-24-000001"),
        (1005, "net_income_ttm", 400.0, _D(2023, 12, 31), _D(2024, 2, 28), "0005-24-000001"),
        (1005, "shares_outstanding", 250.0, _D(2023, 12, 31), _D(2024, 2, 28), "0005-24-000001"),
    ],
    columns=["cik", "metric", "value", "period_end", "filed", "accession"],
)


def _synthetic_prices() -> pd.DataFrame:
    """Deterministic daily bars. Open and close differ on every day, so a test can tell
    a next-day-open fill apart from a same-day-close fill (R16)."""
    rows: list[tuple] = []
    dates = pd.bdate_range("2018-01-02", "2025-12-31").date
    starts = {"AAA": 20.0, "BBB": 50.0, "CCC": 8.0, "DDD": 15.0, "EEE": 30.0}
    drifts = {"AAA": 0.00030, "BBB": 0.00010, "CCC": -0.00020, "DDD": 0.00005, "EEE": 0.00020}
    for ticker, start in starts.items():
        last_seen = _SECURITIES.loc[_SECURITIES.ticker == ticker, "last_seen"].iloc[0]
        price = start
        for i, d in enumerate(dates):
            if last_seen is not None and d > last_seen:
                break
            # Deterministic wobble: no RNG, so the fixture is byte-stable across runs.
            wobble = 0.004 * np.sin(i / 7.0)
            close = price * (1.0 + drifts[ticker] + wobble)
            open_ = price * (1.0 + 0.5 * drifts[ticker])
            rows.append((ticker, d, round(open_, 4), round(close, 4), round(close, 4), 500_000))
            price = close
    return pd.DataFrame(
        rows, columns=["ticker", "date", "open", "close", "close_adj", "volume"]
    )


class FixtureSource:
    """In-memory DataSource with known, hand-checkable answers."""

    name = "fixture"

    def __init__(self) -> None:
        self._prices = _synthetic_prices()

    def snapshot(self) -> Snapshot:
        return Snapshot(
            built_at=dt.datetime(2026, 1, 1, 0, 0, 0),
            source=self.name,
            row_counts={
                "securities": len(_SECURITIES),
                "fundamentals": len(_FUNDAMENTALS),
                "prices": len(self._prices),
            },
        )

    def universe(self, asof: dt.date) -> pd.DataFrame:
        df = _SECURITIES.copy()
        alive = (df.first_seen <= asof) & (df.last_seen.isna() | (df.last_seen >= asof))
        return df.loc[alive, ["ticker", "cik", "name", "sector", "exchange"]].reset_index(
            drop=True
        )

    def fundamentals(self, asof: dt.date) -> pd.DataFrame:
        df = _FUNDAMENTALS[_FUNDAMENTALS.filed <= asof].copy()
        # Earliest filed version of each (cik, metric, period_end) wins — never a restatement.
        df = df.sort_values("filed").drop_duplicates(
            subset=["cik", "metric", "period_end"], keep="first"
        )
        return df.reset_index(drop=True)

    def prices(
        self, tickers: list[str], start: dt.date, end: dt.date
    ) -> pd.DataFrame:
        df = self._prices
        m = df.ticker.isin(tickers) & (df.date >= start) & (df.date <= end)
        return df.loc[m].reset_index(drop=True)

    @property
    def survivorship_free(self) -> bool:
        # The fixture deliberately keeps DDD's prices after it delists.
        return True
