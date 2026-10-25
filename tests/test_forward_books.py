"""Forward books A-F: bookkeeping by hand, timing, sizing, idempotence — no network and
no research files (artifacts and vintages are fixtures)."""

import datetime as dt
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import filing_butterfly as cal
from portfolio_lab.forward import engine as en
from portfolio_lab.forward import features as fx
from portfolio_lab.forward import ledger as lg
from portfolio_lab.forward import models as md
from portfolio_lab.forward import report as rp
from portfolio_lab.forward import strategies as stg

UTC = dt.UTC


def at(day, hour=8, minute=0):
    """`hour`:`minute` New York time on `day`, naive UTC."""
    t = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=cal.ET)
    return t.astimezone(UTC)


# -- the ledger by hand -------------------------------------------------------------------

@pytest.fixture
def con(tmp_path):
    c = lg.connect(tmp_path / "fw.duckdb")
    yield c
    c.close()


def test_a_long_is_booked_converted_and_closed_by_hand(con):
    now = dt.datetime(2026, 10, 22)
    lg.book(con, "x", now, None, "deposit", 1000.0)
    pid = lg.open_position(con, "x", now, dt.date(2026, 10, 22), "AAA", "long", "r",
                           price_usd=10.0, fx=1.10, stake_usd=110.0)
    # 110 USD = 100 EUR, fee 0.10 % = 0.10 EUR
    assert lg.cash(con, "x") == pytest.approx(1000 - 100 - 0.1)
    row = lg.mark(con, "x", now, dt.date(2026, 10, 22), 1.0, {pid: 11.0})
    # 11 shares x 11 USD = 121 USD at 1.00 USD/EUR = 121 EUR
    assert row["positions"] == pytest.approx(121.0)
    assert row["equity"] == pytest.approx(899.9 + 121.0)
    assert row["unrealized"] == pytest.approx(121.0 - 100.0 - 0.1)
    pos = lg.open_positions(con, "x").iloc[0].to_dict()
    pnl = lg.close_position(con, pos, now, dt.date(2026, 10, 23), 12.0, 1.2, "test")
    # proceeds 132 USD / 1.2 = 110 EUR; exit fee 0.10 % of 110 EUR = 0.11
    assert pnl == pytest.approx(110 - 0.11 - 100 - 0.1)
    assert lg.cash(con, "x") == pytest.approx(1000 - 100.1 + 110 - 0.11)


def test_a_short_gains_when_the_price_falls_and_pays_borrow_per_day(con):
    now = dt.datetime(2026, 10, 22)
    lg.book(con, "x", now, None, "deposit", 1000.0)
    pid = lg.open_position(con, "x", now, dt.date(2026, 10, 22), "BBB", "short", "r",
                           price_usd=20.0, fx=1.0, stake_usd=100.0)
    paid = lg.accrue_borrow(con, "x", now, dt.date(2026, 10, 22), dt.date(2026, 10, 26),
                            dt.date(2026, 10, 26))
    assert paid == pytest.approx(0.03 * 100 * 4 / 365)          # four calendar days
    row = lg.mark(con, "x", now, dt.date(2026, 10, 26), 1.0, {pid: 18.0})
    # collateral 100 + gain 10 % = 110
    assert row["positions"] == pytest.approx(110.0)
    assert row["equity"] == pytest.approx(1000 - 100 - 0.1 - paid + 110)


def test_a_missing_close_keeps_the_last_mark_and_counts_stale(con):
    now = dt.datetime(2026, 10, 22)
    lg.book(con, "x", now, None, "deposit", 1000.0)
    lg.open_position(con, "x", now, dt.date(2026, 10, 22), "CCC", "long", "r", 10.0, 1.0,
                     50.0)
    row = lg.mark(con, "x", now, dt.date(2026, 10, 23), 1.0, {})
    assert row["stale"] == 1 and row["positions"] == pytest.approx(50.0)


# -- rules by hand ------------------------------------------------------------------------

