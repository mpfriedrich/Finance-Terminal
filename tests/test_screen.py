"""Screener behaviour, including the user's own two messages."""
import pandas as pd
import pytest

from portfolio_lab.screen.spec import Filter, PortfolioSpec
from portfolio_lab.screen.engine import merge_selection, run_screen, derive_metrics


def frame():
    return pd.DataFrame([
        # ticker, shares, price, book, net_income, age, volume
        ("AAA", 100, 20.0, 1000.0,  500.0,  30, 5e6),   # P/B 2.0  P/E 4.0
        ("BBB", 100, 10.0,  200.0,  900.0,  30, 5e6),   # P/B 5.0  P/E 1.1
        ("CCC", 100,  8.0,  900.0, 1200.0,  30, 5e6),   # P/B 0.89 P/E 0.67  <- cheap on both
        ("DDD", 100,  2.0,  100.0,  400.0,  30, 5e6),   # below price floor
        ("EEE", 100, 50.0, 1000.0,  900.0,  30, 1e5),   # below turnover floor
        ("FFF", 100, 15.0,  500.0,  800.0, 900, 5e6),   # stale filing
        ("GGG", 100, 12.0, -300.0,  600.0,  30, 5e6),   # negative equity
    ], columns=["ticker", "shares_outstanding", "price", "book_value",
                "net_income_ttm", "data_age_days", "median_dollar_volume_60d"])


def test_negative_book_value_is_undefined_not_cheap():
    d = derive_metrics(frame())
    assert pd.isna(d.loc[d.ticker == "GGG", "price_to_book"]).all()


def test_eligibility_floors_are_counted_not_silent():
    r = run_screen(frame(), PortfolioSpec(size=10))
    assert r.n_universe == 7
    assert "price below 5" in r.exclusions
    assert "below minimum turnover" in r.exclusions
    assert any("older than" in k for k in r.exclusions)
    assert r.n_eligible == 4


def test_users_own_two_messages_refine_rather_than_restart():
    """'10 stocks, P/E max 1, P/B max 2' then 'cap not above 2bn'."""
    spec = PortfolioSpec(size=10)
    spec = spec.with_filter(Filter("price_to_earnings", "<=", 1.0))
    spec = spec.with_filter(Filter("price_to_book", "<=", 2.0))
    spec = spec.with_filter(Filter("market_cap", "<=", 2e9))
    metrics = [f.metric for f in spec.filters]
    assert metrics.count("price_to_earnings") == 1
    assert set(metrics) == {"price_to_earnings", "price_to_book", "market_cap"}


def test_tightening_the_same_filter_replaces_it():
    spec = PortfolioSpec().with_filter(Filter("market_cap", "<=", 5e9))
    spec = spec.with_filter(Filter("market_cap", "<=", 2e9))
    caps = [f.value for f in spec.filters if f.metric == "market_cap"]
    assert caps == [2e9]


def test_ranking_defaults_to_the_strictest_valuation_filter_and_is_visible():
    spec = PortfolioSpec().with_filter(Filter("price_to_book", "<=", 2.0))
    assert spec.effective_rank() == ("price_to_book", True)
    assert any("rank every passing name by price to book" in line
               for line in spec.describe())


def test_zero_hits_reports_which_filter_binds():
    spec = PortfolioSpec(size=10, filters=(
        Filter("price_to_earnings", "<=", 0.1),
        Filter("price_to_book", "<=", 2.0),
    ))
    r = run_screen(frame(), spec)
    assert r.n_passing == 0
    assert any("no security passed" in n for n in r.notes)
    survivors = r.per_filter_survivors
    assert survivors["price to earnings at most 0.1"] == 0
    assert survivors["price to book at most 2.0"] > 0


def test_short_result_says_so():
    spec = PortfolioSpec(size=10, filters=(Filter("price_to_book", "<=", 1.0),))
    r = run_screen(frame(), spec)
    assert len(r.picks) < 10
    assert any("fewer than the 10 proposed" in n for n in r.notes)


