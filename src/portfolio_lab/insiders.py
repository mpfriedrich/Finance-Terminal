"""Insider transactions from SEC Form 4 — who in the company bought or sold, at what price.

Every director, officer and 10% owner has to report a trade in the company's shares
within two business days, on Form 4. The filing is XML, free on EDGAR.

Most Form 4 lines are not decisions: grants (A), option exercises (M), shares withheld
for tax (F), gifts (G). The signal people mean by "insider buying" is the open-market
purchase (code P) — someone paying their own money at the market price — and, less
tellingly, the open-market sale (S), which has many reasons. The summary counts only
those two; the table shows everything, labelled.
"""

from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import pandas as pd

from .config import SEC_USER_AGENT

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

CODES = {
    "P": "open-market purchase", "S": "open-market sale", "A": "grant or award",
    "M": "option exercise", "F": "tax withholding", "G": "gift", "D": "disposed to issuer",
    "C": "conversion", "X": "option exercise", "J": "other", "W": "inheritance",
}


def _get_text(url: str, timeout: int = 30) -> str:
    import requests

    r = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.text


def form4_index(submissions: dict, limit: int = 40) -> list[dict]:
    """The latest Form 4 filings in a company's submissions document."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out = []
    for i, form in enumerate(forms):
        if form not in ("4", "4/A"):
            continue
        document = recent["primaryDocument"][i]
        # The listed document is the XSLT-rendered view ("xslF345X05/form4.xml"); the
        # raw XML is the same file name at the top of the filing folder.
        raw = document.split("/")[-1]
        out.append({"accession": recent["accessionNumber"][i],
                    "filed": recent["filingDate"][i], "document": raw})
        if len(out) >= limit:
            break
    return out


def _text(node, path: str) -> str | None:
    found = node.find(path)
    if found is None:
        return None
    value = found.find("value")
    text = (value.text if value is not None else found.text) or ""
    return text.strip() or None


def _num(node, path: str) -> float | None:
    t = _text(node, path)
    try:
        return float(t) if t is not None else None
    except ValueError:
        return None


def parse_form4(xml: str) -> list[dict]:
    """One row per non-derivative transaction in a Form 4 document."""
    root = ET.fromstring(xml)
    owner = root.find("reportingOwner")
    name = _text(owner, "reportingOwnerId/rptOwnerName") if owner is not None else None
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        if (_text(rel, "isDirector") or "").lower() in ("1", "true"):
            roles.append("director")
        if (_text(rel, "isOfficer") or "").lower() in ("1", "true"):
            roles.append(_text(rel, "officerTitle") or "officer")
        if (_text(rel, "isTenPercentOwner") or "").lower() in ("1", "true"):
            roles.append("10% owner")
    rows = []
    table = root.find("nonDerivativeTable")
    for tx in (table.findall("nonDerivativeTransaction") if table is not None else []):
        code = _text(tx, "transactionCoding/transactionCode")
        shares = _num(tx, "transactionAmounts/transactionShares")
        price = _num(tx, "transactionAmounts/transactionPricePerShare")
        direction = _text(tx, "transactionAmounts/transactionAcquiredDisposedCode")
        rows.append({
            "date": _text(tx, "transactionDate"),
            "insider": name, "role": ", ".join(roles) or "—",
            "code": code, "what": CODES.get(code or "", code or "—"),
            "shares": shares, "price": price,
            "value": shares * price if shares is not None and price else None,
            "direction": direction,
            "owned_after": _num(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
        })
    return rows


def fetch(cik: int, limit: int = 30, getter=None) -> pd.DataFrame:
    """The last `limit` Form 4 filings for a company, parsed. `getter(url) -> text`
    replaces the network in tests. A filing that fails to load or parse is skipped."""
    import json

    getter = getter or _get_text
    index = form4_index(json.loads(getter(SUBMISSIONS.format(cik=int(cik)))), limit)
    rows = []
    for f in index:
        url = ARCHIVE.format(cik=int(cik), accession=f["accession"].replace("-", ""),
                             document=f["document"])
        try:
            for r in parse_form4(getter(url)):
                rows.append({**r, "filed": f["filed"], "url": url})
        except Exception:                                           # noqa: BLE001
            continue
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.sort_values("date", ascending=False).reset_index(drop=True)
    return frame


def summary(frame: pd.DataFrame, asof: dt.date, months: int = 12) -> dict:
    """Open-market buying and selling over the last `months`: count, insiders, value."""
    if frame.empty:
        return {}
    since = asof - dt.timedelta(days=int(months * 30.44))
    recent = frame[pd.to_datetime(frame["date"]) >= pd.Timestamp(since)]
    out = {"months": months}
    for code, key in (("P", "bought"), ("S", "sold")):
        part = recent[recent.code == code]
        out[f"{key}_trades"] = len(part)
        out[f"{key}_insiders"] = int(part.insider.nunique())
        out[f"{key}_value"] = float(part["value"].fillna(0).sum())
    return out
