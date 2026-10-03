"""The command bar: Bloomberg-style input such as ``SAP GR FA <GO>``, turned into a tab,
a section and a security.

Pure on purpose: no Streamlit import, so every rule here is tested without a running
app. app.py only renders the box, calls ``parse`` -> ``resolve`` -> ``dispatch`` and acts
on the returned ``Action``.

Grammar (case-insensitive, extra spaces ignored)::

    [security] [function] [GO | <GO>]
    security := SYMBOL [MARKET] [YELLOW]

* MARKET is a Bloomberg exchange code (``GR``, ``JT``, ``LN`` ...) or an ISO-3 country
  code (``DEU``). It is stored as ISO-3, because the world store names Compustat lines
  ``TIC.ISO3`` (CLAUDE.md section 7).
* YELLOW is Bloomberg's market-sector key (``EQUITY``, ``INDEX`` ...). It carries no
  information the store could use except that INDEX/CURNCY/COMDTY make an alias
  (``SPX`` -> ``^GSPC``) win over a same-named share.
* A single word that is a function code is the function (``MA`` opens precedent deals),
  because that is what the code means on a Bloomberg keyboard; ``MA US`` or ``MA EQUITY``
  is the security MA. With several words the last one is the function if it is a code.

Nothing here raises on user input: a bad command returns a ``Parsed`` with ``error``
set, and the app shows that text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from portfolio_lab.data.wrds import DEFAULT_COUNTRIES
from portfolio_lab.layout import SECURITY_SECTIONS, TAB_LABELS

SECURITY, SCREEN, PORTFOLIOS, MARKETS, WORLD, EVENTS = TAB_LABELS
(OVERVIEW, FINANCIALS, VALUATION, OPTIONS, EXPOSURE, ANALYSTS, OWNERSHIP,
 NEWS_FILINGS) = SECURITY_SECTIONS


@dataclass(frozen=True)
class Target:
    tab: str
    section: str | None
    needs_security: bool
    label: str


FUNCTIONS: dict[str, Target] = {
    # SECURITY
    "DES": Target(SECURITY, OVERVIEW, True, "Description and key figures"),
    "GP": Target(SECURITY, OVERVIEW, True, "Price chart"),
    "HP": Target(SECURITY, OVERVIEW, True, "Price history table"),
    "FA": Target(SECURITY, FINANCIALS, True, "Financial statements"),
    "ERN": Target(SECURITY, FINANCIALS, True, "Earnings history and next report date"),
    "DVD": Target(SECURITY, FINANCIALS, True, "Dividend history"),
    "RV": Target(SECURITY, VALUATION, True, "Relative valuation against peers"),
    "COMP": Target(SECURITY, VALUATION, True, "Comparable companies"),
    "WACC": Target(SECURITY, VALUATION, True, "Cost of capital"),
    "DCF": Target(SECURITY, VALUATION, True, "Discounted cash flow model"),
    # Precedent deals stand on their own; a security in context only narrows them.
    "MA": Target(SECURITY, VALUATION, False, "Precedent transactions"),
    "OMON": Target(SECURITY, OPTIONS, True, "Option chain"),
    "OVDV": Target(SECURITY, OPTIONS, True, "Implied volatility surface"),
    "SKEW": Target(SECURITY, OPTIONS, True, "Volatility skew"),
    "SPLC": Target(SECURITY, EXPOSURE, True, "Commodity and rate exposure"),
    "HRA": Target(SECURITY, EXPOSURE, True, "Historical regression against markets"),
    "ANR": Target(SECURITY, ANALYSTS, True, "Analyst ratings and targets"),
    "INS": Target(SECURITY, OWNERSHIP, True, "Insider transactions"),
    "HDS": Target(SECURITY, OWNERSHIP, True, "Holders"),
    "CN": Target(SECURITY, NEWS_FILINGS, True, "Company news"),
    "CF": Target(SECURITY, NEWS_FILINGS, True, "Company filings"),
    # SCREEN & BACKTEST
    "EQS": Target(SCREEN, None, False, "Equity screen"),
    "BTST": Target(SCREEN, None, False, "Backtest"),
    # PORTFOLIOS
    "PORT": Target(PORTFOLIOS, None, False, "Paper portfolios"),
    "ALRT": Target(PORTFOLIOS, None, False, "Alerts"),
    # MARKETS & MACRO
    "WEI": Target(MARKETS, None, False, "World equity indices"),
    "CORR": Target(MARKETS, None, False, "Correlation matrix"),
    "ECST": Target(MARKETS, None, False, "Economic statistics"),
    "GC": Target(MARKETS, None, False, "Yield curve"),
    "WIRP": Target(MARKETS, None, False, "Policy rate path"),
    "GLCO": Target(MARKETS, None, False, "Commodity prices"),
    # WORLD & LOGISTICS
    "BMAP": Target(WORLD, None, False, "Map of ships, ports and aircraft"),
    # EVENTS & NEWS
    "EVTS": Target(EVENTS, None, False, "Event calendar"),
    "TOP": Target(EVENTS, None, False, "Top news and briefing"),
}

YELLOW = ("EQUITY", "INDEX", "CURNCY", "COMDTY", "CORP", "GOVT")
# Keys after which an alias beats a share with the same ticker (SPX INDEX is the index).
_ALIAS_FIRST = {"INDEX", "CURNCY", "COMDTY"}

# Bloomberg exchange codes -> ISO-3 of the market.
BBG_MARKETS: dict[str, str] = {
    "US": "USA", "GR": "DEU", "GY": "DEU", "LN": "GBR", "JP": "JPN", "JT": "JPN",
    "FP": "FRA", "NA": "NLD", "SW": "CHE", "IM": "ITA", "HK": "HKG", "AU": "AUS",
    "CN": "CAN", "KS": "KOR",
}

# Yahoo symbol suffixes -> ISO-3. Several suffixes can share a country (Xetra .DE and
# Frankfurt .F); ISO3_TO_YAHOO picks the main exchange.
YAHOO_TO_ISO3: dict[str, str] = {
    "DE": "DEU", "F": "DEU", "T": "JPN", "AX": "AUS", "L": "GBR", "PA": "FRA",
    "MI": "ITA", "SS": "CHN", "SZ": "CHN", "HK": "HKG", "TO": "CAN", "V": "CAN",
    "SW": "CHE", "AS": "NLD", "KS": "KOR", "KQ": "KOR", "ST": "SWE", "OL": "NOR",
    "CO": "DNK", "HE": "FIN", "MC": "ESP", "BR": "BEL", "LS": "PRT", "VI": "AUT",
    "IR": "IRL", "TA": "ISR", "NZ": "NZL", "SI": "SGP", "TW": "TWN", "NS": "IND",
}
ISO3_TO_YAHOO: dict[str, str] = {
    "DEU": "DE", "JPN": "T", "AUS": "AX", "GBR": "L", "FRA": "PA", "ITA": "MI",
    "CHN": "SS", "HKG": "HK", "CAN": "TO", "CHE": "SW", "NLD": "AS", "KOR": "KS",
    "SWE": "ST", "NOR": "OL", "DNK": "CO", "FIN": "HE", "ESP": "MC", "BEL": "BR",
    "PRT": "LS", "AUT": "VI", "IRL": "IR", "ISR": "TA", "NZL": "NZ", "SGP": "SI",
    "TWN": "TW", "IND": "NS",
}

# ISO-3 codes accepted as MARKET. A typo such as "SAP DUE" should be an error, not a
# silent search for a market that does not exist.
MARKETS_ISO3 = frozenset(
    {"USA"} | set(BBG_MARKETS.values()) | set(YAHOO_TO_ISO3.values()) | set(DEFAULT_COUNTRIES)
)

# Bloomberg names -> Yahoo symbols for things that are not shares in the store.
ALIASES: dict[str, str] = {
    "SPX": "^GSPC", "INDU": "^DJI", "NDX": "^NDX", "DAX": "^GDAXI", "UKX": "^FTSE",
    "NKY": "^N225", "HSI": "^HSI", "CL1": "CL=F", "CO1": "BZ=F", "GC1": "GC=F",
    "XAU": "GC=F", "EURUSD": "EURUSD=X", "XBT": "BTC-USD", "BTC": "BTC-USD",
}

_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-=^]*$|^\^[A-Z0-9.\-=]+$")
_GO = {"GO", "<GO>"}
NAME_SEARCH_MIN = 3  # shorter text matches half the store by name
# A US ticker: one to five letters, optionally a share class (BRK.B, BRK-B). Review F3
# (03.10.2026): SPY, QQQ and GLD are not in the store (ETFs) and gave "No security found".
_US_TICKER = re.compile(r"^([A-Z]{1,5})(?:[.-]([A-Z]{1,2}))?$")
# Up to this length a ticker-shaped word is taken as a ticker before any name search:
# "SPY" by name finds SPYR INC, "FORD" finds FORD MOTOR CO although FORD is its own
# symbol. Five letters are as often a word ("motor") as a ticker (GOOGL), so there the
# store's names are asked first and Yahoo only when no name matches.
TICKER_FIRST_MAX = 4
MAX_CHOICES = 25
HISTORY_KEY = "sec_history"
HISTORY_MAX = 10


@dataclass(frozen=True)
class Parsed:
    symbol: str | None = None
    market: str | None = None   # ISO-3
    yellow: str | None = None
    function: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Resolution:
    symbol: str | None
    choices: list = field(default_factory=list)
    source: str = ""  # "store" | "yahoo" | "alias" | "name"; "" when nothing was found


@dataclass(frozen=True)
class Action:
    tab: str | None       # None: stay where you are
    section: str | None
    symbol: str | None    # the security in context after the command
    message: str = ""


# --- parsing --------------------------------------------------------------------------

def _market(token: str) -> str | None:
    if token in BBG_MARKETS:
        return BBG_MARKETS[token]
    if len(token) == 3 and token in MARKETS_ISO3:
        return token
    return None


def parse(text: str) -> Parsed:
    tokens = (text or "").upper().split()
    while tokens and tokens[-1] in _GO:
        tokens.pop()
    if not tokens:
        return Parsed(error="Type a symbol, a function code, or both (e.g. AAPL US DES).")

    if len(tokens) == 1 and tokens[0] in FUNCTIONS:
        return Parsed(function=tokens[0])

    function = None
    if len(tokens) > 1 and tokens[-1] in FUNCTIONS:
        function = tokens.pop()

    symbol, rest = tokens[0], tokens[1:]
    if not _SYMBOL.match(symbol):
        return Parsed(error=f"'{symbol}' is not a symbol: letters, digits and . - = ^ only.")
    if len(rest) > 2:
        return Parsed(error=f"Too many words: '{text.strip()}'. "
                            "Format: SYMBOL [MARKET] [EQUITY|INDEX|...] [FUNCTION].")
    market = yellow = None
    for i, token in enumerate(rest):
        if token in YELLOW:
            if yellow or i != len(rest) - 1:
                return Parsed(error=f"'{token}' must come last, after the market.")
            yellow = token
            continue
        found = _market(token)
        if found is None or market:
            return Parsed(error=f"Unknown market '{token}'. Use a code such as US, GR, "
                                "LN, JT or a three-letter country code such as DEU.")
        market = found
    return Parsed(symbol=symbol, market=market, yellow=yellow, function=function)


# --- resolution -----------------------------------------------------------------------

def _yahoo_suffix(symbol: str) -> tuple[str, str] | None:
    """('SAP', 'DEU') for 'SAP.DE'; None when the part after the dot is not a Yahoo
    suffix (SAP.DEU is already a store ticker, BRK.B is a share class)."""
    base, dot, suffix = symbol.rpartition(".")
    if dot and base and suffix in YAHOO_TO_ISO3:
        return base, YAHOO_TO_ISO3[suffix]
    return None


def candidates(parsed: Parsed) -> list[str]:
    """Store tickers to try, best first.

    A non-US market means the local line, ``TIC.ISO3`` as Compustat lines are named,
    and not the bare ticker: bare SAP is the New York ADR. A Yahoo-style symbol
    (SAP.DE) is tried as the store line SAP.DEU first, because the store has
    fundamentals and Yahoo only has prices.
    """
    sym = parsed.symbol
    if not sym:
        return []
    out: list[str] = []
    split = _yahoo_suffix(sym)
    if split:
        out += [f"{split[0]}.{split[1]}", sym]
    elif parsed.market and parsed.market != "USA":
        out.append(f"{sym}.{parsed.market}")
    else:
        out.append(sym)
    alias = ALIASES.get(sym)
    if alias and parsed.yellow in _ALIAS_FIRST:
        return [alias]   # SPX INDEX is the index, never the share SPX
    if alias:
        out.append(alias)
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _yahoo_fallback(parsed: Parsed) -> str | None:
    """A symbol Yahoo knows, for a share that the store does not hold: prices only."""
    sym = parsed.symbol or ""
    if _yahoo_suffix(sym) or "^" in sym or "=" in sym:
        return sym
    if parsed.market and parsed.market != "USA" and parsed.market in ISO3_TO_YAHOO:
        return f"{sym}.{ISO3_TO_YAHOO[parsed.market]}"
    return None


def _us_yahoo(parsed: Parsed) -> str | None:
    """The Yahoo symbol of a US ticker the store does not hold (SPY, BRK.B -> BRK-B),
    or None when the command names another market or the word is not ticker-shaped.
    Yahoo writes share classes with a dash."""
    if parsed.market not in (None, "USA"):
        return None
    m = _US_TICKER.match(parsed.symbol or "")
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)


def _compustat_lines(con, parsed: Parsed) -> list[str]:
    """``TIC.ISO3`` store lines for a bare ticker without a market: DMA -> DMA.DEU in
    the world store. Only suffixes that are markets count, so BRK does not match a
    share class BRK.B."""
    if parsed.market or "." in (parsed.symbol or ""):
        return []
    rows = con.execute(
        "SELECT ticker FROM securities WHERE starts_with(ticker, ?) ORDER BY ticker",
        [parsed.symbol + "."],
    ).fetchall()
    found = [r[0] for r in rows if r[0].rpartition(".")[2] in MARKETS_ISO3]
    return list(dict.fromkeys(found))[:MAX_CHOICES]


def _in_market(ticker: str, market: str | None) -> bool:
    if not market:
        return True
    if market == "USA":
        return "." not in ticker
    return ticker.upper().endswith("." + market)


def resolve(con, parsed: Parsed) -> Resolution:
    """Find the security the command means, against the ``securities`` table on `con`.

    Order: store tickers from ``candidates``, then an alias, then the ``TIC.ISO3``
    lines of a bare ticker (several are choices), then a Yahoo symbol for a non-US
    market or a short US ticker, then a name search, then a longer US ticker on Yahoo.
    Several name hits come back as ``choices`` and are never picked automatically:
    guessing between two companies is how a screen ends up showing the wrong one.
    `con` is used and not kept.
    """
    if parsed.error or not parsed.symbol:
        return Resolution(None)
    for cand in candidates(parsed):
        row = con.execute(
            "SELECT ticker FROM securities WHERE ticker = ? ORDER BY ticker LIMIT 1",
            [cand.upper()],
        ).fetchone()
        if row:
            return Resolution(row[0], source="store")
    if parsed.symbol in ALIASES:
        return Resolution(ALIASES[parsed.symbol], source="alias")
    lines = _compustat_lines(con, parsed)
    if len(lines) == 1:
        return Resolution(lines[0], source="store")
    if lines:
        return Resolution(None, choices=lines, source="store")
    fallback = _yahoo_fallback(parsed)
    if fallback:
        return Resolution(fallback, source="yahoo")
    us = _us_yahoo(parsed)
    if us and len(us.split("-")[0]) <= TICKER_FIRST_MAX:
        return Resolution(us, source="yahoo")
    hits = []
    if len(parsed.symbol) >= NAME_SEARCH_MIN:
        hits = [r[0] for r in con.execute(
            "SELECT DISTINCT ticker FROM securities WHERE name ILIKE '%' || ? || '%' "
            "ORDER BY ticker LIMIT ?",
            [parsed.symbol, MAX_CHOICES],
        ).fetchall()]
    local = [t for t in hits if _in_market(t, parsed.market)]
    hits = local or hits
    if len(hits) == 1:
        return Resolution(hits[0], source="name")
    if hits:
        return Resolution(None, choices=hits, source="name")
    if us:
        return Resolution(us, source="yahoo")
    return Resolution(None)


# --- dispatch -------------------------------------------------------------------------

def dispatch(parsed: Parsed, resolution: Resolution, current: str | None) -> Action:
    """Where the command goes. ``tab=None`` means: stay, and show ``message``."""
    if parsed.error:
        return Action(None, None, current, parsed.error)
    symbol = current
    note = ""
    if parsed.symbol:
        if resolution.symbol:
            symbol = resolution.symbol
            if resolution.source == "yahoo":
                note = f" {symbol} is not in the store: prices only."
        elif resolution.choices:
            shown = ", ".join(resolution.choices[:10])
            more = " ..." if len(resolution.choices) > 10 else ""
            return Action(None, None, current,
                          f"Several matches for '{parsed.symbol}': {shown}{more}. "
                          "Type the one you mean.")
        else:
            return Action(None, None, current, f"No security found for '{parsed.symbol}'.")
    code = parsed.function or "DES"
    target = FUNCTIONS[code]
    if target.needs_security and not symbol:
        return Action(None, None, None,
                      f"{code} ({target.label}) needs a security. Type one first, "
                      f"e.g. AAPL US {code}.")
    head = f"{symbol} {code}" if symbol and target.tab == SECURITY else code
    return Action(target.tab, target.section, symbol, f"{head}: {target.label}.{note}")


def run(con, text: str, current: str | None) -> tuple[Parsed, Resolution, Action]:
    """parse -> resolve -> dispatch in one call, for the app's callback."""
    parsed = parse(text)
    resolution = resolve(con, parsed)
    return parsed, resolution, dispatch(parsed, resolution, current)


# --- history (works on any dict, so st.session_state works) ---------------------------

def current(state) -> str | None:
    hist = state.get(HISTORY_KEY) or []
    return hist[-1] if hist else None


def push(state, symbol: str | None) -> None:
    if not symbol:
        return
    hist = list(state.get(HISTORY_KEY) or [])
    if hist and hist[-1] == symbol:
        return
    hist.append(symbol)
    state[HISTORY_KEY] = hist[-HISTORY_MAX:]


def back(state) -> str | None:
    """Drop the current security and return the one before it (None if there is none;
    the only entry is kept, so 'back' never empties the context)."""
    hist = list(state.get(HISTORY_KEY) or [])
    if len(hist) < 2:
        return None
    hist.pop()
    state[HISTORY_KEY] = hist
    return hist[-1]
