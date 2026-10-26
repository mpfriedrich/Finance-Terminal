"""The forward engine on a hand-made market of two shares, every number worked out by
hand (audit 24.10.2026 F21): a dividend while held (the long receives it, the short
pays it), EUR/USD jumping between the open and the close, borrow over a weekend and a
month change, and books D, E and F filled through `process_session`. Plus the training's
look-ahead guard on a real panel, not a mock."""

import datetime as dt
import json

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab.forward import data as dd
from portfolio_lab.forward import engine as en
from portfolio_lab.forward import features as fx
from portfolio_lab.forward import ledger as lg
from portfolio_lab.forward import models as md

OCT28, OCT29, OCT30, NOV2 = (dt.date(2026, 10, 28), dt.date(2026, 10, 29),
                             dt.date(2026, 10, 30), dt.date(2026, 11, 2))
F_L = 108 / 110          # L1 pays 2.00 going ex on 02.11. (close 110 before): adj factor
F_S = 44 / 45            # S1 pays 1.00 going ex on 02.11. (close 45 before)


def _hand_market(path):
    """The store as read on 02.11.: close_adj back-adjusted for both dividends."""
    c = duckdb.connect(str(path))
    c.execute("CREATE TABLE prices (ticker VARCHAR, date DATE, open DOUBLE, close DOUBLE, "
              "close_adj DOUBLE, close_raw DOUBLE, volume BIGINT)")
    rows = [("L1", OCT29, 100, 100, 100 * F_L), ("L1", OCT30, 105, 110, 110 * F_L),
            ("L1", NOV2, 108, 108, 108),
            ("S1", OCT29, 50, 50, 50 * F_S), ("S1", OCT30, 48, 45, 45 * F_S),
            ("S1", NOV2, 44, 44, 44)]
    c.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,0)",
                  [(t, d, o, cl, adj, cl) for t, d, o, cl, adj in rows])
    c.close()
    return duckdb.connect(str(path), read_only=True)


@pytest.fixture
def booked(tmp_path):
    market = _hand_market(tmp_path / "m.duckdb")
    con = lg.connect(tmp_path / "fw.duckdb")
    start = dt.datetime(2026, 10, 29, 12)               # 08:00 New York: first OCT29
    en.ensure_books(con, start)
    for day, rate in ((OCT28, 1.10), (OCT29, 1.20), (OCT30, 1.25), (NOV2, 1.25)):
        lg.store_fx(con, day, rate, "yahoo", dt.datetime(2026, 11, 3))
    made = dt.datetime(2026, 10, 29, 1)                  # 21:00 New York on 28.10.
    ranking = {"ranking": [{"ticker": "L1", "score": 1.0}, {"ticker": "S1", "score": 0.0}]}
    lg.signal(con, "fw-d", made, OCT28, OCT29, "rebalance", "pending", ranking)
    lg.signal(con, "fw-e", made, OCT28, OCT29, "monthly", "pending", ranking)
    lg.signal(con, "fw-f", made, OCT28, OCT29, "rare", "pending", {"candidates": [
        {"ticker": "L1", "rule": "drop_long", "side": "long", "value": -0.5,
         "unresolved": ""}]})
    notes = []
    for day in (OCT29, OCT30, NOV2):
        assert en.process_session(con, market, day, dt.datetime(2026, 11, 3), notes), notes
    yield con
    con.close()
    market.close()


def _mark(con, book, day):
    return con.execute("SELECT * FROM fw_marks WHERE book_id=? AND session=?",
                       [book, day]).fetch_df().iloc[0]


def test_d_fx_jump_dividends_and_weekend_borrow_by_hand(booked):
    con = booked
    e = 0.5 * 1000 / 1.001                     # EUR per side, all cash in at 0.10 %
    usd = e * 1.10                             # bought at the open: the rate of 28.10.
    # 29.10.: unchanged prices, but EUR/USD 1.10 -> 1.20 by the close
    m = _mark(con, "fw-d", OCT29)
    assert m.cash == pytest.approx(0.0, abs=1e-9)
    assert m.equity == pytest.approx(1000 / 1.001 * 1.10 / 1.20)
    # 30.10.: L1 100 -> 110 (+10 %), S1 50 -> 45 (+10 % for the short); one day's borrow
    b1 = 0.03 * e * 1 / 365
    m = _mark(con, "fw-d", OCT30)
    assert m.equity == pytest.approx(2 * usd * 1.10 / 1.25 - b1)
    # 02.11. (Monday, new month): both ex-dividend. L1 closes at 108 + 2 paid = 110 on
    # the entry basis (the long receives it); S1 at 44 + 1 owed = 45 (the short pays
    # it: on the traded 44 it would have shown 1.12, not 1.10). Borrow for three days.
    b3 = 0.03 * e * 3 / 365
    m = _mark(con, "fw-d", NOV2)
    assert m.equity == pytest.approx(2 * usd * 1.10 / 1.25 - b1 - b3)
    pos = con.execute("SELECT side, mark_usd, borrow_eur FROM fw_positions WHERE "
                      "book_id='fw-d'").fetch_df().set_index("side")
    assert pos.loc["long", "mark_usd"] == pytest.approx(110.0)
    assert pos.loc["short", "mark_usd"] == pytest.approx(45.0)
    assert pos.loc["short", "borrow_eur"] == pytest.approx(b1 + b3)
    assert pos.loc["long", "borrow_eur"] == 0
    days = con.execute("SELECT note FROM fw_ledger WHERE book_id='fw-d' AND kind='borrow' "
                       "ORDER BY session").fetch_df().note.tolist()
    assert days == ["1 Tag(e) Leihe", "3 Tag(e) Leihe"]


