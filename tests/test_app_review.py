"""The app on fixture stores: the faults of the independent review of 03.10.2026.

Same harness as test_app.py (tests/app_fixture.py, network blocked). Each test names
its finding (F1 … F13, PLAN.md → „Gegencheck 03.10.2026“) and fails on f8d2c8f.
"""
import shutil

import duckdb
import pytest

from portfolio_lab import command, layout
from tests.app_fixture import build_data, errors, run, texts


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return build_data(tmp_path_factory.mktemp("appdata") / "data")


def _store(path):
    from portfolio_lab.data.schema import open_user
    from portfolio_lab.store import Store

    return Store(open_user(path / "user.duckdb"))


@pytest.fixture()
def fresh(data, tmp_path):
    """A private copy of the demo data with one portfolio, 'Demo' (as in test_app)."""
    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    store = _store(copy)
    store.create("Demo")
    store.con.close()
    return copy


def _add_security(path, ticker: str, name: str, cik: int = 1000000077) -> None:
    """A line in a copy of the store, swapped in the way a build does it."""
    work = path / "next.duckdb"
    shutil.copy(path / "market.duckdb", work)
    con = duckdb.connect(str(work))
    con.execute("INSERT INTO securities (cik, ticker, name, exchange) VALUES (?, ?, ?, ?)",
                [cik, ticker, name, "NYSE"])
    con.close()
    work.replace(path / "market.duckdb")


# ------------------------------------------------------------------- the command line
def test_go_right_after_enter_keeps_the_answer(monkeypatch, fresh):
    """F9: Enter ran the command, then GO ran an empty line over it."""
    at = run(monkeypatch, fresh)
    at.text_input(key="cmd_input").input("AAA US FA")
    at.button(key="cmd_go").click()
    at.run()
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert command.current(at.session_state) == "AAA"
    assert "Type a symbol" not in at.session_state["cmd_msg"]
    at.button(key="cmd_go").click().run()          # GO on an empty line: nothing
    assert at.session_state["cmd_msg"].startswith("AAA FA")


def test_the_first_command_after_a_rebuild_runs(monkeypatch, fresh):
    """F10: the swap check's st.rerun() inside the callback dropped the command."""
    at = run(monkeypatch, fresh)
    _add_security(fresh, "ZZZ", "Zeta Holdings")
    at.text_input(key="cmd_input").input("BBB FA").run()
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert command.current(at.session_state) == "BBB"
    assert at.session_state[layout.section_key(layout.SECURITY)] == "FINANCIALS"
    at.text_input(key="cmd_input").input("ZZZ").run()   # the new build is the one read
    assert command.current(at.session_state) == "ZZZ"


def test_picking_a_ticker_that_is_a_function_code_loads_the_company(monkeypatch, fresh):
    """F11: the pick was parsed again, and the share MA opened precedent deals."""
    _add_security(fresh, "MA", "Mastercard Incorporated")
    at = run(monkeypatch, fresh, state={"cmd_choices": (["AAA", "MA"], None)})
    at.button(key="cand_MA").click().run()
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert command.current(at.session_state) == "MA"
    assert at.session_state[layout.section_key(layout.SECURITY)] == "OVERVIEW"
    assert at.session_state["cmd_focus"] == "DES"


def test_back_clears_the_match_buttons(monkeypatch, fresh):
    """F12c: the 'several matches' buttons stayed under the security one went back to."""
    at = run(monkeypatch, fresh, state={command.HISTORY_KEY: ["AAA", "BBB"],
                                        "cmd_choices": (["CCC", "DDD"], None)})
    assert "cand_CCC" in [b.key for b in at.button]
    at.button(key="cmd_back").click().run()
    assert command.current(at.session_state) == "AAA"
    assert "cand_CCC" not in [b.key for b in at.button]


def test_a_price_only_symbol_says_what_it_does_not_have(monkeypatch, fresh):
    """F12f, wired: GC=F FA named "Financial statements" over a gold chart."""
    at = run(monkeypatch, fresh)
    at.text_input(key="cmd_input").input("GC=F FA").run()
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert "not available for GC=F" in texts(at)


# --------------------------------------------------------------- options builder
def _legs(path, *underlyings):
    store = _store(path)
    pid = store.list_portfolios().id.iloc[0]
    for u in underlyings:
        store.add_leg(pid, u, "share", 0.0, "2030-01-18", 1, 10.0, 0.0)
    store.con.close()


def _closes(path):
    con = duckdb.connect(str(path / "market.duckdb"), read_only=True)
    out = {t: con.execute("SELECT close FROM prices WHERE ticker = ? ORDER BY date "
                          "DESC LIMIT 1", [t]).fetchone()[0] for t in ("AAA", "BBB")}
    con.close()
    return out


