"""The app itself, run headless on fixture stores (tests/app_fixture.py).

Every network call is blocked; the app must still draw every view, say what it could
not reach, and keep views that do not need a portfolio working without one. Each test
below names the fault it guards against (audit 02.10.2026); each fails on main@b1cbaa4.
"""
import shutil

import pytest

from portfolio_lab import layout
from tests.app_fixture import build_data, run, texts


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return build_data(tmp_path_factory.mktemp("appdata") / "data")


def _store(path):
    from portfolio_lab.data.schema import open_user
    from portfolio_lab.store import Store

    return Store(open_user(path / "user.duckdb"))


@pytest.fixture()
def fresh(data, tmp_path):
    """A private copy of the demo data with one portfolio, 'Demo'."""
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    store = _store(copy)
    store.create("Demo")
    store.con.close()
    return copy


def _errors(at):
    return [str(e.value) for e in at.error if "could not be drawn" in str(e.value)]


def test_every_tab_and_section_draws_on_the_fixture_store(monkeypatch, fresh):
    from portfolio_lab import command

    at = run(monkeypatch, fresh)
    assert not at.exception, at.exception
    at.session_state["sec"] = command.Security("AAA", "store")
    for tab in layout.TAB_LABELS:
        for section in layout.SECTIONS[tab] or [None]:
            at.session_state[layout.MAIN_KEY] = tab
            if section:
                at.session_state[layout.section_key(tab)] = section
            at.run()
            assert not at.exception, (tab, section, at.exception)
            assert not _errors(at), (tab, section, _errors(at))
    assert "places no orders" in texts(at).lower()


def test_the_app_draws_exactly_the_layout_tabs():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "command.TAB_LABELS" in source and "command.SECTIONS[tab]" in source


def test_without_a_portfolio_the_market_views_still_draw(monkeypatch, data, tmp_path):
    """Until 02.10.2026 a missing portfolio ended the script before any tab existed."""
    empty = tmp_path / "data"
    shutil.copytree(data, empty, ignore=shutil.ignore_patterns("user.duckdb*"))
    at = run(monkeypatch, empty, state={layout.MAIN_KEY: layout.MARKETS})
    assert not at.exception
    assert "Americas" in texts(at)


def test_a_failing_view_does_not_take_the_page_down(monkeypatch, fresh):
    from portfolio_lab import world

    def broken(*args, **kwargs):
        raise RuntimeError("boom at https://example.invalid/?token=abc")

    monkeypatch.setattr(world, "monitor", broken)
    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.MARKETS})
    assert not at.exception
    shown = texts(at)
    assert "could not be drawn" in shown and "RuntimeError" in shown
    assert "example.invalid" not in shown and "abc" not in shown
    assert "places no orders" in shown.lower()       # the footer after it still ran


def test_closed_tabs_do_not_run(monkeypatch, fresh):
    """Lazy tabs: the map asked OpenSky on every rerun of any tab (~100 calls a day)."""
    from portfolio_lab import aviation

    calls = []
    monkeypatch.setattr(aviation, "fetch", lambda *a, **k: calls.append(1))
    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.MARKETS})
    assert not at.exception and calls == []


def test_workbooks_are_built_only_when_clicked(monkeypatch, fresh):
    from portfolio_lab import export

    built = []
    real = export.workbook
    monkeypatch.setattr(export, "workbook", lambda *a, **k: built.append(1) or real(*a, **k))
    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.SCREEN})
    assert not at.exception
    assert built == []


def test_the_option_position_is_valued_at_its_own_underlying(monkeypatch, fresh):
    """The builder valued saved legs at the price of whatever was TYPED, and raised a
    TypeError when that symbol had no stored close."""
    import duckdb

    from portfolio_lab import command

    store = _store(fresh)
    pid = store.list_portfolios().id.iloc[0]
    store.add_leg(pid, "AAA", "share", 0.0, "2030-01-18", 1, 10.0, 0.0)
    store.con.close()
    con = duckdb.connect(str(fresh / "market.duckdb"), read_only=True)
    aaa, bbb = (con.execute("SELECT close FROM prices WHERE ticker = ? ORDER BY date "
                            "DESC LIMIT 1", [t]).fetchone()[0] for t in ("AAA", "BBB"))
    con.close()
    at = run(monkeypatch, fresh, state={
        "sec": command.Security("BBB", "store"), "cmd_focus": "OMON",
        layout.MAIN_KEY: layout.SECURITY,
        layout.section_key(layout.SECURITY): "OPTIONS"})
    assert not at.exception, at.exception
    shown = texts(at)
    assert f"AAA at {aaa:,.2f}" in shown
    assert f"AAA at {bbb:,.2f}" not in shown


