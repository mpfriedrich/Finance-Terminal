"""FRED macro series: parsing, the readings, and the dashboard — no network."""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import fred
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

CSV = "observation_date,CPIAUCSL\n" + "\n".join(
    f"{y}-{m:02d}-01,{100 * 1.03 ** (y - 2023) * (1 + 0.0025 * (m - 1)):.4f}"
    for y in (2023, 2024, 2025) for m in range(1, 13)) + "\n2026-01-01,.\n"


def test_parsing_drops_fred_missing_marker():
    frame = fred.parse_csv(CSV, "CPIAUCSL")
    assert list(frame.columns) == ["series_id", "date", "value"]
    assert len(frame) == 36 and frame.date.iloc[0] == dt.date(2023, 1, 1)


def test_inflation_is_read_as_change_on_a_year_earlier():
    s = fred.parse_csv(CSV, "CPIAUCSL").set_index("date")["value"]
    yoy = fred.transformed(s, "yoy").dropna()
    assert yoy.iloc[-1] == pytest.approx(3.0, abs=0.01)


def test_gdp_is_read_as_annualised_quarterly_growth():
    idx = pd.to_datetime(["2025-01-01", "2025-04-01", "2025-07-01"]).date
    s = pd.Series([100.0, 101.0, 102.01], index=idx)
    q = fred.transformed(s, "qoq_ann").dropna()
    assert q.iloc[-1] == pytest.approx((1.01 ** 4 - 1) * 100)


def test_payrolls_are_read_as_the_monthly_change():
    idx = pd.to_datetime(["2025-01-01", "2025-02-01"]).date
    assert fred.transformed(pd.Series([158_000.0, 158_150.0], index=idx), "diff").iloc[-1] == 150


def test_the_dashboard_lists_every_series_and_reads_the_stored_ones():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    fred.store(con, fred.download("CPIAUCSL", getter=lambda url: CSV))
    board = fred.dashboard(con).set_index("id")
    assert len(board) == len(fred.SERIES)
    assert board.at["CPIAUCSL", "latest"] == pytest.approx(3.0, abs=0.01)
    assert pd.isna(board.at["UNRATE", "latest"])
    assert fred.history(con, "CPIAUCSL", dt.date(2024, 6, 1)).index[0] >= pd.Timestamp("2024-06-01")


def test_storing_again_replaces_rather_than_duplicates():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    for _ in range(2):
        fred.store(con, fred.download("CPIAUCSL", getter=lambda url: CSV))
    assert con.execute("SELECT COUNT(*) FROM macro_series").fetchone()[0] == 36


# Eurostat's JSON-stat layout (checked against une_rt_m on 02.10.2026): `value` keys are
# the positions in `dimension.time`; a period without a figure has no key at all.
ESTAT = """{"label": "Unemployment by sex and age - monthly data",
 "value": {"0": 6.5, "1": 6.4, "3": 6.4},
 "dimension": {"time": {"category": {"index":
     {"2026-05": 0, "2026-06": 1, "2026-07": 2, "2026-08": 3}}}}}"""


def test_eurostat_answer_is_read_by_position_and_skips_missing_months():
    frame = fred.parse_eurostat(ESTAT, "ESTAT_UNE_RT_M_EA21")
    assert frame.date.tolist() == [dt.date(2026, 5, 1), dt.date(2026, 6, 1),
                                   dt.date(2026, 8, 1)]
    assert frame.value.tolist() == [6.5, 6.4, 6.4]


def test_euro_unemployment_comes_from_eurostat_not_the_stale_fred_copy():
    # FRED's OECD copy (LRHUTTTTEZM156S) stopped at 2023-01; the board showed it as current.
    assert "LRHUTTTTEZM156S" not in fred.SERIES
    asked = []
    frame = fred.download("ESTAT_UNE_RT_M_EA21",
                          getter=lambda url: asked.append(url) or ESTAT)
    assert asked[0].startswith("https://ec.europa.eu/eurostat/")
    assert "geo=EA21" in asked[0] and "s_adj=SA" in asked[0]
    assert len(frame) == 3
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    fred.store(con, frame)
    row = fred.dashboard(con).set_index("id").loc["ESTAT_UNE_RT_M_EA21"]
    assert row.latest == pytest.approx(6.4)
    assert pd.Timestamp(row.period) == pd.Timestamp("2026-08-01")