def _fake_chain(monkeypatch):
    """A listed chain without the network: one expiry, a call either side of spot."""
    import pandas as pd

    from portfolio_lab import appcache

    def chain(symbol, expiry, spot, *args):
        return pd.DataFrame({"kind": ["call", "call"], "strike": [spot * 0.9, spot * 1.1],
                             "mid": [2.0, 1.0], "iv": [0.3, 0.3],
                             "moneyness": [0.9, 1.1]})

    monkeypatch.setattr(appcache, "expiries", lambda symbol: ["2030-01-18"])
    monkeypatch.setattr(appcache, "chain", chain)


def _options_of(symbol):
    return {command.HISTORY_KEY: [symbol], "cmd_focus": "OMON",
            layout.MAIN_KEY: layout.SECURITY,
            layout.section_key(layout.SECURITY): "OPTIONS"}


def test_a_leg_on_a_second_underlying_is_refused(monkeypatch, fresh):
    """F1: "Add leg" put a BBB call next to AAA shares and valued both at AAA."""
    _legs(fresh, "AAA")
    _fake_chain(monkeypatch)
    at = run(monkeypatch, fresh, state=_options_of("BBB"))
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert at.button(key="opt_b_add").disabled
    assert "The saved position is on AAA" in texts(at)


def test_legs_on_two_underlyings_are_each_valued_at_their_own_price(monkeypatch, fresh):
    """F1: a position saved with legs on AAA and BBB was valued at AAA's price only,
    and the table did not say which leg was on which share."""
    _legs(fresh, "AAA", "BBB")
    close = _closes(fresh)
    at = run(monkeypatch, fresh, state=_options_of("AAA"))
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    shown = texts(at)
    assert "underlying" in shown and "on 2 underlyings" in shown
    assert f"AAA at {close['AAA']:,.2f}" in shown
    at.segmented_control(key="opt_b_value_on").set_value("BBB").run()
    assert f"BBB at {close['BBB']:,.2f}" in texts(at)


def test_clearing_the_position_asks_first(monkeypatch, fresh):
    """F12d: "Clear position" deleted every saved leg on one click."""
    _legs(fresh, "AAA")
    at = run(monkeypatch, fresh, state=_options_of("AAA"))
    assert "Clear position" not in [b.label for b in at.button]
    at.button(key="opt_b_clear").click().run()            # inside the popover
    store = _store(fresh)
    try:
        assert store.legs(store.list_portfolios().id.iloc[0]).empty
    finally:
        store.con.close()


def test_presets_ask_before_replacing_saved_legs(monkeypatch, fresh):
    """F12d: a preset button cleared the saved legs on one click."""
    _legs(fresh, "AAA")
    _fake_chain(monkeypatch)
    at = run(monkeypatch, fresh, state=_options_of("AAA"))
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert "Presets — replace the 1 saved legs" in _popovers(at)
    assert "preset_0" not in _keys_outside_popovers(at)


def test_the_price_table_shows_the_traded_close_apart(monkeypatch, fresh):
    """F7, wired: HP labels the traded close as its own column."""
    at = run(monkeypatch, fresh, state={command.HISTORY_KEY: ["AAA"], "cmd_focus": "HP",
                                        layout.MAIN_KEY: layout.SECURITY})
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    shown = texts(at)
    assert "close as traded" in shown and "adjusted for splits and dividends" in shown


def test_the_map_layer_and_the_rule_inputs_survive_another_tab(monkeypatch, fresh):
    """F12e: a lazy tab is not drawn, and Streamlit drops an undrawn widget's state —
    the map came back on SHIPS and the rule on the basket after any other tab."""
    import datetime as dt

    def on(tab):
        # AppTest sends the tab row's last drawn value with every run; say it each time.
        at.session_state[layout.MAIN_KEY] = tab
        at.session_state[layout.section_key(layout.SCREEN)] = "RULE"
        return at

    def find(kind, label):
        return next(w for w in getattr(at, kind) if w.label == label)

    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.WORLD})
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    find("radio", "Layer").set_value("PORT CARGO")
    on(layout.WORLD).run()
    on(layout.SCREEN).run()
    find("radio", "Test the rule on").set_value("Single stock")
    on(layout.SCREEN).run()
    find("text_input", "Ticker").input("AAA")
    on(layout.SCREEN).run()
    find("date_input", "From").set_value(dt.date(2015, 6, 1))
    on(layout.SCREEN).run()
    on(layout.MARKETS).run()
    on(layout.SCREEN).run()
    assert find("radio", "Test the rule on").value == "Single stock"
    assert find("text_input", "Ticker").value == "AAA"
    assert find("date_input", "From").value == dt.date(2015, 6, 1)
    on(layout.WORLD).run()
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert find("radio", "Layer").value == "PORT CARGO"


