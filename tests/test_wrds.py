"""World stage 3: Compustat Global translated into the market schema (data/wrds.py)."""
import datetime as dt

import pytest

from portfolio_lab.data import pit, wrds
from tests.wrds_fixture import (DIVIDEND_ON, END, EURUSD, GBPUSD, GONE_ON, JPYUSD,
                                SPLIT_ON, build_store)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("world"))


@pytest.fixture(scope="module")
def src(world):
    return world[0]


def _close(src, ticker, day, col="close_raw"):
    return src.con.execute(f"SELECT {col} FROM prices WHERE ticker = ? AND date = ?",
                           [ticker, day]).fetchone()[0]


def test_one_line_per_company_named_by_ticker_and_country(src):
    tickers = {r[0] for r in src.con.execute("SELECT ticker FROM securities").fetchall()}
    assert tickers == {"DMA.DEU", "6701.JPN", "BPP.GBR", "GONE.FRA"}   # no preferred line
    ciks = [r[0] for r in src.con.execute("SELECT cik FROM securities").fetchall()]
    assert all(wrds.is_compustat(c) for c in ciks)                     # never an SEC CIK
    assert not wrds.is_compustat(320193)


def test_prices_are_in_dollars_at_that_days_rate(src):
    assert _close(src, "DMA.DEU", END) == pytest.approx(50.0 * EURUSD)
    assert _close(src, "6701.JPN", END) == pytest.approx(3000.0 * JPYUSD)


def test_pence_are_pounds_divided_by_a_hundred(src):
    assert _close(src, "BPP.GBR", END) == pytest.approx(2.50 * GBPUSD)


def test_a_split_moves_the_traded_price_but_not_the_adjusted_one(src):
    before, after = SPLIT_ON - dt.timedelta(days=3), SPLIT_ON        # Friday, Monday
    assert _close(src, "DMA.DEU", before) == pytest.approx(2 * _close(src, "DMA.DEU", after))
    assert _close(src, "DMA.DEU", before, "close") == pytest.approx(
        _close(src, "DMA.DEU", after, "close"))


def test_the_total_return_series_carries_the_dividend(src):
    before = _close(src, "DMA.DEU", DIVIDEND_ON - dt.timedelta(days=3), "close_adj")
    after = _close(src, "DMA.DEU", DIVIDEND_ON, "close_adj")
    assert after / before == pytest.approx(1.02)


def test_an_annual_report_is_visible_only_after_the_legal_deadline(src):
    """FY2025 (book 5,000m EUR) ends 2025-12-31; with the four-month deadline it counts
    as published on 2026-04-30. The day before, FY2024 (4,000m) is still the latest."""
    cik = src.con.execute("SELECT cik FROM securities WHERE ticker = 'DMA.DEU'").fetchone()[0]

    def book(day):
        wide = pit.fundamentals_wide(src.con, day)
        return float(wide.loc[wide.cik == cik, "book_value"].iloc[0])

    assert book(dt.date(2026, 4, 29)) == pytest.approx(4000e6 * EURUSD)
    assert book(dt.date(2026, 4, 30)) == pytest.approx(5000e6 * EURUSD)


def test_quarterly_reports_use_their_report_date_and_build_a_ttm(src):
    cik = src.con.execute("SELECT cik FROM securities WHERE ticker = '6701.JPN'").fetchone()[0]
    wide = pit.fundamentals_wide(src.con, END)
    row = wide[wide.cik == cik].iloc[0]
    assert row["revenue_ttm"] == pytest.approx(4 * 250_000e6 * JPYUSD)
    assert row["reporting_currency"] == "JPY"
    # Q1 2026 ends 2026-03-31 and was reported 45 days later; a day before that the
    # four quarters end in December.
    filed = src.con.execute(
        "SELECT MIN(filed), MIN(fp) FROM fundamentals WHERE cik = ? AND metric = 'revenue' "
        "AND period_end = DATE '2026-03-31'", [cik]).fetchone()
    assert filed == (dt.date(2026, 5, 15), "rdq")


