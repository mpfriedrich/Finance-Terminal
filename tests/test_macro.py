"""macro.rate_sensitivity and macro.event_study on bars that are not stored (F5)."""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd

from portfolio_lab import macro


def _store(days: pd.DatetimeIndex, moves: np.ndarray, market: np.ndarray):
    con = duckdb.connect()
    tnx = 4.0 + np.cumsum(moves)
    bench = 1000 * np.cumprod(1 + market)
    rows = ([("^TNX", d.date(), t, t) for d, t in zip(days, tnx)]
            + [("^SP500TR", d.date(), b, b) for d, b in zip(days, bench)])
    frame = pd.DataFrame(rows, columns=["ticker", "date", "close", "close_adj"])
    con.register("frame", frame)
    con.execute("CREATE TABLE prices AS SELECT * FROM frame")
    return con


def test_a_symbol_outside_the_store_is_measured_from_its_own_bars():
    """Yahoo-only symbols (SPY, a foreign index) never reached the rate measures: they
    read the store only. Hand-made: the fund moves 2 % per percentage point of the
    ten-year, exactly, so the beta is 0.02 and the correlation 1."""
    rng = np.random.default_rng(7)
    days = pd.bdate_range("2024-01-01", periods=200)
    moves = np.r_[0.0, rng.normal(0, 0.05, 199)]
    market = rng.normal(0, 0.01, 200)
    con = _store(days, moves, market)
    fund = pd.DataFrame({"date": days.date,
                         "close_adj": 100 * np.cumprod(1 + 0.02 * moves)})
    assert macro.rate_sensitivity(con, "SPY").empty            # not stored
    got = macro.rate_sensitivity(con, "SPY", prices=fund)
    row = got[got.symbol == "^TNX"].iloc[0]
    assert abs(row.beta_per_1pp - 0.02) < 1e-9 and abs(row.correlation - 1) < 1e-9


def test_the_event_study_runs_on_fetched_bars():
    rng = np.random.default_rng(3)
    days = pd.bdate_range("2024-01-01", periods=200)
    market = rng.normal(0, 0.01, 200)
    con = _store(days, np.zeros(200), market)
    # The fund is the market twice over, plus a 5 % jump on day 180.
    own = 2 * market
    own[180] += 0.05
    fund = pd.DataFrame({"date": days.date, "close_adj": 100 * np.cumprod(1 + own)})
    assert macro.event_study(con, "SPY", days[180].date()) == {}
    res = macro.event_study(con, "SPY", days[180].date(), window=(0, 0), prices=fund)
    assert abs(res["beta"] - 2.0) < 1e-6
    assert abs(res["car"] - 0.05) < 1e-6
    assert res["event_date"] == dt.date.fromisoformat(str(days[180].date()))
