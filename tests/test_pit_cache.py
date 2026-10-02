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


def test_an_interrupted_write_leaves_nothing_readable(tmp_path, monkeypatch):
    """The original version of this test performed a SUCCESSFUL write and checked that no
    .partial file remained — which stays green even if the temp-name-and-rename is
    deleted. It never simulated an interruption. This one does."""
    src = CountingSource()
    cache = FrameCache(src, tmp_path, "build-A")
    day = dt.date(2020, 1, 2)

    real_to_parquet = pd.DataFrame.to_parquet

    def die_halfway(self, path, *args, **kwargs):
        real_to_parquet(self, path, *args, **kwargs)   # the temporary file now exists
        raise KeyboardInterrupt("killed mid-write")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", die_halfway)
    with pytest.raises(KeyboardInterrupt):
        cache.frame_for(day)

    monkeypatch.undo()
    # The real path must not exist, so the next run recomputes instead of reading a
    # truncated universe that would screen perfectly well.
    assert not cache.path_for(day).exists()
    again = cache.frame_for(day)
    assert len(again) == 2


def test_frames_from_a_fallback_universe_are_not_served_after_it_is_fixed(tmp_path):
    """The universe basis is part of the cache key (independent review, 24.09.2026)."""
    from portfolio_lab.pit_cache import FrameCache

    class Source:
        def __init__(self, basis):
            self.universe_basis = basis

    fallback = FrameCache(Source("securities"), tmp_path, "same-fingerprint")
    current = FrameCache(Source("ticker_history"), tmp_path, "same-fingerprint")
    assert fallback.root != current.root


def _aged(directory, days, now):
    import os

    stamp = now.timestamp() - days * 86400
    for f in directory.iterdir():
        os.utime(f, (stamp, stamp))


def test_pruning_removes_orphaned_builds_and_keeps_current_and_recent(tmp_path):
    from portfolio_lab.pit_cache import prune

    now = dt.datetime(2026, 10, 2, 12)
    day = dt.date(2024, 1, 2)
    src = CountingSource()
    current = FrameCache(src, tmp_path, "today")
    yesterday = FrameCache(src, tmp_path, "yesterday")      # e.g. the world build
    orphan = FrameCache(src, tmp_path, "last-month")
    for cache in (current, yesterday, orphan):
        cache.frame_for(day)
    old_version = tmp_path / "v1" / "abc"
    old_version.mkdir(parents=True)
    (old_version / "2020-01-02.parquet").write_bytes(b"x")
    _aged(current.root, 30, now)        # current is kept however old its files are
    _aged(yesterday.root, 1, now)
    _aged(orphan.root, 30, now)
    _aged(old_version, 400, now)

    assert prune(tmp_path, current.root, older_than_days=7, now=now) == 2
    assert current.path_for(day).exists()
    assert yesterday.path_for(day).exists()
    assert not orphan.root.exists()
    assert not (tmp_path / "v1").exists()


def test_pruning_an_empty_cache_is_harmless(tmp_path):
    from portfolio_lab.pit_cache import prune

    assert prune(tmp_path / "missing", tmp_path / "missing" / "x") == 0
