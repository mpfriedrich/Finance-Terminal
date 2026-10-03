"""The Streamlit feature the command line is built on, proven before relying on it.

Streamlit 1.64 gives st.tabs a key, an on_change and a `.open` flag. The command line
needs two things from it: a callback that sets the key selects that tab on the same
rerun, and a tab that is not selected does not run its body (lazy). If an upgrade
breaks either, this fails before the app silently stops jumping to the right tab.
Fallback, should it ever be needed: a segmented_control as the tab bar (audit
02.10.2026).
"""
from streamlit.testing.v1 import AppTest


def _script():
    import streamlit as st

    def jump():
        if st.session_state.get("cmd") == "B":
            st.session_state["main"] = "B"
            st.session_state["sub"] = "Y"

    st.text_input("cmd", key="cmd", on_change=jump)
    for name, tab in zip("ABC", st.tabs(["A", "B", "C"], key="main", on_change="rerun")):
        with tab:
            if tab.open:
                st.write(f"body {name}")
                if name == "B":
                    for sub, inner in zip("XY", st.tabs(["X", "Y"], key="sub",
                                                         on_change="rerun")):
                        with inner:
                            if inner.open:
                                st.write(f"body {sub}")


def _bodies(at):
    return [m.value for m in at.markdown if str(m.value).startswith("body")]


def test_only_the_open_tab_runs():
    at = AppTest.from_function(_script).run()
    assert _bodies(at) == ["body A"]


def test_a_callback_selects_a_tab_and_a_sub_tab():
    at = AppTest.from_function(_script).run()
    at.text_input(key="cmd").input("B").run()
    assert at.session_state["main"] == "B"
    assert _bodies(at) == ["body B", "body Y"]
