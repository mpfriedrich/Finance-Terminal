"""The cache may make a backtest faster. It may not make it different."""
import datetime as dt

import pandas as pd
import pytest

from portfolio_lab.pit_cache import FrameCache


class CountingSource:
    """A source that records how often the expensive call was actually made."""

    def __init__(self) -> None:
        self.calls: list[dt.date] = []

    def screening_frame(self, asof: dt.date) -> pd.DataFrame:
        self.calls.append(asof)
        return pd.DataFrame({
            "ticker": ["AAA", "BBB"],
            "cik": [1, 2],
            "price": [10.0, 20.0],
            "asof_marker": [asof.isoformat()] * 2,
        })


def test_the_second_ask_does_not_recompute(tmp_path):
    src = CountingSource()
    cache = FrameCache(src, tmp_path, "build-A")
    day = dt.date(2020, 1, 2)

    first = cache.frame_for(day)
    second = cache.frame_for(day)

    assert src.calls == [day]
    assert cache.hits == 1 and cache.misses == 1
    pd.testing.assert_frame_equal(first, second)


def test_the_cached_frame_is_the_computed_frame(tmp_path):
    """The only claim that matters: nothing here changes a number."""
    src = CountingSource()
    cache = FrameCache(src, tmp_path, "build-A")
    day = dt.date(2020, 1, 2)

    cached = cache.frame_for(day)
    fresh = CountingSource().screening_frame(day)
    pd.testing.assert_frame_equal(cached, fresh)


def test_a_different_data_build_does_not_read_the_old_frames(tmp_path):
    """Adjusted closes get restated and the SEC publishes corrections, so a frame from
    last month's build is not this month's frame (R22)."""
    src = CountingSource()
    day = dt.date(2020, 1, 2)
    FrameCache(src, tmp_path, "build-A").frame_for(day)
    FrameCache(src, tmp_path, "build-B").frame_for(day)
    assert src.calls == [day, day]


def test_clearing_the_cache_costs_time_and_nothing_else(tmp_path):
    src = CountingSource()
    cache = FrameCache(src, tmp_path, "build-A")
    day = dt.date(2020, 1, 2)

    before = cache.frame_for(day)
    assert cache.clear() == 1
    after = cache.frame_for(day)

    assert src.calls == [day, day]
    pd.testing.assert_frame_equal(before, after)


def test_warming_reports_what_it_had_to_compute(tmp_path):
    src = CountingSource()
    cache = FrameCache(src, tmp_path, "build-A")
    dates = [dt.date(2020, 1, 2), dt.date(2020, 4, 1)]

    first = cache.warm(dates)
    second = cache.warm(dates)

    assert first["computed"] == 2 and first["reused"] == 0
    assert second["computed"] == 2 and second["reused"] == 2   # counters accumulate
    assert len(src.calls) == 2


def test_an_empty_frame_is_not_written(tmp_path):
    """An empty universe is usually a date before the data starts. Caching it would make
    that emptiness permanent for this build."""
    class Empty(CountingSource):
        def screening_frame(self, asof):
            self.calls.append(asof)
            return pd.DataFrame()

    src = Empty()
    cache = FrameCache(src, tmp_path, "build-A")
    day = dt.date(2009, 1, 2)
    cache.frame_for(day)
    cache.frame_for(day)
    assert src.calls == [day, day]
    assert not cache.path_for(day).exists()


def test_a_half_written_file_cannot_be_read_as_a_universe(tmp_path):
    """Written under a temporary name and renamed, so an interrupted run leaves either
    the whole frame or nothing — never a truncated universe that screens fine."""
    src = CountingSource()
    cache = FrameCache(src, tmp_path, "build-A")
    cache.frame_for(dt.date(2020, 1, 2))
    assert not list(cache.root.glob("*.partial"))
