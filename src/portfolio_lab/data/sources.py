"""Which data source the app runs on (Phase 10).

One setting, `PORTFOLIO_LAB_SOURCE` in .env.local: `free` (the default), `paid` or
`world` (the free US store plus Compustat Global from WRDS, stage 3). The
paid source needs two things the free one does not — a key and a store built with it —
and a missing one of either has to end in a sentence that says what to do, never in a
traceback.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from ..config import MARKET_DB, PAID_DB, WORLD_DB, Settings, secret
from .free import FreeSource
from .paid import KEY_NAME, MissingKey, PaidSource, missing_key_message
from .schema import open_market
from .wrds import WorldSource

CHOICES = ("free", "paid", "world")


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