def test_market_cap_is_the_dollar_price_times_the_market_share_count(src):
    frame = src.screening_frame(END)
    row = frame[frame.ticker == "DMA.DEU"].iloc[0]
    assert row["price"] * row["shares_outstanding"] == pytest.approx(50.0 * EURUSD * 2e9)
    assert not row["listing_ratio_unknown"]


def test_a_company_that_stopped_trading_is_in_the_universe_only_while_it_traded(src):
    assert "GONE.FRA" in set(src.universe(GONE_ON).ticker)
    assert "GONE.FRA" not in set(src.universe(END).ticker)


def test_the_build_counts_what_rests_on_an_estimated_date(world):
    _, counts = world
    assert counts["companies"] == 4
    assert counts["facts_on_estimated_date"] > 0
    assert counts["price_rows_without_rate"] == 0


def test_a_rebuild_replaces_the_compustat_rows_and_keeps_the_rest(tmp_path):
    src, _ = build_store(tmp_path)
    src.con.execute("INSERT INTO securities (cik, ticker, name) VALUES (320193, 'AAPL', 'Apple')")
    src.con.execute("INSERT INTO prices (ticker, date, close) VALUES ('AAPL', DATE '2026-06-30', 200)")
    before = src.con.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    wrds.ingest(src.con, tmp_path / "wrds")
    assert src.con.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == before
    assert src.con.execute("SELECT COUNT(*) FROM securities WHERE ticker = 'AAPL'").fetchone()[0] == 1
    assert src.con.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 5


def test_countries_are_counted_for_the_build_report(src):
    counts = wrds.country_counts(src.con).set_index("country")
    assert counts.loc["DEU", "companies"] == 1
    assert counts.loc["FRA", "trading"] == 0


# --- the switch ---------------------------------------------------------------------------

def test_world_without_a_store_says_how_to_build_it(tmp_path):
    from portfolio_lab.config import Settings
    from portfolio_lab.data import sources

    with pytest.raises(sources.SourceNotReady) as err:
        sources.open_selected(Settings(data_source="world"),
                              world_db=tmp_path / "missing.duckdb")
    assert "build_wrds.py" in str(err.value) and "WRDS_USERNAME" in str(err.value)


def test_world_with_a_store_opens_the_world_source(tmp_path):
    from portfolio_lab.config import Settings
    from portfolio_lab.data import sources

    store, _ = build_store(tmp_path)
    store.con.close()
    source, path = sources.open_selected(Settings(data_source="world"),
                                         world_db=tmp_path / "world.duckdb")
    assert isinstance(source, wrds.WorldSource) and path.name == "world.duckdb"
    assert source.survivorship_free is False


def test_the_us_half_is_copied_in_and_replaced_without_touching_compustat(tmp_path):
    from tests.paid_fixture import build_store as build_us

    us = build_us(tmp_path / "us")
    us.con.close()
    src, _ = build_store(tmp_path)
    before = src.con.execute("SELECT COUNT(*) FROM securities").fetchone()[0]
    for _ in range(2):                                  # a second copy replaces the first
        wrds.copy_us(src.con, tmp_path / "us" / "paid.duckdb")
    tickers = set(src.universe(END).ticker)
    assert {"AAA", "DMA.DEU", "6701.JPN"} <= tickers
    assert src.con.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == before + 7
    frame = src.screening_frame(END)
    assert frame.loc[frame.ticker == "DMA.DEU", "price"].iloc[0] == pytest.approx(50.0 * EURUSD)


# --- the download, against a stand-in for the WRDS server ----------------------------------

class _FakeServer:
    """Answers the two kinds of query `download` sends: the column list, and data."""

    def __init__(self, columns: dict[str, list[str]]):
        self.columns = columns
        self.queries: list[tuple[str, tuple]] = []

    def cursor(self, name=None):
        return _FakeCursor(self)


