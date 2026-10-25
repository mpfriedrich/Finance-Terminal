"""The US half from CRSP (data/crsp.py): every listing, its end, and what holders got."""
import datetime as dt

import pandas as pd
import pytest

from portfolio_lab.data import crsp, history, wrds
from tests.crsp_fixture import CRSP_END, DAYS, NO_OPEN, SPLIT_ON, build


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build(tmp_path_factory.mktemp("crsp"))


@pytest.fixture(scope="module")
def con(world):
    return world[0].con


def _px(con, ticker, day, col="close"):
    row = con.execute(f"SELECT {col} FROM prices WHERE ticker = ? AND date = ?",
                      [ticker, day]).fetchone()
    return row[0] if row else None


def test_every_line_is_counted_with_how_it_ended(world):
    _, counts, _, _ = world
    assert counts["lines"] == 4 and counts["still_trading"] == 1 and counts["ended"] == 3
    assert counts["with_delisting_return"] == 2          # Bust and Merged, not Oldco
    assert counts["without_sec_cik"] == 1 and counts["crsp_end"] == CRSP_END


def test_a_line_still_trading_takes_todays_ticker_and_its_sec_cik(con):
    row = con.execute("SELECT cik, name FROM securities WHERE ticker = 'ALPH'").fetchone()
    assert row == (1001, "ALPHA INC")
    assert con.execute("SELECT COUNT(*) FROM securities WHERE ticker = 'ALP'").fetchone()[0] == 0


def test_an_ended_line_that_collides_with_a_current_ticker_is_suffixed(con):
    row = con.execute("SELECT cik FROM securities WHERE ticker = 'ALPH-10003'").fetchone()
    assert row and row[0] == crsp.CRSP_OFFSET + 10003
    assert wrds.is_compustat(row[0])                      # no SEC filings to look for


def test_negative_prices_are_crsps_midpoint_and_splits_are_adjusted(con):
    before, after = SPLIT_ON - dt.timedelta(days=3), SPLIT_ON
    assert _px(con, "ALPH", before, "close_raw") == pytest.approx(
        2 * _px(con, "ALPH", after, "close_raw"), rel=0.01)
    assert _px(con, "ALPH", before) == pytest.approx(_px(con, "ALPH", after), rel=0.01)
    assert all(v > 0 for (v,) in con.execute(
        "SELECT close_raw FROM prices WHERE ticker = 'BST'").fetchall())


@pytest.mark.parametrize("ciz", [False, True])
def test_a_missing_open_stays_missing_and_the_fill_waits(tmp_path, ciz):
    """CRSP leaves `openprc` blank on a day without a recorded open. The build used to
    fill it with the close, so a fill "at the open" ran at the close (audit 24.10.2026
    F9, R16). The bar keeps its close, the open stays NULL, and Stage B's fill price for
    that session is missing, so it cannot trade the name then."""
    from portfolio_lab.stage_b import bars_for

    src, *_ = build(tmp_path, ciz=ciz)
    assert _px(src.con, "MRG", NO_OPEN, "open") is None
    assert _px(src.con, "MRG", NO_OPEN, "close") == pytest.approx(20.0)
    assert _px(src.con, "MRG", DAYS[41], "open") == pytest.approx(20.0)
    bars = bars_for(src, ["MRG"], DAYS[38], DAYS[42]).set_index("date")
    assert pd.isna(bars.at[NO_OPEN, "open_adj"])
    assert bars.at[DAYS[41], "open_adj"] == pytest.approx(20.0)


def test_the_total_return_series_compounds_crsps_return(con):
    a, b = DAYS[100], DAYS[101]
    assert _px(con, "ALPH", b, "close_adj") / _px(con, "ALPH", a, "close_adj") == \
        pytest.approx(1.001)


def test_after_crsp_ends_the_line_continues_on_crsps_basis(con):
    last = _px(con, "ALPH", CRSP_END)
    first_after = con.execute(
        "SELECT close, close_raw FROM prices WHERE ticker = 'ALPH' AND date > ? "
        "ORDER BY date LIMIT 1", [CRSP_END]).fetchone()
    assert first_after[0] == pytest.approx(last)          # no jump at the splice
    assert first_after[1] == pytest.approx(102.0)          # traded price as it traded
    assert con.execute("SELECT MAX(date) FROM prices WHERE ticker = 'ALPH'"
                       ).fetchone()[0] == dt.date(2025, 3, 31)