def test_unsupported_criteria_surface_in_the_output():
    spec = PortfolioSpec(size=5, unsupported=("management quality",))
    r = run_screen(frame(), spec)
    assert any("management quality" in n for n in r.notes)


def test_equal_weights_sum_to_one():
    spec = PortfolioSpec(size=3, filters=(Filter("price_to_book", "<=", 10.0),))
    r = run_screen(frame(), spec)
    assert r.picks.weight.sum() == pytest.approx(1.0)


def _rich():
    return pd.DataFrame([{
        "ticker": "AAA", "shares_outstanding": 100, "price": 20.0,
        "book_value": 1000.0, "net_income_ttm": 200.0, "revenue_ttm": 2000.0,
        "operating_cash_flow_ttm": 300.0, "cash": 400.0,
        "long_term_debt": 500.0, "current_debt": 100.0,
        "total_liabilities": 900.0, "current_assets": 800.0,
        "current_liabilities": 400.0, "data_age_days": 30, "median_dollar_volume_60d": 5e6,
    }])


def test_debt_to_equity_counts_only_interest_bearing_debt():
    d = derive_metrics(_rich())
    assert d.debt_to_equity.iloc[0] == pytest.approx(600.0 / 1000.0)
    assert d.liabilities_to_equity.iloc[0] == pytest.approx(900.0 / 1000.0)


def test_margins_stay_signed_so_losses_are_visible():
    f = _rich()
    f.loc[0, "net_income_ttm"] = -400.0
    d = derive_metrics(f)
    assert d.net_margin.iloc[0] == pytest.approx(-0.20)


def test_a_filer_tagging_no_debt_at_all_gets_nothing_not_zero():
    """Zero debt would read as a pristine balance sheet. Absence is not zero."""
    f = _rich().drop(columns=["long_term_debt", "current_debt"])
    d = derive_metrics(f)
    assert pd.isna(d.debt_to_equity.iloc[0])


def test_price_to_cash_and_current_ratio():
    d = derive_metrics(_rich())
    assert d.price_to_cash.iloc[0] == pytest.approx(2000.0 / 400.0)
    assert d.current_ratio.iloc[0] == pytest.approx(2.0)


def _sectored():
    return pd.DataFrame([
        {"ticker": "AAA", "sector": "Pharmaceutical Preparations", "shares_outstanding": 100,
         "price": 20.0, "book_value": 1000.0, "data_age_days": 30,
         "median_dollar_volume_60d": 5e6},
        {"ticker": "BBB", "sector": "State Commercial Banks", "shares_outstanding": 100,
         "price": 20.0, "book_value": 1000.0, "data_age_days": 30,
         "median_dollar_volume_60d": 5e6},
    ])


def test_sector_substring_matching_works():
    spec = PortfolioSpec(size=5, filters=(Filter("sector", "contains", "Bank"),))
    r = run_screen(_sectored(), spec)
    assert r.picks.ticker.tolist() == ["BBB"]


def test_an_invented_sector_is_refused_loudly_not_applied_silently():
    """A model that answers 'Healthcare' — a label that exists nowhere in SEC industry
    names — would, with not_contains, keep every row while looking like it excluded
    something. That is the worst possible failure mode, so it is named instead."""
    spec = PortfolioSpec(size=5, filters=(Filter("sector", "not_contains", "Healthcare"),))
    r = run_screen(_sectored(), spec)
    assert len(r.picks) == 2
    assert any("matches no sector" in n for n in r.notes)
    assert any("Pharmaceutical Preparations" in n for n in r.notes)


def _named():
    return pd.DataFrame([
        {"ticker": t, "name": t, "shares_outstanding": 100, "price": 20.0,
         "book_value": 1000.0, "net_income_ttm": 100.0, "data_age_days": 30,
         "median_dollar_volume_60d": 5e6}
        for t in ("AAPL", "MSFT", "NVDA")
    ])


def test_hand_picked_list_is_the_portfolio():
    spec = PortfolioSpec(tickers=("MSFT", "AAPL"))
    r = run_screen(_named(), spec)
    assert r.hand_picked
    assert r.picks.ticker.tolist() == ["MSFT", "AAPL"]   # the order you typed
    assert r.picks.weight.sum() == pytest.approx(1.0)


