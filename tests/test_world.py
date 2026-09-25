"""The market monitor and on-demand prices for any Yahoo ticker.

Every move is worked out by hand; no network — the downloader is replaced.
"""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import world
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply


def series(values, start="2025-12-24"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_a_day_is_the_previous_close_and_a_week_the_last_close_before_it():
    s = series(range(100, 120))            # 2025-12-24 .. 2026-01-20, +1 a day
    m = world.moves(s)
    assert m["last"] == 119
    assert m["1D"] == pytest.approx(119 / 118 - 1)
    # 7 calendar days before Tue 20 Jan is Tue 13 Jan, the 14th bar: 114
    assert m["1W"] == pytest.approx(119 / 114 - 1)


def test_the_year_to_date_starts_from_the_last_close_of_the_previous_year():
    s = series(range(100, 120))            # last 2025 close: Wed 31 Dec = 105
    assert world.moves(s)["YTD"] == pytest.approx(119 / 105 - 1)


def test_a_horizon_longer_than_the_history_is_empty_not_invented():
    assert world.moves(series(range(100, 120)))["1Y"] is None


def test_yields_move_in_basis_points():
    m = world.moves(series([4.00, 4.10]), "^TNX")
    assert m["1D"] == pytest.approx(10.0)


def test_the_monitor_lists_a_symbol_it_has_no_prices_for():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    today = dt.date.today()
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES ('^AXJO', ?, ?, ?, ?, 0)",
        [(today - dt.timedelta(days=i), 8000.0 + i, 8000.0 + i, 8000.0 + i)
         for i in range(10, 0, -1)])
    tables = world.monitor(con)
    apac = tables["Asia-Pacific"].set_index("symbol")
    assert apac.at["^AXJO", "last"] == 8001.0
    assert pd.isna(apac.at["^N225", "last"]), "listed, but empty"


def test_rebased_series_all_start_at_100():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    days = pd.bdate_range("2026-01-05", periods=5).date
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
        "VALUES (?,?,?,?,?,0)",
        [(t, d, v, v, v) for t, base in (("A", 10.0), ("B", 2000.0))
         for i, d in enumerate(days) for v in [base * (1 + 0.1 * i)]])
    r = world.rebased(con, ["A", "B"], days[0])
    assert (r.iloc[0] == 100.0).all()
    assert r["A"].iloc[-1] == pytest.approx(140.0) and r["B"].iloc[-1] == pytest.approx(140.0)


def test_a_yahoo_ticker_comes_back_with_an_adjusted_open():
    def fake(tickers, start, end):
        return pd.DataFrame({"ticker": tickers[0], "date": [dt.date(2026, 1, 5)],
                             "open": [40.0], "high": [42.0], "low": [39.0],
                             "close": [41.0], "close_adj": [20.5], "volume": [1000]})
    bars = world.fetch_history("BHP.AX", downloader=fake)
    assert bars.open_adj.iloc[0] == pytest.approx(20.0), "open on the close's adjusted basis"


def test_an_unknown_yahoo_ticker_is_empty():
    assert world.fetch_history("NOPE", downloader=lambda *a: pd.DataFrame()).empty


def test_stats_from_bars_alone():
    days = pd.bdate_range("2025-01-01", periods=300)
    bars = pd.DataFrame({"date": days, "close_adj": [100.0 + i for i in range(300)]})
    st = world.stats(bars)
    assert st["max_drawdown"] == 0.0
    assert st["1M"] > 0


def test_compare_puts_any_stored_series_side_by_side():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    days = pd.bdate_range("2025-01-02", periods=120).date
    rows = []
    for i, d in enumerate(days):
        a = 100.0 * (1.01 if i % 2 else 1.0)
        rows += [("AAA", d, a, a, a), ("BBB", d, 2 * a, 2 * a, 2 * a),
                 ("CCC", d, 300.0 - a, 300.0 - a, 300.0 - a), ("^SP500TR", d, a, a, a)]
    con.executemany("INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
                    "VALUES (?,?,?,?,?,0)", rows)
    out = world.compare(con, ["aaa", "BBB", "CCC", "NOPE"], days[0])
    assert out["missing"] == ["NOPE"]
    assert out["correlation"].at["AAA", "BBB"] == pytest.approx(1.0)
    assert out["correlation"].at["AAA", "CCC"] < -0.9
    stats = out["stats"].set_index("symbol")
    assert stats.at["AAA", "beta"] == pytest.approx(1.0)
    assert (out["curves"].iloc[0] == 100.0).all()


