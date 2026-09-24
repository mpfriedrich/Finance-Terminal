"""Portfolio risk measures on series whose answers are known."""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import risk
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

ASOF = dt.date(2026, 6, 30)


def store(paths: dict, start="2019-06-03", end=ASOF, sectors=None):
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    days = pd.bdate_range(start, end)
    rows = []
    for t, f in paths.items():
        for i, d in enumerate(days):
            v = f(i, d)
            if v is not None:
                rows.append((t, d.date(), v, v, v, 0))
    con.executemany("INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
                    "VALUES (?,?,?,?,?,?)", rows)
    for t, sector in (sectors or {}).items():
        con.execute("INSERT INTO securities (cik, ticker, sector) VALUES (?,?,?)",
                    [hash(t) % 10**6, t, sector])
    return con


def alternating(i, d):          # +1% / −1% every other day
    return 100.0 * (1.01 if i % 2 else 1.0) * (0.9999 ** i)


def test_a_position_that_mirrors_the_index_has_beta_one():
    con = store({"AAA": alternating, "^SP500TR": alternating})
    m = risk.measures(con, pd.DataFrame({"ticker": ["AAA"], "weight": [1.0]}), ASOF)
    assert m["beta"] == pytest.approx(1.0, abs=1e-6)
    assert m["correlation_to_index"] == pytest.approx(1.0, abs=1e-6)


def test_var_is_the_loss_exceeded_on_one_day_in_twenty():
    """Returns alternate +1% and about −1%: the 5% worst day is a ~1% loss."""
    con = store({"AAA": alternating, "^SP500TR": alternating})
    m = risk.measures(con, pd.DataFrame({"ticker": ["AAA"], "weight": [1.0]}), ASOF)
    assert m["var_95"] == pytest.approx(0.0100, abs=0.0005)
    assert m["cvar_95"] >= m["var_95"]


def test_two_perfectly_offsetting_names_cancel_out():
    def up(i, d):
        return 100.0 * (1.01 if i % 2 else 1.0)

    def down(i, d):
        return 100.0 * (1.0 if i % 2 else 1.01)
    con = store({"AAA": up, "BBB": down, "^SP500TR": alternating})
    m = risk.measures(con, pd.DataFrame({"ticker": ["AAA", "BBB"], "weight": [1, 1]}), ASOF)
    assert m["correlation"].at["AAA", "BBB"] == pytest.approx(-1.0, abs=1e-6)
    assert m["volatility"] < 0.01
    assert m["effective_names"] == pytest.approx(2.0)


def test_sector_exposure_sums_the_weights():
    con = store({"AAA": alternating}, sectors={"AAA": "Banks", "BBB": "Banks", "CCC": "Oil"})
    e = risk.exposure(con, pd.DataFrame({"ticker": ["AAA", "BBB", "CCC", "ZZZ"],
                                         "weight": [1, 1, 1, 1]})).set_index("sector")
    assert e.at["Banks", "weight"] == pytest.approx(0.5)
    assert e.at["unknown", "weight"] == pytest.approx(0.25)


def test_a_stress_window_uses_the_prices_from_its_start_to_its_end():
    """AAA halves exactly across the covid window; BBB has no prices then."""
    covid = risk.SCENARIOS[3]

    def halving(i, d):
        return 100.0 if d.date() <= covid[1] else 50.0

    def late(i, d):
        return 10.0 if d.date() > dt.date(2021, 1, 1) else None
    con = store({"AAA": halving, "BBB": late, "^SP500TR": halving})
    s = risk.stress(con, pd.DataFrame({"ticker": ["AAA", "BBB"], "weight": [3, 1]}))
    row = s.set_index("scenario").loc["Covid crash 2020"]
    assert row["portfolio"] == pytest.approx(-0.5)
    assert row["coverage"] == pytest.approx(0.75), "BBB did not trade then"
    assert row["index"] == pytest.approx(-0.5)
