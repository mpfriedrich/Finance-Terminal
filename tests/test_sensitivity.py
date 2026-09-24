"""R20: the result beside its variations, so a fragile one is visible before the money is."""
import datetime as dt

import pandas as pd
import pytest

from portfolio_lab import sensitivity


class FakeResult:
    def __init__(self, annual, held=("AAA", "BBB", "AAA")):
        self.metrics = {"annual_return": annual, "excess_annual": annual - 0.10,
                        "max_drawdown": -0.3}
        self.ever_held = list(held)


class FakeStage:
    """Stands in for stage_b.run, and records exactly how it was called."""

    def __init__(self, by_start=None, by_exclusion=None, default=0.12):
        self.calls = []
        self.by_start = by_start or {}
        self.by_exclusion = by_exclusion or {}
        self.default = default

    def __call__(self, source, spec, start, end, exclude=None, **kwargs):
        self.calls.append({"start": start, "exclude": exclude})
        if exclude:
            name = sorted(exclude)[0]
            if name in self.by_exclusion:
                return FakeResult(self.by_exclusion[name])
        if start in self.by_start:
            return FakeResult(self.by_start[start])
        return FakeResult(self.default)


@pytest.fixture()
def patched(monkeypatch):
    def install(stage):
        monkeypatch.setattr(sensitivity.stage_b, "run", stage)
        return stage
    return install


START, END = dt.date(2015, 1, 5), dt.date(2025, 1, 5)


def test_the_start_date_is_moved_both_ways(patched):
    stage = patched(FakeStage())
    sensitivity.run(None, None, START, END, candidates=1)
    starts = {c["start"] for c in stage.calls}
    assert dt.date(2014, 7, 5) in starts
    assert dt.date(2015, 7, 5) in starts


def test_the_strongest_name_is_removed_at_selection_not_afterwards(patched):
    """Excluding it from the screen lets the next name down take the weight — which is
    what would have happened. Deleting it from the finished result would leave a
    portfolio that held 90% of itself."""
    stage = patched(FakeStage(by_exclusion={"AAA": 0.02}))
    report = sensitivity.run(None, None, START, END)
    assert report.strongest_name == "AAA"
    assert any(c["exclude"] == {"AAA"} for c in stage.calls)
    assert any("without AAA" in v.name for v in report.variations)


def test_the_name_whose_absence_hurts_most_is_the_one_reported(patched):
    patched(FakeStage(by_exclusion={"AAA": 0.11, "BBB": 0.01}))
    report = sensitivity.run(None, None, START, END)
    assert report.strongest_name == "BBB"


def test_a_result_that_moves_little_is_called_robust(patched):
    patched(FakeStage(by_start={dt.date(2014, 7, 5): 0.125,
                                dt.date(2015, 7, 5): 0.118},
                      by_exclusion={"AAA": 0.119, "BBB": 0.121}, default=0.12))
    report = sensitivity.run(None, None, START, END)
    assert report.spread < 0.02
    assert "does not depend" in report.verdict()


def test_a_result_carried_by_one_holding_is_called_out(patched):
    patched(FakeStage(by_exclusion={"AAA": -0.02}, default=0.12))
    report = sensitivity.run(None, None, START, END)
    assert report.spread > 0.05
    assert "large part of it" in report.verdict()


def test_a_run_that_produced_nothing_reports_nothing_rather_than_zero(patched):
    patched(lambda *a, **k: None)
    report = sensitivity.run(None, None, START, END)
    assert report.main.annual_return is None
    assert report.spread is None
    assert "Not enough variations" in report.verdict()


def test_a_shifted_start_past_the_end_is_skipped(patched):
    stage = patched(FakeStage())
    sensitivity.run(None, None, dt.date(2024, 12, 1), dt.date(2025, 1, 5), candidates=1)
    assert all(c["start"] < dt.date(2025, 1, 5) for c in stage.calls)


def test_the_table_lists_the_main_run_first(patched):
    patched(FakeStage())
    table = sensitivity.as_table(sensitivity.run(None, None, START, END, candidates=1))
    assert table.iloc[0]["variation"] == "as specified"
    assert set(table.columns) == {"variation", "return p.a.", "vs index",
                                  "max drawdown", "note"}


def test_a_month_shift_lands_on_a_real_date_clamped_to_the_month_end():
    """31 March back six months is 30 September, and 31 October forward six months is
    30 April — the last valid day, not a fixed 28th, which would move the start date by
    three days in exactly the months where the shift is being tested."""
    assert sensitivity._shift(dt.date(2020, 3, 31), -6) == dt.date(2019, 9, 30)
    assert sensitivity._shift(dt.date(2020, 10, 31), 6) == dt.date(2021, 4, 30)
    assert sensitivity._shift(dt.date(2020, 8, 31), 6) == dt.date(2021, 2, 28)
