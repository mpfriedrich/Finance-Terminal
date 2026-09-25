"""FreeSource: the SEC + yfinance implementation of DataSource.

`survivorship_free` is False and says so loudly. The universe is read from
`ticker_history` (Phase 7), so a name is only screened on dates its price window covers;
but on free data that table holds hardly any vanished companies, so it is still today's
survivors in practice (PLAN.md A21, Phase 7 measured 0/20).
"""

from __future__ import annotations

import datetime as dt
import json

import duckdb
import pandas as pd

from . import history, industry, listing, pit, prices as px
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

    @property
    def universe_basis(self) -> str:
        """Where `universe()` reads listings from, for the run's notes.

        `ticker_history` when its price windows are current; otherwise the securities
        master, which holds today's survivors only — the result has to say so.
        """
        return "ticker_history" if history.windows_current(self.con) else "securities"

    def universe(self, asof: dt.date) -> pd.DataFrame:
        if self.universe_basis == "ticker_history":
            return history.universe_at(self.con, asof)
        return self.con.execute(
            "SELECT ticker, cik, name, sector, sic, exchange FROM securities "
            "WHERE (first_seen IS NULL OR first_seen <= ?) "
            "AND (last_seen IS NULL OR last_seen >= ?)",
            [asof, asof - dt.timedelta(days=10)],
        ).fetch_df()

    def fundamentals(self, asof: dt.date) -> pd.DataFrame:
        return pit.fundamentals_wide(self.con, asof)

    def prices(self, tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
        # `open` has to come back on the SAME basis as the close beside it. The stored
        # open is split-adjusted like the stored close, so handing over the reconstructed
        # close next to the untouched open would put two different bases in one row —
        # and any consumer taking their ratio (a next-day fill against that day's close,
        # for instance) would read a split as a gap of several hundred percent.
        return self.con.execute(
            "SELECT ticker, date, "
            "open * COALESCE(close_raw / NULLIF(close, 0), 1) AS open, "
            "COALESCE(close_raw, close) AS close, "
            "close_adj, volume FROM prices "
            "WHERE ticker IN (SELECT UNNEST(?)) AND date BETWEEN ? AND ?",
            [tickers, start, end],
        ).fetch_df()

    def sessions(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Market sessions in the window: the days on which any share traded.

        Equities only, like `session_coverage`: futures and indices keep their own
        calendar. Used by stage B so its clock does not stop when its holdings do.
        """
        rows = self.con.execute(
            "SELECT DISTINCT p.date FROM prices p WHERE p.date BETWEEN ? AND ? "
            "AND EXISTS (SELECT 1 FROM securities s WHERE s.ticker = p.ticker) "
            "ORDER BY 1",
            [start, end],
        ).fetchall()
        return [pd.Timestamp(r[0]).date() for r in rows]

    @property
    def survivorship_free(self) -> bool:
        return False

    def delisting_returns(self, tickers) -> dict[str, float]:
        """Known returns at the end of a listing (CRSP); empty on the free data."""
        return history.delisting_returns(self.con, tickers)

    # -- convenience for the screener -------------------------------------------------
    def screening_frame(self, asof: dt.date) -> pd.DataFrame:
        """One row per security with everything a screen needs."""
        uni = self.universe(asof)
        fund = self.fundamentals(asof)
        # close_raw, not close: this price gets multiplied by a share count to make a
        # market cap, and `close` arrives split-adjusted from the source. Using it would
        # understate the market cap of every company that has split since by exactly its
        # split factor — invisible on today's date, systematic on any historical one.
        last_px = self.con.execute(
            """
            SELECT ticker, COALESCE(close_raw, close) AS price FROM (
                SELECT ticker, close, close_raw, ROW_NUMBER() OVER (
                    PARTITION BY ticker ORDER BY date DESC) rn
                FROM prices WHERE date <= ?
            ) WHERE rn = 1
            """,
            [asof],
        ).fetch_df()
        liq = px.median_dollar_volume(self.con, asof)
        shvol = px.median_share_volume(self.con, asof)
        sessions = px.session_coverage(self.con, asof)
        # A date where nothing has been filed yet is a real date, not an error. Walking a
        # point-in-time backtest back far enough reaches one, and merging against an
        # empty frame with no columns raises instead of returning an empty screen.
        if fund.empty or "cik" not in fund.columns:
            fund = pd.DataFrame({"cik": pd.Series(dtype="int64")})
        out = (uni.merge(fund, on="cik", how="left")
                  .merge(last_px, on="ticker", how="left")
                  .merge(liq, on="ticker", how="left")
                  .merge(shvol, on="ticker", how="left")
                  .merge(sessions, on="ticker", how="left"))
        # Same guard as build_screening_frame: on a date where every fact is too old to
        # be visible, the column is missing, and the screen must count everyone as stale
        # rather than raise. Found by a stage B run whose last screen date lay more than
        # 400 days after the only filing.
        if "data_age_days" not in out.columns:
            out["data_age_days"] = float("nan")
        out = listing.add_country(listing.apply(out, self.con))
        # The SEC wording stays in `sector`; `industry_group` is the coarse, typeable
        # version of the same fact. Both travel with the row so the screen can filter on
        # one and still show the other.
        return industry.add_industry_group(out)
