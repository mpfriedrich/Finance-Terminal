"""The command bar (command.py): grammar, resolution order, routing, history.

Resolution runs against a tiny in-memory store built with the real migrations, plus one
check against the Compustat Global fixture, so the TIC.ISO3 naming is the one wrds.py
actually writes.
"""
import duckdb
import pytest

from portfolio_lab import command as cmd
from portfolio_lab import wording
from portfolio_lab.command import Parsed, Resolution, dispatch, parse, resolve
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply
from portfolio_lab.layout import SECURITY_SECTIONS, TAB_LABELS

ROWS = [
    (320193, "AAPL", "APPLE INC"),
    (1067983, "BRK-B", "BERKSHIRE HATHAWAY INC"),
    (1141391, "MA", "MASTERCARD INC"),
    (1, "HP", "HELMERICH & PAYNE INC"),
    (2, "SAP", "SAP SE ADR"),
    (1_000_000_001, "SAP.DEU", "SAP SE"),
    (1_000_000_002, "7203.JPN", "TOYOTA MOTOR CORP"),
    (2_000_012_345, "XYZ-12345", "XYZ HOLDINGS (ENDED)"),
    (3, "SPX", "SPX TECHNOLOGIES INC"),
    (4, "GM", "GENERAL MOTORS CO"),
    (5, "F", "FORD MOTOR CO"),
]


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    c.executemany("INSERT INTO securities (cik, ticker, name) VALUES (?, ?, ?)", ROWS)
    yield c
    c.close()


# --- parse ----------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("AAPL US DES", Parsed("AAPL", "USA", None, "DES")),
    ("aapl gp", Parsed("AAPL", None, None, "GP")),
    ("  aapl    gp  ", Parsed("AAPL", None, None, "GP")),
    ("SAP GR FA", Parsed("SAP", "DEU", None, "FA")),
    ("SAP GY", Parsed("SAP", "DEU")),
    ("SAP DEU FA", Parsed("SAP", "DEU", None, "FA")),
    ("SAP.DE GP", Parsed("SAP.DE", None, None, "GP")),
    ("7203 JT OMON", Parsed("7203", "JPN", None, "OMON")),
    ("SPX INDEX GP", Parsed("SPX", None, "INDEX", "GP")),
    ("CL1 COMDTY", Parsed("CL1", None, "COMDTY")),
    ("SAP GR EQUITY FA", Parsed("SAP", "DEU", "EQUITY", "FA")),
    ("BRK-B", Parsed("BRK-B")),
    ("XYZ-12345", Parsed("XYZ-12345")),
    ("GC=F", Parsed("GC=F")),
    ("^GSPC GP", Parsed("^GSPC", None, None, "GP")),
    ("000001.SS", Parsed("000001.SS")),
    ("AAPL US DES GO", Parsed("AAPL", "USA", None, "DES")),
    ("aapl us des <go>", Parsed("AAPL", "USA", None, "DES")),
])
def test_parse(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("MA", Parsed(function="MA")),                      # precedent deals, not Mastercard
    ("ma <GO>", Parsed(function="MA")),
    ("GP", Parsed(function="GP")),                      # chart of whatever is in context
    ("EQS", Parsed(function="EQS")),
    ("MA US", Parsed("MA", "USA")),                     # Mastercard
    ("MA EQUITY", Parsed("MA", None, "EQUITY")),
    ("MA US GP", Parsed("MA", "USA", None, "GP")),
    ("HP US HP", Parsed("HP", "USA", None, "HP")),      # Helmerich & Payne price table
    ("HP HP", Parsed("HP", None, None, "HP")),
    # CN is Canada on Bloomberg and also the news code; the last word wins as function,
    # so Canada needs the ISO-3 code or a following function.
    ("RY CN", Parsed("RY", None, None, "CN")),
    ("RY CAN", Parsed("RY", "CAN")),
    ("RY CN DES", Parsed("RY", "CAN", None, "DES")),
])
def test_ambiguity(text, expected):
    assert parse(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "GO", "<GO>",
    "SAP XX FA",            # unknown market
    "SAP DUE",              # three letters, but no such country
    "SAP GR LN",            # two markets
    "A B C D E",            # too many words
    "SAP GR EQUITY INDEX",  # too many words
    "SAP EQUITY GR",        # yellow key before market
    "AA$PL", "<SCRIPT>",
])
def test_bad_input_is_an_error_text_never_an_exception(text):
    parsed = parse(text)
    assert isinstance(parsed.error, str) and parsed.error
    assert parsed.symbol is None and parsed.function is None


