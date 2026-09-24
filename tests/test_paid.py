"""Phase 10: the paid source behind the same seam, and what happens when it is not ready.

The data here is hand-made in the Sharadar layout (tests/paid_fixture.py); no
subscription exists. What these tests can prove is the TRANSLATION and the SWITCH, not
that the real service delivers what its documentation says.
"""
import datetime as dt

import pytest

from portfolio_lab.config import Settings
from portfolio_lab.data import paid, sources
from tests.paid_fixture import DELISTED_ON, RESTATED_AAA_EQUITY, build_store


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("sharadar"))


def test_a_fund_is_not_a_company(src):
    assert "ETF1" not in set(src.universe(dt.date(2026, 6, 30)).ticker)


def test_the_restated_dimension_is_never_loaded(src):
    """MR* is Sharadar's 'most recent' — restated — view. Loading it is look-ahead."""
    values = {r[0] for r in src.con.execute(
        "SELECT value FROM fundamentals WHERE metric = 'book_value'").fetchall()}
    assert RESTATED_AAA_EQUITY not in values


def test_capex_is_stored_as_a_positive_payment_like_the_sec_tag(src):
    lo = src.con.execute(
        "SELECT MIN(value) FROM fundamentals WHERE metric = 'capex'").fetchone()[0]
    assert lo > 0


def test_share_counts_are_restored_to_units(src):
    """BBB reports 400 thousand in units of 1,000."""
    frame = src.screening_frame(dt.date(2026, 6, 30)).set_index("ticker")
    assert frame.at["BBB", "shares_outstanding"] == 400_000_000


def test_quarters_carry_a_span_the_ttm_assembly_accepts(src):
    frame = src.screening_frame(dt.date(2026, 6, 30)).set_index("ticker")
    assert frame.at["AAA", "revenue_ttm"] == pytest.approx(1_000e6)


def test_a_delisted_company_is_in_the_universe_while_it_traded_and_not_after(src):
    assert "DDD1" in set(src.universe(DELISTED_ON - dt.timedelta(days=30)).ticker)
    assert "DDD1" not in set(src.universe(DELISTED_ON + dt.timedelta(days=60)).ticker)


def test_the_source_reports_itself_survivorship_free_because_it_measured_it(src):
    assert src.survivorship_free is True
    assert paid.delisted_count(src.con) == 1


# --- the switch ---------------------------------------------------------------------------

def test_the_default_is_the_free_source(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_LAB_SOURCE", raising=False)
    monkeypatch.setattr("portfolio_lab.config.load_env", lambda path=None: None)
    assert Settings().data_source == "free"


def test_paid_without_a_key_says_what_to_do_instead_of_crashing(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "secret", lambda name: None)
    settings = Settings(data_source="paid")
    with pytest.raises(paid.MissingKey) as err:
        sources.open_selected(settings, paid_db=tmp_path / "paid.duckdb")
    assert paid.KEY_NAME in str(err.value) and ".env.local" in str(err.value)


def test_paid_with_a_key_but_no_store_says_to_build_it(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "secret", lambda name: "not-a-real-key")
    with pytest.raises(sources.SourceNotReady) as err:
        sources.open_selected(Settings(data_source="paid"),
                              paid_db=tmp_path / "missing.duckdb")
    assert "build_paid.py" in str(err.value)
    assert "not-a-real-key" not in str(err.value)


def test_paid_with_key_and_store_opens_the_paid_source(monkeypatch, tmp_path):
    store = build_store(tmp_path)
    store.con.close()
    monkeypatch.setattr(sources, "secret", lambda name: "not-a-real-key")
    source, path = sources.open_selected(Settings(data_source="paid"),
                                         paid_db=tmp_path / "paid.duckdb")
    assert isinstance(source, paid.PaidSource) and path.name == "paid.duckdb"


def test_an_unknown_choice_is_named(monkeypatch):
    with pytest.raises(sources.SourceNotReady, match="sharadar"):
        sources.open_selected(Settings(data_source="sharadar"))
