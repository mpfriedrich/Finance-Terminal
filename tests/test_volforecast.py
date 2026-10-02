"""Volatility forecast: right on series whose risk is known, blind to the future, honest
about frozen prices."""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import volforecast as vf
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

DAYS = pd.bdate_range("2019-01-01", periods=1000)


def walk(daily_vol: float, seed: int, days=DAYS) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(rng.normal(0, daily_vol, len(days)))), index=days)


def panel() -> dict[str, pd.Series]:
    # five calm and five wild shares, constant risk each: the model has to tell them apart
    return {f"C{i}": walk(0.01, i) for i in range(5)} | {f"W{i}": walk(0.03, 10 + i)
                                                          for i in range(5)}


def test_features_by_hand():
    # +10 %, -10 %, +10 %, ... : every log return is ±ln(1.1) = ±0.09531
    s = pd.Series(100 * 1.1 ** (np.arange(30) % 2), index=DAYS[:30])
    f = vf.features(s)
    r2 = np.log(1.1) ** 2                                   # 0.0090840
    assert f.l1.iloc[1] == pytest.approx(np.log(r2 + vf.EPS))
    assert f.l5.iloc[5] == pytest.approx(np.log(r2 + vf.EPS))
    assert np.isnan(f.l5.iloc[4])                           # only four returns by then
    # the 20 sessions after day 0 all move ±ln(1.1), and day 0 ends 20 sessions later
    # at the same level (an even number of moves)
    assert f.target.iloc[0] == pytest.approx(np.log(r2 + vf.EPS))
    assert f.fwd.iloc[0] == pytest.approx(0.0, abs=1e-12)
    # the last 20 rows can never be a training target: their window is not over yet
    assert f.target.iloc[-20:].isna().all() and f.fwd.iloc[-20:].isna().all()


def test_a_known_risk_is_recovered():
    model = vf.fit(panel(), DAYS[-1].date())
    assert model is not None and model.rows > vf.MIN_ROWS
    calm = vf.forecast(model, walk(0.01, 99))
    wild = vf.forecast(model, walk(0.03, 98))
    assert calm["volatility"] == pytest.approx(0.01 * np.sqrt(252), rel=0.2)
    assert wild["volatility"] == pytest.approx(0.03 * np.sqrt(252), rel=0.2)
    # normal returns: the multiplier that held 80 % of moves is the bell curve's 1.28
    assert model.z80 == pytest.approx(1.2816, abs=0.12)


def test_the_range_is_centred_on_today_and_says_nothing_about_direction():
    model = vf.fit(panel(), DAYS[-1].date())
    f = vf.forecast(model, walk(0.02, 7))
    assert f["low"] < f["price"] < f["high"]
    assert f["low"] * f["high"] == pytest.approx(f["price"] ** 2)


def test_prices_after_the_asof_date_change_nothing():
    asof = DAYS[850].date()     # ~5,800 training rows; below 5,000 fit() declines
    p = panel()
    wrecked = {t: s.where(s.index <= pd.Timestamp(asof), s * 50) for t, s in p.items()}
    a, b = vf.fit(p, asof), vf.fit(wrecked, asof)
    assert a.beta == pytest.approx(b.beta) and a.z80 == pytest.approx(b.z80)
    s = walk(0.02, 5)
    fa = vf.forecast(a, s, asof)
    fb = vf.forecast(a, s.where(s.index <= pd.Timestamp(asof), s * 50), asof)
    assert fa == fb and fa["date"] == asof


def test_a_frozen_price_is_a_gap_not_calm():
    # Measured 02.10.2026: one sampled line with 1,741 unchanged closes pulled Duan's
    # factor to 9.7 and every forecast to roughly 2.6x. Frozen windows must not train.
    p = panel()
    frozen = walk(0.02, 50).copy()
    frozen.iloc[300:600] = frozen.iloc[300]
    clean = vf.fit(p, DAYS[-1].date())
    dirty = vf.fit(p | {"FRZ": frozen}, DAYS[-1].date())
    assert dirty.smear == pytest.approx(clean.smear, rel=0.15)
    # and a share whose price stopped moving gets no number at all
    stopped = walk(0.02, 51).copy()
    stopped.iloc[-10:] = stopped.iloc[-11]
    assert vf.forecast(clean, stopped) is None


def test_too_little_data_gives_no_answer():
    assert vf.fit({"A": walk(0.01, 1)[:200]}, DAYS[-1].date()) is None
    model = vf.fit(panel(), DAYS[-1].date())
    assert vf.forecast(model, walk(0.01, 2)[:200]) is None


def test_the_sample_is_liquid_us_shares_with_history():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    days = pd.bdate_range("2020-01-01", "2024-06-28")
    rows = []
    for t, px, vol, start in (("BIG", 50.0, 1e6, 0), ("TINY", 50.0, 10.0, 0),
                              ("CHEAP", 2.0, 1e8, 0), ("NEW", 50.0, 1e6, 800),
                              ("7203.JPN", 50.0, 1e6, 0), ("^GSPC", 50.0, 1e6, 0)):
        for d in days[start:]:
            rows.append((t, d.date(), px, px, px, px, vol))
    con.executemany("INSERT INTO prices (ticker, date, open, close, close_adj, close_raw, "
                    "volume) VALUES (?,?,?,?,?,?,?)", rows)
    for i, t in enumerate(("BIG", "TINY", "CHEAP", "NEW", "7203.JPN")):
        con.execute("INSERT INTO securities (cik, ticker) VALUES (?, ?)", [i + 1, t])
    assert vf.sample_tickers(con, dt.date(2024, 6, 28)) == ["BIG"]
