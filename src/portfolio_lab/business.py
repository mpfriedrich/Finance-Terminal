"""Who a company depends on, taken from its own annual report.

There is no free structured dataset of supply-chain relationships — that is what
Bloomberg sells. But companies describe their customers, suppliers and competitors
themselves, in Item 1 of the 10-K, and a customer worth more than 10% of revenue must be
named under the SEC's concentration-of-risk rules. The relationships are in the filings;
they are just in prose.

So a language model reads the filing and pulls them out. That is the one place in this
project where a model touches facts, so it is fenced in hard:

1. **Every claim must carry a verbatim quote** from the document. The schema requires it.
2. **Every quote is then checked against the source text in code.** A quote that does not
   appear — even off by punctuation — is dropped before the reader ever sees it. The
   model cannot talk its way past this; it is string matching, not judgement.
3. **Nothing is inferred.** "Apple probably buys chips from TSMC" is exactly the kind of
   plausible, unsourced statement this project exists to avoid. If the filing does not
   say it, it does not appear.

What survives is not analysis. It is a set of passages from a filing, with the company's
own words attached, and the date it said them.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd
import requests

from .config import SEC_USER_AGENT

MODEL = "claude-opus-5"
MAX_CHARS = 120_000  # Item 1 of a large 10-K; beyond this the tail is rarely relationships


def fetch_filing_text(url: str, timeout: int = 60) -> str:
    """Download a filing and reduce it to plain text."""
    resp = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", resp.text)
    return normalise(text)


def normalise(text: str) -> str:
    """Collapse whitespace and unify the characters filings are full of.

    Filings use non-breaking spaces, curly quotes and soft hyphens freely. Without
    flattening them, a quote the model copied correctly would fail the verification check
    on a character the reader cannot even see.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("­", "")
    return re.sub(r"\s+", " ", text).strip()


def business_section(text: str) -> str:
    """The part of a 10-K that describes the business, if it can be located.

    Item 1 is where customers, suppliers, competition and dependencies are described.
    Falling back to the whole document is fine — it only costs tokens, and the quote
    check does the real work either way.
    """
    lowered = text.lower()
    start = None
    for marker in ("item 1. business", "item 1 business", "item 1. business."):
        idx = lowered.find(marker)
        # The table of contents mentions it first; the section itself is further in.
        if idx != -1:
            later = lowered.find(marker, idx + 1)
            start = later if later != -1 else idx
            break
    if start is None:
        return text[:MAX_CHARS]
    # Look for the next item heading past a short offset. The offset exists because a
    # table of contents lists "Item 1" and "Item 1B" within a line or two of each other;
    # a real section is always longer than that. Too large an offset, though, and a short
    # filing loses its boundary and swallows the next section.
    end = lowered.find("item 1b", start + 200)
    if end == -1:
        end = lowered.find("item 2", start + 200)
    if end == -1:
        end = start + MAX_CHARS
    return text[start:end][:MAX_CHARS]


TOOL = {
    "name": "record_relationships",
    "description": (
        "Record business relationships that the filing STATES. Only what the document "
        "says — never what you know from elsewhere, and never an inference."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["relationships", "notes"],
        "properties": {
            "relationships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["counterparty", "relationship", "quote"],
                    "properties": {
                        "counterparty": {
                            "type": "string",
                            "description": "The other party, exactly as the filing names it.",
                        },
                        "relationship": {
                            "type": "string",
                            "enum": ["customer", "supplier", "partner", "competitor",
                                     "distributor", "dependency"],
                        },
                        "quote": {
                            "type": "string",
                            "description": (
                                "A VERBATIM span copied from the filing, 15 to 400 "
                                "characters, that states this. Copied exactly — it is "
                                "checked against the document and dropped if it does "
                                "not match."
                            ),
                        },
                    },
                },
            },
            "notes": {
                "type": "array",
                "description": (
                    "What the filing says about dependencies WITHOUT naming anyone — "
                    "concentration of revenue, reliance on single-source suppliers, and "
                    "the like. Each entry must also be a verbatim quote."
                ),
                "items": {"type": "string"},
            },
        },
    },
}

SYSTEM = """You read one company's annual report and extract the business relationships \
it states.

Rules, in order of importance:
- Only record what the document says. Never add a company you know is a supplier or \
customer from anywhere else. An unstated relationship is not a finding.
- Every entry carries a verbatim quote copied from the text. It is checked against the \
document in code, and anything that does not match is discarded before anyone reads it.
- Do not paraphrase inside the quote, do not join two sentences, do not fix typography.
- Unnamed but material dependencies go in `notes` — "one customer accounted for 22% of \
net sales" is valuable even without the name.
- If the filing names nobody, return empty lists. That is a correct answer."""


def _fuzzy_contains(haystack: str, needle: str) -> bool:
    """Is this quote really in the document?

    Compared on letters and digits only. A model that copies a passage correctly can
    still differ on a space or a stray hyphen, and failing it for that would throw away
    honest work — while a fabricated quote does not survive this at all.
    """
    strip = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    return strip(needle) in strip(haystack)


def extract(text: str, company: str, client=None) -> dict:
    """Ask the model, then verify every quote against the text."""
    if client is None:
        import anthropic

        from .config import require_secret

        client = anthropic.Anthropic(api_key=require_secret("ANTHROPIC_API_KEY"))

    section = business_section(text)
    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        tools=[TOOL],
        tool_choice={"type": "tool", "name": TOOL["name"]},
        messages=[{
            "role": "user",
            "content": f"Company: {company}\n\nFiling text:\n\n{section}",
        }],
    )
    payload = next((b.input for b in response.content if b.type == "tool_use"), None)
    if payload is None:
        return {"relationships": pd.DataFrame(), "notes": [], "rejected": 0}

    rows, rejected = [], 0
    for item in payload.get("relationships", []):
        quote = (item.get("quote") or "").strip()
        if quote and _fuzzy_contains(section, quote):
            rows.append({
                "counterparty": item.get("counterparty", ""),
                "relationship": item.get("relationship", ""),
                "quote": quote,
            })
        else:
            rejected += 1

    notes, note_rejected = [], 0
    for note in payload.get("notes", []):
        if note and _fuzzy_contains(section, note.strip()):
            notes.append(note.strip())
        else:
            note_rejected += 1

    return {
        "relationships": pd.DataFrame(rows),
        "notes": notes,
        "rejected": rejected + note_rejected,
        "section_chars": len(section),
    }
