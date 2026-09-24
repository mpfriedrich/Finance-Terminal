"""Phase 8: the point-in-time backtest, and the three ways it could quietly cheat.

The whole phase rests on three rules. Each one, broken, produces a better-looking curve
than the truth — which is why each one gets a test whose answer is known in advance:

  1. a figure filed after the screen date cannot inform it;
  2. the first filed version wins, not the restatement published two years later;
  3. a name that was not listed then, or was already gone, cannot be bought.

Plus the fill rule from R16: the screen reads a close, the order fills at the NEXT open.

The world below is small enough to work out by hand: three companies, one of which is
cheap only because of a figure nobody had yet, one of which was restated, and one that
lists halfway through.
"""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import stage_b
from portfolio_lab.config import CostModel
from portfolio_lab.data.free import FreeSource
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply
from portfolio_lab.screen.spec import Filter, PortfolioSpec

FREE = CostModel(commission=0.0, spread_tiers=((float("inf"), 0.0),))
DAYS = pd.bdate_range("2023-01-02", "2024-12-31").date


def build_source(fundamentals, listings=None, prices=None) -> FreeSource:
    """A live-shaped store with hand-written contents.

    `FreeSource`, not a stand-in: the point-in-time behaviour being tested lives in its
    queries, so testing anything else would test the wrong thing.
    """
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    listings = listings or {
        "AAA": (1001, None, None), "BBB": (1002, None, None), "CCC": (1003, None, None)}
    con.executemany(
        "INSERT INTO securities (cik, ticker, name, sector, sic, exchange, first_seen, "
        "last_seen) VALUES (?,?,?,?,?,?,?,?)",
        [(cik, ticker, f"{ticker} Inc", "Industrials", "3550", "NYSE", first, last)
         for ticker, (cik, first, last) in listings.items()],
    )
    con.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_end, filed, accession, "
        "xbrl_tag, tag_rank) VALUES (?,?,?,?,?,?,?,1)",
        fundamentals,
    )
    rows = prices or [
        (ticker, day, 10.0, 10.0, 10.0, 10.0, 1_000_000)
        for ticker in listings for day in DAYS
    ]
    con.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_raw, close_adj, volume) "
        "VALUES (?,?,?,?,?,?,?)", rows,
    )
    return FreeSource(con)


def facts(cik, book, shares, filed, period_end=dt.date(2023, 12, 31)):
    """Numbers at a realistic scale, because the screener's own guards are realistic.

    Share counts under 1,000 are discarded as implausible, facts older than 400 days stop
    being visible, and a name whose daily volume is a large fraction of its shares
    outstanding is excluded by the volume sanity check. A fixture with 100 shares and a
    million shares traded a day trips all three — as the first version of this file did.
    """
    return [
        (cik, "book_value", book, period_end, filed, f"{cik}-1", "us-gaap:StockholdersEquity"),
        (cik, "shares_outstanding", shares, period_end, filed, f"{cik}-1",
         "dei:EntityCommonStockSharesOutstanding"),
    ]


# --- rule 1: nothing from the future --------------------------------------------------

def test_a_figure_filed_after_the_screen_date_cannot_reach_it():
    """BBB looks cheap — but only on a filing published in June. A screen dated March
    must not see it, and must therefore not hold BBB."""
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 2, 1))
        + facts(1002, book=2_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 6, 1))
    )
    march = src.screening_frame(dt.date(2023, 3, 1))
    june = src.screening_frame(dt.date(2023, 7, 1))

    assert pd.isna(march.set_index("ticker").at["BBB", "book_value"])
    assert june.set_index("ticker").at["BBB", "book_value"] == 2_000_000_000.0


def test_the_run_holds_nothing_it_could_not_have_known_about():
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 2, 1))
        + facts(1002, book=2_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 6, 1))
    )
    spec = PortfolioSpec(size=1, filters=(Filter("price_to_book", "<=", 100.0),),
                         min_dollar_volume=0, min_trading_days_60=0)
    result = stage_b.run(src, spec, dt.date(2023, 2, 15), dt.date(2023, 5, 31),
                         costs=FREE, benchmark="^SP500TR")
    assert result is not None
    # Before June only AAA is visible at all, so every rebalance in this window held it.
    assert (result.rebalances.names == 1).all()


# --- rule 2: the first filing wins ----------------------------------------------------