def test_d_scores_standardise_clip_and_weight_by_hand():
    raw = np.array([[1.0, 0.0, 0.0], [3.0, 0.0, 0.0], [np.nan, 1.0, 1.0]])
    ok = np.array([True, True, False])
    s = fx.d_scores(raw, ok)
    # column a over the two eligible: mean 2, sd 1 -> z -1, +1; b and c are 0 -> z 0
    assert s[0] == pytest.approx(-0.5) and s[1] == pytest.approx(0.5)
    assert np.isnan(s[2])


def test_rare_rule_fires_only_on_the_switch():
    days = pd.bdate_range("2024-01-01", periods=320)
    p = pd.Series(100 * np.exp(np.cumsum(np.full(320, 0.002)
                                         + 0.02 * np.sin(np.arange(320)))), index=days)
    p.iloc[-3:] = p.iloc[-4] * np.array([0.5, 0.5 * 1.001, 0.5 * 1.002])   # -50 %, then flat
    raw = p.copy()

    P = SimpleNamespace(tickers=["AAA"], exclude=set(), close=pd.DataFrame({"AAA": p}),
                        raw=pd.DataFrame({"AAA": raw}))
    on_day = stg.rare_candidates(P, len(p) - 3)
    after = stg.rare_candidates(P, len(p) - 2)
    assert [c["rule"] for c in on_day] == ["drop_long"]
    assert after == []


def test_e_counts_shares_on_todays_split_basis():
    # 2:1 split between the two frames: 1,000 old shares are 2,000 today; now 2,100
    days = pd.bdate_range("2024-01-01", periods=300)
    close = pd.Series(np.linspace(50, 60, 300), index=days)
    raw = close.copy()
    raw.iloc[:-200] = close.iloc[:-200] * 2            # before the split: raw = 2 x adjusted

    P = SimpleNamespace(tickers=["AAA"], exclude=set(), calendar=days,
                        close=pd.DataFrame({"AAA": close}), raw=pd.DataFrame({"AAA": raw}))
    frame = pd.DataFrame({"ticker": ["AAA"], "cik": [1], "shares_outstanding": [2100e6],
                          "gross_profit_ttm": [1e9], "total_assets": [5e9],
                          "median_dollar_volume_60d": [50e6]})
    year_ago = frame.assign(shares_outstanding=1000e6)
    s, ok = stg.e_scores(P, len(days) - 1, frame, year_ago, np.array([True]))
    # growth = 2100 / (1000 x 2) - 1 = 5 %; one share: percentile ranks are 1.0
    assert ok[0] and s[0] == pytest.approx(1.0)


def test_vintages_are_cut_where_our_own_snapshot_begins(con, monkeypatch):
    alfred = pd.DataFrame({"available_from": ["2026-09-10"], "available_through": [en.FAR],
                           "observation_date": ["2026-08-01"], "value": [100.0],
                           "series_id": ["CPIAUCSL"]})
    monkeypatch.setattr(en, "_ALFRED", alfred)
    con.execute("INSERT INTO fw_macro VALUES ('CPIAUCSL','2026-08-01',101.0,"
                "'2026-10-22','9999-12-31')")
    v = en.vintages(con).sort_values("available_from")
    assert list(v.available_through) == ["2026-10-21", "9999-12-31"]
    assert list(v.value) == [100.0, 101.0]


def test_the_first_session_is_the_first_open_after_the_start():
    # Thursday 22.10.2026 08:00 New York: that day's open is still ahead
    assert en.first_session_after(at(dt.date(2026, 10, 22), 8).replace(tzinfo=None)) == \
        dt.date(2026, 10, 22)
    # after the open: the next session
    assert en.first_session_after(at(dt.date(2026, 10, 22), 10).replace(tzinfo=None)) == \
        dt.date(2026, 10, 23)
    # Thanksgiving is skipped
    assert en.first_session_after(at(dt.date(2026, 11, 26), 8).replace(tzinfo=None)) == \
        dt.date(2026, 11, 27)


