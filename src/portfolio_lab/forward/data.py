"""Prices, SPY, EUR/USD and macro vintages for the forward books.

Shares come from the market store (read-only), the same table the research read. SPY is
fetched from Yahoo into the forward store because the market store's SPY row stopped on
23.09.2026 (measured 22.10.2026) while the shares kept updating. Macro series: the
research's ALFRED vintages (known up to 14.10.2026) plus our own FRED snapshots, each
stored with the day it was fetched as the day it became known — so a later revision is
never treated as known earlier.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from . import features as fx

UNIVERSE_SQL = ("SELECT DISTINCT ticker, sic FROM securities WHERE exchange IN "
                "('NYSE','NASDAQ','CBOE') AND ticker NOT IN ('SPY','GC=F')")


def coverage(market, since: dt.date) -> pd.Series:
    """Shares of the research universe with a close, per day."""
    df = market.execute(
        "SELECT date, COUNT(*) n FROM prices JOIN (" + UNIVERSE_SQL + ") USING (ticker) "
        "WHERE date >= ? AND close > 0 GROUP BY 1 ORDER BY 1", [since]).fetch_df()
    return pd.Series(df["n"].to_numpy(), index=pd.to_datetime(df["date"]).dt.date)


def complete_sessions(market, since: dt.date, share: float = 0.9) -> list[dt.date]:
    """Days whose coverage is at least `share` of the median of the 20 days before:
    a partly failed price update (20./21.10.2026: 595 of ~5,300) is not a session to
    trade on."""
    cov = coverage(market, since - dt.timedelta(days=60))
    med = cov.rolling(20, min_periods=5).median().shift(1)
    ok = cov >= share * med
    return [d for d, good in ok.items() if good and d >= since]


class Panel:
    """Share prices of the research universe between two dates, on SPY's calendar."""

    def __init__(self, market, start: dt.date, end: dt.date, spy: pd.DataFrame,
                 vintages: pd.DataFrame | None = None, exclude=("SSM",)):
        meta = market.execute(UNIVERSE_SQL).fetch_df().drop_duplicates("ticker")
        self.sic = dict(zip(meta["ticker"], meta["sic"].astype(str), strict=True))
        names = sorted(set(meta["ticker"]))
        data = market.execute(
            "SELECT ticker, date, open, close, close_adj, close_raw FROM prices WHERE "
            "ticker IN (SELECT UNNEST(?)) AND date BETWEEN ? AND ? ORDER BY ticker, date",
            [names, start, end]).fetch_df()
        data["date"] = pd.to_datetime(data["date"])
        s = spy[(spy.index >= pd.Timestamp(start)) & (spy.index <= pd.Timestamp(end))]
        self.calendar = pd.DatetimeIndex(s.index)
        self.spy_close = s["close"]
        self.spy_open_adj = s["open"] * s["close_adj"] / s["close"]
        self.spy_close_adj = s["close_adj"]

        def wide(col):
            return data.pivot(index="date", columns="ticker", values=col).reindex(
                self.calendar)

        self.close = wide("close").where(lambda v: v > 0)
        self.raw = wide("close_raw")
        self.close_adj = wide("close_adj")
        self.open = (wide("open") * self.close_adj / self.close)
        self.tickers = list(self.close.columns)
        self.exclude = set(exclude)
        self._peers = None
        self._macro = None
        self.vintages = vintages
        self._d = None

    # per ticker
    def p(self, t):
        return self.close[t]

    def open_adj(self, t):
        return self.open[t]

    def eligible(self, t):
        p = self.p(t)
        vol60 = np.log(p / p.shift()).rolling(60, min_periods=60).std() * np.sqrt(252)
        return fx.eligible(p, self.raw[t], vol60)

    @property
    def peers(self):
        if self._peers is None:
            self._peers = fx.peer_features(self.close, self.sic)
        return self._peers

    @property
    def macro(self):
        if self._macro is None:
            self._macro = fx.macro_asof(self.calendar, self.vintages)
        return self._macro

    def model_frame(self, t, columns, spy=None):
        p = self.p(t)
        spy = self.spy_close if spy is None else spy
        f = fx.base23(p, spy)
        if any(c in fx.PEER for c in columns):
            f = fx.add_peers(f, self.peers.get(t))
        if any(c in fx.MACRO for c in columns):
            f = fx.add_macro(f, p, spy, self.macro)
        return f[columns]

    def rows_at(self, i: int, columns: list[str]) -> tuple[np.ndarray, np.ndarray]:
        """Features of every share on session i, and whether it is eligible (research
        eligibility, every input finite, SSM excluded)."""
        X = np.full((len(self.tickers), len(columns)), np.nan)
        ok = np.zeros(len(self.tickers), bool)
        for j, t in enumerate(self.tickers):
            if t in self.exclude:
                continue
            f = self.model_frame(t, columns)
            x = f.iloc[i].to_numpy(dtype=float)
            X[j] = x
            ok[j] = bool(self.eligible(t).iloc[i]) and np.isfinite(x).all()
        return X, ok

    def d_inputs(self):
        if self._d is None:
            self._d = fx.d_raw(self.close, self.sic)
        return self._d


# -- outside data -----------------------------------------------------------------------

def yahoo_spy(start: dt.date) -> pd.DataFrame:
    """SPY open, split-adjusted close and dividend-adjusted close from Yahoo."""
    import yfinance as yf

    h = yf.Ticker("SPY").history(start=str(start), auto_adjust=False)
    if h.empty:
        return pd.DataFrame(columns=["open", "close", "close_adj"])
    out = pd.DataFrame({"open": h["Open"].to_numpy(), "close": h["Close"].to_numpy(),
                        "close_adj": h["Adj Close"].to_numpy()},
                       index=pd.to_datetime(h.index).tz_localize(None).normalize())
    return out


def fred_snapshot(series: list[str] = fx.MACRO_SERIES, getter=None) -> pd.DataFrame:
    """Today's FRED values (the vintage known now) via the project's FRED reader."""
    from .. import fred

    frames = []
    for sid in series:
        f = fred.download(sid, getter=getter)
        f = f.rename(columns={"date": "observation_date"})
        f["series_id"] = sid
        frames.append(f[["series_id", "observation_date", "value"]])
    return pd.concat(frames, ignore_index=True)