def test_none_input_does_not_raise():
    assert parse(None).error


# --- candidates and resolution --------------------------------------------------------

def test_candidates():
    assert cmd.candidates(parse("SAP.DE")) == ["SAP.DEU", "SAP.DE"]
    assert cmd.candidates(parse("SAP GR")) == ["SAP.DEU"]   # never the US ADR
    assert cmd.candidates(parse("SAP US")) == ["SAP"]
    assert cmd.candidates(parse("SPX")) == ["SPX", "^GSPC"]
    assert cmd.candidates(parse("SPX INDEX")) == ["^GSPC"]
    assert cmd.candidates(parse("BRK.B")) == ["BRK.B"]       # .B is not a Yahoo suffix
    assert cmd.candidates(parse("GP")) == []


@pytest.mark.parametrize("text, symbol, source", [
    ("AAPL US DES", "AAPL", "store"),
    ("aapl", "AAPL", "store"),
    ("SAP GR FA", "SAP.DEU", "store"),
    ("SAP.DE GP", "SAP.DEU", "store"),           # store line beats the Yahoo symbol
    ("SAP", "SAP", "store"),
    ("7203 JT OMON", "7203.JPN", "store"),
    ("7203 JPN", "7203.JPN", "store"),
    ("BRK-B", "BRK-B", "store"),
    ("XYZ-12345", "XYZ-12345", "store"),
    ("SPX", "SPX", "store"),                      # the share with that ticker
    ("SPX INDEX GP", "^GSPC", "alias"),           # the index
    ("CL1 COMDTY", "CL=F", "alias"),
    ("XAU CURNCY", "GC=F", "alias"),
    ("BTC", "BTC-USD", "alias"),
    ("GC=F", "GC=F", "yahoo"),
    ("^GSPC", "^GSPC", "yahoo"),
    ("000001.SS", "000001.SS", "yahoo"),
    ("BMW GR", "BMW.DE", "yahoo"),                # not in the store: prices only
    ("6758 JT", "6758.T", "yahoo"),
    ("mastercard", "MA", "name"),
    ("toyota", "7203.JPN", "name"),
])
def test_resolution_order(con, text, symbol, source):
    res = resolve(con, parse(text))
    assert (res.symbol, res.source) == (symbol, source)


def test_several_name_hits_are_choices_never_an_automatic_pick(con):
    res = resolve(con, parse("motor"))
    assert res.symbol is None
    assert res.source == "name"
    assert res.choices == ["7203.JPN", "F", "GM"]
    action = dispatch(parse("motor"), res, "AAPL")
    assert action.tab is None and action.symbol == "AAPL"
    assert "7203.JPN" in action.message and "GM" in action.message


def test_name_hits_are_narrowed_to_the_market(con):
    assert resolve(con, parse("motor US")).choices == ["F", "GM"]
    # A non-US market reaches the Yahoo fallback before any name search.
    assert resolve(con, parse("motor JPN")) == Resolution("MOTOR.T", source="yahoo")


def test_nothing_found(con):
    # Neither can be a US ticker (six letters; a digit), so Yahoo is not asked; Q1 is
    # also too short for a name search. A ticker-shaped word such as ZZZZQ now goes to
    # Yahoo instead (review F3), which then has prices or says it has none.
    for text in ("ZZZZQX", "Q1"):
        res = resolve(con, parse(text))
        assert res == Resolution(None)
        action = dispatch(parse(text), res, "AAPL")
        assert action.tab is None and action.symbol == "AAPL" and action.message


def test_resolve_on_an_error_or_a_bare_function_touches_nothing():
    # No connection is needed: None would raise if it were used.
    assert resolve(None, parse("SAP XX")) == Resolution(None)
    assert resolve(None, parse("EQS")) == Resolution(None)


def test_world_store_line_from_the_compustat_fixture(tmp_path):
    from tests.wrds_fixture import build_store

    world, _ = build_store(tmp_path)
    assert resolve(world.con, parse("DMA GR FA")) == Resolution("DMA.DEU", source="store")
    assert resolve(world.con, parse("DMA.DE")).symbol == "DMA.DEU"
    assert resolve(world.con, parse("6701 JT")).symbol == "6701.JPN"


