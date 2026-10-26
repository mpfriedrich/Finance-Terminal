"""Pieces of the historical simulation of book E (scripts/e_backtest.py) that can be
tested without the real stores: which pit-cache frames it reads, which frame stands for
"a year earlier", and how a share that left the market is closed.

Audit 24.10.2026 F12/F22: the script had a hard-coded cache folder (pruned after seven
days, after which it printed a flat 1,000 curve without an error), took the year-earlier
frame as `frames[k - 4]` (only right for purely quarterly frames), closed every delisted
long at -30 % without reading the store's real delisting returns, and said nothing
about survivorship.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import ledger as lg

MIN_FRAMES = 5                 # a year earlier needs at least five quarterly frames
YEAR = pd.Timedelta(days=365)
# How much older than exactly a year the earlier frame may be: one quarter of frames
# plus slack. Older than that the share growth is not over a year any more.
YEAR_SLACK = pd.Timedelta(days=120)
ASSUMED_LONG = -0.30           # R18, as the live books (engine.ASSUMED_DELISTING_RETURN)
SURVIVORSHIP = ("survivorship NOT corrected: the universe is today's list of the free "
                "store, which holds almost no dead companies (Phase 7: 0 of 20)")


class TooFewFrames(RuntimeError):
    pass


def cache_root(source, root: Path) -> Path:
    """The folder of the frames the live books read: the same fingerprint as
    engine.Live.pit_frame, so it follows every price update and CACHE_VERSION."""
    from ..pit_cache import FrameCache

    return FrameCache(source, root, source.snapshot().fingerprint()).root


def frame_dates(folder: Path) -> list[pd.Timestamp]:
    """The dates of the frames in `folder`; fewer than MIN_FRAMES is an error, never a
    silent flat curve."""
    dates = sorted(pd.Timestamp(p.stem) for p in Path(folder).glob("*.parquet"))
    if len(dates) < MIN_FRAMES:
        raise TooFewFrames(
            f"{len(dates)} pit-cache frame(s) in {Path(folder).name} — need at least "
            f"{MIN_FRAMES}. Warm the cache first: uv run python scripts/warm_pit_cache.py")
    return dates


def frame_on(dates: list[pd.Timestamp], day) -> pd.Timestamp | None:
    """The newest frame dated on or before `day`."""
    day = pd.Timestamp(day)
    older = [d for d in dates if d <= day]
    return older[-1] if older else None


def year_before(dates: list[pd.Timestamp], frame_day) -> pd.Timestamp | None:
    """The frame for "a year earlier": the newest dated on or before frame_day - 365
    days, and not more than YEAR_SLACK older. Chosen by date, not by position, so a
    cache with monthly or irregular frames gives a year too."""
    target = pd.Timestamp(frame_day) - YEAR
    d = frame_on(dates, target)
    return d if d is not None and d >= target - YEAR_SLACK else None


def delisting_exit(stake: float, entry: float, last: float, side: str,
                   real_return: float | None) -> dict:
    """Close a position whose prices ended, at its last adjusted close times one plus
    the delisting return: the store's real one (CRSP, history.delisting_returns) where
    there is one, else -30 % for a long and 0 % for a short (the conservative side for
    the book, as in the live engine). The exit fee is charged as for any close."""
    if real_return is None:
        r, how = (ASSUMED_LONG if side == "long" else 0.0), "assumed"
    else:
        r, how = float(real_return), "real"
    price = last * (1 + r)
    d = 1 if side == "long" else -1
    value = stake * (1 + d * (price / entry - 1))
    return {"value": value, "fee": lg.COST * stake * price / entry, "price": price,
            "return": r, "how": how}


def departures_line(trades: list) -> str:
    """How many positions left the market and how they were valued."""
    gone = [t for t in trades if t[5].startswith("delisted")]
    real = sum(1 for t in gone if t[5] == "delisted (real return)")
    return (f"departures {len(gone)} (real delisting return {real}, assumed "
            f"{len(gone) - real}: long -30 %, short 0 %)")
