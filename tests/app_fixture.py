"""The whole app on a store built from fixtures, for streamlit.testing.v1.AppTest.

The market store is the Sharadar fixture (paid_fixture.py) opened as the free source —
both use the same schema — plus a few price-only series and the benchmark, so every
tab has something to draw. No test using this touches the network: `run()` blocks
sockets, and every network call of the app goes through appcache.py, which then raises
and is shown as "unreachable".
"""
from __future__ import annotations

import datetime as dt
import shutil
import socket
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def build_data(directory: Path) -> Path:
    """data/ with market.duckdb (fixture) and an empty user store."""
    import duckdb

    from tests.paid_fixture import build_store

    directory.mkdir(parents=True, exist_ok=True)
    work = directory / "_build"
    source = build_store(work)
    source.con.close()
    shutil.copy(work / "paid.duckdb", directory / "market.duckdb")
    shutil.rmtree(work)
    con = duckdb.connect(str(directory / "market.duckdb"))
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM prices ORDER BY date").fetchall()]
    rows = []
    for i, d in enumerate(days):
        rows += [("^SP500TR", d, 10000 * 1.0003 ** i), ("GC=F", d, 2000 + i),
                 ("^IRX", d, 4.0), ("^TNX", d, 4.2), ("ZW=F", d, 550 + i % 7)]
    frame = pd.DataFrame(rows, columns=["ticker", "date", "close"])
    con.register("extra", frame)
    con.execute("INSERT INTO prices (ticker, date, close, close_raw, close_adj) "
                "SELECT ticker, date, close, close, close FROM extra")
    con.close()
    (directory / "cache").mkdir(exist_ok=True)
    return directory


def _no_network(*args, **kwargs):
    raise ConnectionError("network is blocked in tests")


def run(monkeypatch, data: Path, query: dict | None = None,
        state: dict | None = None, timeout: float = 60) -> AppTest:
    """Run the app once against `data`, with the network blocked."""
    from portfolio_lab import config

    monkeypatch.setenv("PORTFOLIO_LAB_SOURCE", "free")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name, path in (("DATA_DIR", data), ("MARKET_DB", data / "market.duckdb"),
                       ("PAID_DB", data / "paid.duckdb"),
                       ("WORLD_DB", data / "world.duckdb"),
                       ("USER_DB", data / "user.duckdb"), ("CACHE_DIR", data / "cache"),
                       ("ALERTS_STATE", data / "alerts_state.json")):
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    # yfinance talks through curl_cffi, which does not use Python's socket module: a
    # first version of this fixture blocked sockets only, and live quotes still came in.
    import requests
    import yfinance

    monkeypatch.setattr(yfinance, "Ticker", _no_network)
    monkeypatch.setattr(yfinance, "download", _no_network)
    monkeypatch.setattr(requests.Session, "request", _no_network)
    try:
        from curl_cffi import requests as curl

        monkeypatch.setattr(curl.Session, "request", _no_network)
    except ImportError:
        pass
    st.cache_data.clear()
    st.cache_resource.clear()
    at = AppTest.from_file(str(APP), default_timeout=timeout)
    for k, v in (query or {}).items():
        at.query_params[k] = v
    for k, v in (state or {}).items():
        at.session_state[k] = v
    return at.run()


def texts(at: AppTest) -> str:
    """Everything the run wrote as text, in one string, for `in` checks."""
    parts = []
    for kind in ("markdown", "caption", "info", "warning", "error", "success"):
        parts += [str(e.value) for e in getattr(at, kind)]
    return "\n".join(parts)


def today() -> dt.date:
    return dt.date.today()