@pytest.mark.parametrize("text, symbol", [
    ("SPY", "SPY"), ("QQQ", "QQQ"), ("GLD", "GLD"), ("SPY US", "SPY"),
    ("spy us gp", "SPY"), ("SPY EQUITY", "SPY"),
    ("BRK.B", "BRK-B"),            # Yahoo writes share classes with a dash
    ("GOOGL", "GOOGL"),            # five letters: no name matches, then Yahoo
])
def test_a_us_ticker_outside_the_store_is_a_yahoo_line(con, text, symbol):
    """Review F3 (03.10.2026): ETFs are not in the store and gave "No security found"."""
    p = parse(text)
    res = resolve(con, p)
    assert res == Resolution(symbol, source="yahoo")
    a = dispatch(p, res, None)
    assert a.symbol == symbol and a.message.endswith("is not in the store: prices only.")


def test_the_us_fallback_never_overrides_the_store_or_a_name_word(con):
    assert resolve(con, parse("AAPL")).source == "store"
    assert resolve(con, parse("SPX")) == Resolution("SPX", source="store")
    assert resolve(con, parse("BTC")) == Resolution("BTC-USD", source="alias")
    assert resolve(con, parse("motor")).choices == ["7203.JPN", "F", "GM"]
    assert resolve(con, parse("motor US")).choices == ["F", "GM"]
    assert resolve(con, parse("BMW GR")) == Resolution("BMW.DE", source="yahoo")


def test_a_bare_ticker_finds_its_only_compustat_line(con):
    assert resolve(con, parse("7203")) == Resolution("7203.JPN", source="store")
    assert resolve(con, parse("7203 US")) == Resolution(None)    # US: not the JPN line


def test_a_bare_ticker_with_several_compustat_lines_is_choices(con):
    con.executemany("INSERT INTO securities (cik, ticker, name) VALUES (?, ?, ?)",
                    [(1_000_000_010, "VOW.DEU", "VOLKSWAGEN AG"),
                     (1_000_000_011, "VOW.CHE", "VOLKSWAGEN AG (SIX)"),
                     (6, "VOWX.B", "NOT A MARKET SUFFIX")])
    res = resolve(con, parse("VOW"))
    assert res.symbol is None and res.choices == ["VOW.CHE", "VOW.DEU"]
    assert "Several matches" in dispatch(parse("VOW"), res, "AAPL").message
    assert resolve(con, parse("VOW GR")) == Resolution("VOW.DEU", source="store")


def test_a_bare_compustat_ticker_in_the_world_fixture(tmp_path):
    from tests.wrds_fixture import build_store

    world, _ = build_store(tmp_path)
    assert resolve(world.con, parse("DMA")) == Resolution("DMA.DEU", source="store")
    assert resolve(world.con, parse("DMA FA")).symbol == "DMA.DEU"
    assert resolve(world.con, parse("6701")).symbol == "6701.JPN"


# --- dispatch -------------------------------------------------------------------------

def test_security_without_function_is_des(con):
    p = parse("SAP GR")
    a = dispatch(p, resolve(con, p), None)
    assert (a.tab, a.section, a.symbol) == ("SECURITY", "OVERVIEW", "SAP.DEU")


def test_function_routes(con):
    p = parse("AAPL US FA")
    a = dispatch(p, resolve(con, p), None)
    assert (a.tab, a.section, a.symbol) == ("SECURITY", "FINANCIALS", "AAPL")
    for code, tab, section in [("OMON", "SECURITY", "OPTIONS"), ("OVDV", "SECURITY", "OPTIONS"),
                               ("SKEW", "SECURITY", "OPTIONS"), ("HRA", "SECURITY", "EXPOSURE"),
                               ("EQS", "SCREEN & BACKTEST", None),
                               ("BTST", "SCREEN & BACKTEST", None),
                               ("PORT", "PORTFOLIOS", None), ("ALRT", "PORTFOLIOS", None),
                               ("WEI", "MARKETS & MACRO", None), ("GC", "MARKETS & MACRO", None),
                               ("BMAP", "WORLD & LOGISTICS", None),
                               ("EVTS", "EVENTS & NEWS", None), ("TOP", "EVENTS & NEWS", None)]:
        a = dispatch(parse(code), Resolution(None), "AAPL")
        assert (a.tab, a.section) == (tab, section), code


def test_function_alone_uses_the_context():
    a = dispatch(parse("GP"), Resolution(None), "SAP.DEU")
    assert (a.tab, a.section, a.symbol) == ("SECURITY", "OVERVIEW", "SAP.DEU")


def test_function_that_needs_a_security_without_one_is_a_message():
    a = dispatch(parse("GP"), Resolution(None), None)
    assert a.tab is None and a.symbol is None and "needs a security" in a.message


