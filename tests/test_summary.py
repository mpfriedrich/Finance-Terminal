"""The AI summary: what it is given, what it asks for, and what it does on a refusal.

No network: the model is replaced by a stand-in that records the request. What can be
tested is that the data sheet is the app's own numbers, that the request carries the
rules and the fallback, and that a refusal never looks like an empty summary.
"""
import datetime as dt
import json
from types import SimpleNamespace

import duckdb
import pytest

from portfolio_lab import summary
from portfolio_lab.data.schema import USER_MIGRATIONS, _apply
from portfolio_lab.screen.engine import derive_metrics
from tests.paid_fixture import END, build_store


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    return build_store(tmp_path_factory.mktemp("sheet"))


@pytest.fixture(scope="module")
def sheet(market):
    frame = derive_metrics(market.screening_frame(END))
    row = frame.set_index("ticker").loc["AAA"]
    row["ticker"] = "AAA"
    return summary.data_sheet(market.con, "AAA", 101, row, END, frame=frame)


def test_the_sheet_carries_the_apps_own_numbers(sheet):
    assert sheet["valuation_now"]["book_value"] == 900e6
    assert sheet["annual_as_first_filed"], "the filed history is in the sheet"
    assert sheet["quarterly_trend"]
    assert sheet["price_behaviour"]


def test_the_sheet_is_plain_json(sheet):
    json.dumps(sheet)


def test_peers_come_from_the_same_frame(sheet):
    assert "CCC" in {p["ticker"] for p in sheet["peers"]}


class FakeClient:
    def __init__(self, stop_reason="end_turn", text="**In one line** — ok."):
        self.kwargs = None
        self._reply = SimpleNamespace(
            stop_reason=stop_reason, model=summary.MODEL,
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=text)] if text else [])
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return self._reply


def test_the_request_carries_the_rules_the_data_and_the_fallback(sheet):
    client = FakeClient()
    out = summary.summarize(sheet, client=client)
    assert out["text"].startswith("**In one line**")
    k = client.kwargs
    assert k["model"] == "claude-opus-5"
    assert k["fallbacks"] == "default" and summary.FALLBACK_BETA in k["betas"]
    assert "ONLY the figures" in k["system"] and "No recommendation" in k["system"]
    assert '"book_value": 900000000.0' in k["messages"][0]["content"]


def test_a_refusal_is_reported_not_shown_as_an_empty_summary(sheet):
    out = summary.summarize(sheet, client=FakeClient(stop_reason="refusal", text=None))
    assert out["text"] is None and out["stop_reason"] == "refusal" and out["error"]


def test_a_summary_is_stored_per_data_build():
    con = duckdb.connect(":memory:")
    _apply(con, USER_MIGRATIONS)
    summary.save(con, "aaa", "build-1", {"text": "one", "model": "m"})
    assert summary.stored(con, "AAA", "build-1")["text"] == "one"
    assert summary.stored(con, "AAA", "build-2") is None
    assert summary.latest(con, "AAA")["data_fingerprint"] == "build-1"
