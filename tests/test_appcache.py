"""The network cache keeps answers and never keeps failures (audit 02.10.2026).

Before: `_insider_trades` returned None for "the SEC could not be reached" and cached
that None for six hours; `_yahoo_bars` cached an empty frame for an hour. One dropped
connection hid the data long after the network came back. No test here touches the
network: every source is replaced by a counter.
"""
import json
import zipfile

import pandas as pd
import pytest
import streamlit as st

from portfolio_lab import appcache as ac
from portfolio_lab import research as rs
from portfolio_lab import ui


@pytest.fixture(autouse=True)
def _fresh_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


class Flaky:
    """Fails `failures` times, then answers. Counts every call."""

    def __init__(self, answer, failures=1):
        self.answer, self.failures, self.calls = answer, failures, 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("https://example.invalid/q?apikey=SECRET timed out")
        return self.answer


def test_a_failed_insider_download_is_not_kept(monkeypatch):
    frame = pd.DataFrame({"date": [], "what": []})
    fake = Flaky(frame)
    monkeypatch.setattr(ac.insiders, "fetch", fake)
    value, err = ac.attempt(ac.insider_trades, 320193)
    assert value is None and err.startswith("ConnectionError")
    value, err = ac.attempt(ac.insider_trades, 320193)
    assert err is None and value is not None      # the network is back: asked again
    assert fake.calls == 2


def test_an_answer_is_kept_for_the_next_rerun(monkeypatch):
    fake = Flaky(pd.DataFrame({"close": [1.0]}), failures=0)
    monkeypatch.setattr(ac.world, "fetch_history", fake)
    ac.yahoo_bars("SAP.DE")
    ac.yahoo_bars("SAP.DE")
    assert fake.calls == 1


def test_a_failed_quote_raises_rather_than_answering_no_price(monkeypatch):
    """Strict mode is what lets the cache tell the two apart."""
    class Broken:
        @property
        def fast_info(self):
            raise ConnectionError("down")

    monkeypatch.setattr("yfinance.Ticker", lambda symbol: Broken())
    assert rs.live_quote("AAPL") is None                  # the old, forgiving behaviour
    with pytest.raises(ConnectionError):
        rs.live_quote("AAPL", strict=True)
    value, err = ac.attempt(ac.live_quote, "AAPL")
    assert value is None and "ConnectionError" in err


def test_option_quotes_are_fetched_once_for_several_spots(monkeypatch):
    """The raw chain does not depend on the spot; the cleaning does."""
    calls = pd.DataFrame({
        "strike": [95.0, 100.0, 105.0], "bid": [6.0, 3.0, 1.0], "ask": [6.4, 3.3, 1.2],
        "lastPrice": [6.2, 3.1, 1.1], "openInterest": [100, 100, 100],
        "volume": [50, 50, 50], "impliedVolatility": [0.0, 0.0, 0.0],
        "lastTradeDate": [pd.Timestamp.now(tz="UTC")] * 3})
    raw = type("Raw", (), {"calls": calls, "puts": calls.iloc[0:0]})()
    fake = Flaky(raw, failures=0)
    monkeypatch.setattr(ac.op, "fetch_chain", fake)
    expiry = (pd.Timestamp.today() + pd.Timedelta(days=40)).date().isoformat()
    ac.chain("XYZ", expiry, 100.0, 0.04, 0.01)
    ac.chain("XYZ", expiry, 101.0, 0.04, 0.01)
    assert fake.calls == 1


def test_error_text_drops_addresses_and_credentials():
    text = ui.error_text(ConnectionError(
        "HTTPSConnectionPool: https://api.example.com/v1?api_key=abc123 refused; "
        "token=XYZ"))
    assert "example.com" not in text and "abc123" not in text and "XYZ" not in text
    assert text.startswith("ConnectionError")


