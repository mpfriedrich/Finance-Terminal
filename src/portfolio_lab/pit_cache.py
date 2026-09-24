"""Screening frames kept on disk, one per rebalance date.

Measured, because the plan asks for the number and not for an impression: assembling one
point-in-time screening frame over the live store costs **6 to 10 seconds**, and 98% of
that is `fundamentals_wide` — three window functions over 6.1 million filed facts. A
quarterly run from 2011 needs 63 of them, so a full stage B backtest takes about **eight
and a half minutes** against a five-minute budget.

The plan's own instruction for exactly this case is to cache the per-date intermediate
results until the budget is met, which is what this does. Two properties make it safe:

  * **The cache is keyed by the data build.** Free price sources restate adjusted closes
    and the SEC publishes corrections, so a frame computed last month is not the same
    frame. A new build gets new files rather than stale ones (R22).
  * **It is spec-independent.** The expensive part is the point-in-time assembly, not the
    filtering. So one warm-up serves every strategy afterwards — which matters, because
    trying many specs is precisely what the person will do, and it is also precisely what
    the attempt counter is there to keep honest.

Nothing here changes a single number. If the cache is deleted the run is identical and
slower, and a test asserts exactly that.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

CACHE_VERSION = "v1"


def _build_id(fingerprint: str) -> str:
    import hashlib

    return hashlib.sha1(fingerprint.encode()).hexdigest()[:10] if fingerprint else "nofp"


class FrameCache:
    """Screening frames for a set of dates, stored as parquet beside the market data."""

    def __init__(self, source, root: Path, data_fingerprint: str = "") -> None:
        self.source = source
        self.build = _build_id(data_fingerprint)
        self.root = Path(root) / CACHE_VERSION / self.build
        self.hits = 0
        self.misses = 0

    def path_for(self, asof: dt.date) -> Path:
        return self.root / f"{asof.isoformat()}.parquet"

    def frame_for(self, asof: dt.date) -> pd.DataFrame:
        """The screening frame for that date, from disk if it is there.

        Drop-in for `source.screening_frame`: same signature, same contents. stage_b
        takes it as `frame_for`, so the backtest loop itself knows nothing about caching.
        """
        path = self.path_for(asof)
        if path.exists():
            self.hits += 1
            return pd.read_parquet(path)
        frame = self.source.screening_frame(asof)
        self.misses += 1
        if frame is not None and not frame.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A temporary name first, then a rename: a run interrupted halfway through a
            # write would otherwise leave a half-written file that reads as a valid but
            # truncated universe on the next run.
            temporary = path.with_suffix(".parquet.partial")
            frame.to_parquet(temporary, index=False)
            temporary.replace(path)
        return frame

    def warm(self, dates: list[dt.date], progress=None) -> dict:
        """Fill the cache for these dates. Returns what it had to compute."""
        started = dt.datetime.now()
        for i, asof in enumerate(dates, start=1):
            self.frame_for(asof)
            if progress:
                progress(i, len(dates), asof)
        return {
            "dates": len(dates),
            "computed": self.misses,
            "reused": self.hits,
            "seconds": (dt.datetime.now() - started).total_seconds(),
        }

    def clear(self) -> int:
        """Delete this build's frames. The next run recomputes them, identically."""
        if not self.root.exists():
            return 0
        files = list(self.root.glob("*.parquet"))
        for file in files:
            file.unlink()
        return len(files)