# --- the gold view ----------------------------------------------------------------------

def _gold_store(rows):
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    days = pd.bdate_range(dt.date.today() - dt.timedelta(days=40), dt.date.today()).date
    for symbol, first, last in rows:
        step = (last - first) / (len(days) - 1)
        for i, d in enumerate(days):
            con.execute("INSERT INTO prices (ticker, date, close) VALUES (?, ?, ?)",
                        [symbol, d, first + i * step])
    return con


def test_in_gold_an_index_is_divided_by_the_gold_price():
    con = _gold_store([("GC=F", 2000.0, 4000.0), ("^GSPC", 6000.0, 6000.0)])
    row = world.monitor(con, unit="gold")["Americas"].set_index("symbol").loc["^GSPC"]
    assert row["kind"] == "oz"
    assert row["last"] == pytest.approx(1.5)                 # 6000 / 4000
    assert row["1M"] < -0.3                                  # gold rose, so it fell


def test_a_foreign_index_becomes_dollars_before_it_becomes_ounces():
    con = _gold_store([("GC=F", 4000.0, 4000.0), ("^N225", 40000.0, 40000.0),
                       ("JPY=X", 150.0, 150.0)])
    row = world.monitor(con, unit="gold")["Asia-Pacific"].set_index("symbol").loc["^N225"]
    assert row["last"] == pytest.approx(40000.0 / 150.0 / 4000.0)


def test_an_index_whose_currency_has_no_rate_is_said_so_not_left_in_its_own_currency():
    con = _gold_store([("GC=F", 4000.0, 4000.0), ("^BSESN", 80000.0, 80000.0)])
    row = world.monitor(con, unit="gold")["Asia-Pacific"].set_index("symbol").loc["^BSESN"]
    assert row["kind"] == "no_rate" and pd.isna(row.get("last"))


def test_a_currency_in_gold_is_milligrams_per_unit_and_yields_are_untouched():
    con = _gold_store([("GC=F", 3110.34768, 3110.34768), ("EURUSD=X", 1.0, 1.0),
                       ("^TNX", 4.0, 4.5)])
    boards = world.monitor(con, unit="gold")
    eur = boards["Currencies"].set_index("symbol").loc["EURUSD=X"]
    assert eur["kind"] == "mg" and eur["label"] == "EUR"
    assert eur["last"] == pytest.approx(10.0)                # $1 at $3,110/oz = 10 mg
    tnx = boards["US rates (yield, %)"].set_index("symbol").loc["^TNX"]
    assert tnx["kind"] == "unitless" and tnx["last"] == pytest.approx(4.5)
    gold = boards["Commodities"].set_index("symbol").loc["GC=F"]
    assert gold["kind"] == "unit"


def test_the_gold_view_needs_its_rates_stored():
    assert set(world.FX_FOR_GOLD) <= set(world.stored_symbols())
    assert all(c == "USD" or f"{c}USD=X" in world.stored_symbols()
               or f"{c}=X" in world.stored_symbols() for c in world.CURRENCY.values())


def test_compare_in_gold_measures_everything_in_ounces():
    con = _gold_store([("GC=F", 2000.0, 4000.0), ("AAPL", 200.0, 200.0),
                       ("^SP500TR", 10000.0, 10000.0)])
    start = dt.date.today() - dt.timedelta(days=40)
    usd = world.compare(con, ["AAPL"], start)
    gold = world.compare(con, ["AAPL"], start, unit="gold")
    assert usd["stats"].iloc[0]["return"] == pytest.approx(0.0)
    assert gold["stats"].iloc[0]["return"] == pytest.approx(-0.5)   # gold doubled
    assert world.rebased(con, ["GC=F"], start, unit="gold").empty    # gold is the unit


def test_in_gold_a_series_not_stored_is_missing_not_short_of_a_rate():
    con = _gold_store([("GC=F", 4000.0, 4000.0), ("JPY=X", 150.0, 150.0)])
    boards = world.monitor(con, unit="gold")
    assert boards["Commodities"].set_index("symbol").loc["SI=F", "kind"] == "missing"
    jpy = boards["Currencies"].set_index("symbol").loc["JPY=X"]
    assert jpy["label"] == "JPY"
    assert jpy["last"] == pytest.approx(31_103.4768 / 150.0 / 4000.0)   # mg per yen
