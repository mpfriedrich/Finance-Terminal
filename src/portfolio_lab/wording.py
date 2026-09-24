"""R23: the interface applies criteria and shows results. It does not advise.

The line this guards is not a matter of taste. The app has no idea what else someone
owns, what they earn, what they owe or when they need the money, and a tool that phrases
an output as "you should buy this" is making a recommendation it has no basis for. The
plan forbids it, and forbidding it in prose is worth nothing without a check, because the
tempting phrasing arrives one caption at a time.

So the banned phrasings are listed here and a test walks the interface's own strings
looking for them. The list is phrase-level on purpose: "buy" is an ordinary word in "buy
and hold", and a rule that banned it outright would be turned off within a week.

Also checked: no broker or order-routing dependency is installed. The surest way not to
place an order by accident is to have nothing in the environment that could.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# (pattern, why it is banned). Case-insensitive.
BANNED: tuple[tuple[str, str], ...] = (
    (r"\byou should (buy|sell|hold|invest|own)\b", "tells the reader what to do"),
    (r"\b(we|i) recommend\b", "a recommendation"),
    (r"\brecommend(s|ed)? (buying|selling|this|these|it)\b", "a recommendation"),
    (r"\bstrong buy\b", "broker rating language"),
    (r"\b(buy|sell) now\b", "urgency plus instruction"),
    (r"\bmust[- ]have\b", "instruction dressed as description"),
    (r"\bguaranteed (return|profit|gain|income)\b", "no return is guaranteed"),
    (r"\brisk[- ]free (return|profit|gain)\b", "no return is risk-free"),
    (r"\bcan'?t lose\b", "claims an impossible certainty"),
    (r"\bsure thing\b", "claims an impossible certainty"),
    (r"\bwill (outperform|beat the|rise|go up|double)\b", "predicts the future"),
    (r"\bis undervalued\b", "states an opinion as fact — say what the ratio is instead"),
    (r"\bbest (stock|pick|buy)s?\b", "a recommendation in superlative form"),
    (r"\btop pick", "a recommendation"),
    # German, because the plan names these and the person using this speaks it.
    (r"\bsolltest du\b", "tells the reader what to do"),
    (r"\bwir empfehlen\b", "a recommendation"),
    (r"\bjetzt kaufen\b", "urgency plus instruction"),
    (r"\bgarantierte? (rendite|gewinn)", "no return is guaranteed"),
)

# Anything that could route an order. Absence is the control (R23).
FORBIDDEN_DEPENDENCIES: tuple[str, ...] = (
    "ib_insync", "ibapi", "alpaca", "alpaca-trade-api", "ccxt", "robin_stocks",
    "tda-api", "tradier", "oandapyV20", "backtrader-live", "interactivebrokers",
)

# Strings that contain a banned phrase and are allowed anyway, each with the reason.
# The list is short on purpose: every entry is a hole in the rule, so an entry that
# cannot be justified in one line does not belong here.
ATTRIBUTED: dict[str, str] = {
    # Wall Street's own rating buckets, reported as third-party data under a heading that
    # says whose opinion it is. Renaming them would misrepresent the source.
    "strong buy": "analyst rating bucket, attributed to the analysts",
    "strong sell": "analyst rating bucket, attributed to the analysts",
}

# A banned phrase inside a prohibition is the rule being stated, not broken:
# "never recommend buying" must not trip the check that enforces it.
NEGATIONS = re.compile(r"\b(never|not|no|don'?t|cannot|can'?t|kein[e]?|nie|nicht)\b",
                       re.IGNORECASE)
NEGATION_WINDOW = 40  # characters before the match

_COMPILED = tuple((re.compile(p, re.IGNORECASE), why) for p, why in BANNED)


def scan_text(text: str) -> list[tuple[str, str]]:
    """Return (matched phrase, reason) for every banned phrasing in `text`.

    Two exemptions, both narrow: a phrase that appears inside an explicit prohibition,
    and a phrase listed in ATTRIBUTED as somebody else's words.
    """
    if text.strip().lower() in ATTRIBUTED:
        return []
    found: list[tuple[str, str]] = []
    for pattern, why in _COMPILED:
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - NEGATION_WINDOW):match.start()]
            if NEGATIONS.search(before):
                continue
            found.append((match.group(0), why))
    return found


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Ids of the string nodes that are docstrings.

    Docstrings are for whoever reads the code, not for whoever uses the app — and this
    very module's docstring quotes the phrases it bans, so scanning them would make the
    check fail on itself.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def strings_in(path: Path) -> list[str]:
    """Every string literal in a source file except docstrings and comments."""
    tree = ast.parse(path.read_text())
    skip = _docstring_nodes(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
    ]


def scan_files(paths: list[Path]) -> list[tuple[str, str, str]]:
    """(file, phrase, reason) for every banned phrasing in those files' UI strings."""
    hits: list[tuple[str, str, str]] = []
    for path in paths:
        for text in strings_in(path):
            for phrase, why in scan_text(text):
                hits.append((path.name, phrase, why))
    return hits
