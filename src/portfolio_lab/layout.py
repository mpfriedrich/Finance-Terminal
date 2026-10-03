"""Names of the app's tabs and of their sections, in one place (no Streamlit).

app.py builds its tabs from these and command.py routes function codes onto them, so a
renamed tab cannot silently break the command bar: tests/test_command.py checks every
routing target against these tuples. Thirteen tabs became six on 02.10.2026 (PLAN.md →
„Audit Bloomberg-Annäherung 02.10.2026“).
"""

TAB_LABELS = ("SECURITY", "SCREEN & BACKTEST", "PORTFOLIOS", "MARKETS & MACRO",
              "WORLD & LOGISTICS", "EVENTS & NEWS")
SECURITY_SECTIONS = ("OVERVIEW", "FINANCIALS", "VALUATION", "OPTIONS", "EXPOSURE",
                     "ANALYSTS", "OWNERSHIP", "NEWS & FILINGS")

SECURITY, SCREEN, PORTFOLIOS, MARKETS, WORLD, EVENTS = TAB_LABELS

# The other tabs' own sections. The command line opens the tab (command.py); app.py
# then picks the section a function belongs to (SUB_SECTION below).
SECTIONS: dict[str, tuple[str, ...]] = {
    SECURITY: SECURITY_SECTIONS,
    SCREEN: ("SCREEN", "BACKTEST", "RULE", "SAVINGS PLAN"),
    PORTFOLIOS: ("BOOKS", "TRACKING", "WATCHLIST & ALERTS", "COMMODITY LINKS"),
    MARKETS: ("BOARDS", "COMPARE", "ECONOMY", "RATES"),
    WORLD: (),
    EVENTS: ("BRIEFING", "CALENDAR", "FILING SEARCH"),
}
# Function codes whose tab has sections of its own -> the section they open.
SUB_SECTION = {
    "EQS": "SCREEN", "BTST": "BACKTEST", "PORT": "BOOKS", "ALRT": "WATCHLIST & ALERTS",
    "WEI": "BOARDS", "GLCO": "BOARDS", "CORR": "COMPARE", "ECST": "ECONOMY",
    "GC": "RATES", "WIRP": "RATES", "TOP": "BRIEFING", "EVTS": "CALENDAR",
}
MAIN_KEY = "main_tab"


def section_key(tab: str) -> str:
    """The session key of a tab's own row of sections ("sub_security", …)."""
    out = "".join(ch if ch.isalpha() else "_" for ch in tab.lower())
    while "__" in out:
        out = out.replace("__", "_")
    return "sub_" + out.strip("_")