def test_years_mark_the_running_year_as_partial():
    m = pd.DataFrame({"session": [dt.date(2026, 10, 22), dt.date(2026, 12, 31),
                                  dt.date(2027, 3, 1)],
                      "equity": [1000.0, 1100.0, 1210.0], "spy": [1000.0, 1050.0, 1050.0]})
    y = rp.years(m, 1000.0).set_index("year")
    assert y.loc[2026, "book"] == pytest.approx(0.10) and y.loc[2026, "partial"]
    assert y.loc[2027, "gap_pp"] == pytest.approx(10.0) and y.loc[2027, "partial"]


# -- the whole engine on a synthetic market ----------------------------------------------

N = 60
SESSIONS = [d for d in pd.date_range("2024-01-02", "2026-11-06").date if cal.is_session(d)]


def _market(path, last: dt.date, partial: dt.date | None = None,
            gone: dict | None = None, null_open: dict | None = None,
            holes: tuple = ()):
    """`gone` maps a ticker to its last day with a bar (a delisting); `null_open` maps a
    ticker to a day whose bar has open = NULL; `holes` are days without any bar."""
    gone, null_open = gone or {}, null_open or {}
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0004, 0.02, (len(SESSIONS), N))
    close = 50 * np.exp(np.cumsum(rets, axis=0))
    opens = close * (1 + rng.normal(0, 0.003, close.shape))
    c = duckdb.connect(str(path))
    c.execute("CREATE TABLE securities (cik BIGINT, ticker VARCHAR, sic VARCHAR, "
              "exchange VARCHAR)")
    c.execute("CREATE TABLE prices (ticker VARCHAR, date DATE, open DOUBLE, close DOUBLE, "
              "close_adj DOUBLE, close_raw DOUBLE, volume BIGINT)")
    c.executemany("INSERT INTO securities VALUES (?,?,?,?)",
                  [(j + 1, f"T{j:02d}", "2834" if j < 30 else "7372", "NYSE")
                   for j in range(N)])
    rows = []
    for i, d in enumerate(SESSIONS):
        if d > last:
            break
        if d in holes:
            continue
        names = range(3) if d == partial else range(N)
        rows += [(f"T{j:02d}", d, None if null_open.get(f"T{j:02d}") == d else opens[i, j],
                  close[i, j], close[i, j], close[i, j], 1e6)
                 for j in names if d <= gone.get(f"T{j:02d}", d)]
        rows.append(("EURUSD=X", d, 1.1, 1.1, 1.1, 1.1, 0))
    c.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)", rows)
    c.close()
    return duckdb.connect(str(path), read_only=True)


class FakeLive:
    def spy(self, start):
        rng = np.random.default_rng(1)
        px = 500 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(SESSIONS))))
        return pd.DataFrame({"open": px * 0.999, "close": px, "close_adj": px},
                            index=pd.to_datetime(SESSIONS))

    def fx(self, start):
        return pd.Series(1.1, index=SESSIONS)

    def fred(self):
        return pd.DataFrame(columns=["series_id", "observation_date", "value"])

    def pit_frame(self, day):
        return pd.DataFrame({"ticker": [f"T{j:02d}" for j in range(N)],
                             "cik": range(1, N + 1), "shares_outstanding": 1e9,
                             "gross_profit_ttm": np.linspace(1e8, 6e9, N),
                             "total_assets": 1e10, "median_dollar_volume_60d": 5e7})


def _artifact(cols, impute=False):
    rng = np.random.default_rng(len(cols))
    a = {"features": cols, "scaler": {"lo": [-1e9] * len(cols), "hi": [1e9] * len(cols),
                                       "mean": [0.0] * len(cols), "std": [1.0] * len(cols)},
         "model": {"kind": "ridge", "alpha": 1.0, "intercept": 0.0,
                   "coef": rng.normal(0, 1, len(cols)).tolist()},
         "latest_outcome": "2025-12-30", "window_start": "2016-01-01", "samples": 1}
    if impute:
        a["impute"] = [0.0] * len(cols)
        a["latest_training_label"] = "2024-12-31"
    return a


