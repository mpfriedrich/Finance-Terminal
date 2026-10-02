"""Filing extraction. The quote check is the whole guarantee — everything else is prose."""

from portfolio_lab import business as bz

FILING = bz.normalise("""
Item 1. Business  The Company designs and sells widgets. During fiscal 2025, one
customer, Northwind Retail, accounted for 22% of net sales. The Company purchases
titanium housings from a single supplier, Vulcan Metals, and competes primarily with
Globex Corporation. Certain components are obtained from limited sources.
Item 1B. Unresolved Staff Comments  None.
""")


class FakeModel:
    """Returns whatever payload the test hands it, as the API would."""

    def __init__(self, payload):
        self.payload = payload
        self.messages = self

    def create(self, **kwargs):
        block = type("B", (), {"type": "tool_use", "input": self.payload})()
        return type("R", (), {"content": [block]})()


def test_a_verbatim_quote_is_kept():
    out = bz.extract(FILING, "Widget Co", client=FakeModel({
        "relationships": [{
            "counterparty": "Northwind Retail", "relationship": "customer",
            "quote": "one customer, Northwind Retail, accounted for 22% of net sales",
        }],
        "notes": [],
    }))
    assert len(out["relationships"]) == 1
    assert out["rejected"] == 0


def test_a_fabricated_quote_is_discarded():
    """The guarantee: a company the model 'knows' about but that the filing never
    mentions cannot reach the reader, because its quote is not in the text."""
    out = bz.extract(FILING, "Widget Co", client=FakeModel({
        "relationships": [{
            "counterparty": "Taiwan Semiconductor", "relationship": "supplier",
            "quote": "the Company sources its chips from Taiwan Semiconductor",
        }],
        "notes": [],
    }))
    assert out["relationships"].empty
    assert out["rejected"] == 1


def test_a_real_relationship_with_a_paraphrased_quote_is_also_discarded():
    """Even a true statement is dropped without an exact passage. The rule is about
    evidence, not about whether the claim happens to be right."""
    out = bz.extract(FILING, "Widget Co", client=FakeModel({
        "relationships": [{
            "counterparty": "Vulcan Metals", "relationship": "supplier",
            "quote": "Vulcan Metals is the sole supplier of titanium housings",
        }],
        "notes": [],
    }))
    assert out["relationships"].empty
    assert out["rejected"] == 1


def test_typographic_differences_do_not_reject_an_honest_quote():
    """Filings are full of non-breaking spaces and curly quotes. Failing a correct
    passage over a character nobody can see would throw away real work."""
    out = bz.extract(FILING, "Widget Co", client=FakeModel({
        "relationships": [{
            "counterparty": "Globex Corporation", "relationship": "competitor",
            "quote": "competes  primarily   with Globex Corporation.",
        }],
        "notes": [],
    }))
    assert len(out["relationships"]) == 1


def test_unnamed_dependencies_survive_as_notes():
    out = bz.extract(FILING, "Widget Co", client=FakeModel({
        "relationships": [],
        "notes": ["Certain components are obtained from limited sources"],
    }))
    assert out["notes"] == ["Certain components are obtained from limited sources"]


def test_the_business_section_is_found_and_bounded():
    section = bz.business_section(FILING)
    assert section.startswith("Item 1. Business")
    assert "Unresolved Staff Comments" not in section


def test_the_schema_demands_a_quote_for_every_relationship():
    props = bz.TOOL["input_schema"]["properties"]["relationships"]["items"]
    assert "quote" in props["required"]
    assert props["additionalProperties"] is False
