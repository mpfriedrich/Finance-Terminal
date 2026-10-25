"""The two butterfly books in the PORTFOLIOS view: drawn from the tracker's own store,
with the shared candidate and each book's decision; nothing else on the page changes."""
import shutil

import pytest

from portfolio_lab import filing_butterfly as fb
from tests.app_fixture import build_data, errors, run, texts
from tests.test_filing_butterfly import ENTRY, FILED, FakeLive, at


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return build_data(tmp_path_factory.mktemp("fbapp") / "data")


@pytest.fixture()
def fresh(data, tmp_path):
    from portfolio_lab.data.schema import open_user
    from portfolio_lab.store import Store

    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    store = Store(open_user(copy / "user.duckdb"))
    store.create("Demo")
    store.con.close()
    return copy


def _market():
    import duckdb
    import pandas as pd

    con = duckdb.connect()
    con.execute("CREATE TABLE securities (cik BIGINT, ticker VARCHAR)")
    con.execute("CREATE TABLE prices (ticker VARCHAR, date DATE, open DOUBLE, close DOUBLE, "
                "close_adj DOUBLE, close_raw DOUBLE, volume BIGINT)")
    con.execute("INSERT INTO securities VALUES (111, 'AAA')")
    days = pd.bdate_range(end="2026-10-20", periods=300).date
    con.executemany("INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
                    [("AAA", d, 100.0, 100.0, 100.0, 100.0, 1_000_000) for d in days])
    return con


def test_the_view_says_how_to_start_before_the_first_run(monkeypatch, fresh):
    app = run(monkeypatch, fresh, query={"cmd": "PORT"})
    assert not app.exception
    assert errors(app) == [], errors(app)
    body = texts(app)
    assert "SEC Filing Butterfly" in body and "butterfly_tracker.py" in body


def test_both_books_and_the_shared_candidate_are_shown(monkeypatch, fresh):
    path = fresh / "paper_options.duckdb"
    live, market = FakeLive(), _market()
    fb.run(path, market, live, now=at(FILED, 10), log=lambda *_: None)
    fb.run(path, market, live, now=at(ENTRY, 11), log=lambda *_: None)
    app = run(monkeypatch, fresh, query={"cmd": "PORT"})
    assert not app.exception
    assert errors(app) == [], errors(app)
    labels = "\n".join(e.label for e in app.expander)
    assert "SEC Filing Butterfly – 1.000 EUR · value 1,000.00 €" in labels
    assert "SEC Filing Butterfly – Vollbudget 100.000 USD" in labels
    frames = "\n".join(str(d.value) for d in app.dataframe)
    assert "Budget reicht nicht" in frames            # the small book's refusal
    assert "opened" in frames                         # the big book took it
    assert "AAA" in frames
    # the view only reads: the bookings are unchanged afterwards
    con = fb.connect(path, read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM fb_positions").fetchone()[0] == 1
    finally:
        con.close()
