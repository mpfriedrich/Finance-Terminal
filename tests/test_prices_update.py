"""The daily price update survives tickers without data (incident 21.10.2026, PLAN.md).

A fake downloader stands in for Yahoo; nothing here touches the network.
"""
import datetime as dt
import importlib.util
import subprocess
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from portfolio_lab.data import prices as px
from portfolio_lab.data.schema import open_market

D = dt.date
START, END = D(2026, 10, 15), D(2026, 10, 21)


def bars(ticker: str, days: list[dt.date], close: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": ticker, "date": days, "open": close, "high": close, "low": close,
        "close": close, "close_adj": close, "volume": 1000, "split": float("nan")})


def fake(answers: dict[str, pd.DataFrame], raise_for: str | None = None):
    """Yahoo in a box: the stored answer per ticker, nothing for the others (what a
    delisted symbol gets), an exception for any chunk holding `raise_for`."""
    calls = []

    def download(tickers, start, end):
        calls.append(list(tickers))
        if raise_for in tickers:
            raise ConnectionError("dropped")
        got = [answers[t] for t in tickers if t in answers]
        return pd.concat(got, ignore_index=True) if got else bars("X", []).iloc[0:0]
    download.calls = calls
    return download


@pytest.fixture()
def con(tmp_path):
    return open_market(tmp_path / "market.duckdb")


def stored(con, ticker):
    return con.execute("SELECT date, close FROM prices WHERE ticker = ? ORDER BY date",
                       [ticker]).fetchall()


def test_a_ticker_without_data_is_a_note_and_the_rest_is_stored(con):
    days = [D(2026, 10, 20), D(2026, 10, 21)]
    download = fake({"AAA": bars("AAA", days), "CCC": bars("CCC", days)})
    stats = px.fetch_into_store(con, ["AAA", "BLFS", "CCC"], START, END,
                                chunk=2, download=download)
    assert stats["no_data"] == ["BLFS"]
    assert stats["failed"] == []
    assert stats["tickers_with_data"] == 2
    assert len(stored(con, "AAA")) == 2 and len(stored(con, "CCC")) == 2


def test_a_stale_last_bar_before_the_window_does_not_stop_the_update(con):
    # BRR on 21.10.2026: halted since 14.10., Yahoo still answers with that bar for a
    # window from 15.10. The old code hit the primary key here and died.
    con.execute("INSERT INTO prices (ticker, date, close) VALUES ('BRR', ?, 4.07)",
                [D(2026, 10, 14)])
    download = fake({"BRR": bars("BRR", [D(2026, 10, 14)], close=4.07),
                     "ZZZ": bars("ZZZ", [D(2026, 10, 21)])})
    stats = px.fetch_into_store(con, ["BRR", "ZZZ"], START, END, download=download)
    assert stats["failed"] == []
    assert stored(con, "BRR") == [(D(2026, 10, 14), 4.07)]
    assert stored(con, "ZZZ") == [(D(2026, 10, 21), 10.0)]


def test_a_failing_chunk_is_rolled_back_alone_and_the_next_one_runs(con):
    con.execute("INSERT INTO prices (ticker, date, close) VALUES ('BAD', ?, 1.0)",
                [D(2026, 10, 16)])
    download = fake({"OK1": bars("OK1", [D(2026, 10, 21)]),
                     "OK2": bars("OK2", [D(2026, 10, 21)])}, raise_for="BAD")
    stats = px.fetch_into_store(con, ["OK1", "BAD", "OK2"], START, END,
                                chunk=1, download=download)
    assert len(stats["failed"]) == 1 and stats["failed"][0].startswith("BAD..BAD")
    assert stored(con, "OK1") and stored(con, "OK2")
    assert stored(con, "BAD") == [(D(2026, 10, 16), 1.0)]   # nothing lost


def test_the_insert_is_undone_with_the_delete_when_it_fails(con):
    # Duplicate keys inside one answer are dropped, so force the failure further down:
    # a frame without the columns the insert needs. The window's delete must come back.
    con.execute("INSERT INTO prices (ticker, date, close) VALUES ('AAA', ?, 1.0)",
                [D(2026, 10, 16)])
    with pytest.raises(duckdb.Error):
        px._upsert(con, pd.DataFrame({"ticker": ["AAA"], "date": [D(2026, 10, 16)]}),
                   START, END)
    assert stored(con, "AAA") == [(D(2026, 10, 16), 1.0)]


def test_the_increment_starts_after_the_last_complete_session(con):
    # 21.10.2026 in miniature: ten equities, all have the 19th, only two the 20th.
    tickers = [f"T{i}" for i in range(10)]
    con.executemany("INSERT INTO securities (cik, ticker) VALUES (?, ?)",
                    [(i, t) for i, t in enumerate(tickers)])
    rows = [(t, D(2026, 10, 19), 1.0) for t in tickers] + \
           [(t, D(2026, 10, 20), 1.0) for t in tickers[:2]] + \
           [("EURUSD=X", D(2026, 10, 21), 1.0)]           # not an equity: not counted
    con.executemany("INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)", rows)
    assert px.last_complete_session(con) == D(2026, 10, 19)
    con.executemany("INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)",
                    [(t, D(2026, 10, 20), 1.0) for t in tickers[2:]])
    assert px.last_complete_session(con) == D(2026, 10, 20)


def test_an_empty_store_has_no_complete_session(con):
    assert px.last_complete_session(con) is None


def test_the_morning_job_reports_the_end_of_stderr(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "morning_briefing",
        Path(__file__).resolve().parents[1] / "scripts" / "morning_briefing.py")
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    noise = "$BLFS: possibly delisted; no price data found\n" * 20
    monkeypatch.setattr(job.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", noise + "prices: 1 chunk(s) failed, all others stored: BAD..BAD"))
    note = job.refresh_prices()
    assert note.startswith("failed:") and note.endswith("BAD..BAD")
