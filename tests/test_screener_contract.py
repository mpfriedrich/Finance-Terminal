"""Phase 2's real acceptance: ONE test series, run against every data source.

The screener is supposed to know nothing about where its numbers come from. Until this
file existed that was a claim — `FixtureSource` was tested on its own traps, the screener
was tested on hand-built frames, and the two never met. So the assertions below are
written once and parametrised over the sources: the fixture, whose answers can be worked
out by hand, and the live free stack, which has 5,000-odd real companies in it.

A test that passes against the fixture and fails against the live source (or the reverse)
is the finding, not an inconvenience — it means the seam leaks.

The live source is skipped, never faked, when the local market store is missing: a
green run that silently tested one source would defeat the whole point of the file.
"""
import datetime as dt

import pandas as pd
import pytest

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.fixture import FixtureSource
from portfolio_lab.screen.engine import derive_metrics, run_screen
from portfolio_lab.screen.spec import Filter, PortfolioSpec

REQUIRED_COLUMNS = {
    "ticker", "cik", "name", "sector", "industry_group", "exchange",
    "price", "median_dollar_volume_60d", "trading_days_60", "data_age_days",
}


def _fixture_case():
    src = FixtureSource()
    return src, dt.date(2024, 6, 30)


def _live_case():
    """The live source, or a skip when a build currently holds the store.

    DuckDB gives a writer exclusive access, so a price download in another terminal locks
    this out. That is a busy machine, not a broken seam — erroring here would train
    everyone to ignore a red suite while a build runs.
    """
    import duckdb

    from portfolio_lab.data.free import FreeSource
    from portfolio_lab.data.schema import open_market

    try:
        con = open_market(MARKET_DB, read_only=True)
    except (duckdb.IOException, RuntimeError) as exc:
        pytest.skip(f"market store is locked by another process: {exc}")
    src = FreeSource(con)
    return src, src.latest_equity_date()


@pytest.fixture(scope="module", params=["fixture", "free", "paid"])
def frame_and_spec(request, tmp_path_factory):
    """(source name, screening frame). Built once per source: the live one costs seconds.

    "paid" is the Sharadar translation (Phase 10) over hand-made files in that layout,
    since no subscription exists; the criterion is that this series runs unchanged
    against it.
    """
    if request.param == "paid":
        from tests.paid_fixture import END, build_store

        return request.param, build_store(
            tmp_path_factory.mktemp("sharadar")).screening_frame(END)
    if request.param == "free":
        if not MARKET_DB.exists():
            pytest.skip("no local market store — the live half of the seam cannot run")
        src, asof = _live_case()
        if asof is None:
            pytest.skip("market store has no equity prices yet")
    else:
        src, asof = _fixture_case()
    return request.param, src.screening_frame(asof)


def test_every_source_answers_the_same_column_contract(frame_and_spec):
    name, frame = frame_and_spec
    missing = REQUIRED_COLUMNS - set(frame.columns)
    assert not missing, f"{name} is missing {sorted(missing)}"
    assert len(frame), f"{name} produced an empty universe"


def test_derived_ratios_follow_from_the_raw_inputs(frame_and_spec):
    """price_to_book has to BE market cap over equity, in both worlds."""
    name, frame = frame_and_spec
    d = derive_metrics(frame)
    usable = d[(d.book_value > 0) & d.market_cap.notna() & d.price_to_book.notna()]
    assert len(usable), f"{name} produced nothing to check"
    expected = usable.market_cap / usable.book_value
    assert (usable.price_to_book - expected).abs().max() < 1e-6


def test_negative_equity_is_undefined_rather_than_cheap(frame_and_spec):
    name, frame = frame_and_spec
    d = derive_metrics(frame)
    negative = d[d.book_value < 0]
    if negative.empty:
        pytest.skip(f"{name} has no negative-equity company to check")
    assert negative.price_to_book.isna().all()


def test_the_floors_are_counted_and_actually_hold(frame_and_spec):
    """Whatever the source, an eligible name is priced, liquid and recently filed."""
    name, frame = frame_and_spec
    spec = PortfolioSpec(size=10)
    r = run_screen(frame, spec)
    assert r.n_universe == len(frame)
    assert r.n_eligible <= r.n_universe
    assert sum(r.exclusions.values()) == r.n_universe - r.n_eligible, (
        f"{name}: excluded names are not fully accounted for"
    )
    if not r.picks.empty:
        assert (r.picks.price >= spec.min_price).all()
        assert (r.picks.data_age_days <= spec.max_filing_age_months * 31).all()
        assert (r.picks.median_dollar_volume_60d >= spec.min_dollar_volume).all()
        assert (r.picks.trading_days_60 >= spec.min_trading_days_60).all()


