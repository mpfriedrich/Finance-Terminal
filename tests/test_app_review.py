"""The app on fixture stores: the faults of the independent review of 03.10.2026.

Same harness as test_app.py (tests/app_fixture.py, network blocked). Each test names
its finding (F1 … F13, PLAN.md → „Gegencheck 03.10.2026“) and fails on f8d2c8f.
"""
import shutil

import duckdb

from portfolio_lab import command, layout
from tests.app_fixture import run, texts
from tests.test_app import _store, data, fresh  # noqa: F401  (fixtures)


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
    assert "not available for GC=F" in texts(at)
