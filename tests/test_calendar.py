"""Earnings calendar and news aggregation."""
import datetime as dt
import json
import zipfile

import pandas as pd
import pytest

from portfolio_lab import research as rs


@pytest.fixture()
def archive(tmp_path):
    """A filer with a steady quarterly rhythm, as the real archive stores it."""
    today = dt.date.today()
    filings = []
    for back in range(1, 9):
        period = today - dt.timedelta(days=364 + back * 91)
        filed = period + dt.timedelta(days=34)
        filings.append(("10-Q" if back % 4 else "10-K", filed, period))
    # one filing whose projection lands a fortnight from now
    soon_period = today - dt.timedelta(days=364 + 20)
    filings.append(("10-Q", soon_period + dt.timedelta(days=34), soon_period))

    doc = {
        "cik": 4242,
        "filings": {"recent": {
            "form": [f[0] for f in filings],
            "filingDate": [f[1].isoformat() for f in filings],
            "reportDate": [f[2].isoformat() for f in filings],
            "accessionNumber": ["0001-24-00000" + str(i) for i in range(len(filings))],
            "primaryDocument": ["x.htm"] * len(filings),
            "primaryDocDescription": ["10-Q"] * len(filings),
        }},
    }
    path = tmp_path / "submissions.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("CIK0000004242.json", json.dumps(doc))
    return path


def test_the_next_report_is_projected_from_last_years_filing(archive):
    est = rs.next_report_estimate(archive, 4242)
    assert est is not None
    assert est["days_away"] >= 0
    assert est["source"].startswith("estimated")
    assert est["typical_lag_days"] == 34


def test_an_unknown_filer_yields_nothing_rather_than_a_guess(archive):
    assert rs.next_report_estimate(archive, 999999) is None


def test_news_from_several_companies_is_deduplicated_by_headline(monkeypatch):
    """One industry story filed under six holdings is one event, not six."""
    def fake_news(symbol, limit=6):
        return pd.DataFrame([
            {"when": "2026-09-20", "title": "Chip tariffs announced",
             "summary": "", "source": "Reuters", "url": "u1"},
            {"when": "2026-09-19", "title": f"{symbol} beats estimates",
             "summary": "", "source": "AP", "url": "u2"},
        ])

    monkeypatch.setattr(rs, "news", fake_news)
    feed = rs.market_news(["AAA", "BBB", "CCC"])
    shared = feed[feed.title == "Chip tariffs announced"].iloc[0]
    assert shared.n_tickers == 3
    assert shared.tickers == "AAA, BBB, CCC"
    assert len(feed) == 4          # one shared story plus three company-specific


def test_an_empty_symbol_list_returns_nothing():
    assert rs.market_news([]).empty