def test_e_fills_one_name_a_side_at_its_share_of_the_value(booked):
    con = booked
    pos = con.execute("SELECT ticker, side, stake_eur, entry_usd, note FROM fw_positions "
                      "WHERE book_id='fw-e' ORDER BY side").fetch_df()
    # 2 ranked names: one a side (never one name twice); 2.5 % of 1,000 EUR each
    assert list(zip(pos.ticker, pos.side)) == [("L1", "long"), ("S1", "short")]
    assert pos.stake_eur.tolist() == pytest.approx([25.0, 25.0])
    assert pos.entry_usd.tolist() == [100.0, 50.0]                 # the open, unadjusted
    assert set(pos.note) == {"E3 long", "E3 short"}
    assert _mark(con, "fw-e", OCT29).cash == pytest.approx(1000 - 2 * 25 * 1.001)


def test_f_takes_75_percent_and_plans_the_exit_five_sessions_on(booked):
    con = booked
    p = con.execute("SELECT * FROM fw_positions WHERE book_id='fw-f'").fetch_df().iloc[0]
    assert p.stake_eur == pytest.approx(750.0) and p.entry_fee_eur == pytest.approx(0.75)
    assert p.planned_exit == pd.Timestamp("2026-11-05")      # 29.10. + five sessions
    # 02.11.: 825 USD at 110 / 100 = 907.50 USD at 1.25 = 726 EUR, cash 249.25
    m = _mark(con, "fw-f", NOV2)
    assert m.cash == pytest.approx(249.25) and m.equity == pytest.approx(249.25 + 726.0)


def test_signals_are_executed_once_and_the_ledger_balances(booked):
    con = booked
    st = con.execute("SELECT book_id, status FROM fw_signals").fetch_df()
    assert set(st.status) == {"executed"}
    for r in con.execute("SELECT * FROM fw_marks").fetch_df().itertuples():
        assert r.equity == pytest.approx(r.cash + r.positions)
        assert np.isfinite(r.equity)
    assert json.loads(con.execute("SELECT payload_json FROM fw_signals WHERE "
                                  "book_id='fw-d'").fetchone()[0])["ranking"]


# -- the training's look-ahead guard, on a real panel ----------------------------------

def _panel_market(path, days):
    rng = np.random.default_rng(3)
    c = duckdb.connect(str(path))
    c.execute("CREATE TABLE securities (cik BIGINT, ticker VARCHAR, sic VARCHAR, "
              "exchange VARCHAR)")
    c.execute("CREATE TABLE prices (ticker VARCHAR, date DATE, open DOUBLE, close DOUBLE, "
              "close_adj DOUBLE, close_raw DOUBLE, volume BIGINT)")
    names = [f"P{j}" for j in range(12)]
    c.executemany("INSERT INTO securities VALUES (?,?,?,?)",
                  [(j, t, "2834", "NYSE") for j, t in enumerate(names)])
    close = 40 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, (len(days), len(names))),
                                  axis=0))
    c.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,1000000)", [
        (t, d.date(), close[i, j] * 1.001, close[i, j], close[i, j], close[i, j])
        for i, d in enumerate(days) for j, t in enumerate(names)])
    c.close()
    return duckdb.connect(str(path), read_only=True)


def test_training_targets_end_before_the_cutoff(tmp_path):
    # Audit 24.10.2026 F21: the guard `cal[sig + 6] < cutoff` ran in no test (training
    # was mocked) and hung on an assert. Here the panel runs a year past the cutoff, so
    # a later outcome would be there to leak.
    days = pd.bdate_range("2021-01-04", "2024-06-28")
    market = _panel_market(tmp_path / "p.duckdb", days)
    rng = np.random.default_rng(5)
    px = 300 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(days))))
    spy = pd.DataFrame({"open": px * 0.999, "close": px, "close_adj": px}, index=days)
    panel = dd.Panel(market, days[0].date(), days[-1].date(), spy)
    cutoff = dt.date(2023, 7, 3)
    X, y, dates, ends = md.training_rows(panel, fx.BASE23, dt.date(2022, 1, 3), cutoff,
                                         log=lambda *_: None)
    assert len(y) > 0
    assert ends.max() < np.datetime64(cutoff)
    # each outcome ends six sessions after its signal, on the panel's calendar
    pos = panel.calendar.get_indexer(pd.DatetimeIndex(dates))
    assert (panel.calendar[pos + 6].to_numpy() == ends).all()
    # the last usable signal is the last one whose +6 session is before the cutoff
    last = panel.calendar.get_loc(pd.Timestamp(dates.max()))
    assert panel.calendar[last + 6 + 5] >= pd.Timestamp(cutoff)
    art = md.train_window(panel, fx.BASE23, dt.date(2022, 1, 3), cutoff, "t",
                          log=lambda *_: None)
    assert art["latest_outcome"] < str(cutoff) and art["outcomes_before"] == str(cutoff)
    market.close()


def test_the_training_guard_raises_instead_of_asserting(monkeypatch):
    late = np.array([np.datetime64("2026-01-05")])
    monkeypatch.setattr(md, "training_rows", lambda *a, **k: (
        np.zeros((1, 1)), np.zeros(1), np.array([np.datetime64("2025-12-24")]), late))
    with pytest.raises(ValueError, match="not yet known"):
        md.train_window(None, ["x"], dt.date(2016, 1, 1), dt.date(2026, 1, 1), "t")
