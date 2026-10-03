"""The app's tabs and their sections, as plain constants (no Streamlit).

app.py draws exactly these, and the command line (command.py) maps its function codes
onto them, so a renamed tab breaks a test instead of a jump. Thirteen tabs became six
on 02.10.2026 (PLAN.md → „Audit Bloomberg-Annäherung 02.10.2026“).
"""

SECURITY = "SECURITY"
SCREEN = "SCREEN & BACKTEST"
PORTFOLIOS = "PORTFOLIOS"
MARKETS = "MARKETS & MACRO"
WORLD = "WORLD & LOGISTICS"
EVENTS = "EVENTS & NEWS"
TAB_LABELS = [SECURITY, SCREEN, PORTFOLIOS, MARKETS, WORLD, EVENTS]

# The price table (HP) is part of OVERVIEW; the counterparties read out of the 10-K
# (SPLC) are part of NEWS & FILINGS, next to the filing they come from.
SECURITY_SECTIONS = ["OVERVIEW", "FINANCIALS", "VALUATION", "OPTIONS", "EXPOSURE",
                     "ANALYSTS", "OWNERSHIP", "NEWS & FILINGS"]
SCREEN_SECTIONS = ["SCREEN", "BACKTEST", "RULE", "SAVINGS PLAN"]
PORTFOLIO_SECTIONS = ["BOOKS", "TRACKING", "WATCHLIST & ALERTS", "COMMODITY LINKS"]
MARKET_SECTIONS = ["BOARDS", "COMPARE", "ECONOMY", "RATES"]
EVENT_SECTIONS = ["BRIEFING", "CALENDAR", "FILING SEARCH"]

SECTIONS: dict[str, list[str]] = {
    SECURITY: SECURITY_SECTIONS, SCREEN: SCREEN_SECTIONS, PORTFOLIOS: PORTFOLIO_SECTIONS,
    MARKETS: MARKET_SECTIONS, WORLD: [], EVENTS: EVENT_SECTIONS,
}
MAIN_KEY = "main_tab"


def section_key(tab: str) -> str:
    """The session key of a tab's own row of sections ("sub_security", …)."""
    out = "".join(ch if ch.isalpha() else "_" for ch in tab.lower())
    while "__" in out:
        out = out.replace("__", "_")
    return "sub_" + out.strip("_")