def test_hand_picked_ignores_filters_and_floors():
    """Naming a company is a decision. The floors exist to stop a screen wandering, not
    to overrule someone who typed a ticker on purpose."""
    f = _named()
    f.loc[0, "price"] = 0.40                 # below the price floor
    f.loc[0, "median_dollar_volume_60d"] = 1  # below the turnover floor
    spec = PortfolioSpec(tickers=("AAPL",), filters=(Filter("price_to_book", "<=", 0.0001),))
    r = run_screen(f, spec)
    assert r.picks.ticker.tolist() == ["AAPL"]


def test_unknown_tickers_are_named_not_dropped():
    spec = PortfolioSpec(tickers=("AAPL", "WHOOPS"))
    r = run_screen(_named(), spec)
    assert r.unknown_tickers == ["WHOOPS"]
    assert any("WHOOPS" in n for n in r.notes)
    assert r.picks.ticker.tolist() == ["AAPL"]


def test_lowercase_input_is_accepted():
    r = run_screen(_named(), PortfolioSpec(tickers=("aapl", " msft ")))
    assert r.picks.ticker.tolist() == ["AAPL", "MSFT"]


# ---------------------------------------------------------------- selection

def _wide():
    """Twelve names that all pass, so `size` and the passing count differ."""
    return pd.DataFrame(
        [(f"T{i:02d}", 100, 10.0 + i, 1000.0, 500.0, 30, 5e6) for i in range(12)],
        columns=["ticker", "shares_outstanding", "price", "book_value",
                 "net_income_ttm", "data_age_days", "median_dollar_volume_60d"],
    )


def test_ranked_keeps_every_passing_name():
    """The list is not cut to `size`. That was the old behaviour and it hid names."""
    r = run_screen(_wide(), PortfolioSpec(size=3))
    assert r.n_passing == 12
    assert len(r.ranked) == 12
    assert list(r.ranked["rank"]) == list(range(1, 13))


def test_uncurated_screen_proposes_the_top_n_without_removing_the_rest():
    r = run_screen(_wide(), PortfolioSpec(size=3))
    assert len(r.picks) == 3
    assert len(r.ranked) == 12          # the other nine are still there to be seen
    assert list(r.picks.ticker) == list(r.ranked.ticker.head(3))


def test_a_selection_larger_than_size_is_honoured():
    """The whole point: `size` proposes, it does not cap."""
    wanted = tuple(f"T{i:02d}" for i in range(9))
    r = run_screen(_wide(), PortfolioSpec(size=3, selected=wanted))
    assert len(r.picks) == 9
    assert r.picks.weight.sum() == pytest.approx(1.0)


def test_selection_keeps_ranking_order_not_the_order_it_was_clicked():
    r = run_screen(_wide(), PortfolioSpec(size=3, selected=("T05", "T00", "T02")))
    assert list(r.picks["rank"]) == sorted(r.picks["rank"])


def test_selection_is_case_and_whitespace_tolerant():
    r = run_screen(_wide(), PortfolioSpec(size=3, selected=(" t01 ", "T03")))
    assert set(r.picks.ticker) == {"T01", "T03"}


def test_a_selected_name_that_stops_passing_is_dropped_and_named():
    """Filters can change under a selection made earlier. Silence there would mean
    holding a company the screen no longer accepts."""
    spec = PortfolioSpec(size=3, selected=("T00", "ZZZ"))
    r = run_screen(_wide(), spec)
    assert set(r.picks.ticker) == {"T00"}
    assert any("ZZZ" in n for n in r.notes)


def test_select_reweights_without_rerunning_the_screen():
    r = run_screen(_wide(), PortfolioSpec(size=3))
    subset = r.select(["T00", "T01", "T02", "T03"])
    assert len(subset) == 4
    assert subset.weight.sum() == pytest.approx(1.0)
    assert subset.weight.iloc[0] == pytest.approx(0.25)