class _FakeCursor:
    def __init__(self, server):
        self.server, self.itersize, self._rows, self.description = server, 0, [], None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.server.queries.append((sql, params))
        if "information_schema" in sql:
            self._rows = [(c,) for c in self.server.columns.get(params[0], [])]
            return
        table = sql.split("FROM comp.")[1].split()[0]
        cols = [c.strip().split(".")[-1] for c in
                sql.split("SELECT ", 1)[1].split(" FROM ")[0].split(",")]
        self.description = [(c,) for c in cols]
        self._rows = [tuple(f"{table}-{c}" for c in cols)]

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows


def test_download_asks_only_for_columns_wrds_has_and_caches_closed_years(tmp_path):
    columns = {t: list(cols) for t, cols in wrds.WANTED.items()}
    columns["g_secd"].remove("qunit")                   # a column WRDS does not carry
    columns["g_funda"].remove("ni")                     # Global's net income is NICON
    server = _FakeServer(columns)
    logged = []
    since = dt.date(dt.date.today().year - 1, 1, 1)
    wrds.download(server, tmp_path, ("DEU",), since, log=logged.append)
    assert any("qunit" in line for line in logged)
    assert any("net_income: from nicon (no ni)" in line for line in logged)
    secd_sql = [q for q, _ in server.queries if "comp.g_secd" in q]
    assert secd_sql and all("qunit" not in q for q in secd_sql)
    assert len(list((tmp_path / "g_secd").glob("DEU_*.parquet"))) == 2
    # Run again: last year is closed and cached, only the current year is fetched.
    server.queries.clear()
    wrds.download(server, tmp_path, ("DEU",), since, log=logged.append)
    years = [p[1].year for q, p in server.queries if "comp.g_secd" in q]
    assert years == [dt.date.today().year]
    # Fundamentals use the as-first-reported standard, never the restated one.
    funda_sql = next(q for q, _ in server.queries if "comp.g_funda " in q)
    assert "HIST_STD" in funda_sql


def test_a_failed_login_says_what_failed_without_the_password(monkeypatch):
    import psycopg2

    def refuse(**kwargs):
        raise psycopg2.OperationalError(
            f'connection to server failed: FATAL: password authentication failed '
            f'for user "{kwargs["user"]}"\npassword={kwargs["password"]}')

    monkeypatch.setattr(psycopg2, "connect", refuse)
    with pytest.raises(RuntimeError) as err:
        wrds.connect("someone", "hunter2-secret")
    assert "password authentication failed" in str(err.value)
    assert "hunter2-secret" not in str(err.value)


def test_global_names_are_used_when_the_north_american_ones_are_absent(tmp_path):
    """Compustat Global has no NI, CSHO or GP: net income is NICON, shares CSHOI, and
    gross profit is sales minus cost of goods sold."""

    from tests import wrds_fixture

    directory = wrds_fixture.write_files(tmp_path / "wrds")
    funda = wrds_fixture.funda().rename(columns={"ni": "nicon", "csho": "cshoi"})
    funda = funda.drop(columns=["gp"]).assign(cogs=5000.0)
    for old in (directory / "g_funda").glob("*.parquet"):
        old.unlink()
    funda.to_parquet(directory / "g_funda" / "g_funda_000.parquet", index=False)

    import duckdb
    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    wrds.ingest(con, directory)
    rows = dict(con.execute(
        "SELECT xbrl_tag, value FROM fundamentals f JOIN securities s USING (cik) "
        "WHERE s.ticker = 'DMA.DEU' AND period_end = DATE '2025-12-31' "
        "AND metric IN ('net_income', 'gross_profit', 'shares_outstanding')").fetchall())
    assert rows["compustat:nicon"] == pytest.approx(600e6)
    assert rows["compustat:sale-cogs"] == pytest.approx(3000e6)
    assert rows["compustat:cshoi"] == pytest.approx(1000e6)
    assert len(set(wrds.WANTED["g_funda"])) == len(wrds.WANTED["g_funda"])


def test_the_user_name_is_sent_in_lower_case(monkeypatch):
    import psycopg2

    seen = {}
    monkeypatch.setattr(psycopg2, "connect", lambda **kw: seen.update(kw) or "conn")
    assert wrds.connect(" MPFriedrich ", "x") == "conn"
    assert seen["user"] == "mpfriedrich"