def test_ma_alone_opens_precedents_with_or_without_context():
    assert cmd.FUNCTIONS["MA"].needs_security is False
    a = dispatch(parse("MA"), Resolution(None), None)
    assert (a.tab, a.section, a.symbol) == ("SECURITY", "VALUATION", None)
    a = dispatch(parse("MA"), Resolution(None), "AAPL")
    assert (a.tab, a.section, a.symbol) == ("SECURITY", "VALUATION", "AAPL")


def test_market_wide_functions_keep_the_context():
    assert dispatch(parse("EQS"), Resolution(None), "AAPL").symbol == "AAPL"


def test_yahoo_only_security_says_so(con):
    p = parse("BMW GR GP")
    a = dispatch(p, resolve(con, p), None)
    assert a.symbol == "BMW.DE" and "prices only" in a.message


def test_parse_error_keeps_the_context():
    a = dispatch(parse("SAP XX"), Resolution(None), "AAPL")
    assert a.tab is None and a.symbol == "AAPL" and "Unknown market" in a.message


def test_run_chains_the_three_steps(con):
    _, res, action = cmd.run(con, "sap gr fa <go>", None)
    assert res.symbol == "SAP.DEU"
    assert (action.tab, action.section) == ("SECURITY", "FINANCIALS")


# --- completeness and wording ---------------------------------------------------------

def test_every_function_lands_on_a_real_tab_and_section():
    for code, target in cmd.FUNCTIONS.items():
        assert target.tab in TAB_LABELS, code
        if target.tab == "SECURITY":
            assert target.section in SECURITY_SECTIONS, code
        else:
            assert target.section is None, code
        assert target.label, code


def test_every_listed_function_exists():
    wanted = ["DES", "GP", "HP", "FA", "ERN", "SPLC", "RV", "COMP", "WACC", "DCF", "OMON",
              "OVDV", "SKEW", "HRA", "ANR", "INS", "HDS", "DVD", "CN", "CF", "BTST", "EQS",
              "PORT", "MA", "WEI", "CORR", "ECST", "GC", "WIRP", "GLCO", "BMAP", "EVTS",
              "TOP", "ALRT"]
    assert set(cmd.FUNCTIONS) == set(wanted)


def test_function_codes_are_not_yellow_keys():
    assert not set(cmd.FUNCTIONS) & set(cmd.YELLOW)


def test_markets_map_to_known_iso3():
    assert set(cmd.BBG_MARKETS.values()) <= cmd.MARKETS_ISO3
    assert set(cmd.ISO3_TO_YAHOO) <= cmd.MARKETS_ISO3
    for iso3, suffix in cmd.ISO3_TO_YAHOO.items():
        assert cmd.YAHOO_TO_ISO3[suffix] == iso3


def test_labels_and_messages_pass_r23(con):
    texts = [t.label for t in cmd.FUNCTIONS.values()]
    for text in ("GP", "motor", "ZZZZQ", "SAP XX", "", "BMW GR GP", "AAPL US FA", "MA"):
        texts.append(cmd.run(con, text, None)[2].message)
    for text in texts:
        assert wording.scan_text(text) == [], text


def test_command_module_is_scanned_by_the_wording_test():
    from tests.test_wording import UI_FILES

    assert any(p.name == "command.py" for p in UI_FILES)


# --- history --------------------------------------------------------------------------

def test_history():
    state = {}
    assert cmd.current(state) is None
    assert cmd.back(state) is None
    cmd.push(state, "AAPL")
    cmd.push(state, "AAPL")                    # no adjacent duplicates
    cmd.push(state, None)                      # nothing to remember
    assert state[cmd.HISTORY_KEY] == ["AAPL"]
    assert cmd.back(state) is None             # the only entry stays
    assert cmd.current(state) == "AAPL"
    cmd.push(state, "SAP.DEU")
    cmd.push(state, "AAPL")                    # not adjacent: kept
    assert state[cmd.HISTORY_KEY] == ["AAPL", "SAP.DEU", "AAPL"]
    assert cmd.back(state) == "SAP.DEU"
    assert cmd.current(state) == "SAP.DEU"


def test_history_keeps_the_last_ten():
    state = {}
    for i in range(15):
        cmd.push(state, f"T{i}")
    assert state[cmd.HISTORY_KEY] == [f"T{i}" for i in range(5, 15)]
    assert cmd.current(state) == "T14"
