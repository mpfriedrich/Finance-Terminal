"""Form 4 parsing and the open-market summary — no network; the SEC is a stand-in."""
import datetime as dt
import json

import pytest

from portfolio_lab import insiders

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>0000320193</issuerCik><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>COOK TIMOTHY D</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector><isOfficer>1</isOfficer>
      <officerTitle>Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-03</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>200.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts><sharesOwnedFollowingTransaction><value>51000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-03</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>300</value></transactionShares>
        <transactionPricePerShare><value>200.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""

SUBMISSIONS = {"filings": {"recent": {
    "form": ["10-Q", "4", "4"],
    "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002",
                        "0000320193-26-000003"],
    "filingDate": ["2026-08-01", "2026-08-05", "2026-02-10"],
    "primaryDocument": ["q.htm", "xslF345X05/wk-form4_1.xml", "xslF345X05/wk-form4_2.xml"],
}}}


def test_a_form4_line_is_read_with_the_insider_and_role():
    rows = insiders.parse_form4(FORM4)
    assert len(rows) == 2
    p = rows[0]
    assert p["insider"] == "COOK TIMOTHY D"
    assert p["role"] == "director, Chief Executive Officer"
    assert p["what"] == "open-market purchase" and p["value"] == pytest.approx(200_500)
    assert p["owned_after"] == 51000
    assert rows[1]["what"] == "tax withholding"


def test_the_index_finds_form4s_and_their_raw_xml():
    idx = insiders.form4_index(SUBMISSIONS)
    assert [f["document"] for f in idx] == ["wk-form4_1.xml", "wk-form4_2.xml"]


def test_fetch_puts_it_together_and_skips_a_filing_that_fails():
    def getter(url):
        if "submissions" in url:
            return json.dumps(SUBMISSIONS)
        if url.endswith("wk-form4_2.xml"):
            raise OSError("gone")
        return FORM4
    frame = insiders.fetch(320193, getter=getter)
    assert len(frame) == 2 and set(frame.code) == {"P", "F"}
    assert frame.url.iloc[0].endswith("000032019326000002/wk-form4_1.xml")


def test_the_summary_counts_only_open_market_trades():
    frame = insiders.fetch(320193, getter=lambda u: json.dumps(SUBMISSIONS)
                           if "submissions" in u else FORM4)
    s = insiders.summary(frame, dt.date(2026, 9, 1))
    assert s["bought_trades"] == 2 and s["bought_insiders"] == 1   # the P line, twice filed
    assert s["sold_trades"] == 0, "tax withholding is not a sale decision"