@pytest.fixture
def world(tmp_path, monkeypatch):
    months = pd.date_range("2021-01-01", "2026-09-01", freq="MS")
    vint = pd.concat([pd.DataFrame({
        "available_from": (months + pd.Timedelta(days=35)).strftime("%Y-%m-%d"),
        "available_through": en.FAR, "observation_date": months.strftime("%Y-%m-%d"),
        "value": np.linspace(100, 110, len(months)) + k, "series_id": sid})
        for k, sid in enumerate(fx.MACRO_SERIES)], ignore_index=True)
    monkeypatch.setattr(en, "_ALFRED", vint)
    monkeypatch.setattr(md, "load_b", lambda *a: _artifact(fx.BASE23, impute=True))
    monkeypatch.setattr(md, "load_c_2026", lambda *a: _artifact(fx.V13))
    monkeypatch.setattr(md, "sha256", lambda p: "fixture")
    trained = []

    def train(panel, cols, start, cutoff, variant, log=print):
        trained.append((variant, start, cutoff))
        return {**_artifact(cols), "outcomes_before": str(cutoff)}

    monkeypatch.setattr(md, "train_window", train)
    return {"path": tmp_path / "fw.duckdb", "dir": tmp_path, "trained": trained}


def _read(path, sql):
    c = duckdb.connect(str(path), read_only=True)
    try:
        return c.execute(sql).fetch_df()
    finally:
        c.close()


WED, THU, FRI = dt.date(2026, 10, 21), dt.date(2026, 10, 22), dt.date(2026, 10, 23)


def test_start_signals_then_fills_at_the_next_open(world):
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    sig = _read(world["path"], "SELECT book_id, kind, status, exec_session FROM fw_signals")
    by = sig.set_index("book_id")
    for b in ("fw-a", "fw-b", "fw-c", "fw-d", "fw-e"):
        assert by.loc[b, "status"] == "pending" and by.loc[b, "exec_session"].date() == THU
    # F: the threshold episodes at Wednesday's close began before the start
    assert by.loc["fw-f", "status"] == "observed"
    assert _read(world["path"], "SELECT COUNT(*) n FROM fw_orders")["n"][0] == 0
    assert world["trained"][0][0] == "v29"                # A trained at the start
    m1.close()

    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    pos = _read(world["path"], "SELECT book_id, side, COUNT(*) n FROM fw_positions WHERE "
                               "status='open' GROUP BY 1, 2").set_index(["book_id", "side"])
    for b in ("fw-a", "fw-b", "fw-c", "fw-d"):
        assert pos.loc[(b, "long"), "n"] == pos.loc[(b, "short"), "n"] == 20
    orders = _read(world["path"], "SELECT created_at, exec_session FROM fw_orders")
    assert (pd.to_datetime(orders.exec_session).dt.date == THU).all()
    sigs = _read(world["path"], "SELECT created_at FROM fw_signals WHERE exec_session = "
                                f"'{THU}'")
    assert (pd.to_datetime(sigs.created_at) < pd.Timestamp(en.open_time(THU))).all()
    # every book: value = cash + positions = 1,000 + unrealised (no borrow on day one)
    marks = _read(world["path"], "SELECT * FROM fw_marks")
    for r in marks.itertuples():
        assert r.equity == pytest.approx(r.cash + r.positions)
        assert r.equity == pytest.approx(1000 + r.realized + r.unrealized, abs=1e-6)
    ab = marks.set_index("book_id")
    assert ab.loc["fw-a", "gross"] == pytest.approx(1.0, abs=0.01)   # 50/50, 100 % gross
    assert abs(ab.loc["fw-a", "net"]) < 0.01
    m2.close()


def test_repeating_a_run_books_nothing_twice(world):
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    before = {t: _read(world["path"], f"SELECT COUNT(*) n FROM {t}")["n"][0]
              for t in ("fw_ledger", "fw_orders", "fw_positions", "fw_signals", "fw_books")}
    en.run(world["path"], m2, live, now=at(THU, 21), log=lambda *_: None)
    after = {t: _read(world["path"], f"SELECT COUNT(*) n FROM {t}")["n"][0] for t in before}
    assert after == before
    m2.close()


