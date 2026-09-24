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
    # The NAMES, not the count. This assertion used to read `names == 1`, which is a
    # count and is true whichever company was bought — it would have stayed green with
    # the look-ahead fully broken. An independent review caught that.
    assert result.ever_held == ["AAA"]
    assert "BBB" not in result.ever_held


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
    assert "next session's open" in label
    assert "first filed version wins" in label
    assert "SURVIVOR-BASED" in label


def test_the_label_does_not_claim_a_cost_tier_it_does_not_use():
    """It claimed costs were charged 'at the cost tier of the names actually traded'
    while every trade paid the most expensive tier. Overstating a cost is conservative;
    claiming a precision that does not exist is not, and the plan bans it either way."""
    label = stage_b.stage_b_label()
    assert "names actually traded" not in label
    assert "most expensive tier" in label


def test_the_label_names_the_sizing_and_the_delisting_rule():
    """Both were claimed by stage A and silently absent from stage B."""
    label = stage_b.stage_b_label()
    assert "SIZED" in label
    assert "delisting" in label


# --- the trade is SIZED at the open too, not just priced there ------------------------

def flat_with_spike(spike_day, open_px=10.0, close_px=13.0):
    """Every price 10, except one day that opens at 10 and closes at `close_px`."""
    days = pd.bdate_range("2023-01-02", "2023-12-29").date
    return days, [("AAA", d, open_px, close_px if d == spike_day else 10.0,
                   close_px if d == spike_day else 10.0,
                   close_px if d == spike_day else 10.0, 1_000_000) for d in days]


def one_name_source(rows):
    return build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2022, 12, 15)),
        listings={"AAA": (1001, None, None)}, prices=rows)


def test_a_spike_on_the_fill_day_is_not_turned_into_a_gain():
    """The bug this test exists for: the book was valued at the fill day's CLOSE and the
    money deployed at its OPEN, which re-levers the portfolio by close/open at every
    single rebalance. On a market that never moves except for one 30% spike on a fill
    day — a price nobody traded at — the run reported +30%.

    Measured on the live store the same defect was worth +0.92 percentage points a year.
    """
    days, rows = flat_with_spike(dt.date(2023, 4, 3))
    spec = PortfolioSpec(size=1, min_dollar_volume=0, min_trading_days_60=0)
    r = stage_b.run(one_name_source(rows), spec, days[0], days[-1],
                    costs=FREE, frequency="quarterly")
    assert r.metrics["total_return"] == pytest.approx(0.0, abs=1e-9)


def test_a_slump_on_the_fill_day_is_not_turned_into_a_loss_either():
    """The mirror image. The original R16 test used a fixture where close < open, which
    is the one sign under which the sizing bug pushes the answer DOWN — so it could not
    see it. Both directions now."""
    days, rows = flat_with_spike(dt.date(2023, 4, 3), close_px=7.0)
    spec = PortfolioSpec(size=1, min_dollar_volume=0, min_trading_days_60=0)
    r = stage_b.run(one_name_source(rows), spec, days[0], days[-1],
                    costs=FREE, frequency="quarterly")
    assert r.metrics["total_return"] == pytest.approx(0.0, abs=1e-9)


# --- R18 in stage B, not only in stage A ---------------------------------------------

def two_names_one_dies():
    """AAA trades all year at 10. BBB trades at 10 and stops at the end of March."""
    days = pd.bdate_range("2023-01-02", "2023-12-29").date
    rows = [("AAA", d, 10.0, 10.0, 10.0, 10.0, 1_000_000) for d in days]
    rows += [("BBB", d, 10.0, 10.0, 10.0, 10.0, 1_000_000)
             for d in days if d <= dt.date(2023, 3, 31)]
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2022, 12, 15))
        + facts(1002, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2022, 12, 15)),
        listings={"AAA": (1001, None, None), "BBB": (1002, None, None)}, prices=rows)
    return days, src


def test_a_position_that_stops_trading_takes_the_stated_loss():
    """Half in a name that goes nowhere, half in one that vanishes at -30%: the book is
    worth 100 × (0.5 + 0.5 × 0.7) = 85. Stage A did this; stage B did not, and held the
    dead name at its last close instead — which is the single most flattering assumption
    a backtest can make."""
    days, src = two_names_one_dies()
    spec = PortfolioSpec(size=2, min_dollar_volume=0, min_trading_days_60=0)
    r = stage_b.run(src, spec, days[0], days[-1], costs=FREE,
                    frequency="buy_and_hold", delisting_return=-0.30)
    assert r.portfolio[-1] == pytest.approx(85.0, rel=1e-6)
    assert any("stopped trading" in n for n in r.notes)


