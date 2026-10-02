"""Which data source the app runs on (Phase 10).

One setting, `PORTFOLIO_LAB_SOURCE` in .env.local: `free` (the default), `paid` or
`world` (the free US store plus Compustat Global from WRDS, stage 3). The
paid source needs two things the free one does not — a key and a store built with it —
and a missing one of either has to end in a sentence that says what to do, never in a
traceback.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path

import duckdb

from ..config import MARKET_DB, PAID_DB, WORLD_DB, Settings, secret
from .free import FreeSource
from .paid import KEY_NAME, MissingKey, PaidSource, missing_key_message
from .schema import open_market
from .wrds import WorldSource

CHOICES = ("free", "paid", "world")

_LOCAL = threading.local()


def per_thread(holder):
    """A shallow copy of `holder` whose `.con` is this thread's own DuckDB cursor.

    The app opens each database once (`st.cache_resource`) and Streamlit runs every
    script run in its own thread, so a fast reload or a second window ran two queries on
    one connection at the same time. A DuckDB connection is not thread-safe: results got
    crossed, `fetchone()` came back None and a frame arrived without its columns ("'Pandas'
    object has no attribute 'metric'" in coverage.latest(), seen 02.10.2026). Measured:
    four threads on one connection, 10 failures in 1,200 queries; one cursor each, none.
    A cursor shares the database and its lock but has its own query state. Same thread,
    same copy, so a run reuses its cursor instead of opening one per call.
    """
    clones = getattr(_LOCAL, "clones", None)
    if clones is None:
        clones = _LOCAL.clones = {}
    hit = clones.get(id(holder))
    if hit is None or hit[0] is not holder:      # the tuple keeps `holder` alive, so its
        clone = copy.copy(holder)                # id cannot be reused by a new object
        clone.con = holder.con.cursor()
        hit = clones[id(holder)] = (holder, clone)
    return hit[1]


class SourceNotReady(RuntimeError):
    """The chosen source cannot be opened yet. The message is the fix."""


def open_selected(settings: Settings | None = None, read_only: bool = True,
                  market_db: Path = MARKET_DB,
                  paid_db: Path = PAID_DB,
                  world_db: Path = WORLD_DB) -> tuple[FreeSource, Path]:
    """The source the settings choose, and the file it reads (for the reopen check)."""
    choice = (settings or Settings()).data_source
    if choice not in CHOICES:
        raise SourceNotReady(
            f"PORTFOLIO_LAB_SOURCE is '{choice}', which is not a data source. "
            f"Use one of: {', '.join(CHOICES)}."
        )
    if choice == "paid":
        if not secret(KEY_NAME):
            raise MissingKey(missing_key_message())
        if not paid_db.exists():
            raise SourceNotReady(
                f"The paid data source is selected and the key is set, but the store "
                f"{paid_db} has not been built yet. Run: uv run python "
                "scripts/build_paid.py"
            )
        return PaidSource(open_market(paid_db, read_only=read_only)), paid_db
    if choice == "world":
        # The login is needed to BUILD the store, not to read it: the app opens the file.
        if not world_db.exists():
            raise SourceNotReady(
                f"The world source is selected, but {world_db} has not been built yet. "
                "Add WRDS_USERNAME (and WRDS_PASSWORD, or ~/.pgpass) to .env.local, then "
                "run: uv run python scripts/build_wrds.py"
            )
        return WorldSource(open_market(world_db, read_only=read_only)), world_db
    try:
        return FreeSource(open_market(market_db, read_only=read_only)), market_db
    except duckdb.IOException as exc:
        if not market_db.exists():
            raise SourceNotReady(
                f"No market store at {market_db}. Build it first: scripts/build_prices.py "
                "and scripts/build_fundamentals.py (see README)."
            ) from None
        raise exc
