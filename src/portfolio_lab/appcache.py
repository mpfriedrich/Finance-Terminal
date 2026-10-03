"""Every network call the app makes, behind a cache that never keeps a failure.

Streamlit re-runs the whole script on every click. Before the audit of 02.10.2026 the
app asked Yahoo for the live quote, the option chain (eleven requests), the news, the
analysts, the holders, the dividends and the earnings dates again on each of those
reruns, and two wrappers did the opposite mistake: `_insider_trades` stored "the SEC
could not be reached" (None) for six hours, `_yahoo_bars` stored an empty frame for an
hour, so one dropped connection hid the data long after the network was back.

The rule here: the wrapped function raises when the source cannot be reached
(`strict=True` on the research/options calls), `st.cache_data` does not store a call
that raised, and the call site catches the error with `attempt()` and says
"unreachable" — which is not the same sentence as "no data". An empty answer that did
arrive is cached like any other answer.

How long each answer is kept follows how fast it changes: a quote a minute, a chain ten
minutes, headlines a quarter of an hour, analysts, holders, dividends and earnings
dates half a day.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from . import aviation, insiders, ui, world
from . import options as op
from . import research as rs

MINUTE = 60
HOUR = 3600


def attempt(fn, *args, **kwargs):
    """(value, None) on success, (None, safe error text) when the call raised.

    The text carries the error type and message without URLs (ui.error_text, R24)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:                                        # noqa: BLE001
        return None, ui.error_text(exc)


# ------------------------------------------------------------------------- quotes
@st.cache_data(ttl=MINUTE, show_spinner=False)
def live_quote(ticker: str) -> dict | None:
    """None = Yahoo answered without a price; raises = Yahoo was not reached."""
    return rs.live_quote(ticker, strict=True)


@st.cache_data(ttl=HOUR, show_spinner=False)
def yahoo_bars(symbol: str) -> pd.DataFrame:
    """Five years of daily bars for any Yahoo ticker, held in memory, never stored: a
    name typed into the command line is not part of the screen's universe. Empty =
    Yahoo has nothing under that symbol; raises = it was not reached."""
    return world.fetch_history(symbol)


# ------------------------------------------------------------------------ options
@st.cache_data(ttl=10 * MINUTE, show_spinner=False)
def expiries(symbol: str) -> list[str]:
    return op.expiries(symbol, strict=True)


@st.cache_data(ttl=10 * MINUTE, show_spinner=False)
def _raw_chain(symbol: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The quotes of one expiry as downloaded. Cached without the spot: the quotes do
    not depend on it, only the cleaning and the volatilities do."""
    raw = op.fetch_chain(symbol, expiry)
    return raw.calls, raw.puts


def _fetch(symbol: str, expiry: str):
    calls, puts = _raw_chain(symbol, expiry)
    return SimpleNamespace(calls=calls.copy(), puts=puts.copy())


def chain(symbol: str, expiry: str, spot: float, risk_free: float,
          dividend_yield: float) -> pd.DataFrame:
    """op.chain over the cached quotes. Every caller passes the dividend yield: without
    it a payer's puts look dearer than its calls for no other reason (options.py)."""
    return op.chain(symbol, expiry, spot, risk_free, dividend_yield,
                    fetch=_fetch, strict=True)


def surface(symbol: str, spot: float, max_expiries: int, risk_free: float,
            dividend_yield: float) -> pd.DataFrame:
    return op.surface(symbol, spot, max_expiries, risk_free, dividend_yield,
                      fetch=_fetch, listed=expiries, strict=True)


# ----------------------------------------------------------- company information
@st.cache_data(ttl=15 * MINUTE, show_spinner=False)
def news(symbol: str) -> pd.DataFrame:
    return rs.news(symbol, strict=True)


@st.cache_data(ttl=12 * HOUR, show_spinner=False)
def analyst_view(symbol: str) -> dict:
    return rs.analyst_view(symbol, strict=True)


@st.cache_data(ttl=12 * HOUR, show_spinner=False)
def holders(symbol: str) -> pd.DataFrame:
    return rs.holders(symbol, strict=True)


@st.cache_data(ttl=12 * HOUR, show_spinner=False)
def dividend_history(symbol: str) -> pd.DataFrame:
    return rs.dividend_history(symbol, strict=True)


@st.cache_data(ttl=12 * HOUR, show_spinner=False)
def earnings_history(symbol: str) -> pd.DataFrame:
    return rs.earnings_history(symbol, strict=True)


@st.cache_data(ttl=6 * HOUR, show_spinner=False)
def insider_trades(cik: int) -> pd.DataFrame:
    """Form 4 trades from EDGAR. Raises when the SEC cannot be reached."""
    return insiders.fetch(int(cik))


@st.cache_data(ttl=6 * HOUR, show_spinner=False)
def earnings_calendar(_con, zip_path: str, tickers: tuple[str, ...], weeks: int,
                      asof: dt.date | None) -> pd.DataFrame:
    """Keyed by the ticker list, the horizon and the data date; the connection is
    not part of the key (leading underscore), the store it reads is (asof)."""
    del asof
    return rs.earnings_calendar(_con, zip_path, list(tickers), weeks=weeks, strict=True)


# ----------------------------------------------------------------------- aircraft
@st.cache_data(ttl=15 * MINUTE, show_spinner=False)
def aircraft_now():
    """Anonymous OpenSky allows ~100 worldwide calls a day; a quarter of an hour per
    answer keeps a long session well inside that. Fetched only when the AIR or
    FREIGHTERS layer is on."""
    return aviation.fetch()
