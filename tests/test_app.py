"""The app itself, run headless on fixture stores (tests/app_fixture.py).

Every network call is blocked; the app must still draw every view, say what it could
not reach, and keep views that do not need a portfolio working without one.
"""
import pytest

from tests.app_fixture import build_data, run, texts


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return build_data(tmp_path_factory.mktemp("appdata") / "data")


@pytest.fixture()
def with_portfolio(data):
    from portfolio_lab.data.schema import open_user
    from portfolio_lab.store import Store

    con = open_user(data / "user.duckdb")
    store = Store(con)
    if store.list_portfolios().empty:
        store.create("Demo")
    con.close()
    return data


# The tab labels; step D of the restructuring replaces these with command.TAB_LABELS.
TAB_LABELS = ["SCREEN", "MONITOR", "RESEARCH", "OPTIONS", "STRATEGY", "LAB", "BOOKS",
              "DEALS", "MACRO", "COMMODITIES", "MAP", "CALENDAR", "NEWS"]
MARKETS = "MONITOR"


def test_the_first_run_draws_without_an_exception(monkeypatch, with_portfolio):
    at = run(monkeypatch, with_portfolio)
    assert not at.exception, at.exception
    assert "places no orders" in texts(at).lower()


def test_every_tab_draws_on_the_fixture_store(monkeypatch, with_portfolio):
    at = run(monkeypatch, with_portfolio)
    for label in TAB_LABELS:
        at.session_state["main_tab"] = label
        at.run()
        assert not at.exception, (label, at.exception)
        errors = [e.value for e in at.error if "could not be drawn" in str(e.value)]
        assert not errors, (label, errors)


def test_without_a_portfolio_the_market_views_still_draw(monkeypatch, data, tmp_path):
    """Until 02.10.2026 a missing portfolio ended the script before any tab existed."""
    import shutil

    empty = tmp_path / "data"
    shutil.copytree(data, empty, ignore=shutil.ignore_patterns("user.duckdb*"))
    at = run(monkeypatch, empty, state={"main_tab": MARKETS})
    assert not at.exception
    assert "Americas" in texts(at)


def test_a_failing_view_does_not_take_the_page_down(monkeypatch, with_portfolio):
    from portfolio_lab import world

    def broken(*args, **kwargs):
        raise RuntimeError("boom at https://example.invalid/?token=abc")

    monkeypatch.setattr(world, "monitor", broken)
    at = run(monkeypatch, with_portfolio, state={"main_tab": MARKETS})
    assert not at.exception
    shown = texts(at)
    assert "could not be drawn" in shown and "RuntimeError" in shown
    assert "example.invalid" not in shown and "abc" not in shown
    assert "places no orders" in shown.lower()       # the footer after it still ran
