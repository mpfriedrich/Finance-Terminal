"""layout.py: the command line's answer and the labels it shows (review 03.10.2026)."""
from portfolio_lab import command, layout


def _answer(text: str, equity: bool, current: str | None = None) -> str:
    parsed = command.parse(text)
    found = command.Resolution(parsed.symbol, source="store") if parsed.symbol \
        else command.Resolution(None)
    action = command.dispatch(parsed, found, current)
    code = parsed.function or "DES"
    return layout.status_line(action.message, code, command.FUNCTIONS[code].label,
                              action.symbol, equity, action.tab)


def test_a_company_function_on_a_price_only_symbol_says_it_is_not_there():
    """F12f: "GC=F FA: Financial statements." was shown over a gold chart."""
    shown = _answer("GC=F FA", equity=False)
    assert "not available for GC=F" in shown and "Financial statements" not in shown
    # What the price page does offer is named as usual.
    assert _answer("GC=F HP", equity=False) == "GC=F HP: Price history table."
    # A share is untouched, and so is a function on another tab.
    assert _answer("AAA FA", equity=True) == "AAA FA: Financial statements."
    assert "not available" not in _answer("EQS", equity=False, current="GC=F")


def test_splc_and_hra_are_named_for_what_they_show():
    """F12a: SPLC is the 10-K's suppliers and customers, HRA the regressions."""
    assert "Suppliers and customers" in _answer("AAA SPLC", equity=True)
    assert "Commodity and rate exposure" not in _answer("AAA SPLC", equity=True)
    assert "commodities, rates" in _answer("AAA HRA", equity=True)
    assert set(layout.LABELS) <= set(command.FUNCTIONS)
    assert layout.PRICE_ONLY_FUNCTIONS <= set(command.FUNCTIONS)