def test_a_company_listed_after_crsp_comes_from_the_free_store(world):
    src = world[0]
    assert "NEWCO" in set(src.universe(dt.date(2025, 3, 31)).ticker)
    assert "BST" not in set(src.universe(dt.date(2025, 3, 31)).ticker)
    assert "BST" in set(src.universe(dt.date(2023, 3, 1)).ticker)


def test_the_sec_filings_apply_to_crsp_companies_and_compustat_fills_only_before(con):
    rows = con.execute(
        "SELECT period_end, xbrl_tag FROM fundamentals WHERE cik = 1002 "
        "AND metric = 'book_value' ORDER BY period_end").fetchall()
    assert rows == [(dt.date(2022, 9, 30), "compustat-na:ceqq"),
                    (dt.date(2022, 12, 31), "compustat-na:ceqq"),
                    (dt.date(2023, 3, 31), "sec:x")]
    filed = con.execute("SELECT filed, fp FROM fundamentals WHERE cik = 1002 AND "
                        "xbrl_tag = 'compustat-na:ceqq' AND period_end = DATE '2022-12-31'"
                        ).fetchone()
    assert filed == (dt.date(2023, 1, 30), "rdq")


def test_the_market_share_count_comes_from_crsp(con):
    shares = con.execute(
        "SELECT value FROM fundamentals WHERE cik = 1001 AND xbrl_tag = 'crsp:shrout' "
        "ORDER BY period_end DESC LIMIT 1").fetchone()[0]
    assert shares == pytest.approx(100_000_000)


def test_the_delisting_return_replaces_the_assumed_loss(world):
    src, *_ = world
    assert history.delisting_returns(src.con, ["BST", "MRG", "ALPH-10003", "ALPH"]) == \
        {"BST": pytest.approx(-0.85), "MRG": pytest.approx(0.25)}
    assert src.delisting_returns(["BST"]) == {"BST": pytest.approx(-0.85)}


def test_buy_and_hold_takes_the_real_exit(world):
    import pandas as pd

    from portfolio_lab.backtest import buy_and_hold

    src, *_ = world
    held = pd.DataFrame({"ticker": ["BST", "ALPH"], "weight": [0.5, 0.5],
                         "market_cap": [2e8, 4e9]})
    result = buy_and_hold(src.con, held, dt.date(2023, 1, 3), dt.date(2024, 6, 28))
    assert any("CRSP records the actual delisting return" in n and "-85%" in n
               for n in result.notes)


def test_a_basket_of_one_vanished_name_takes_crsps_exit(world):
    """Only BST held: its bars stop on 2023-06-30, the benchmark runs on. The run
    ends on the market's last day and BST's real −85% applies: 100 × 0.15 = 15
    (audit 24.10.2026 F8; flat price, no costs)."""
    import pandas as pd

    from portfolio_lab.backtest import buy_and_hold
    from portfolio_lab.config import CostModel

    src, *_ = world
    held = pd.DataFrame({"ticker": ["BST"], "weight": [1.0], "market_cap": [2e8]})
    free = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
    result = buy_and_hold(src.con, held, dt.date(2023, 1, 3), dt.date(2023, 12, 29),
                          costs=free)
    assert result.portfolio[-1] == pytest.approx(15.0, rel=1e-9)
    assert result.dates[-1] == dt.date(2023, 12, 29)


def test_copying_the_free_store_again_changes_nothing(world):
    src, _, _, market = world
    before = {t: src.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("prices", "securities", "fundamentals", "crsp_link")}
    wrds.copy_us(src.con, market)
    after = {t: src.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in before}
    assert after == before


def test_a_global_rebuild_leaves_crsp_lines_alone(world, tmp_path):
    from tests import wrds_fixture

    src, *_ = world
    wrds.ingest(src.con, wrds_fixture.write_files(tmp_path / "global"))
    assert src.con.execute("SELECT COUNT(*) FROM securities WHERE ticker = 'ALPH-10003'"
                           ).fetchone()[0] == 1
    assert src.con.execute("SELECT COUNT(*) FROM securities WHERE ticker = 'DMA.DEU'"
                           ).fetchone()[0] == 1


