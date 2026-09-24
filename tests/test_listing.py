"""Foreign filers end to end: euros, an exchange rate, and an ADR that stands for ten
ordinary shares. Each step has a way to be silently wrong by a large factor, so each
answer here is worked out by hand.
"""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab.data import listing
from portfolio_lab.data.base import build_screening_frame
from portfolio_lab.data.free import FreeSource
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply
from portfolio_lab.screen.engine import derive_metrics, run_screen
from portfolio_lab.screen.spec import PortfolioSpec

ASOF = dt.date(2024, 6, 28)
DAYS = pd.bdate_range("2024-01-02", ASOF).date


# --- snapping a measurement to a real ratio --------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (10.02, 10.0), (1.0, 1.0), (0.51, 0.5), (1.97, 2.0), (3.7, None), (None, None),
    (0.0, None),
])
def test_a_measurement_counts_only_near_a_ratio_programmes_use(raw, expected):
    assert listing.snap_ratio(raw) == expected


# --- the whole path ----------------------------------------------------------------------

def store(with_ratio: bool) -> FreeSource:
    """TOYX: a Japanese-style filer reporting in euros (to keep the rate simple) under a
    20-F, 1 billion ordinary shares, equity EUR 5bn, listed in the US as an ADR worth
    ten ordinary shares at $100. DOMX: a domestic 10-K filer, 50m shares at $20."""
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    c.executemany(
        "INSERT INTO securities (cik, ticker, name, sector, sic, exchange) "
        "VALUES (?,?,?,?,?,?)",
        [(501, "TOYX", "Toy Motor ADR", "Motor Vehicles", "3711", "NYSE"),
         (502, "DOMX", "Domestic Co", "Motor Vehicles", "3711", "NYSE")])
    filed = dt.date(2024, 5, 20)
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank, currency, form) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(501, "book_value", 5e9, None, dt.date(2024, 3, 31), filed, "t", "Equity", 0,
          "EUR", "20-F"),
         (501, "shares_outstanding", 1e9, None, dt.date(2024, 3, 31), filed, "t",
          "EntityCommonStockSharesOutstanding", 0, None, "20-F"),
         (502, "book_value", 500e6, None, dt.date(2024, 3, 31), filed, "d",
          "StockholdersEquity", 0, "USD", "10-K"),
         (502, "shares_outstanding", 50e6, None, dt.date(2024, 3, 31), filed, "d",
          "EntityCommonStockSharesOutstanding", 0, None, "10-K")])
    rows = ([("TOYX", d, 100.0, 100.0, 100.0, 100.0, 2_000_000) for d in DAYS]
            + [("DOMX", d, 20.0, 20.0, 20.0, 20.0, 2_000_000) for d in DAYS]
            + [("EURUSD=X", d, 1.1, 1.1, 1.1, 1.1, 0) for d in DAYS])
    c.executemany("INSERT INTO prices (ticker, date, open, close, close_raw, close_adj, "
                  "volume) VALUES (?,?,?,?,?,?,?)", rows)
    if with_ratio:
        c.execute("INSERT INTO listing_ratios VALUES ('TOYX', 10.0, 'test', ?, NULL)",
                  [ASOF])
    return FreeSource(c)


def test_an_adr_is_valued_on_its_listed_shares_in_dollars():
    """1bn ordinary / 10 = 100m ADRs × $100 = $10bn market cap. Equity EUR 5bn × 1.10 =
    $5.5bn. Price to book 10 / 5.5 = 1.818…; the ordinary count would have said 18.2."""
    d = derive_metrics(store(with_ratio=True).screening_frame(ASOF)).set_index("ticker")
    assert d.at["TOYX", "shares_outstanding"] == pytest.approx(100e6)
    assert d.at["TOYX", "market_cap"] == pytest.approx(10e9)
    assert d.at["TOYX", "book_value"] == pytest.approx(5.5e9)
    assert d.at["TOYX", "price_to_book"] == pytest.approx(10 / 5.5)


def test_a_domestic_filer_is_untouched():
    d = derive_metrics(store(with_ratio=True).screening_frame(ASOF)).set_index("ticker")
    assert d.at["DOMX", "listing_ratio"] == 1.0
    assert d.at["DOMX", "market_cap"] == pytest.approx(1e9)


def test_a_foreign_listing_without_a_measured_ratio_is_excluded_by_name():
    frame = store(with_ratio=False).screening_frame(ASOF)
    r = run_screen(frame, PortfolioSpec(size=10, min_dollar_volume=0,
                                        min_trading_days_60=0))
    assert r.exclusions.get("foreign listing: ADR ratio not measured") == 1
    assert "TOYX" not in set(r.ranked.ticker)
    assert sum(r.exclusions.values()) == r.n_universe - r.n_eligible


def test_the_fast_frame_and_the_reference_frame_agree_on_a_foreign_filer():
    src = store(with_ratio=True)
    fast = src.screening_frame(ASOF).set_index("ticker")
    ref = build_screening_frame(src, ASOF).set_index("ticker")
    for col in ("shares_outstanding", "book_value", "listing_ratio"):
        assert fast.at["TOYX", col] == pytest.approx(ref.at["TOYX", col]), col


def test_the_valuation_history_puts_a_foreign_filer_on_the_prices_basis():
    """Year-end 2024 is after the stored prices, so use a store where the filing year is
    covered: FY2023 figures, priced at the last 2023 close."""
    from portfolio_lab import research as rs

    src = store(with_ratio=True)
    c = src.con
    c.execute("UPDATE fundamentals SET period_end = DATE '2023-12-31', "
              "filed = DATE '2024-03-01'")
    c.executemany("INSERT INTO prices (ticker, date, open, close, close_raw, close_adj, "
                  "volume) VALUES (?,?,?,?,?,?,0)",
                  [("TOYX", dt.date(2023, 12, 29), 100.0, 100.0, 100.0, 100.0),
                   ("EURUSD=X", dt.date(2023, 12, 29), 1.1, 1.1, 1.1, 1.1)])
    vh = rs.valuation_history(c, "TOYX", 501).set_index("year")
    assert vh.at[2023, "market_cap"] == pytest.approx(10e9)
    assert vh.at[2023, "price_to_book"] == pytest.approx(10 / 5.5)


def test_the_valuation_history_shows_nothing_rather_than_a_wrong_multiple():
    from portfolio_lab import research as rs

    src = store(with_ratio=False)
    assert rs.valuation_history(src.con, "TOYX", 501).empty
