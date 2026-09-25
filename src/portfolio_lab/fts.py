"""Full-text search across SEC filings (EDGAR full-text search, 2001 onwards).

Words that appear in filings but in no number: "pricing pressure", "going concern",
"material weakness", "tariff". The SEC indexes the text of every filing since 2001 and
serves the search as JSON without a key. Phrases go in double quotes; forms, dates and a
single company can narrow it.

A hit is a document that contains the words, not a judgement about the company: "going
concern" appears in the risk factors of healthy firms too. The link opens the filing.
"""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlencode

import pandas as pd

from .config import SEC_USER_AGENT

ENDPOINT = "https://efts.sec.gov/LATEST/search-index"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
PAGE = 100          # the API returns up to 100 hits per request


def build_url(query: str, forms: list[str] | None = None, start: dt.date | None = None,
              end: dt.date | None = None, cik: int | None = None, offset: int = 0) -> str:
    params = {"q": query}
    if forms:
        params["forms"] = ",".join(forms)
    if start or end:
        params["dateRange"] = "custom"
        params["startdt"] = (start or dt.date(2001, 1, 1)).isoformat()
        params["enddt"] = (end or dt.date.today()).isoformat()
    if cik:
        params["ciks"] = f"{int(cik):010d}"
    if offset:
        params["from"] = str(offset)
    return f"{ENDPOINT}?{urlencode(params)}"


_TICKER = re.compile(r"\(([A-Z0-9.\-]{1,10}(?:, [A-Z0-9.\-]{1,10})*)\)")
_CIK = re.compile(r"\(CIK (\d+)\)")


def parse(payload: dict) -> tuple[pd.DataFrame, int]:
    """Hits as rows, and the total the SEC reports (it may be more than returned)."""
    hits = (payload.get("hits") or {}).get("hits") or []
    total = ((payload.get("hits") or {}).get("total") or {}).get("value", len(hits))
    rows = []
    for h in hits:
        src = h.get("_source") or {}
        names = src.get("display_names") or [""]
        display = names[0]
        ticker = _TICKER.search(display)
        cik = (src.get("ciks") or [None])[0]
        if cik is None:
            m = _CIK.search(display)
            cik = m.group(1) if m else None
        accession, _, document = (h.get("_id") or ":").partition(":")
        company = display.split(" (")[0].strip()
        rows.append({
            "company": company,
            "ticker": ticker.group(1).split(",")[0] if ticker else "",
            "form": src.get("form") or (src.get("root_forms") or [""])[0],
            "filed": src.get("file_date"),
            "period": src.get("period_ending"),
            "url": ARCHIVE.format(cik=int(cik), accession=accession.replace("-", ""),
                                  document=document) if cik and accession else "",
        })
    return pd.DataFrame(rows), int(total)


def search(query: str, forms: list[str] | None = None, start: dt.date | None = None,
           end: dt.date | None = None, cik: int | None = None, pages: int = 1,
           getter=None) -> tuple[pd.DataFrame, int]:
    """Run a search; `pages` × 100 hits at most. `getter(url) -> dict` for tests."""
    if not query.strip():
        return pd.DataFrame(), 0
    if getter is None:
        import requests

        def getter(url):
            r = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
            r.raise_for_status()
            return r.json()
    frames, total = [], 0
    for page in range(pages):
        frame, total = parse(getter(build_url(query, forms, start, end, cik,
                                              offset=page * PAGE)))
        frames.append(frame)
        if len(frame) < PAGE or (page + 1) * PAGE >= total:
            break
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out, total
