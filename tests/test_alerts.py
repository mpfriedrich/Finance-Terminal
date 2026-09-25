"""Alerts: each kind fires on the bar it should, once, and never again for that bar."""
import datetime as dt
import json

import duckdb
import pandas as pd
import pytest

from portfolio_lab import alerts
from portfolio_lab.data.schema import MARKET_MIGRATIONS, USER_MIGRATIONS, _apply


def bars(closes, volumes=None):
    n = len(closes)
    return pd.DataFrame({"date": pd.bdate_range("2026-06-01", periods=n).date,
                         "close": closes, "volume": volumes or [1000] * n})


@pytest.mark.parametrize("kind, x, closes, fires", [
    ("price_above", 100, [99, 101], True),
    ("price_above", 100, [101, 102], False),     # already above: no repeat
    ("price_below", 50, [51, 49.5], True),
    ("move_up", 5, [100, 106], True),
    ("move_up", 5, [100, 104], False),
    ("move_down", 5, [100, 94], True),
])
def test_each_price_kind(kind, x, closes, fires):
    msg = alerts.evaluate({"ticker": "AAA", "kind": kind, "threshold": x}, bars(closes))
    assert bool(msg) is fires


def test_a_volume_spike_against_the_twenty_days_before():
    b = bars([10.0] * 22, [1000] * 21 + [3500])
    msg = alerts.evaluate({"ticker": "AAA", "kind": "volume_spike", "threshold": 3}, b)
    assert "3.5×" in msg


@pytest.fixture()
def stores():
    user = duckdb.connect(":memory:")
    _apply(user, USER_MIGRATIONS)
    market = duckdb.connect(":memory:")
    _apply(market, MARKET_MIGRATIONS)
    market.execute("INSERT INTO securities (cik, ticker) VALUES (320193, 'AAA')")
    days = pd.bdate_range("2026-06-01", periods=3).date
    market.executemany("INSERT INTO prices (ticker, date, open, close, close_adj, volume) "
                       "VALUES ('AAA', ?, ?, ?, ?, 1000)",
                       [(d, c, c, c) for d, c in zip(days, [99.0, 99.5, 101.0])])
    return user, market


def test_an_event_is_reported_once_per_bar(stores):
    user, market = stores
    alerts.add(user, "aaa", "price_above", 100)
    events, state = alerts.run(user, market, {})
    assert len(events) == 1 and "above 100.00" in events[0]["message"]
    again, state = alerts.run(user, market, state)
    assert again == [] and len(state["log"]) == 1


def test_a_new_filing_is_reported_after_the_first_check(stores):
    user, market = stores
    alerts.add(user, "AAA", "new_filing", None)
    sub = {"filings": {"recent": {"form": ["8-K", "10-Q"],
                                  "filingDate": ["2026-06-10", "2026-05-01"]}}}
    getter = lambda url: json.dumps(sub)
    first, state = alerts.run(user, market, {}, getter, today=dt.date(2026, 6, 5))
    assert first == [], "the first check only remembers where it is"
    second, state = alerts.run(user, market, state, getter, today=dt.date(2026, 6, 12))
    assert len(second) == 1 and second[0]["message"] == "AAA filed 8-K"
    third, _ = alerts.run(user, market, state, getter, today=dt.date(2026, 6, 13))
    assert third == []


def test_a_threshold_is_required_where_it_means_something(stores):
    user, _ = stores
    with pytest.raises(ValueError):
        alerts.add(user, "AAA", "price_above", None)


def test_state_survives_a_round_trip(tmp_path):
    path = tmp_path / "s.json"
    alerts.save_state(path, {"fired": {"x|2026-06-03": "t"}})
    assert alerts.load_state(path)["fired"] == {"x|2026-06-03": "t"}
    assert alerts.load_state(tmp_path / "missing.json") == {}