def test_a_late_run_creates_no_order_for_an_open_that_has_passed(world):
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(FRI, 12), log=lambda *_: None)   # after Fri open
    f = _read(world["path"], f"SELECT status FROM fw_signals WHERE book_id='fw-f' AND "
                             f"signal_session='{THU}'")
    assert list(f["status"]) == ["missed"]
    assert _read(world["path"], f"SELECT COUNT(*) n FROM fw_orders WHERE exec_session="
                                f"'{FRI}'")["n"][0] == 0
    m2.close()


def test_an_incomplete_day_is_not_traded_on(world):
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED, partial=WED)
    s = en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    assert s["latest_complete"] != str(WED)
    assert _read(world["path"], "SELECT COUNT(*) n FROM fw_signals WHERE status='pending'"
                 )["n"][0] == 0
    m1.close()


def _state(path):
    """What a run leaves behind, without the timestamps of when it ran."""
    return {
        "marks": _read(path, "SELECT book_id, session, equity, cash FROM fw_marks "
                             "ORDER BY 1, 2").round(9).to_dict("records"),
        "ledger": _read(path, "SELECT book_id, kind, COUNT(*) n, ROUND(SUM(amount), 9) s "
                              "FROM fw_ledger GROUP BY 1, 2 ORDER BY 1, 2").to_dict("records"),
        "orders": _read(path, "SELECT book_id, COUNT(*) n FROM fw_orders GROUP BY 1 "
                              "ORDER BY 1").to_dict("records"),
    }


def _clean_run(world, live):
    """WED then a full THU, nothing in between: the state every detour must end in."""
    clean = world["dir"] / "clean.duckdb"
    m1 = _market(world["dir"] / "c1.duckdb", WED)
    en.run(clean, m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "c2.duckdb", THU)
    en.run(clean, m2, live, now=at(THU, 20), log=lambda *_: None)
    m2.close()
    return _state(clean)


def test_an_incomplete_session_is_not_booked_until_its_prices_are_complete(world):
    # Audit 24.10.2026 F1: only signals waited for a complete day; fills and marks were
    # booked on any day with some prices (20./21.10.: 595 of 5,364) and then frozen.
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU, partial=THU)       # 3 of 60 names
    s = en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    m2.close()
    for t in ("fw_orders", "fw_marks", "fw_positions"):
        assert _read(world["path"], f"SELECT COUNT(*) n FROM {t}")["n"][0] == 0, t
    assert _read(world["path"], "SELECT COUNT(*) n FROM fw_signals WHERE status='pending' "
                                f"AND exec_session='{THU}'")["n"][0] == 5
    assert any(str(THU) in n and "unvollständig" in n for n in s["notes"])
    m3 = _market(world["dir"] / "m3.duckdb", THU)                    # the rest arrives
    en.run(world["path"], m3, live, now=at(THU, 21), log=lambda *_: None)
    m3.close()
    assert _state(world["path"]) == _clean_run(world, live)


def test_a_session_is_not_booked_before_its_close(world):
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)     # Thursday's bar, but it is noon
    en.run(world["path"], m2, live, now=at(THU, 12), log=lambda *_: None)
    assert _read(world["path"], "SELECT COUNT(*) n FROM fw_marks")["n"][0] == 0
    assert _read(world["path"], "SELECT COUNT(*) n FROM fw_signals WHERE "
                                f"signal_session='{THU}'")["n"][0] == 0
    m2.close()


