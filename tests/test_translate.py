"""Translator contract. No network: the model's answer is supplied directly, so these
tests pin the CONTRACT (what a given answer must become) rather than the model's mood."""

from portfolio_lab.screen.spec import PortfolioSpec
from portfolio_lab.screen import translate as T


def _all_property_names(node) -> set[str]:
    """Every key the model could write into, at any depth."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                found |= set(value)
            found |= _all_property_names(value)
    elif isinstance(node, list):
        for item in node:
            found |= _all_property_names(item)
    return found


def test_schema_has_no_field_for_a_ticker():
    """The structural guarantee: the model cannot name a stock, because there is nowhere
    to put one. This is the single most important test in the project.

    Checks FIELD NAMES, not prose — a description may legitimately use the word 'names'
    while offering no field to write one into."""
    fields = {f.lower() for f in _all_property_names(T.TOOL["input_schema"])}
    for forbidden in ("ticker", "symbol", "company", "cik", "stock", "name", "security"):
        offenders = [f for f in fields if forbidden in f]
        assert not offenders, f"schema exposes {offenders}, which could carry a stock"
    assert fields == {
        "size", "filters", "rank_by", "weighting", "rebalance", "unsupported",
        "metric", "op", "value",
    }, "the model's vocabulary changed — re-check that none of it can name a security"


def test_schema_is_strict_and_closed():
    assert T.TOOL["strict"] is True
    schema = T.TOOL["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["filters"]["items"]["additionalProperties"] is False


def test_metrics_are_a_closed_enum():
    from portfolio_lab.screen.spec import FILTERABLE
    enum = T.TOOL["input_schema"]["properties"]["filters"]["items"]["properties"]["metric"]["enum"]
    assert set(enum) == set(FILTERABLE)


def test_users_first_message_becomes_two_filters():
    payload = {
        "size": 10,
        "filters": [
            {"metric": "price_to_earnings", "op": "<=", "value": 1},
            {"metric": "price_to_book", "op": "<=", "value": 2},
        ],
        "rank_by": None, "weighting": "equal", "rebalance": "quarterly",
        "unsupported": [],
    }
    spec = T.payload_to_spec(payload, PortfolioSpec())
    assert spec.size == 10
    assert {f.metric for f in spec.filters} == {"price_to_earnings", "price_to_book"}


def test_second_message_keeps_the_first_messages_filters():
    first = T.payload_to_spec({
        "size": 10,
        "filters": [{"metric": "price_to_book", "op": "<=", "value": 2}],
        "rank_by": None, "weighting": "equal", "rebalance": "quarterly", "unsupported": [],
    }, PortfolioSpec())
    second = T.payload_to_spec({
        "size": 10,
        "filters": [
            {"metric": "price_to_book", "op": "<=", "value": 2},
            {"metric": "market_cap", "op": "<=", "value": 2_000_000_000},
        ],
        "rank_by": None, "weighting": "equal", "rebalance": "quarterly", "unsupported": [],
    }, first)
    assert {f.metric for f in second.filters} == {"price_to_book", "market_cap"}
    assert T.diff_summary(first, second) == ["added: market_cap <= 2000000000"]


def test_a_chat_message_cannot_remove_the_safety_floors():
    """Eligibility rails are not model-controlled. A message must not be able to open the
    screen up to penny stocks by asking nicely."""
    previous = PortfolioSpec(min_price=5.0, min_dollar_volume=1e6, max_filing_age_months=6)
    spec = T.payload_to_spec({
        "size": 10, "filters": [], "rank_by": None,
        "weighting": "equal", "rebalance": "quarterly", "unsupported": [],
        "min_price": 0.0, "min_dollar_volume": 0.0,
    }, previous)
    assert spec.min_price == 5.0
    assert spec.min_dollar_volume == 1e6
    assert spec.max_filing_age_months == 6


def test_unmappable_criteria_are_carried_through_not_dropped():
    spec = T.payload_to_spec({
        "size": 10, "filters": [], "rank_by": None,
        "weighting": "equal", "rebalance": "quarterly",
        "unsupported": ["companies with a likeable CEO"],
    }, PortfolioSpec())
    assert spec.unsupported == ("companies with a likeable CEO",)


def test_unknown_metric_is_discarded_rather_than_trusted():
    spec = T.payload_to_spec({
        "size": 10,
        "filters": [{"metric": "vibes", "op": "<=", "value": 1}],
        "rank_by": "vibes", "weighting": "equal", "rebalance": "quarterly", "unsupported": [],
    }, PortfolioSpec())
    assert spec.filters == ()
    assert spec.rank_by is None


def test_uses_the_current_model_id():
    assert T.MODEL == "claude-opus-5"


def test_the_model_cannot_set_hand_picked_tickers():
    """The person may name companies; the model may not. Even if a future schema change
    let a ticker list through, payload_to_spec refuses it."""
    previous = PortfolioSpec(tickers=("AAPL",))
    spec = T.payload_to_spec({
        "size": 10, "filters": [], "rank_by": "auto",
        "weighting": "equal", "rebalance": "quarterly", "unsupported": [],
        "tickers": ["NVDA", "TSLA"],
    }, previous)
    assert spec.tickers == ("AAPL",)