# ------------------------------------------- Yahoo not reached is not "no data" (F6)
def test_an_unreachable_yahoo_is_not_shown_as_no_data(monkeypatch, fresh):
    """F6, app side. The fixture fakes yfinance as it behaves offline (an empty frame,
    dividends None); appcache raises YahooUnreachable, and each view says "could not be
    reached" instead of "no price history" / nothing at all."""
    at = run(monkeypatch, fresh, state={command.HISTORY_KEY: ["SPY"], "cmd_focus": "HP",
                                        layout.MAIN_KEY: layout.SECURITY})
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    shown = texts(at)
    assert "Yahoo could not be reached" in shown            # the page and HP
    assert "No price history for SPY" not in shown
    assert "Nothing matches" not in shown

    at = run(monkeypatch, fresh, state={command.HISTORY_KEY: ["AAA"],
                                        layout.MAIN_KEY: layout.SECURITY,
                                        layout.section_key(layout.SECURITY): "FINANCIALS"})
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert "Dividend history could not be reached" in texts(at)


def test_a_rule_on_an_unreachable_yahoo_symbol_says_so(monkeypatch, fresh):
    """F6: the rule on a Yahoo-only ticker reported "no overlapping price history"."""
    at = run(monkeypatch, fresh, state={layout.MAIN_KEY: layout.SCREEN,
                                        layout.section_key(layout.SCREEN): "RULE"})
    next(r for r in at.radio if r.label == "Test the rule on").set_value("Single stock")
    at.session_state[layout.MAIN_KEY] = layout.SCREEN
    at.run()
    next(t for t in at.text_input if t.label == "Ticker").input("SPY")
    at.session_state[layout.MAIN_KEY] = layout.SCREEN
    at.run()
    next(b for b in at.button if b.label == "Run rule").click()
    at.session_state[layout.MAIN_KEY] = layout.SCREEN
    at.run()
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    shown = texts(at)
    assert "Yahoo could not be reached" in shown
    assert "No overlapping price history" not in shown


def _blocks(node, inside=False):
    """(node, inside a popover?) for every element under `node`."""
    here = inside or getattr(node, "type", None) == "popover"
    yield node, here
    for child in getattr(node, "children", {}).values():
        yield from _blocks(child, here)


def _popovers(at) -> list[str]:
    return [n.proto.popover.label for n, _ in _blocks(at._tree)
            if getattr(n, "type", None) == "popover"]


def _keys_outside_popovers(at) -> set:
    return {getattr(n, "key", None) for n, inside in _blocks(at._tree) if not inside}


# ------------------------------------------- a security without filings, or none
def test_the_builder_and_saved_legs_without_a_loaded_security(monkeypatch, fresh):
    """F2: with nothing loaded the builder and the saved legs were unreachable."""
    _legs(fresh, "AAA")
    close = _closes(fresh)
    at = run(monkeypatch, fresh, state={"cmd_focus": "OMON",
                                        layout.MAIN_KEY: layout.SECURITY})
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    assert f"AAA at {close['AAA']:,.2f}" in texts(at)


def test_a_price_only_symbol_has_options_deals_and_rates(monkeypatch, fresh):
    """F2, F4, F5: GC=F (and SPY, an index) opened a page without the option builder,
    precedent deals or the rate and event measures."""
    _legs(fresh, "AAA")
    for focus, expect in (("OMON", "Build an options position"),
                          ("HRA", "What it does when rates move"),
                          ("MA", "deal_target")):
        at = run(monkeypatch, fresh, state={command.HISTORY_KEY: ["GC=F"],
                                            "cmd_focus": focus,
                                            layout.MAIN_KEY: layout.SECURITY})
        assert not at.exception, (focus, at.exception)
        assert errors(at) == [], errors(at)
        shown = texts(at) + " ".join(str(t.key) for t in at.text_input)
        assert expect in shown, focus


def test_a_yahoo_only_symbol_is_measured_against_rates(monkeypatch, fresh):
    """F5: SPY is not stored; its rate and event measures use the fetched bars."""
    import numpy as np
    import pandas as pd

    from portfolio_lab import appcache

    con = duckdb.connect(str(fresh / "market.duckdb"), read_only=True)
    days = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM prices WHERE ticker = '^TNX' ORDER BY date").fetchall()]
    con.close()
    close = 400 * np.cumprod(np.r_[1.0, 1 + 0.01 * np.sin(np.arange(1, len(days)))])
    bars = pd.DataFrame({"ticker": "SPY", "date": days, "open": close, "high": close,
                         "low": close, "close": close, "close_adj": close,
                         "volume": 1e6, "open_adj": close})
    monkeypatch.setattr(appcache, "yahoo_bars", lambda symbol: bars)
    at = run(monkeypatch, fresh, state={command.HISTORY_KEY: ["SPY"],
                                        "cmd_focus": "HRA",
                                        layout.MAIN_KEY: layout.SECURITY})
    assert not at.exception, at.exception
    assert errors(at) == [], errors(at)
    shown = texts(at)
    assert "What it does when rates move" in shown
    assert "No price history for SPY" not in shown