def test_a_run_that_breaks_mid_session_leaves_nothing_half_booked(world, monkeypatch):
    # Audit 24.10.2026 F2: without a transaction a failure after the borrow, the fills
    # or some of the 40 openings left them in place, and the next run booked them again.
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    real = en.fill_rebalance

    def breaks_in_c(con, market, book_id, *a):
        out = real(con, market, book_id, *a)         # writes orders, positions, ledger
        if book_id == "fw-c":
            raise RuntimeError("simulated crash")
        return out

    monkeypatch.setattr(en, "fill_rebalance", breaks_in_c)
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    s = en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    booked = set(_read(world["path"], "SELECT book_id FROM fw_marks")["book_id"])
    assert booked == {"fw-a", "fw-b"}                  # C rolled back, D-F wait for it
    assert _read(world["path"], "SELECT COUNT(*) n FROM fw_positions WHERE "
                                "book_id='fw-c'")["n"][0] == 0
    assert any("RuntimeError" in n for n in s["notes"])
    monkeypatch.setattr(en, "fill_rebalance", real)
    en.run(world["path"], m2, live, now=at(THU, 21), log=lambda *_: None)
    m2.close()
    assert _state(world["path"]) == _clean_run(world, live)


def test_a_share_without_bars_for_five_sessions_is_realised_as_delisted(world):
    # Audit 24.10.2026 F4 (rule 3, R18): a share that stopped trading stayed open at its
    # last mark forever, counted only as stale, its capital tied up.
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    m2.close()
    held = _read(world["path"], "SELECT * FROM fw_positions WHERE book_id='fw-d' AND "
                                "status='open' ORDER BY side, ticker")
    long_t = held[held.side == "long"].ticker.iloc[0]
    short_t = held[held.side == "short"].ticker.iloc[0]
    marks = held.set_index("ticker")["mark_usd"]
    fifth = cal.sessions_from(cal.next_session(THU), 5)[-1]          # 29.10.2026
    m3 = _market(world["dir"] / "m3.duckdb", fifth, gone={long_t: THU, short_t: THU})
    en.run(world["path"], m3, live, now=at(cal.prev_session(fifth), 20),
           log=lambda *_: None)
    still = _read(world["path"], "SELECT status FROM fw_positions WHERE book_id='fw-d' "
                                 f"AND ticker IN ('{long_t}', '{short_t}')")
    assert set(still.status) == {"open"}                 # four sessions: maybe a halt
    en.run(world["path"], m3, live, now=at(fifth, 20), log=lambda *_: None)
    m3.close()
    out = _read(world["path"], "SELECT * FROM fw_positions WHERE book_id='fw-d' AND "
                               f"ticker IN ('{long_t}', '{short_t}')").set_index("ticker")
    assert set(out.status) == {"closed"}
    assert (pd.to_datetime(out.exit_session).dt.date == fifth).all()
    # no CRSP return in this store: the long loses the assumed 30 % from its last mark,
    # the short is closed at its last mark (no gain is assumed for it)
    assert out.loc[long_t, "exit_usd"] == pytest.approx(0.7 * marks[long_t])
    assert out.loc[short_t, "exit_usd"] == pytest.approx(marks[short_t])
    orders = _read(world["path"], "SELECT ticker, status FROM fw_orders WHERE "
                                  f"book_id='fw-d' AND exec_session='{fifth}'")
    assert set(orders[orders.status == "delisted"].ticker) == {long_t, short_t}
    for r in _read(world["path"], "SELECT * FROM fw_marks").itertuples():
        assert r.equity == pytest.approx(r.cash + r.positions)


def test_a_nan_amount_is_refused_by_the_ledger(con):
    # Audit 24.10.2026 F5: one NaN row made SUM(amount), the cash and the book NaN.
    lg.book(con, "x", dt.datetime(2026, 10, 22), None, "deposit", 1000.0)
    for bad in (float("nan"), float("inf"), None):
        with pytest.raises(ValueError):
            lg.book(con, "x", dt.datetime(2026, 10, 22), None, "proceeds", bad)
    assert lg.cash(con, "x") == pytest.approx(1000.0)


