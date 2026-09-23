"""Industry classification: what counts as a company, and what only looks like one."""
import duckdb
import pytest

from portfolio_lab.data import sic
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    rows = [
        (1, "AAPL", "Apple Inc.", "Electronic computers", "3571", "NASDAQ"),
        (2, "DLR", "Digital Realty Trust", "REIT", "6798", "NYSE"),
        (3, "PPLT", "abrdn Platinum ETF Trust", "Commodity contracts", "6221", "NYSE"),
        (4, "XYZ", "Iron Horse Acquisition Corp", "Blank checks", "6770", "NASDAQ"),
        (5, "CEF", "Some Closed End Fund", "Investment offices", "6726", "NYSE"),
        (6, "ABS", "Receivables Trust 2019-A", "Asset-backed", "6189", "NYSE"),
    ]
    c.executemany(
        "INSERT INTO securities (cik, ticker, name, sector, sic, exchange) VALUES (?,?,?,?,?,?)",
        rows,
    )
    return c


def test_reits_are_kept(con):
    """A REIT owns buildings and collects rent. Book value means something for it."""
    sic.drop_non_operating(con)
    kept = {r[0] for r in con.execute("SELECT ticker FROM securities").fetchall()}
    assert "DLR" in kept


def test_funds_spacs_and_securitisations_are_removed(con):
    removed = sic.drop_non_operating(con)
    assert removed == 4
    kept = {r[0] for r in con.execute("SELECT ticker FROM securities").fetchall()}
    assert kept == {"AAPL", "DLR"}


def test_every_excluded_code_is_documented(con):
    """No silent exclusions: each code carries a reason a user could read."""
    for code, reason in sic.NON_OPERATING_SIC.items():
        assert code.isdigit() and len(code) == 4
        assert reason and not reason.isupper()


def test_the_reit_code_is_not_on_the_exclusion_list():
    assert "6798" not in sic.NON_OPERATING_SIC
