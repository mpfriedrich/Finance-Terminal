"""EDGAR full-text search: the request and the reading of the answer — no network."""
import datetime as dt
from urllib.parse import parse_qs, urlparse

from portfolio_lab import fts

PAYLOAD = {"hits": {"total": {"value": 2}, "hits": [
    {"_id": "0000320193-25-000079:aapl-20250927.htm",
     "_source": {"ciks": ["0000320193"], "display_names": ["Apple Inc.  (AAPL)  (CIK 0000320193)"],
                 "form": "10-K", "file_date": "2025-10-31", "period_ending": "2025-09-27"}},
    {"_id": "0001045810-26-000010:nvda-10q.htm",
     "_source": {"ciks": ["0001045810"],
                 "display_names": ["NVIDIA CORP  (NVDA)  (CIK 0001045810)"],
                 "root_forms": ["10-Q"], "file_date": "2026-05-28"}},
]}}


def test_the_request_carries_the_phrase_forms_dates_and_company():
    url = fts.build_url('"pricing pressure"', ["10-K", "10-Q"], dt.date(2024, 1, 1),
                        dt.date(2026, 9, 24), cik=320193, offset=100)
    q = parse_qs(urlparse(url).query)
    assert q["q"] == ['"pricing pressure"'] and q["forms"] == ["10-K,10-Q"]
    assert q["startdt"] == ["2024-01-01"] and q["ciks"] == ["0000320193"]
    assert q["from"] == ["100"]


def test_hits_become_rows_with_ticker_and_a_link():
    frame, total = fts.parse(PAYLOAD)
    assert total == 2
    a = frame.iloc[0]
    assert a.company == "Apple Inc." and a.ticker == "AAPL" and a.form == "10-K"
    assert a.url == ("https://www.sec.gov/Archives/edgar/data/320193/"
                     "000032019325000079/aapl-20250927.htm")
    assert frame.iloc[1].form == "10-Q", "falls back to root_forms"


def test_an_empty_query_does_not_call_the_sec():
    def never(url):
        raise AssertionError("called")
    frame, total = fts.search("  ", getter=never)
    assert frame.empty and total == 0


def test_search_stops_when_the_results_run_out():
    calls = []

    def getter(url):
        calls.append(url)
        return PAYLOAD
    frame, total = fts.search("tariff", pages=5, getter=getter)
    assert len(calls) == 1 and len(frame) == 2
