"""The command line (command.py): grammar, resolution, history, and that every function
points at something that exists."""
import pytest

from portfolio_lab import command as C


# --- grammar ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, symbol, market, function", [
    ("AAPL", "AAPL", None, None),
    ("aapl us fa", "AAPL", "USA", "FA"),
    ("AAPL US FA <GO>", "AAPL", "USA", "FA"),
    ("AAPL US EQUITY DES GO", "AAPL", "USA", "DES"),
    ("SAP GR", "SAP", "DEU", None),
    ("7203 JP GP", "7203", "JPN", "GP"),
    ("SAP DEU", "SAP", "DEU", None),
    ("BRK-B HP", "BRK-B", None, "HP"),
    ("SAP.DEU DES", "SAP.DEU", None, "DES"),
    ("XYZ-12345", "XYZ-12345", None, None),
    ("GC=F GP", "GC=F", None, "GP"),
    ("^GSPC", "^GSPC", None, None),
    ("000001.SS", "000001.SS", None, None),
    ("SPX INDEX GP", "^GSPC", None, "GP"),
    ("CL1 COMDTY", "CL=F", None, None),
    ("XBT CURNCY", "BTC-USD", None, None),
    ("MA US", "MA", "USA", None),            # Mastercard, not the deals function
])
def test_parse(text, symbol, market, function):
    p = C.parse(text)
    assert p.error is None, p.error
    assert (p.symbol, p.market, p.function) == (symbol, market, function)


def test_one_word_is_a_function_first():
    assert C.parse("MA") == C.Parsed(function="MA")
    assert C.parse("gp go").function == "GP"


@pytest.mark.parametrize("text", ["", "   ", "GO", "SAP XX", "A B C D E F G",
                                  "AAPL! FA"])
def test_bad_input_is_a_sentence_not_an_exception(text):
    p = C.parse(text)
    assert p.error and isinstance(p.error, str)


def test_several_words_are_a_name_search():
    p = C.parse("american airlines")
    assert p.name == "american airlines" and p.symbol is None


# --- resolution ------------------------------------------------------------------------
STORE = {"AAPL", "MA", "DMA.DEU", "6701.JPN", "AAL"}
PRICE_ONLY = {"^GSPC", "GC=F", "BTC-USD"}
NAMES = {"AAPL": "Apple Inc", "MA": "Mastercard", "AAL": "American Airlines Group",
         "AA": "Alcoa", "DMA.DEU": "Deutsche Maschinen AG"}


def _find(text):
    t = text.lower()
    return [(k, v) for k, v in NAMES.items() if t in v.lower() or t == k.lower()]


def _resolve(text):
    return C.resolve(C.parse(text), STORE.__contains__, PRICE_ONLY.__contains__, _find)


def test_exact_listing():
    assert _resolve("AAPL US FA").symbol == "AAPL"


def test_market_maps_to_the_world_store_line():
    r = _resolve("DMA GR")
    assert (r.symbol, r.kind) == ("DMA.DEU", "store")


def test_a_yahoo_suffix_is_tried_as_the_store_line_first():
    assert _resolve("DMA.DE").symbol == "DMA.DEU"
    r = _resolve("SAP.DE")
    assert (r.symbol, r.kind) == ("SAP.DE", "yahoo")


def test_a_foreign_line_not_stored_goes_to_yahoo_with_its_suffix():
    r = _resolve("SAP GR")
    assert (r.symbol, r.kind) == ("SAP.DE", "yahoo") and "not in the store" in r.note


def test_price_only_series_and_aliases():
    assert _resolve("SPX").kind == "price_only"
    assert _resolve("XAU").symbol == "GC=F"


def test_one_name_hit_is_taken_several_are_offered_never_guessed():
    assert _resolve("mastercard").symbol == "MA"
    r = _resolve("american airlines")
    assert r.symbol == "AAL"
    many = C.resolve(C.parse("americ"), STORE.__contains__, PRICE_ONLY.__contains__,
                     lambda t: [("AA", "Alcoa"), ("AAL", "American Airlines Group")])
    assert many.symbol is None and len(many.candidates) == 2


def test_unknown_symbol_goes_to_yahoo():
    assert _resolve("BHP.AX").kind == "yahoo"


def test_resolution_against_the_world_store_fixture(tmp_path):
    """The Compustat fixture's German company, typed the Bloomberg way."""
    from tests.wrds_fixture import build_store

    source, _ = build_store(tmp_path)
    con = source.con

    def in_store(t):
        return bool(con.execute("SELECT 1 FROM securities WHERE ticker = ?",
                                [t]).fetchone())

    r = C.resolve(C.parse("DMA GR DES"), in_store, lambda t: False, lambda t: [])
    assert r.symbol == "DMA.DEU"
    r = C.resolve(C.parse("6701 JP"), in_store, lambda t: False, lambda t: [])
    assert r.symbol == "6701.JPN"


# --- history ---------------------------------------------------------------------------
def test_history_keeps_ten_and_goes_back():
    h = ()
    for i in range(12):
        h = C.push(h, C.Security(f"T{i}", "store"))
    assert len(h) == 10 and h[-1].symbol == "T11"
    assert C.push(h, h[-1]) == h
    rest, prev = C.back(h)
    assert prev.symbol == "T10" and len(rest) == 9
    assert C.back((C.Security("X", "store"),))[1] is None


# --- completeness ----------------------------------------------------------------------
def test_every_function_points_at_a_tab_and_section_that_exist():
    for code, f in C.FUNCTIONS.items():
        assert f.tab in C.TAB_LABELS, code
        assert f.section is None or f.section in C.SECTIONS[f.tab], code
        assert code == code.upper()
    # CN is both (company news, and Canada); two words make it the market.
    assert C.parse("RY CN").market == "CAN" and C.parse("RY CN CN").function == "CN"


def test_every_section_is_reachable_from_some_function():
    reached = {(f.tab, f.section) for f in C.FUNCTIONS.values()}
    for tab, sections in C.SECTIONS.items():
        for s in sections:
            assert (tab, s) in reached, (tab, s)