def test_download_fetches_each_year_once_and_says_when_crsp_is_not_licensed(tmp_path):
    from tests.test_wrds import _FakeServer

    columns = {table: list(cols) for (schema, table), cols in crsp.WANTED.items()}
    since = dt.date(dt.date.today().year - 1, 1, 1)
    # The current format (CIZ) is taken when the account has it ...
    server = _FakeServer(columns)
    crsp.download(server, tmp_path, since, log=lambda _: None)
    assert len(list((tmp_path / "crsp_dsf_v2").glob("dsf_*.parquet"))) == 2
    names = next(q for q, _ in server.queries if "FROM crsp.stksecurityinfohist WHERE" in q)
    assert "sharetype = 'NS'" in names
    server.queries.clear()
    crsp.download(server, tmp_path, since, log=lambda _: None)
    years = [p[1].year for q, p in server.queries if "FROM crsp.dsf_v2 " in q and p]
    assert years == [dt.date.today().year]
    fundq = next(q for q, _ in server.queries if "FROM comp.fundq" in q and "LIMIT 1" not in q)
    assert "popsrc = 'D'" in fundq
    # ... the legacy one otherwise.
    legacy = _FakeServer({t: c for t, c in columns.items()
                          if t not in ("dsf_v2", "stksecurityinfohist")})
    crsp.download(legacy, tmp_path / "old", since, log=lambda _: None)
    assert len(list((tmp_path / "old" / "crsp_dsf").glob("dsf_*.parquet"))) == 2

    no_crsp = _FakeServer({t: c for t, c in columns.items()
                           if t not in ("dsf", "dsenames", "dsf_v2", "stksecurityinfohist")})
    with pytest.raises(RuntimeError, match="subscription does not include CRSP"):
        crsp.download(no_crsp, tmp_path / "x", since, log=lambda _: None)


def test_with_crsp_the_world_source_reports_itself_survivorship_free(world):
    assert world[0].survivorship_free is True


def test_a_crsp_table_the_account_may_see_but_not_read_ends_in_a_clear_message(tmp_path):
    """First real run: WRDS listed crsp.dsf's columns, then refused the rows."""
    from tests.test_wrds import _FakeCursor, _FakeServer

    class InsufficientPrivilege(Exception):
        pass

    class Refusing(_FakeCursor):
        def execute(self, sql, params=()):
            if "FROM crsp." in sql and "information_schema" not in sql:
                raise InsufficientPrivilege("permission denied for schema crsp_a_stock")
            super().execute(sql, params)

    columns = {table: list(cols) for (schema, table), cols in crsp.WANTED.items()}
    server = _FakeServer(columns)
    server.cursor = lambda name=None: Refusing(server)
    server.rollback = lambda: None
    with pytest.raises(RuntimeError, match="subscription does not include CRSP"):
        crsp.download(server, tmp_path, dt.date(2024, 1, 1), log=lambda _: None)


def test_the_current_crsp_format_gives_the_same_store(world, tmp_path):
    """CIZ: no cumulative price factor (split basis rebuilt from the price return), and
    the delisting on a row of its own. Same lines, same prices, same exits."""
    ciz, counts, _, _ = build(tmp_path, ciz=True)
    legacy, legacy_counts, _, _ = world
    for key in ("lines", "still_trading", "ended", "with_delisting_return", "crsp_end"):
        assert counts[key] == legacy_counts[key], key
    query = ("SELECT ticker, date, close, close_raw, close_adj FROM prices "
             "WHERE ticker IN ('ALPH', 'BST', 'MRG') ORDER BY 1, 2")
    a = ciz.con.execute(query).fetch_df()
    b = legacy.con.execute(query).fetch_df()
    assert len(a) == len(b)
    for c in ("close", "close_raw", "close_adj"):
        assert a[c].to_numpy() == pytest.approx(b[c].to_numpy(), rel=1e-6), c
    assert ciz.delisting_returns(["BST", "MRG"]) == \
        {"BST": pytest.approx(-0.85), "MRG": pytest.approx(0.25)}