def test_the_delisting_assumption_moves_the_stage_b_result_too():
    days, src = two_names_one_dies()
    spec = PortfolioSpec(size=2, min_dollar_volume=0, min_trading_days_60=0)
    mild = stage_b.run(src, spec, days[0], days[-1], costs=FREE,
                       frequency="buy_and_hold", delisting_return=-0.10)
    total = stage_b.run(src, spec, days[0], days[-1], costs=FREE,
                        frequency="buy_and_hold", delisting_return=-1.00)
    assert mild.portfolio[-1] == pytest.approx(95.0, rel=1e-6)
    assert total.portfolio[-1] == pytest.approx(50.0, rel=1e-6)


# --- the universe from Phase 7 --------------------------------------------------------
# The criterion says "das Universum aus Phase 7". Until 24.09.2026 it came from the
# securities master instead — numerically the same on free data, but the criterion was
# not met, and a source that did know vanished companies would have been ignored.

from portfolio_lab.data import history  # noqa: E402


def phase7_source(windows_to_newest_bar: bool = True) -> FreeSource:
    """Securities master WITHOUT dates, so only ticker_history can know who was listed.

    BBB trades until 30 June 2023. ZZZ is a vanished company the securities master has
    never heard of — the shape a survivorship-free source would add.
    """
    bbb_days = [d for d in DAYS if d <= dt.date(2023, 6, 30)]
    rows = (
        [("AAA", d, 10.0, 10.0, 10.0, 10.0, 1_000_000) for d in DAYS]
        + [("BBB", d, 10.0, 10.0, 10.0, 10.0, 1_000_000) for d in bbb_days]
        + [("ZZZ", d, 10.0, 10.0, 10.0, 10.0, 1_000_000) for d in bbb_days]
    )
    src = build_source(
        facts(1001, book=1_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5))
        + facts(1002, book=2_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5))
        + facts(1009, book=3_000_000_000.0, shares=100_000_000, filed=dt.date(2023, 1, 5)),
        listings={"AAA": (1001, None, None), "BBB": (1002, None, None)},
        prices=rows,
    )
    history.refresh_price_windows(src.con)
    src.con.execute(
        "INSERT INTO ticker_history (cik, ticker, valid_from, valid_to, source) "
        "VALUES (1009, 'ZZZ', DATE '2023-01-02', DATE '2023-06-30', ?)",
        [history.SOURCE_PRICES],
    )
    if not windows_to_newest_bar:
        src.con.execute("UPDATE ticker_history SET valid_to = DATE '2023-06-30'")
    return src


def test_the_universe_is_read_from_ticker_history():
    src = phase7_source()
    assert src.universe_basis == "ticker_history"
    during = set(src.universe(dt.date(2023, 5, 1)).ticker)
    after = set(src.universe(dt.date(2024, 1, 3)).ticker)
    assert {"AAA", "BBB"} <= during
    assert "BBB" not in after, "a name past its price window stayed in the universe"
    assert "AAA" in after


def test_a_company_only_ticker_history_knows_is_screened_while_it_was_listed():
    src = phase7_source()
    frame = src.screening_frame(dt.date(2023, 5, 1)).set_index("ticker")
    assert "ZZZ" in frame.index
    assert frame.at["ZZZ", "book_value"] == 3_000_000_000.0
    assert "ZZZ" not in set(src.universe(dt.date(2024, 1, 3)).ticker)


def test_stale_windows_fall_back_to_the_master_and_the_run_says_so():
    """Windows that end before the newest bar would make every living company look
    delisted. The source falls back, and the result must not stay quiet about it."""
    src = phase7_source(windows_to_newest_bar=False)
    assert src.universe_basis == "securities"
    spec = PortfolioSpec(size=1, min_dollar_volume=0, min_trading_days_60=0)
    r = stage_b.run(src, spec, dt.date(2023, 2, 1), dt.date(2024, 6, 28), costs=FREE)
    assert any("securities master" in n for n in r.notes)


def test_names_that_leave_the_universe_are_counted():
    """R19: the result says how many names left the universe during the run."""
    src = phase7_source()
    spec = PortfolioSpec(size=1, min_dollar_volume=0, min_trading_days_60=0)
    r = stage_b.run(src, spec, dt.date(2023, 2, 1), dt.date(2024, 6, 28), costs=FREE)
    assert any("2 name(s) left the universe" in n for n in r.notes), r.notes
    assert not any("securities master" in n for n in r.notes)
