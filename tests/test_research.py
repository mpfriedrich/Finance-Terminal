"""Single-company research: history, restatements, peers."""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import research as rs
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

D = dt.date


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    c.executemany(
        "INSERT INTO securities (cik, ticker, name, sector, exchange) VALUES (?,?,?,?,?)",
        [(1, "AAA", "Alpha Mining Corp", "Metal Mining", "NYSE"),
         (2, "BBB", "Beta Mining Inc", "Metal Mining", "NYSE"),
         (3, "CCC", "Gamma Software", "Services-Prepackaged Software", "NASDAQ")],
    )
    rows = []
    for year, rev, ni, eq in [(2023, 1000.0, 100.0, 500.0), (2024, 1200.0, 150.0, 600.0)]:
        rows += [
            (1, "revenue", rev, D(year, 1, 1), D(year, 12, 31), D(year + 1, 2, 1), f"a{year}", "Revenues", 0),
            (1, "net_income", ni, D(year, 1, 1), D(year, 12, 31), D(year + 1, 2, 1), f"a{year}", "NetIncomeLoss", 0),
            (1, "book_value", eq, None, D(year, 12, 31), D(year + 1, 2, 1), f"a{year}", "StockholdersEquity", 0),
            (1, "shares_outstanding", 100.0, None, D(year, 12, 31), D(year + 1, 2, 1), f"a{year}", "EntityCommonStockSharesOutstanding", 0),
        ]
    # 2023 equity later revised sharply upward
    rows.append((1, "book_value", 750.0, None, D(2023, 12, 31), D(2025, 6, 1), "a-rev", "StockholdersEquity", 0))
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) VALUES (?,?,?,?,?,?)",
        [("AAA", D(2023, 12, 29), 9.0, 10.0, 10.0, 1000),
         ("AAA", D(2024, 12, 31), 14.0, 15.0, 15.0, 1000)])
    return c


def test_lookup_by_ticker_and_by_name(con):
    assert rs.find_ticker(con, "aaa").iloc[0].ticker == "AAA"
    assert "AAA" in rs.find_ticker(con, "alpha").ticker.tolist()
    assert rs.find_ticker(con, "zzz").empty


def test_annual_history_keeps_flows_and_stocks_apart(con):
    h = rs.annual_history(con, 1).set_index("year")
    assert h.loc[2024, "revenue"] == 1200.0        # 12-month span
    assert h.loc[2024, "book_value"] == 600.0      # point in time
    assert h.loc[2024, "net_margin"] == pytest.approx(0.125)


def test_history_uses_the_first_reported_figure(con):
    """The 2023 equity was later revised to 750. History shows what was said then."""
    h = rs.annual_history(con, 1).set_index("year")
    assert h.loc[2023, "book_value"] == 500.0


def test_restatement_is_surfaced_with_its_size(con):
    r = rs.restatements(con, 1)
    row = r[r.metric == "book_value"].iloc[0]
    assert row.first_reported == 500.0
    assert row.later_value == 750.0
    assert row.change == pytest.approx(0.50)


def test_tiny_revisions_are_not_reported_as_findings(con):
    assert rs.restatements(con, 1, min_pct=0.90).empty


def test_valuation_history_uses_the_price_of_that_year(con):
    v = rs.valuation_history(con, "AAA", 1).set_index("year")
    assert v.loc[2024, "market_cap"] == pytest.approx(1500.0)   # 15 x 100 shares
    assert v.loc[2024, "price_to_earnings"] == pytest.approx(10.0)
    assert v.loc[2023, "price_to_book"] == pytest.approx(2.0)   # 1000 / 500


def test_peers_share_the_industry_and_exclude_the_company_itself():
    frame = pd.DataFrame([
        {"ticker": "AAA", "name": "Alpha", "sector": "Metal Mining", "market_cap": 1e9},
        {"ticker": "BBB", "name": "Beta", "sector": "Metal Mining", "market_cap": 1.2e9},
        {"ticker": "HUGE", "name": "Huge", "sector": "Metal Mining", "market_cap": 9e11},
        {"ticker": "CCC", "name": "Gamma", "sector": "Software", "market_cap": 1e9},
    ])
    p = rs.peers(frame, "AAA", limit=1)
    assert p.ticker.tolist() == ["BBB"]      # same industry, closest in size