def test_a_held_share_with_a_null_open_stays_open_and_the_cash_finite(world):
    # Audit 24.10.2026 F5: `not nan` and `nan <= 0` are both False, so a NULL open on a
    # rebalancing day became NaN proceeds and a NaN book for good.
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    m2.close()
    held = _read(world["path"], "SELECT ticker FROM fw_positions WHERE book_id='fw-d' AND "
                                "status='open' AND side='long' ORDER BY ticker")
    t = held.ticker.iloc[0]
    sig_day, fill_day = dt.date(2026, 10, 28), dt.date(2026, 10, 29)   # D's next basket
    m3 = _market(world["dir"] / "m3.duckdb", fill_day, null_open={t: fill_day})
    en.run(world["path"], m3, live, now=at(sig_day, 20), log=lambda *_: None)
    en.run(world["path"], m3, live, now=at(fill_day, 20), log=lambda *_: None)
    m3.close()
    o = _read(world["path"], f"SELECT status FROM fw_orders WHERE book_id='fw-d' AND "
                             f"ticker='{t}' AND action='close' AND exec_session='{fill_day}'")
    assert list(o.status) == ["unresolved"]
    assert _read(world["path"], f"SELECT status FROM fw_positions WHERE book_id='fw-d' AND "
                                f"ticker='{t}'").status.tolist() == ["open"]
    marks = _read(world["path"], "SELECT * FROM fw_marks")
    assert str(fill_day) in set(marks.session.astype(str))
    assert np.isfinite(marks[["cash", "equity", "positions"]].to_numpy()).all()
    led = _read(world["path"], "SELECT amount FROM fw_ledger")
    assert np.isfinite(led.amount.to_numpy()).all()


def test_a_book_opened_under_an_older_version_moves_to_the_new_one(con, monkeypatch):
    # The six real books opened on 22.10.2026 as A1-F1; the delisting rule is version 2.
    old = [{**b, "version": b["code"] + "1"} for b in stg.BOOKS]
    new = list(stg.BOOKS)
    monkeypatch.setattr(stg, "BOOKS", old)
    en.ensure_books(con, at(WED, 20).replace(tzinfo=None))
    monkeypatch.setattr(stg, "BOOKS", new)
    en.ensure_books(con, at(THU, 20).replace(tzinfo=None))
    en.ensure_books(con, at(THU, 21).replace(tzinfo=None))   # once only
    v = con.execute("SELECT book_id, version, note FROM fw_versions ORDER BY 1, 2"
                    ).fetch_df()
    assert list(v[v.book_id == "fw-a"].version) == ["A1", "A2"]
    assert "Delisting" in v[v.version == "E2"].note.iloc[0]
    assert set(con.execute("SELECT version FROM fw_books").fetch_df().version) == \
        {b["version"] for b in new}
    assert con.execute("SELECT COUNT(*) FROM fw_ledger WHERE kind='deposit'"
                       ).fetchone()[0] == 6                # no second deposit


def test_the_brief_names_every_book_and_says_it_is_paper(world):
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    m2.close()
    c = lg.connect(world["path"], read_only=True)
    text = rp.brief_section(c)
    c.close()
    for b in stg.BOOKS:
        assert b["name"] in text
    assert "nur Papier" in text and "keine annualisierten Werte" in text


def test_the_portfolios_view_shows_all_six_books(world, tmp_path, monkeypatch):
    """PORTFOLIOS → BOOKS draws the six books from the tracker's own store."""
    import shutil

    from tests.app_fixture import build_data, run

    data = build_data(tmp_path / "appdata" / "data")
    from portfolio_lab.data.schema import open_user
    from portfolio_lab.store import Store

    store = Store(open_user(data / "user.duckdb"))
    store.create("Demo")
    store.con.close()
    live = FakeLive()
    m1 = _market(world["dir"] / "m1.duckdb", WED)
    en.run(world["path"], m1, live, now=at(WED, 20), log=lambda *_: None)
    m1.close()
    m2 = _market(world["dir"] / "m2.duckdb", THU)
    en.run(world["path"], m2, live, now=at(THU, 20), log=lambda *_: None)
    m2.close()
    shutil.copy(world["path"], data / "paper_forward.duckdb")
    app = run(monkeypatch, data, query={"cmd": "PORT"})
    assert not app.exception
    labels = "\n".join(e.label for e in app.expander)
    for b in stg.BOOKS:
        assert b["name"] in labels
    frames = "\n".join(str(d.value) for d in app.dataframe)
    assert "unbestätigt" in frames                   # short borrow is never claimed