def test_a_stale_filer_is_dropped_by_the_freshness_floor(frame_and_spec):
    name, frame = frame_and_spec
    stale = frame[frame.data_age_days > 6 * 31]
    if stale.empty:
        pytest.skip(f"{name} has no stale filer")
    r = run_screen(frame, PortfolioSpec(size=10))
    assert any("older than" in k for k in r.exclusions)
    assert not set(stale.ticker) & set(r.picks.ticker)


def test_an_impossible_filter_names_the_rule_that_binds(frame_and_spec):
    name, frame = frame_and_spec
    spec = PortfolioSpec(size=10, filters=(Filter("price_to_book", "<=", -1.0),))
    r = run_screen(frame, spec)
    assert r.n_passing == 0
    assert any("no security passed" in n for n in r.notes)
    assert r.per_filter_survivors, f"{name} gave no per-filter breakdown"
    assert all(v == 0 for v in r.per_filter_survivors.values())


def test_ranking_puts_the_cheapest_first(frame_and_spec):
    name, frame = frame_and_spec
    spec = PortfolioSpec(size=50, filters=(Filter("price_to_book", "<=", 1e9),))
    r = run_screen(frame, spec)
    if len(r.ranked) < 2:
        pytest.skip(f"{name} has too few passing names to order")
    values = r.ranked.price_to_book.dropna()
    assert values.is_monotonic_increasing


def test_an_industry_filter_returns_only_that_industry(frame_and_spec):
    name, frame = frame_and_spec
    group = frame.industry_group.value_counts().idxmax()
    spec = PortfolioSpec(size=50, filters=(Filter("industry_group", "in", [group]),))
    r = run_screen(frame, spec)
    if r.picks.empty:
        pytest.skip(f"{name}: nothing in {group} survives the floors")
    assert set(r.picks.industry_group) == {group}


def test_equal_weights_sum_to_one(frame_and_spec):
    name, frame = frame_and_spec
    r = run_screen(frame, PortfolioSpec(size=3))
    if r.picks.empty:
        pytest.skip(f"{name} produced no picks")
    assert abs(r.picks.weight.sum() - 1.0) < 1e-9


def test_no_fact_from_the_future_reaches_the_frame(frame_and_spec):
    """The floor under everything: a screen dated `asof` cannot see later filings."""
    name, frame = frame_and_spec
    ages = pd.to_numeric(frame.data_age_days, errors="coerce").dropna()
    assert (ages >= 0).all(), f"{name} carries a filing dated after the screen date"


# --- the optimisation may not drift from the reference --------------------------------

@pytest.mark.slow
def test_the_fast_frame_equals_the_reference_frame():
    """`FreeSource` builds the frame in SQL for speed; the fixture uses the shared
    assembler. That is only safe while the two produce the same numbers — otherwise the
    test series above proves the seam for one construction and the app uses another.

    Costs about 20 seconds because it builds the whole universe twice. That is the price
    of knowing, and it is paid once per run rather than per screen.
    """
    from portfolio_lab.data.base import build_screening_frame

    if not MARKET_DB.exists():
        pytest.skip("no local market store")
    src, asof = _live_case()
    if asof is None:
        pytest.skip("market store has no equity prices yet")

    fast, reference = src.screening_frame(asof), build_screening_frame(src, asof)
    assert len(fast) == len(reference)

    # Every column, not four of them: comparing a hand-picked few missed that the
    # reference frame had no share volume at all (audit 24.10.2026 F7).
    assert set(fast.columns) == set(reference.columns), \
        set(fast.columns) ^ set(reference.columns)
    merged = fast.merge(reference, on="ticker", suffixes=("_fast", "_ref"))
    for column in sorted(set(fast.columns) - {"ticker"}):
        a, b = merged[f"{column}_fast"], merged[f"{column}_ref"]
        assert (a.isna() == b.isna()).all(), f"{column}: the two disagree about missing"
        both = a.notna() & b.notna()
        if not pd.api.types.is_numeric_dtype(a) or not pd.api.types.is_numeric_dtype(b):
            assert (a[both].astype(str) == b[both].astype(str)).all(), column
            continue
        a, b = a[both].astype(float), b[both].astype(float)
        worst = ((a - b).abs() / b.abs().clip(lower=1e-9)).max()
        assert not worst >= 1e-9, f"{column}: builders drifted apart by {worst:.2%}"
    spec = PortfolioSpec(size=20)
    assert run_screen(fast, spec).exclusions == run_screen(reference, spec).exclusions