def test_a_later_restatement_does_not_displace_what_was_filed_first():
    """The market acted on the first number. A correction published later is the story
    of the restatement, not of the decision that was made at the time."""
    src = build_source(
        facts(1001, book=900_000_000.0, shares=100_000_000, filed=dt.date(2023, 2, 15))
        + facts(1001, book=1_400_000_000.0, shares=100_000_000, filed=dt.date(2023, 8, 20))
    )
    later = src.screening_frame(dt.date(2023, 12, 1)).set_index("ticker")
    assert later.at["AAA", "book_value"] == 900_000_000.0


# --- rule 3: not listed then, not bought ---------------------------------------------

def test_a_company_that_had_not_listed_yet_cannot_be_bought():
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5))
        + facts(1003, book=500_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5)),
        listings={"AAA": (1001, dt.date(2023, 1, 2), None),
                  "CCC": (1003, dt.date(2024, 6, 3), None)},
    )
    early = set(src.screening_frame(dt.date(2023, 6, 1)).ticker)
    late = set(src.screening_frame(dt.date(2024, 9, 2)).ticker)
    assert "CCC" not in early
    assert "CCC" in late


def test_a_company_that_had_already_gone_cannot_be_bought():
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5))
        + facts(1002, book=500_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5)),
        listings={"AAA": (1001, dt.date(2023, 1, 2), None),
                  "BBB": (1002, dt.date(2023, 1, 2), dt.date(2023, 6, 30))},
    )
    during = set(src.screening_frame(dt.date(2023, 5, 1)).ticker)
    after = set(src.screening_frame(dt.date(2024, 1, 3)).ticker)
    assert "BBB" in during
    assert "BBB" not in after


# --- R16: the fill happens at the next open ------------------------------------------

def test_the_order_fills_at_the_next_session_not_at_the_close_it_screened_on():
    sessions = pd.Index(DAYS)
    screen_day = DAYS[10]
    assert stage_b.next_session(sessions, screen_day) == DAYS[11]


def test_the_last_screen_date_without_a_following_session_fills_nothing():
    sessions = pd.Index(DAYS)
    assert stage_b.next_session(sessions, DAYS[-1]) is None


def test_a_gap_up_on_the_morning_of_the_fill_is_not_collected():
    """Prices flat at 10, then the fill morning opens at 13. A run that filled at the
    close it read would book that 30% and call it skill."""
    rows = []
    for ticker in ("AAA",):
        for i, day in enumerate(DAYS[:40]):
            gap = 13.0 if i == 11 else 10.0
            rows.append((ticker, day, gap, 10.0, 10.0, 10.0, 1_000_000))
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 3)),
        listings={"AAA": (1001, None, None)}, prices=rows,
    )
    spec = PortfolioSpec(size=1, min_dollar_volume=0, min_trading_days_60=0)
    result = stage_b.run(src, spec, DAYS[10], DAYS[30], costs=FREE, frequency="buy_and_hold")
    assert result is not None
    # Bought at 13 and valued at 10 afterwards: the run must be DOWN, not up.
    assert result.metrics["total_return"] < 0


# --- the schedule ---------------------------------------------------------------------

def test_quarterly_means_four_dates_a_year():
    dates = stage_b.rebalance_dates(dt.date(2020, 1, 1), dt.date(2020, 12, 31), "quarterly")
    assert dates == [dt.date(2020, 1, 1), dt.date(2020, 4, 1),
                     dt.date(2020, 7, 1), dt.date(2020, 10, 1)]


def test_buy_and_hold_rebalances_once():
    dates = stage_b.rebalance_dates(dt.date(2020, 1, 1), dt.date(2030, 1, 1), "buy_and_hold")
    assert dates == [dt.date(2020, 1, 1)]


def test_a_month_end_start_does_not_fall_off_the_calendar():
    """31 January plus three months is not 31 April."""
    dates = stage_b.rebalance_dates(dt.date(2021, 1, 31), dt.date(2021, 12, 31), "quarterly")
    assert all(isinstance(d, dt.date) for d in dates)
    assert len(dates) == 4


# --- the label ------------------------------------------------------------------------

def test_the_label_still_admits_the_survivors():
    """Point-in-time discipline fixes look-ahead and does nothing at all about the
    companies that are missing because they failed."""
    label = stage_b.stage_b_label("Universe: SURVIVOR-BASED. 0 of 20 recovered.")
    assert "NEXT session's open" in label
    assert "first filed version wins" in label
    assert "SURVIVOR-BASED" in label
