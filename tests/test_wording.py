"""R23: the interface applies criteria and shows results — it does not advise.

Two halves, and the first matters more than it looks: a scanner that never catches
anything passes on an empty codebase just as happily as on a clean one. So the samples
below prove the check has teeth BEFORE the real interface is asked to pass it.
"""
import tomllib
from pathlib import Path

import pytest

from portfolio_lab import wording

ROOT = Path(__file__).resolve().parents[1]
UI_FILES = [ROOT / "app.py"] + [
    p for p in sorted((ROOT / "src" / "portfolio_lab").rglob("*.py"))
    if "__pycache__" not in str(p)
]


# --- the check has teeth ---------------------------------------------------------------

@pytest.mark.parametrize("sentence", [
    "You should buy this one before the gap closes.",
    "We recommend buying the top three names.",
    "Analysts call it a strong buy and so do we.",
    "Buy now while the discount lasts.",
    "A guaranteed return of 8% a year.",
    "This is a risk-free profit.",
    "It will outperform the index.",
    "PRGO is undervalued.",
    "The best stocks in the screen.",
    "Solltest du kaufen, bevor es teurer wird.",
    "Wir empfehlen die drei günstigsten Titel.",
])
def test_recommendation_language_is_caught(sentence):
    assert wording.scan_text(sentence), f"missed: {sentence}"


@pytest.mark.parametrize("sentence", [
    "Buy and hold — no rebalancing.",
    "Stage A buys once and holds.",
    "Ticking is what decides the portfolio.",
    "Price to book at most 2.",
    "The screen applies your criteria to public filings.",
    "Entry costs are charged to the portfolio and not to the index.",
])
def test_ordinary_description_is_not_caught(sentence):
    """A rule that fires on 'buy and hold' gets switched off within a week."""
    assert wording.scan_text(sentence) == [], f"false positive: {sentence}"


def test_a_prohibition_is_not_a_violation():
    """'Never recommend buying anything' enforces the rule; it does not break it."""
    assert wording.scan_text("Never recommend buying or selling anything.") == []
    assert wording.scan_text("Do not tell anyone they should buy something.") == []
    # ...but the same words without the prohibition still fail
    assert wording.scan_text("Recommend buying the cheapest three.")


def test_attributed_third_party_labels_are_allowed_and_listed():
    """'Strong buy' as an analyst rating bucket is somebody else's word, reported as
    data. The exemption is explicit and named, not a hole in the pattern."""
    assert wording.scan_text("strong buy") == []
    assert "strong buy" in wording.ATTRIBUTED
    # The exemption is exact-match only: it cannot be used to smuggle a sentence through.
    assert wording.scan_text("Our verdict: strong buy on this one.")


# --- and the real interface passes it --------------------------------------------------

def test_the_interface_contains_no_recommendation_language():
    hits = wording.scan_files(UI_FILES)
    assert hits == [], "\n".join(f"{f}: '{phrase}' — {why}" for f, phrase, why in hits)


def test_nothing_installed_could_place_an_order():
    """The dependency half of R23. Not routing orders is easiest to guarantee when there
    is nothing in the environment that knows how to."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    declared = " ".join(pyproject["project"]["dependencies"]).lower()
    for package in wording.FORBIDDEN_DEPENDENCIES:
        assert package.lower() not in declared, f"{package} can place orders"


def test_the_footer_disclaimer_is_still_on_screen():
    """The sentence that has to survive every refactor — and it has to be a string the
    app actually renders, not a line in a docstring that only a developer ever reads.
    The first version of this test passed on the module docstring, which says the right
    thing to nobody."""
    rendered = " ".join(wording.strings_in(ROOT / "app.py")).lower()
    assert "places no orders" in rendered
    assert "connects to no broker" in rendered
    assert "recommends no security" in rendered


# --- the chat log is a sink, and text put into it is escaped --------------------------

def test_the_conversation_log_escapes_what_it_renders():
    """Everything in the log came from a text box or from a model, and it is rendered
    with unsafe_allow_html. Escaping at the sink is the only place that can be relied
    on — not the tool schema, and not the person's good manners."""
    from portfolio_lab import ui

    rendered = ui.log([("user", "<script>alert(1)</script>"),
                       ("assistant", "5 < 6 & 7 > 2")])
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered


def test_no_hand_rolled_chat_markup_bypasses_the_escaping():
    """The strategy lab originally built its own <div> with an f-string, which skipped
    the escaping that ui.log does. One helper, one sink."""
    source = (ROOT / "app.py").read_text()
    assert 'class="tl-msg"' not in source
    assert 'class="tl-reply"' not in source
