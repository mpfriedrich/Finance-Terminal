"""R23: the interface applies criteria and shows results — it does not advise.

Two halves, and the first matters more than it looks: a scanner that never catches
anything passes on an empty codebase just as happily as on a clean one. So the samples
below prove the check has teeth BEFORE the real interface is asked to pass it.
"""
import re
import tomllib
from pathlib import Path

import pytest

from portfolio_lab import wording

ROOT = Path(__file__).resolve().parents[1]
UI_FILES = [ROOT / "app.py", ROOT / "src" / "portfolio_lab" / "command.py"] + [
    p for p in sorted((ROOT / "src" / "portfolio_lab").rglob("*.py"))
    if "__pycache__" not in str(p)
    # The banned-phrase list itself: its patterns are strings, and since F23 some of
    # them (a group in brackets) match their own source text.
    and p.name != "wording.py"
]
# Audit 24.10.2026 F23: the morning briefing is written and mailed from scripts/, so
# its strings reach a reader just as the app's do.
SCRIPT_FILES = sorted((ROOT / "scripts").glob("*.py"))


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


# Audit 24.10.2026 F23: one sentence per German pattern, so that each pattern is shown
# to bite on its own and a pattern that silently stops matching fails by name.
GERMAN_ADVICE = [
    "Du solltest die drei billigsten Titel nehmen.",
    "Ihr solltet jetzt umschichten.",
    "Sie sollten diese Aktie halten.",
    "Sollten Sie jetzt kaufen? Ja.",
    "Unsere Kaufempfehlung für diese Woche.",
    "Eine klare Verkaufsempfehlung.",
    "Wir raten zu Vorsicht bei Nebenwerten.",
    "Ich empfehle den günstigsten Titel.",
    "Ein Einstieg empfiehlt sich vor den Zahlen.",
    "Kaufen Sie, solange der Kurs tief ist.",
    "Jetzt einsteigen, bevor die Zahlen kommen.",
    "PRGO ist unterbewertet.",
    "Der Kurs wird steigen.",
    "Ein sicherer Gewinn von 8 % im Jahr.",
    "Eine risikolose Rendite.",
    "Mit diesem Titel kann man nicht verlieren.",
    "Die besten Aktien im Screen: unser Top-Tipp.",
]


@pytest.mark.parametrize("sentence", GERMAN_ADVICE)
def test_german_advice_language_is_caught(sentence):
    assert wording.scan_text(sentence), f"missed: {sentence}"


def test_every_german_pattern_has_its_own_sample():
    """Each German pattern added for F23 matches at least one sample above, so none of
    them is decoration."""
    start = next(i for i, (p, _) in enumerate(wording.BANNED) if "du solltest" in p)
    for pattern, _ in wording.BANNED[start:]:
        assert any(re.search(pattern, s, re.IGNORECASE) for s in GERMAN_ADVICE), pattern


@pytest.mark.parametrize("sentence", [
    "Kaufen und Halten, ohne Umschichtung.",
    "Der risikolose Zins kommt von FRED.",
    "Sollten Sie Fragen haben, steht es im README.",
    "Die Bewertungen der Analysten sind Fremdmeinungen.",
    "Die Regel kauft einmal und hält.",
    "Die Spalte zeigt, wozu die Analysten raten.",
])
def test_ordinary_german_is_not_caught(sentence):
    assert wording.scan_text(sentence) == [], f"false positive: {sentence}"


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
    hits = wording.scan_files(UI_FILES + SCRIPT_FILES)
    assert hits == [], "\n".join(f"{f}: '{phrase}' — {why}" for f, phrase, why in hits)


def test_the_mailed_briefing_is_part_of_the_scan():
    """Audit 24.10.2026 F23: scripts/ was outside the scan, and the morning briefing is
    mailed from there."""
    assert ROOT / "scripts" / "morning_briefing.py" in SCRIPT_FILES


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


def test_model_text_is_escaped_before_it_is_rendered():
    """The daily briefing put the model's text, written from fetched headlines, into
    unsafe_allow_html (audit 02.10.2026). It now goes through ui.markdown_text."""
    from portfolio_lab import ui

    safe = ui.markdown_text('Shares fell <img src=x onerror="alert(1)"> **3 %**')
    assert "<img" not in safe and "**3 %**" in safe
    source = (ROOT / "app.py").read_text()
    assert "{brief['text']}" not in source
    assert 'ui.markdown_text(brief["text"])' in source

    # Headlines (review 03.10.2026, F8): CN rendered title and URL raw inside
    # unsafe_allow_html. The title is escaped; only an http(s) address becomes a link.
    shown = ui.headline('<img src=x onerror="alert(1)"> Fed [cuts]',
                        "https://example.com/a(b)")
    assert "<img" not in shown and "&lt;img" in shown
    assert shown.endswith("(https://example.com/a%28b%29)**")
    assert "\\[cuts\\]" in shown
    assert ui.headline("Click", "javascript:alert(1)") == "**Click**"
    assert ui.headline("Click", "data:text/html,<b>x</b>") == "**Click**"
    assert "[{row.title}]({row.url})" not in source
    assert source.count("ui.headline(row.title, row.url)") == 2


def test_no_raw_exception_text_reaches_the_screen():
    """R24 (review 03.10.2026, F12b): the volatility surface and the briefing showed
    `{exc}` as it came, URL and query string included. Everything shown, or stored as
    a chat line, goes through ui.error_text."""
    import re

    source = (ROOT / "app.py").read_text()
    body = source[source.index("if not st.runtime.exists()"):]   # past the import guard
    raw = re.findall(r'f"[^"\n]*\{exc\}[^"\n]*"', body)
    assert raw == [], raw