def test_selection_survives_a_storage_round_trip_as_a_tuple():
    from portfolio_lab.screen.spec import spec_from_json, spec_to_json

    spec = PortfolioSpec(size=3, selected=("AAA", "BBB"))
    back = spec_from_json(spec_to_json(spec))
    assert back.selected == ("AAA", "BBB")
    assert back == spec
    assert back.fingerprint() == spec.fingerprint()


def test_a_different_selection_is_a_different_portfolio():
    """The fingerprint drives the overfitting counter, so curating by hand has to
    count as a new experiment rather than a re-run of the old one."""
    a = PortfolioSpec(size=3, selected=("AAA",))
    b = PortfolioSpec(size=3, selected=("BBB",))
    assert a.fingerprint() != b.fingerprint()


def test_the_model_cannot_write_a_selection():
    """`selected` holds tickers, so a model with access to it could name stocks."""
    from portfolio_lab.screen.translate import TOOL, payload_to_spec

    assert "selected" not in TOOL["input_schema"]["properties"]
    previous = PortfolioSpec(selected=("AAA",))
    out = payload_to_spec({"size": 5, "filters": [], "selected": ["EVIL"]}, previous)
    assert out.selected == ()


def industry_frame():
    """Same shape as `frame()`, plus the two industry columns the screen can filter on."""
    df = frame().head(4).copy()
    df["price"] = [20.0, 10.0, 8.0, 20.0]  # keep all four above the price floor
    df["industry_group"] = ["Technology", "Health Care", "Unclassified", "Technology"]
    df["sector"] = ["Services-Prepackaged Software", "Pharmaceutical Preparations",
                    None, "Semiconductors & Related Devices"]
    return df


def test_industry_filter_keeps_only_the_named_groups():
    spec = PortfolioSpec(size=10, filters=(
        Filter("industry_group", "in", ["Technology"]),))
    r = run_screen(industry_frame(), spec)
    assert set(r.picks.ticker) == {"AAA", "DDD"}


def test_several_industries_can_be_allowed_at_once():
    spec = PortfolioSpec(size=10, filters=(
        Filter("industry_group", "in", ["Technology", "Health Care"]),))
    r = run_screen(industry_frame(), spec)
    assert set(r.picks.ticker) == {"AAA", "BBB", "DDD"}


def test_companies_without_an_industry_are_counted_not_silently_dropped():
    """A name with no industry fails every industry filter. That is correct and it is
    also invisible, so the screen has to say how many it happened to."""
    spec = PortfolioSpec(size=10, filters=(
        Filter("industry_group", "in", ["Technology"]),))
    r = run_screen(industry_frame(), spec)
    assert any("no industry on file" in n for n in r.notes)
    assert any("1 eligible" in n for n in r.notes)


def test_a_sector_fragment_that_matches_nothing_is_refused_out_loud():
    spec = PortfolioSpec(size=10, filters=(
        Filter("sector", "contains", "Shipbuilding"),))
    r = run_screen(industry_frame(), spec)
    assert any("matches no sector" in n for n in r.notes)
    # The filter was not applied, so nothing was silently excluded by it.
    assert r.n_passing == r.n_eligible


# --- the holding survives a filtered view -------------------------------------------
# The table on screen is not the portfolio. These pin down the one rule that keeps a
# hand-curated holding alive: a row may only speak for itself.

HELD_31 = ("VISN", "RILY", "KROS", "CRMD", "LYFT")  # stands in for the real 31


def test_names_hidden_by_a_filter_are_not_treated_as_unticked():
    """The industry filter shows 2 of 5 held names. The other 3 must survive."""
    visible = ["KROS", "CRMD"]
    merged = merge_selection(HELD_31, visible, ticked=["KROS", "CRMD"])
    assert set(merged) == set(HELD_31)


def test_unticking_a_visible_name_removes_exactly_that_one():
    merged = merge_selection(HELD_31, ["KROS", "CRMD"], ticked=["KROS"])
    assert set(merged) == set(HELD_31) - {"CRMD"}