def test_the_watchlist_feeds_the_calendar(monkeypatch, fresh):
    """_watched() left the watchlist out; the calendar defaulted to AAPL, MSFT, NVDA."""
    store = _store(fresh)
    store.watch("EEE", "")
    store.con.close()
    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.EVENTS,
                                        layout.section_key(layout.EVENTS): "CALENDAR"})
    assert not at.exception
    values = [t.value for t in at.text_input]
    assert any("EEE" in v for v in values), values
    assert not any("AAPL" in v for v in values)


def test_one_set_of_intrinsic_value_assumptions(monkeypatch, fresh):
    """The DCF started at 3 % growth while the screen assumed 0 %: two answers."""
    from portfolio_lab import command

    at = run(monkeypatch, fresh, state={
        "iv_grow": 5, "iv_term": 1.5, "sec": command.Security("AAA", "store"),
        layout.MAIN_KEY: layout.SECURITY,
        layout.section_key(layout.SECURITY): "VALUATION"})
    assert not at.exception, at.exception
    growth = [n.value for n in at.number_input if n.key and n.key.startswith("g_")]
    forever = [n.value for n in at.number_input if n.key and n.key.startswith("tg_")]
    assert growth == [5.0] and forever == [1.5]


def test_deleting_a_portfolio_asks_first(monkeypatch, fresh):
    at = run(monkeypatch, fresh)
    assert "Delete portfolio" not in [b.label for b in at.button]
    assert "delete_portfolio" in [b.key for b in at.button]     # inside the popover
    at.button(key="delete_portfolio").click().run()
    assert not at.exception
    assert not _store_has(fresh)


def _store_has(path) -> bool:
    store = _store(path)
    try:
        return not store.list_portfolios().empty
    finally:
        store.con.close()


def test_a_replaced_store_is_read_afresh(monkeypatch, fresh):
    """A rebuild swaps the file in; the market list stayed cached for an hour."""
    import duckdb

    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.SCREEN})
    assert "Markets" not in [m.label for m in at.multiselect]
    work = fresh / "next.duckdb"
    shutil.copy(fresh / "market.duckdb", work)
    con = duckdb.connect(str(work))
    con.execute("INSERT INTO securities (cik, ticker, name, exchange) VALUES "
                "(1000000007, 'DMA.DEU', 'Deutsche Maschinen AG', 'DEU')")
    con.close()
    work.replace(fresh / "market.duckdb")
    at.run()
    at.run()
    assert "Markets" in [m.label for m in at.multiselect]


def test_a_deep_link_runs_its_command(monkeypatch, fresh):
    at = run(monkeypatch, fresh, query={"cmd": "EQS"})
    assert not at.exception
    assert at.session_state[layout.MAIN_KEY] == layout.SCREEN
    assert at.session_state[layout.section_key(layout.SCREEN)] == "SCREEN"


def test_the_command_line_loads_a_security_and_opens_its_section(monkeypatch, fresh):
    at = run(monkeypatch, fresh)
    at.text_input(key="cmd_input").input("AAA US FA").run()
    assert not at.exception, at.exception
    assert at.session_state["sec"].symbol == "AAA"
    assert at.session_state[layout.MAIN_KEY] == layout.SECURITY
    assert at.session_state[layout.section_key(layout.SECURITY)] == "FINANCIALS"
    at.text_input(key="cmd_input").input("BBB").run()
    at.button(key="cmd_back").click().run()
    assert at.session_state["sec"].symbol == "AAA"
