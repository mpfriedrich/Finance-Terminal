"""FreeSource: the SEC + yfinance implementation of DataSource.

`survivorship_free` is False and says so loudly. The universe here is today's listed
companies; what happened to everyone else is Phase 7's problem and, on free data, may
stay partly unanswerable (PLAN.md A21).
"""

from __future__ import annotations

import datetime as dt
import json

import duckdb
import pandas as pd

from . import pit, prices as px
from .base import Snapshot

TABLES = ("securities", "fundamentals", "prices", "ticker_history", "shares_outstanding")


class FreeSource:
    name = "free (SEC XBRL + yfinance)"

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con

    def latest_equity_date(self) -> dt.date | None:
        """Newest bar among the SHARES, not among everything in the price table.

        Commodity futures and index series live in the same table and trade on a
        different clock — futures nearly around the clock, equities not. Taking the
        maximum over all of them reports the market as current while the shares are
        still on Friday's close, which would put a false age on every screen.
        """
        row = self.con.execute(
            "SELECT MAX(p.date) FROM prices p "
            "WHERE EXISTS (SELECT 1 FROM securities s WHERE s.ticker = p.ticker)"
        ).fetchone()
        return row[0] if row else None

    def snapshot(self) -> Snapshot:
        counts = {
            t: self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES
        }
        built = self.con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        return Snapshot(
            built_at=dt.datetime.combine(built or dt.date.today(), dt.time()),
            source=self.name,
            row_counts=counts,
        )

    def universe(self, asof: dt.date) -> pd.DataFrame:
        return self.con.execute(
            "SELECT ticker, cik, name, sector, exchange FROM securities "
            "WHERE (first_seen IS NULL OR first_seen <= ?) "
            "AND (last_seen IS NULL OR last_seen >= ?)",
            [asof, asof - dt.timedelta(days=10)],
        ).fetch_df()

    def fundamentals(self, asof: dt.date) -> pd.DataFrame:
        return pit.fundamentals_wide(self.con, asof)

    def prices(self, tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
        return self.con.execute(
            "SELECT ticker, date, open, close, close_adj, volume FROM prices "
            "WHERE ticker IN (SELECT UNNEST(?)) AND date BETWEEN ? AND ?",
            [tickers, start, end],
        ).fetch_df()

    @property
    def survivorship_free(self) -> bool:
        return False

    # -- convenience for the screener -------------------------------------------------
    def screening_frame(self, asof: dt.date) -> pd.DataFrame:
        """One row per security with everything a screen needs."""
        uni = self.universe(asof)
        fund = self.fundamentals(asof)
        last_px = self.con.execute(
            """
            SELECT ticker, close AS price FROM (
                SELECT ticker, close, ROW_NUMBER() OVER (
                    PARTITION BY ticker ORDER BY date DESC) rn
                FROM prices WHERE date <= ?
            ) WHERE rn = 1
            """,
            [asof],
        ).fetch_df()
        liq = px.median_dollar_volume(self.con, asof)
        shvol = px.median_share_volume(self.con, asof)
        out = (uni.merge(fund, on="cik", how="left")
                  .merge(last_px, on="ticker", how="left")
                  .merge(liq, on="ticker", how="left")
                  .merge(shvol, on="ticker", how="left"))
        return out