def test_ticking_a_new_name_appends_it_without_disturbing_the_rest():
    merged = merge_selection(HELD_31, ["KROS", "CRMD", "AAPL"],
                             ticked=["KROS", "CRMD", "AAPL"])
    assert set(merged) == set(HELD_31) | {"AAPL"}
    assert merged[:len(HELD_31)] == HELD_31  # stored order is not reshuffled


def test_an_empty_filtered_table_cannot_empty_the_holding():
    """A filter matching nothing shows no rows, so nothing was said about anything."""
    assert merge_selection(HELD_31, [], ticked=[]) == HELD_31


def test_clearing_every_tick_with_all_names_visible_does_empty_it():
    """The unfiltered view IS the whole holding, so unticking all of it means all of it."""
    assert merge_selection(HELD_31, HELD_31, ticked=[]) == ()


def test_case_and_whitespace_do_not_duplicate_a_holding():
    merged = merge_selection(("KROS",), [" kros "], ticked=[" kros "])
    assert merged == ("KROS",)


def test_a_first_selection_starts_from_the_ticks():
    assert set(merge_selection((), ["AAA", "BBB"], ticked=["BBB"])) == {"BBB"}


# --- how often it trades, not how much ------------------------------------------------

def thin_frame():
    """Two names with identical, healthy turnover. One of them trades four days a year."""
    return pd.DataFrame([
        ("LIQUID", 100, 20.0, 1000.0, 500.0, 30, 5e6, 58),
        ("SPORADIC", 100, 20.0, 1000.0, 500.0, 30, 5e6, 4),
    ], columns=["ticker", "shares_outstanding", "price", "book_value",
                "net_income_ttm", "data_age_days", "median_dollar_volume_60d",
                "trading_days_60"])


def test_a_name_that_barely_trades_is_excluded_although_its_turnover_passes():
    """The exact case that started this: four trading days in a year, a million dollars
    on each of them, and a turnover floor waves it through."""
    r = run_screen(thin_frame(), PortfolioSpec(size=10))
    assert set(r.picks.ticker) == {"LIQUID"}


def test_the_frequency_exclusion_is_counted_separately_from_turnover():
    """'Too small to trade' and 'barely trades at all' are different defects, and the
    breakdown has to say which one bit."""
    r = run_screen(thin_frame(), PortfolioSpec(size=10))
    assert any("fewer than 40 of the last 60 sessions" in k for k in r.exclusions)
    assert "below minimum turnover" not in r.exclusions


def test_the_floor_can_be_lowered_deliberately():
    r = run_screen(thin_frame(), PortfolioSpec(size=10, min_trading_days_60=0))
    assert set(r.picks.ticker) == {"LIQUID", "SPORADIC"}


def test_a_frame_without_the_column_still_screens():
    """An older source that cannot answer must not crash the screen; it simply cannot
    apply this floor, and the other floors still hold."""
    frame = thin_frame().drop(columns=["trading_days_60"])
    r = run_screen(frame, PortfolioSpec(size=10))
    assert set(r.picks.ticker) == {"LIQUID", "SPORADIC"}


def test_the_floor_appears_in_the_plain_language_description():
    lines = " ".join(PortfolioSpec(size=10).describe())
    assert "traded on ≥ 40 of the last 60 sessions" in lines


def test_a_frame_with_no_fundamentals_at_all_screens_empty_rather_than_crashing():
    """A point-in-time backtest walking back far enough reaches a date where nothing has
    been filed yet. That is a real date, not an error — and it used to raise
    `Series * None` deep inside the metric derivation."""
    bare = pd.DataFrame({
        "ticker": ["AAA", "BBB"], "cik": [1, 2], "price": [10.0, 20.0],
        "data_age_days": [30, 30], "median_dollar_volume_60d": [5e6, 5e6],
        "trading_days_60": [60, 60],
    })
    r = run_screen(bare, PortfolioSpec(size=10))
    assert r.n_universe == 2
    assert r.picks.empty          # no share count, so nothing is eligible
    assert "no share count" in r.exclusions
