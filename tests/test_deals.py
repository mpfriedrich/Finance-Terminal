"""Precedent transactions: multiples from the target's figures as of the announcement.

Uses the Sharadar-layout test store: AAA reports equity 900m, 100m shares, quarterly
revenue 250m, net income 30m, EBIT 45m, D&A 25m, debt 100m + 10m, cash 50m; each
quarter is filed 40 days after it ends. We add a later filing (debt 900m, filed August
2026) that a June deal must not see.
"""
import datetime as dt

import duckdb
import pytest

from portfolio_lab import deals
from portfolio_lab.data.schema import USER_MIGRATIONS, _apply
from tests.paid_fixture import build_store


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    src = build_store(tmp_path_factory.mktemp("deals"))
    c = src.con
    c.execute("INSERT INTO fundamentals (cik, metric, value, period_end, filed, "
              "accession, xbrl_tag, tag_rank, currency) VALUES (101, 'long_term_debt', "
              "900e6, DATE '2026-06-30', DATE '2026-08-09', 'x', 't', 0, 'USD')")
    return c


@pytest.fixture()
def user():
    c = duckdb.connect(":memory:")
    _apply(c, USER_MIGRATIONS)
    return c


def test_a_deal_is_valued_on_what_was_filed_by_the_announcement(market, user):
    """Announced 1 June 2026: TTM revenue 1,000m, EBITDA 280m (4 × (45 + 25)), net
    debt 60m. Offer 30 a share × 100m shares = 3,000m equity, EV 3,060m."""
    deals.add(user, "AAA", dt.date(2026, 6, 1), "Buyer Co", offer_price=30.0)
    row = deals.value(market, deals.listing(user).iloc[0].to_dict())
    assert row["equity_value"] == pytest.approx(3_000e6)
    assert row["enterprise_value"] == pytest.approx(3_060e6)
    assert row["ev_to_revenue"] == pytest.approx(3.06)
    assert row["ev_to_ebitda"] == pytest.approx(3_060 / 280)
    assert row["price_to_earnings"] == pytest.approx(3_000 / 120)
    assert row["unaffected_price"] is not None and row["premium"] > 0


def test_a_filing_after_the_announcement_is_not_used(market, user):
    """Valued as of 1 June 2026, the 900m of debt filed in August is not known yet —
    the multiple uses the balance sheet on file then."""
    deals.add(user, "AAA", dt.date(2026, 6, 1), offer_price=30.0)
    row = deals.value(market, deals.listing(user).iloc[0].to_dict())
    assert row["net_debt"] == pytest.approx(60e6)


def test_a_delisted_target_is_found_by_cik(market, user):
    deals.add(user, "104", dt.date(2025, 7, 1), equity_value=2_000e6)   # DDD1's permaticker
    row = deals.value(market, deals.listing(user).iloc[0].to_dict())
    assert row["cik"] == 104 and row["equity_value"] == 2_000e6


def test_the_table_summarises_and_the_medians_apply_to_another_company(market, user):
    deals.add(user, "AAA", dt.date(2026, 6, 1), offer_price=30.0)
    frame, stats = deals.table(market, user)
    assert stats.at["n", "ev_to_ebitda"] == 1
    import pandas as pd
    subject = pd.Series({"ebitda_ttm": 100.0, "net_debt": 50.0, "shares_outstanding": 10.0,
                         "price": 20.0, "revenue_ttm": 0.0})
    implied = deals.apply_to(stats, subject).set_index("multiple")
    m = stats.at["median", "ev_to_ebitda"]
    assert implied.at["ev_to_ebitda", "implied price"] == pytest.approx((m * 100 - 50) / 10)
    assert "ev_to_revenue" not in implied.index, "no multiple on a zero figure"


def test_a_deal_needs_a_price(user):
    with pytest.raises(ValueError):
        deals.add(user, "AAA", dt.date(2026, 6, 1))
