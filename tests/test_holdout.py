"""The withheld period, and the ways it quietly stops being one."""
import datetime as dt

import duckdb
import pytest

from portfolio_lab import holdout
from portfolio_lab.data.schema import USER_MIGRATIONS, _apply
from portfolio_lab.store import Store

LATEST = dt.date(2026, 9, 23)
START = dt.date(2011, 1, 3)


def test_the_last_two_years_are_missing_unless_someone_asks_for_them():
    """Withheld by default. An opt-in reserve is not a reserve — it is a setting nobody
    changes once the curve already looks good."""
    w = holdout.window(LATEST, START, years=2)
    assert w.end == dt.date(2024, 9, 23)
    assert (w.withheld_from, w.withheld_to) == (dt.date(2024, 9, 23), LATEST)
    assert not w.unlocked


def test_unlocking_hands_the_period_over_and_says_so():
    w = holdout.window(LATEST, START, years=2, unlocked=True)
    assert w.end == LATEST
    assert w.unlocked
    assert "UNLOCKED" in w.describe()


def test_the_reserve_is_measured_from_the_data_not_from_today():
    """A store nobody refreshed for a month must not quietly hand back part of its own
    reserve just because the calendar moved."""
    stale = dt.date(2026, 1, 15)
    assert holdout.window(stale, START, years=2).end == dt.date(2024, 1, 15)


def test_a_reserve_that_would_swallow_the_window_is_refused():
    """Returning an empty backtest would look like a result of zero rather than like the
    mistake it is."""
    with pytest.raises(ValueError, match="nothing to test"):
        holdout.window(LATEST, dt.date(2025, 6, 1), years=2)


def test_zero_years_means_nothing_is_out_of_sample_and_the_text_admits_it():
    w = holdout.window(LATEST, START, years=0)
    assert w.end == LATEST
    assert w.withheld_from is None
    assert "nothing here is out of sample" in w.describe()


def test_a_leap_day_does_not_break_the_shift():
    assert holdout.window(dt.date(2024, 2, 29), dt.date(2015, 1, 1), years=2).end == \
        dt.date(2022, 2, 28)


def test_the_locked_text_says_what_to_do_with_the_reserve():
    text = holdout.window(LATEST, START, years=2).describe()
    assert "NOT in this result" in text
    assert "once" in text


# --- spending the reserve -------------------------------------------------------------

@pytest.fixture()
def store() -> Store:
    con = duckdb.connect(":memory:")
    _apply(con, USER_MIGRATIONS)
    return Store(con)


def test_an_unopened_reserve_counts_zero_and_warns_about_nothing(store):
    pid = store.create("Deep value")
    store.record_run(pid, "stage_b", "spec1", "build1", None, None, {}, {})
    assert holdout.burn_count(store, "spec1", "build1") == 0
    assert holdout.burn_warning(0) == ""


def test_opening_it_is_recorded_against_the_rule_and_the_data(store):
    pid = store.create("Deep value")
    store.record_run(pid, "stage_b", "spec1", "build1", None, None, {}, {},
                     holdout_unlocked=True)
    assert holdout.burn_count(store, "spec1", "build1") == 1
    assert holdout.burn_count(store, "spec1", "build2") == 0     # other data build
    assert holdout.burn_count(store, "spec2", "build1") == 0     # other rule
    assert "out-of-sample" in holdout.burn_warning(1)


def test_a_second_look_is_called_what_it_is(store):
    pid = store.create("Deep value")
    for _ in range(3):
        store.record_run(pid, "stage_b", "spec1", "build1", None, None, {}, {},
                         holdout_unlocked=True)
    assert holdout.burn_count(store, "spec1", "build1") == 3
    assert "no longer a reserve" in holdout.burn_warning(3)