def test_the_submissions_archive_is_opened_once(tmp_path, monkeypatch):
    """The 1.56 GB archive used to be opened (central directory read) on every lookup:
    three times per company page and once per ticker in the calendar."""
    path = tmp_path / "submissions.zip"
    doc = {"fiscalYearEnd": "0930", "filings": {"recent": {
        "form": ["10-K"], "filingDate": ["2025-11-01"], "reportDate": ["2025-09-30"],
        "accessionNumber": ["0001-25-000001"], "primaryDocument": ["a.htm"]}}}
    with zipfile.ZipFile(path, "w") as zf:
        for cik in (1, 2, 3):
            zf.writestr(f"CIK{cik:010d}.json", json.dumps(doc))
    opened = []
    real = zipfile.ZipFile

    def counting(*args, **kwargs):
        opened.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", counting)
    for cik in (1, 2, 3):
        assert not rs.filings(path, cik).empty
        assert rs.company_details(path, cik)["fiscal_year_end"] == "09-30"
        rs.next_report_estimate(path, cik)
    assert rs.filings(path, 99).empty
    assert len(opened) == 1


# --- Yahoo failures that yfinance does not raise (review F6, 03.10.2026) -----------------
# yfinance 1.7 never raises from yf.download: on a dropped connection it logs "1 Failed
# download" and returns an empty frame, and Ticker.dividends is None. The fakes below
# do exactly that; the old app fixture made them raise, which hid the cached failure.

def _blocked(*args, **kwargs):
    raise ConnectionError("network is blocked in tests")


def _yahoo_down(monkeypatch):
    """Yahoo unreachable the way the real library reports it: no exception."""
    import requests
    import yfinance
    from curl_cffi import requests as curl

    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(requests.Session, "request", _blocked)
    monkeypatch.setattr(curl.Session, "request", _blocked)


def _bars(*args, **kwargs):
    idx = pd.DatetimeIndex(["2026-09-30", "2026-10-01"], name="Date")
    return pd.DataFrame({"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0],
                         "Close": [1.0, 2.0], "Adj Close": [1.0, 2.0],
                         "Volume": [10, 10], "Stock Splits": [0.0, 0.0]}, index=idx)


def test_an_empty_download_from_an_unreachable_yahoo_is_not_kept(monkeypatch):
    _yahoo_down(monkeypatch)
    value, err = ac.attempt(ac.yahoo_bars, "SPY")
    assert value is None and err.startswith("YahooUnreachable")
    assert "http" not in err and "yahoo.com" not in err                 # R24
    monkeypatch.setattr("yfinance.download", _bars)                     # network back
    value, err = ac.attempt(ac.yahoo_bars, "SPY")
    assert err is None and len(value) == 2


def test_dividends_none_from_an_unreachable_yahoo_is_not_kept(monkeypatch):
    _yahoo_down(monkeypatch)

    class Down:
        dividends = None

    class Up:
        dividends = pd.Series([0.5], index=pd.DatetimeIndex(
            [pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)]))

    monkeypatch.setattr("yfinance.Ticker", lambda symbol: Down())
    value, err = ac.attempt(ac.dividend_history, "KO")
    assert value is None and err.startswith("YahooUnreachable")
    assert rs.dividend_history("KO").empty           # the forgiving mode stays quiet
    monkeypatch.setattr("yfinance.Ticker", lambda symbol: Up())
    value, err = ac.attempt(ac.dividend_history, "KO")
    assert err is None and value.dividend.tolist() == [0.5]


def test_yahoo_saying_it_has_nothing_is_an_answer(monkeypatch):
    """404 from the chart endpoint: Yahoo was reached and knows no such symbol, so an
    empty frame is right and may be cached. 429 and 5xx are not answers."""
    from portfolio_lab.data import prices

    status = {"code": 404}
    asked = []

    class Data:
        def get(self, url, params=None, timeout=30):
            asked.append(url)
            return type("R", (), {"status_code": status["code"]})()

    monkeypatch.setattr("yfinance.data.YfData", Data)
    monkeypatch.setattr("yfinance.download", lambda *a, **k: pd.DataFrame())
    assert ac.yahoo_bars("NOPE").empty
    assert ac.yahoo_bars("NOPE").empty and len(asked) == 1               # cached
    for code in (429, 503):
        status["code"] = code
        with pytest.raises(prices.YahooUnreachable, match=f"HTTP {code}"):
            prices.yahoo_answers("NOPE")
    status["code"] = 200
    prices.yahoo_answers("SPY")                       # exists, no bars: no exception
