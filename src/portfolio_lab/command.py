"""The command line: `AAPL US FA <GO>` — a security, a function, or both.

Pure: no Streamlit, no database. The app hands `resolve()` three small lookups and
gets back what to show; everything here can be tested with a dict.

Grammar (case does not matter, extra spaces neither):

    [security] [function] [GO | <GO>]
    security := SYMBOL [MARKET] [YELLOW KEY]

* SYMBOL keeps . - = ^ & : BRK-B, SAP.DEU, XYZ-12345, GC=F, ^GSPC, 000001.SS.
* MARKET is a Bloomberg exchange code (US GR GY LN JP JT FP NA SW IM HK AU CN KS) or an
  ISO-3 country (DEU, JPN); `SAP GR` is the Xetra line, SAP.DEU in the world store.
* A yellow key (EQUITY INDEX CURNCY COMDTY CORP GOVT) is accepted and ignored — the
  store already knows what a symbol is.
* One word is a FUNCTION first (`MA` opens precedent deals); a security with a market
  is never a function (`MA US` is Mastercard). With several words the last one is the
  function if it is one. A security without a function opens its description (DES).
* Several words that are not a symbol and a market are a name search (`american
  airlines`); one hit is taken, several are offered, none is never guessed.

Measured nothing here; it is a user interface. What it promises is tested in
tests/test_command.py: every function points at a tab and section that exist.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# ------------------------------------------------------------------- tabs, sections
from .layout import (EVENTS, MAIN_KEY, MARKETS, PORTFOLIOS, SCREEN, SECTIONS,  # noqa: E402,F401
                     SECURITY, TAB_LABELS, WORLD, section_key)


@dataclass(frozen=True)
class Function:
    code: str
    tab: str
    section: str | None
    label: str
    needs_security: bool = False


def _f(code, tab, section, label, needs=False):
    return code, Function(code, tab, section, label, needs)


FUNCTIONS: dict[str, Function] = dict([
    _f("DES", SECURITY, "OVERVIEW", "description, price, return and risk", True),
    _f("GP", SECURITY, "OVERVIEW", "price chart", True),
    _f("HP", SECURITY, "OVERVIEW", "historical price table", True),
    _f("FA", SECURITY, "FINANCIALS", "financials as first filed", True),
    _f("ERN", SECURITY, "FINANCIALS", "earnings and the next report", True),
    _f("RV", SECURITY, "VALUATION", "relative value: own history and peers", True),
    _f("COMP", SECURITY, "VALUATION", "trading comps", True),
    _f("WACC", SECURITY, "VALUATION", "cost of capital", True),
    _f("DCF", SECURITY, "VALUATION", "discounted cash flow", True),
    _f("OMON", SECURITY, "OPTIONS", "option chain and position builder", True),
    _f("OVDV", SECURITY, "OPTIONS", "volatility surface", True),
    _f("SKEW", SECURITY, "OPTIONS", "smile and put-call spread", True),
    _f("HRA", SECURITY, "EXPOSURE", "moves with commodities and rates", True),
    _f("ANR", SECURITY, "ANALYSTS", "analyst ratings, reported as data", True),
    _f("INS", SECURITY, "OWNERSHIP", "insider trades (Form 4)", True),
    _f("HDS", SECURITY, "OWNERSHIP", "institutional holders", True),
    _f("DVD", SECURITY, "OWNERSHIP", "dividends", True),
    _f("SPLC", SECURITY, "NEWS & FILINGS", "counterparties named in the 10-K", True),
    _f("CN", SECURITY, "NEWS & FILINGS", "company headlines", True),
    _f("CF", SECURITY, "NEWS & FILINGS", "company filings", True),
    _f("MA", SECURITY, "VALUATION", "precedent transactions"),
    _f("EQS", SCREEN, "SCREEN", "equity screen"),
    _f("BTST", SCREEN, "BACKTEST", "backtest: point in time, then buy and hold"),
    _f("RULE", SCREEN, "RULE", "a buy/sell rule on a stock or basket"),
    _f("PLAN", SCREEN, "SAVINGS PLAN", "when to invest: savings plans"),
    _f("PORT", PORTFOLIOS, "BOOKS", "paper books, side by side"),
    _f("TRAK", PORTFOLIOS, "TRACKING", "live tracking of the open portfolio"),
    _f("CMDY", PORTFOLIOS, "COMMODITY LINKS", "followed names against commodities"),
    _f("ALRT", PORTFOLIOS, "WATCHLIST & ALERTS", "watchlist and alerts"),
    _f("WEI", MARKETS, "BOARDS", "world indices, currencies, rates"),
    _f("GLCO", MARKETS, "BOARDS", "commodities board"),
    _f("CORR", MARKETS, "COMPARE", "compare anything, correlation"),
    _f("ECST", MARKETS, "ECONOMY", "the US economy (FRED)"),
    _f("GC", MARKETS, "RATES", "Treasury curve"),
    _f("WIRP", MARKETS, "RATES", "what fed funds futures price"),
    _f("BMAP", WORLD, None, "ships, ports, aircraft"),
    _f("TOP", EVENTS, "BRIEFING", "daily briefing and headlines"),
    _f("EVTS", EVENTS, "CALENDAR", "who reports next"),
    _f("FTS", EVENTS, "FILING SEARCH", "full-text search of SEC filings"),
])

# Bloomberg exchange codes -> ISO-3, the country code of the world store's tickers.
MARKETS_BBG = {
    "US": "USA", "GR": "DEU", "GY": "DEU", "LN": "GBR", "JP": "JPN", "JT": "JPN",
    "FP": "FRA", "NA": "NLD", "SW": "CHE", "IM": "ITA", "HK": "HKG", "AU": "AUS",
    "CN": "CAN", "KS": "KOR",
}
ISO3 = set(MARKETS_BBG.values()) | {
    "AUT", "BEL", "DNK", "ESP", "FIN", "IRL", "NOR", "PRT", "SWE", "SGP", "TWN", "CHN",
    "IND", "BRA", "MEX", "ZAF", "ISR", "NZL"}
# Yahoo suffix -> ISO-3: SAP.DE is tried as SAP.DEU in the store first.
YAHOO_SUFFIX = {
    "DE": "DEU", "F": "DEU", "L": "GBR", "T": "JPN", "PA": "FRA", "AS": "NLD",
    "SW": "CHE", "MI": "ITA", "HK": "HKG", "AX": "AUS", "TO": "CAN", "KS": "KOR",
}
_ISO_TO_YAHOO = {"DEU": "DE", "GBR": "L", "JPN": "T", "FRA": "PA", "NLD": "AS",
                 "CHE": "SW", "ITA": "MI", "HKG": "HK", "AUS": "AX", "CAN": "TO",
                 "KOR": "KS"}
YELLOW = {"EQUITY", "INDEX", "CURNCY", "COMDTY", "CORP", "GOVT", "MTGE", "MUNI", "PFD"}
GO = {"GO", "<GO>"}
ALIASES = {
    "SPX": "^GSPC", "INDU": "^DJI", "NDX": "^NDX", "DAX": "^GDAXI", "UKX": "^FTSE",
    "NKY": "^N225", "HSI": "^HSI", "AS51": "^AXJO", "CL1": "CL=F", "CO1": "BZ=F",
    "GC1": "GC=F", "XAU": "GC=F", "SI1": "SI=F", "HG1": "HG=F", "NG1": "NG=F",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "JPY=X", "AUDUSD": "AUDUSD=X",
    "XBT": "BTC-USD", "BTC": "BTC-USD", "ETH": "ETH-USD", "VIX": "^VIX",
}
_SYMBOL = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=&]{0,19}$")
MAX_NAME_WORDS = 5


@dataclass(frozen=True)
class Parsed:
    symbol: str | None = None        # as typed, aliases applied
    market: str | None = None        # ISO-3
    name: str | None = None          # a name search instead of a symbol
    function: str | None = None
    error: str | None = None


def parse(text: str) -> Parsed:
    """The words of one command, or a sentence saying why they are not one."""
    words = [w for w in str(text or "").upper().split() if w not in GO]
    words = [w for w in words if w not in YELLOW]
    if not words:
        return Parsed(error="Type a ticker, a function, or both — e.g. AAPL US FA.")
    function = None
    # `RY CN` is Royal Bank's Canadian line (CN the market), `RY CN CN` its news: with
    # exactly two words a market code wins over a function of the same name.
    if words[-1] in FUNCTIONS and not (len(words) == 2 and words[-1] in MARKETS_BBG):
        if len(words) == 1:
            return Parsed(function=words[0])
        function = words.pop()
    if len(words) == 1:
        symbol = words[0]
        if not _SYMBOL.match(symbol):
            return Parsed(error=f"'{symbol}' is not a ticker. Tickers keep letters, "
                                "digits and . - = ^ (BRK-B, SAP.DEU, GC=F, ^GSPC).")
        return Parsed(symbol=ALIASES.get(symbol, symbol), function=function)
    if len(words) == 2:
        market = words[1]
        if market in MARKETS_BBG or market in ISO3:
            if not _SYMBOL.match(words[0]):
                return Parsed(error=f"'{words[0]}' is not a ticker.")
            return Parsed(symbol=words[0], market=MARKETS_BBG.get(market, market),
                          function=function)
        # Bloomberg market codes are two letters: a two-letter word after a ticker is
        # a mistyped market, not the second half of a company name.
        if len(market) == 2 and market.isalpha() and _SYMBOL.match(words[0]):
            return Parsed(error=f"'{market}' is not a market code this knows. Use a "
                                "Bloomberg code (US, GR, LN, JP …) or ISO-3 (DEU, JPN).")
    if len(words) > MAX_NAME_WORDS:
        return Parsed(error=f"Too many words ({len(words)}). A command is a security, "
                            "a market and a function, e.g. SAP GR DES.")
    return Parsed(name=" ".join(words).lower(), function=function)


@dataclass(frozen=True)
class Resolution:
    symbol: str | None = None
    kind: str | None = None          # "store" | "price_only" | "yahoo"
    candidates: tuple[tuple[str, str], ...] = ()
    note: str | None = None
    error: str | None = None


def resolve(parsed: Parsed, in_store: Callable[[str], bool],
            price_only: Callable[[str], bool],
            find: Callable[[str], list[tuple[str, str]]]) -> Resolution:
    """Which security a parsed command means.

    `in_store(t)` says whether `t` is a listing in the store, `price_only(t)` whether it
    is a stored index/commodity/crypto series, `find(text)` returns (ticker, name) pairs
    for a name search. Order: exact listing; the market's line (TIC.ISO3); a Yahoo
    suffix tried as the store's line first; a stored price-only series; a name search;
    else the symbol goes to Yahoo, price only.
    """
    if parsed.error:
        return Resolution(error=parsed.error)
    if parsed.name:
        hits = list(find(parsed.name))[:12]
        if len(hits) == 1:
            return Resolution(symbol=hits[0][0], kind="store")
        if hits:
            return Resolution(candidates=tuple(hits),
                              note=f"{len(hits)} companies match '{parsed.name}'.")
        return Resolution(error=f"Nothing in the store is called '{parsed.name}'.")
    symbol = parsed.symbol
    if symbol is None:
        return Resolution()
    if parsed.market and parsed.market != "USA":
        line = f"{symbol}.{parsed.market}"
        if in_store(line):
            return Resolution(symbol=line, kind="store")
        suffix = _ISO_TO_YAHOO.get(parsed.market)
        yahoo = f"{symbol}.{suffix}" if suffix else symbol
        return Resolution(symbol=yahoo, kind="yahoo",
                          note=f"{line} is not in the store; {yahoo} from Yahoo, "
                               "price only.")
    if in_store(symbol):
        return Resolution(symbol=symbol, kind="store")
    if "." in symbol:
        base, _, suffix = symbol.rpartition(".")
        iso = YAHOO_SUFFIX.get(suffix)
        if iso and in_store(f"{base}.{iso}"):
            return Resolution(symbol=f"{base}.{iso}", kind="store")
    if price_only(symbol):
        return Resolution(symbol=symbol, kind="price_only")
    if parsed.market == "USA":
        return Resolution(error=f"{symbol} is not a US listing in the store.")
    hits = [h for h in find(symbol) if h[0] != symbol][:12]
    if len(hits) == 1 and len(symbol) >= 4 and symbol.isalpha():
        return Resolution(symbol=hits[0][0], kind="store")
    if len(hits) > 1 and len(symbol) >= 4 and symbol.isalpha():
        return Resolution(candidates=tuple(hits),
                          note=f"{symbol} is not a ticker here; {len(hits)} names "
                               "contain it.")
    return Resolution(symbol=symbol, kind="yahoo")


@dataclass(frozen=True)
class Security:
    symbol: str
    kind: str


def push(history: tuple, security: Security, limit: int = 10) -> tuple:
    """The visit history, newest last, without an immediate repeat, at most `limit`."""
    if history and history[-1] == security:
        return history
    return (tuple(history) + (security,))[-limit:]


def back(history: tuple) -> tuple[tuple, Security | None]:
    """(history without the current entry, the one before it) — None if there is none."""
    if len(history) < 2:
        return tuple(history), None
    rest = tuple(history[:-1])
    return rest, rest[-1]


def target(function: str | None) -> tuple[str, str | None]:
    """(tab, section) of a function; a security alone opens its description."""
    f = FUNCTIONS[function or "DES"]
    return f.tab, f.section


def help_rows() -> list[tuple[str, str, str]]:
    """(code, where, what) for the function list shown under the command line."""
    return [(f.code, f.tab + (f" → {f.section}" if f.section else ""), f.label)
            for f in FUNCTIONS.values()]
