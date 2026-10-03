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


# What two function codes open, worded as the sections now show it. command.py still
# calls SPLC "Commodity and rate exposure" and HRA "Historical regression against
# markets" from before the sections moved: SPLC is the 10-K's suppliers and customers,
# HRA the commodity and yield regressions (review 03.10.2026, F12a). The command line's
# answer and the Functions list read these.
LABELS = {
    "SPLC": "Suppliers and customers named in the 10-K",
    "HRA": "What it moved with: commodities, rates, one event",
}

# The functions the page of a security without filings offers — an index, a commodity,
# crypto or a Yahoo-only symbol: its description and price (DES, GP, HP), what it moved
# with (HRA), the option position builder (OMON) and precedent deals (MA).
PRICE_ONLY_FUNCTIONS = frozenset({"DES", "GP", "HP", "HRA", "OMON", "MA"})


def status_line(message: str, code: str, label: str, symbol: str | None,
                equity: bool, tab: str | None = SECURITY) -> str:
    """The command line's answer to a command that opens `tab`, as shown under it.

    `message` and `label` come from command.py; `equity` says whether `symbol` is a
    company with filings in the store. On a price-only symbol a company function (FA,
    DCF, ANR …) is not there to open, and the answer used to name it anyway —
    "GC=F FA: Financial statements." over a gold chart (F12f)."""
    if (tab == SECURITY and symbol and not equity
            and code not in PRICE_ONLY_FUNCTIONS):
        return (f"{symbol} {code}: not available for {symbol}, which has prices only — "
                "no filings or company data behind it. Its price page is shown.")
    better = LABELS.get(code)
    return message.replace(label, better) if better else message
