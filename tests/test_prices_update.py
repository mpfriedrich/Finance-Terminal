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


def test_stored_bars_newer_than_the_answer_are_kept(con):
    # 22.10.2026: Yahoo's batch answer ended a session early for a while.
    con.executemany("INSERT INTO prices (ticker, date, close) VALUES ('SPY', ?, ?)",
                    [(D(2026, 10, 20), 1.0), (D(2026, 10, 21), 2.0)])
    download = fake({"SPY": bars("SPY", [D(2026, 10, 20)], close=1.5)})
    px.fetch_into_store(con, ["SPY"], START, END, download=download)
    assert stored(con, "SPY") == [(D(2026, 10, 20), 1.5), (D(2026, 10, 21), 2.0)]


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


# -- a split or a dividend changes the whole history (audit 24.10.2026 F3) --------------

HIST = [D(2026, 10, d) for d in (1, 2, 5, 6, 7, 8, 9, 12, 13, 14)]
WINDOW = [D(2026, 10, d) for d in (15, 16, 19, 20, 21)]


def adjusted_yahoo(ticker, split_day=None, ratio=1.0, dividend_day=None, factor=1.0):
    """Yahoo's answer for every day, on today's basis: closes before a split divided by
    it, adjusted closes before an ex-dividend day times its factor. Traded price 100."""
    rows = []
    for d in HIST + WINDOW:
        close = 100 / ratio if split_day else 100.0
        adj = close * (factor if dividend_day and d < dividend_day else 1.0)
        rows.append({"ticker": ticker, "date": d, "open": close, "high": close,
                     "low": close, "close": close, "close_adj": adj, "volume": 1000,
                     "split": ratio if d == split_day else float("nan")})
    return pd.DataFrame(rows)


def yahoo_now(answer):
    """The same answer, cut to whatever window is asked for, the way Yahoo serves it."""
    calls = []

    def download(tickers, start, end):
        calls.append((list(tickers), start))
        got = answer[answer.ticker.isin(tickers) & (answer.date >= start)
                     & (answer.date <= end)]
        return got.reset_index(drop=True)
    download.calls = calls
    return download


def store_history(con, ticker, days=tuple(HIST + WINDOW[:2])):
    """The bars as fetched day by day before anything happened: traded price 100, up to
    the first two days of the window (the overlap the increment fetches again)."""
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES (?, ?, 100, 100, 100, 1000)", [(ticker, d) for d in days])


def adj(con, ticker):
    return [r[1] for r in con.execute(
        "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date",
        [ticker]).fetchall()]


def test_a_split_in_the_window_refetches_the_whole_history(con):
    # VTAK 05.10.2026, 1-for-10: the increment replaced only its five days of overlap,
    # the older bars stayed at the old basis, and the series fell to a tenth overnight
    # (seven such breaks measured in market.duckdb on 24.10.2026).
    store_history(con, "VTAK")
    answer = adjusted_yahoo("VTAK", split_day=D(2026, 10, 19), ratio=0.1)
    download = yahoo_now(answer)
    stats = px.fetch_into_store(con, ["VTAK"], START, END, download=download)
    assert stats["refetched"] == ["VTAK"]
    assert set(adj(con, "VTAK")) == {1000.0}          # one basis, no jump anywhere
    assert download.calls[-1] == (["VTAK"], HIST[0])  # from its first stored bar
    px.rebuild_raw_closes(con)
    raw = dict(con.execute("SELECT date, close_raw FROM prices WHERE ticker = 'VTAK'"
                           ).fetchall())
    assert [raw[d] for d in HIST] == [pytest.approx(100.0)] * len(HIST)  # as traded
    assert raw[D(2026, 10, 19)] == pytest.approx(1000.0)   # one new share for ten old


def test_a_dividend_in_the_window_refetches_the_whole_history(con):
    # An ex-dividend day rescales every adjusted close before it; without the refetch
    # the stored ones stayed unscaled and the dividend vanished from total return.
    store_history(con, "DIV")
    answer = adjusted_yahoo("DIV", dividend_day=D(2026, 10, 19), factor=0.99)
    stats = px.fetch_into_store(con, ["DIV"], START, END, download=yahoo_now(answer))
    assert stats["refetched"] == ["DIV"]
    a = adj(con, "DIV")
    assert a[:len(HIST) + 2] == [pytest.approx(99.0)] * (len(HIST) + 2)
    assert a[len(HIST) + 2:] == [100.0] * 3


def test_an_unchanged_history_is_not_refetched(con):
    store_history(con, "AAA")
    download = yahoo_now(adjusted_yahoo("AAA"))
    stats = px.fetch_into_store(con, ["AAA"], START, END, download=download)
    assert stats["refetched"] == []
    assert len(download.calls) == 1


def test_a_split_broken_earlier_is_found_and_repaired(con):
    # The seven already in the store: their split lies before today's window, so only
    # the stored jump at the split shows them.
    store_history(con, "KNDI", HIST)
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume, split) "
        "VALUES ('KNDI', ?, 1000, 1000, 1000, 1000, ?)",
        [(d, 0.1 if d == WINDOW[0] else None) for d in WINDOW])
    store_history(con, "OK", HIST)                     # no split, no jump
    assert px.split_breaks(con) == ["KNDI"]
    answer = adjusted_yahoo("KNDI", split_day=WINDOW[0], ratio=0.1)
    stats = px.refetch_history(con, px.split_breaks(con), END, download=yahoo_now(answer))
    assert stats["refetched"] == ["KNDI"]
    assert set(adj(con, "KNDI")) == {1000.0}
    assert px.split_breaks(con) == []


def test_a_split_yahoo_never_restated_is_adjusted_here(con):
    # AIXI, DLXY, FRGT, GMEX, JL on 24.10.2026: Yahoo's own answer still had the closes
    # before the reverse split unadjusted (AIXI 0.47 -> 3.26 on 08.09.2026), so fetching
    # the history again changed nothing. The bars before the split are put on today's
    # basis here, and close_raw comes out as traded.
    store_history(con, "AIXI", HIST)
    con.executemany(
        "INSERT INTO prices (ticker, date, open, high, low, close, close_adj, volume, "
        "split) VALUES ('AIXI', ?, 700, 700, 700, 700, 700, 100, ?)",
        [(d, 1 / 7 if d == WINDOW[0] else None) for d in WINDOW])
    unadjusted = pd.DataFrame([{"ticker": "AIXI", "date": d, "open": p, "high": p,
                                "low": p, "close": p, "close_adj": p,
                                "volume": 1000 if d in HIST else 100,
                                "split": 1 / 7 if d == WINDOW[0] else float("nan")}
                               for d in HIST + WINDOW
                               for p in [100.0 if d in HIST else 700.0]])
    stats = px.repair_split_breaks(con, END, download=yahoo_now(unadjusted))
    assert stats["refetched"] == ["AIXI"] and stats["adjusted"] == ["AIXI"]
    assert px.split_breaks(con) == []
    assert adj(con, "AIXI") == [pytest.approx(700.0)] * len(HIST + WINDOW)
    vol = dict(con.execute("SELECT date, volume FROM prices WHERE ticker='AIXI'").fetchall())
    assert vol[HIST[0]] == 143                         # 1,000 old shares = 143 new
    px.rebuild_raw_closes(con)
    raw = dict(con.execute("SELECT date, close_raw FROM prices WHERE ticker='AIXI'"
                           ).fetchall())
    assert raw[HIST[0]] == pytest.approx(100.0) and raw[WINDOW[0]] == pytest.approx(700.0)
