"""Local store layout and migrations.

Two separate database files, on purpose:

  market.duckdb  — rebuildable from SEC + the price source. Safe to delete.
  user.duckdb    — portfolios, chats, backtest runs. NOT rebuildable. Never touched by a
                   market data rebuild.

Mixing the two is the mistake that eventually costs someone their saved work, because
"just rebuild the data" is a thing you do often and "lose every portfolio" is a thing you
do once.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import duckdb

MARKET_MIGRATIONS: list[str] = [
    # 001 — securities master
    """
    CREATE TABLE IF NOT EXISTS securities (
        cik            BIGINT      NOT NULL,
        ticker         VARCHAR     NOT NULL,
        name           VARCHAR,
        sector         VARCHAR,
        sic            VARCHAR,
        exchange       VARCHAR,
        first_seen     DATE,
        last_seen      DATE,
        PRIMARY KEY (cik, ticker)
    );
    """,
    # 002 — historical ticker validity. Built from filing history + observed price ranges,
    # never from today's ticker list (PLAN.md Phase 7).
    """
    CREATE TABLE IF NOT EXISTS ticker_history (
        cik         BIGINT  NOT NULL,
        ticker      VARCHAR NOT NULL,
        valid_from  DATE    NOT NULL,
        valid_to    DATE,
        source      VARCHAR NOT NULL,
        PRIMARY KEY (cik, ticker, valid_from)
    );
    """,
    # 003 — point-in-time fundamentals. `filed` is the column the whole backtest hangs on.
    """
    CREATE TABLE IF NOT EXISTS fundamentals (
        cik         BIGINT  NOT NULL,
        metric      VARCHAR NOT NULL,
        value       DOUBLE,
        period_end  DATE,
        fy          INTEGER,
        fp          VARCHAR,
        filed       DATE    NOT NULL,
        accession   VARCHAR NOT NULL,
        xbrl_tag    VARCHAR NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_fund_lookup ON fundamentals (cik, metric, filed);",
    # 004 — prices. Both bases stored; see base.py for why.
    """
    CREATE TABLE IF NOT EXISTS prices (
        ticker     VARCHAR NOT NULL,
        date       DATE    NOT NULL,
        open       DOUBLE,
        close      DOUBLE,
        close_adj  DOUBLE,
        volume     BIGINT,
        PRIMARY KEY (ticker, date)
    );
    """,
    # 005 — share counts, kept separate because their as-of date is the cover date, not
    # the period end, and the tag used varies by filer (PLAN.md A19).
    """
    -- NOTE: the column is `asof_date`, not `asof` — ASOF is reserved in DuckDB
    -- (ASOF JOIN), which is the very construct we use for point-in-time lookups.
    CREATE TABLE IF NOT EXISTS shares_outstanding (
        cik        BIGINT  NOT NULL,
        asof_date  DATE    NOT NULL,
        shares     DOUBLE,
        xbrl_tag   VARCHAR NOT NULL,
        filed      DATE    NOT NULL,
        PRIMARY KEY (cik, asof_date, xbrl_tag)
    );
    """,
    # 006 — build identity, stamped onto every backtest run
    """
    CREATE TABLE IF NOT EXISTS data_snapshot (
        built_at    TIMESTAMP NOT NULL,
        source      VARCHAR   NOT NULL,
        row_counts  VARCHAR   NOT NULL,
        PRIMARY KEY (built_at, source)
    );
    """,
    # 007 — measured coverage per field and per year. This table is the evidence for
    # Phase 1(d)/(e): which metrics are usable, and from which year on.
    """
    CREATE TABLE IF NOT EXISTS coverage (
        metric     VARCHAR NOT NULL,
        year       INTEGER NOT NULL,
        n_ciks     BIGINT  NOT NULL,
        pct_of_universe DOUBLE,
        PRIMARY KEY (metric, year)
    );
    """,
    # 008 — income-statement and cash-flow facts cover a span, not an instant. Without the
    # start date a trailing-twelve-months figure cannot be assembled from quarterlies.
    "ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS period_start DATE;",
    # 009 — position of the tag in its candidate chain. Lower wins. Lets a query prefer
    # a point-in-time share count over a period-average fallback that measures
    # something subtly different (PLAN.md A19/A20).
    "ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS tag_rank INTEGER;",
    # 010 — intraday range. A candlestick is open/high/low/close; without the two
    # extremes the only honest drawing is a line. Existing rows stay NULL until the
    # price store is rebuilt, and the chart says so rather than faking a wick from
    # the open and the close.
    "ALTER TABLE prices ADD COLUMN IF NOT EXISTS high DOUBLE;",
    "ALTER TABLE prices ADD COLUMN IF NOT EXISTS low DOUBLE;",
    # 013 — the split event, and the traded price rebuilt from it. `close` arrives
    # already split-adjusted from the free source, which is fine for returns and wrong
    # for anything multiplied by a share count; see prices.py.
    "ALTER TABLE prices ADD COLUMN IF NOT EXISTS split DOUBLE;",
    "ALTER TABLE prices ADD COLUMN IF NOT EXISTS close_raw DOUBLE;",
    # 012 — how much of the vanished world could be reconstructed (Phase 7). Stored
    # rather than recomputed, because the answer belongs on screen next to every
    # backward-looking number and recomputing it means reading a million JSON files.
    """
    CREATE TABLE IF NOT EXISTS universe_recovery (
        measured_at    TIMESTAMP NOT NULL,
        n_sample       INTEGER   NOT NULL,
        n_with_ticker  INTEGER   NOT NULL,
        n_with_prices  INTEGER   NOT NULL,
        PRIMARY KEY (measured_at)
    );
    """,
]

USER_MIGRATIONS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS portfolios (
        id          VARCHAR PRIMARY KEY,
        name        VARCHAR NOT NULL UNIQUE,
        created_at  TIMESTAMP NOT NULL,
        spec_json   VARCHAR NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id            VARCHAR PRIMARY KEY,
        portfolio_id  VARCHAR NOT NULL,
        role          VARCHAR NOT NULL,
        text          VARCHAR NOT NULL,
        spec_json     VARCHAR,
        created_at    TIMESTAMP NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_runs (
        id                 VARCHAR PRIMARY KEY,
        portfolio_id       VARCHAR NOT NULL,
        kind               VARCHAR NOT NULL,
        spec_fingerprint   VARCHAR NOT NULL,
        data_fingerprint   VARCHAR NOT NULL,
        start_date         DATE,
        end_date           DATE,
        holdout_unlocked   BOOLEAN NOT NULL DEFAULT FALSE,
        params_json        VARCHAR NOT NULL,
        metrics_json       VARCHAR NOT NULL,
        created_at         TIMESTAMP NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_fp ON backtest_runs (spec_fingerprint, data_fingerprint);",
    # Option positions. Stored per portfolio with the volatility that was used to price
    # them, so a position can be revalued later against the assumptions it was built on
    # rather than against whatever the market implies today.
    """
    CREATE TABLE IF NOT EXISTS watchlist (
        ticker      VARCHAR PRIMARY KEY,
        note        VARCHAR,
        added_at    TIMESTAMP NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS option_legs (
        id            VARCHAR PRIMARY KEY,
        portfolio_id  VARCHAR NOT NULL,
        underlying    VARCHAR NOT NULL,
        kind          VARCHAR NOT NULL,
        strike        DOUBLE  NOT NULL,
        expiry        VARCHAR NOT NULL,
        quantity      DOUBLE  NOT NULL,
        entry_price   DOUBLE  NOT NULL,
        iv            DOUBLE,
        contract_size INTEGER NOT NULL DEFAULT 100,
        created_at    TIMESTAMP NOT NULL
    );
    """,
    # Live paper positions. Frozen at the moment the user commits, which is what makes a
    # tracked portfolio genuinely out-of-sample: the picks exist before the outcome does.
    # No survivorship bias, no look-ahead, no quiet re-selection after the fact.
    """
    CREATE TABLE IF NOT EXISTS tracked (
        portfolio_id  VARCHAR NOT NULL,
        ticker        VARCHAR NOT NULL,
        name          VARCHAR,
        weight        DOUBLE  NOT NULL,
        entry_price   DOUBLE  NOT NULL,
        entry_date    DATE    NOT NULL,
        entry_cost    DOUBLE  NOT NULL,
        PRIMARY KEY (portfolio_id, ticker, entry_date)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tracking (
        portfolio_id     VARCHAR PRIMARY KEY,
        started_on       DATE      NOT NULL,
        thesis           VARCHAR,
        benchmark        VARCHAR   NOT NULL,
        spec_json        VARCHAR   NOT NULL,
        created_at       TIMESTAMP NOT NULL
    );
    """,
    # 014 — the strategy lab. Chat threads are separated by `kind` so the screen's
    # conversation and the rule's conversation do not read as one muddled thread, and
    # the rule lives per portfolio because it is tested on that portfolio's names.
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS kind VARCHAR DEFAULT 'screen';",
    """
    CREATE TABLE IF NOT EXISTS strategies (
        portfolio_id  VARCHAR PRIMARY KEY,
        spec_json     VARCHAR NOT NULL,
        updated_at    TIMESTAMP NOT NULL
    );
    """,
    # 015 — paper books: simulated purchases from any tab, frozen when opened and
    # valued live (paper.py). Separate from `tracked`, which is one screen per portfolio.
    """
    CREATE TABLE IF NOT EXISTS paper_books (
        id           VARCHAR PRIMARY KEY,
        name         VARCHAR NOT NULL,
        origin       VARCHAR NOT NULL,
        kind         VARCHAR NOT NULL,
        started_on   DATE    NOT NULL,
        amount       DOUBLE  NOT NULL,
        config_json  VARCHAR NOT NULL,
        thesis       VARCHAR,
        created_at   TIMESTAMP NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_positions (
        book_id      VARCHAR NOT NULL,
        ticker       VARCHAR NOT NULL,
        weight       DOUBLE  NOT NULL,
        entry_price  DOUBLE,
        entry_cost   DOUBLE  NOT NULL DEFAULT 0,
        PRIMARY KEY (book_id, ticker)
    );
    """,
    # 016 — AI summaries of a company, keyed by the data build they were written from
    # (summary.py), so the same question on the same data is not paid for twice.
    """
    CREATE TABLE IF NOT EXISTS ai_summaries (
        ticker            VARCHAR   NOT NULL,
        data_fingerprint  VARCHAR   NOT NULL,
        text              VARCHAR   NOT NULL,
        model             VARCHAR,
        created_at        TIMESTAMP NOT NULL
    );
    """,
]


def _name_of(sql: str) -> str:
    """A stable identifier for a migration, taken from what it creates or alters."""
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS (\w+)|CREATE INDEX IF NOT EXISTS (\w+)"
        r"|ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)",
        sql,
    )
    if not m:
        return hashlib.sha1(sql.encode()).hexdigest()[:16]
    parts = [g for g in m.groups() if g]
    return ".".join(parts)


def _apply(con: duckdb.DuckDBPyConnection, migrations: list[str]) -> None:
    """Apply pending migrations, keyed by NAME rather than by list position.

    Position was the original scheme and it failed exactly as it always does: inserting
    two migrations in the middle of the list shifted everything after them, so a database
    that had recorded "0 through 5 applied" treated the two new entries — now sitting at
    positions 4 and 5 — as already done. They were never run, nothing complained, and the
    tables were simply missing. Measured on 2026-09-22 with `watchlist` and `option_legs`.

    Naming each migration after what it creates makes insertion order irrelevant. The
    first run against an older database backfills the names for whatever already exists,
    so nothing is re-applied and nothing is skipped.
    """
    con.execute("CREATE TABLE IF NOT EXISTS _migrations (n INTEGER PRIMARY KEY);")
    con.execute("CREATE TABLE IF NOT EXISTS _migrations_named (name VARCHAR PRIMARY KEY);")
    done = {r[0] for r in con.execute("SELECT name FROM _migrations_named").fetchall()}

    if not done:
        # First run under the named scheme: mark everything that is already there.
        existing = {
            r[0].lower()
            for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        for sql in migrations:
            name = _name_of(sql)
            target = name.split(".")[0].lower()
            if "CREATE TABLE" in sql and target in existing:
                con.execute("INSERT INTO _migrations_named VALUES (?)", [name])
                done.add(name)

    for sql in migrations:
        name = _name_of(sql)
        if name in done:
            continue
        con.execute(sql)
        con.execute("INSERT OR IGNORE INTO _migrations_named VALUES (?)", [name])


def open_market(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the market store.

    DuckDB allows ONE writer or MANY readers. The app only ever reads market data, so it
    must open read-only — otherwise having the app running blocks every build script, and
    running a build script blocks the app. Migrations are skipped in read-only mode
    because the schema is already there by the time anything reads it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        return duckdb.connect(str(path), read_only=True)
    try:
        con = duckdb.connect(str(path))
    except duckdb.IOException as exc:
        if "lock" not in str(exc).lower():
            raise
        raise RuntimeError(
            "The market database is open elsewhere, so this build cannot write to it. "
            "DuckDB allows one writer or many readers. Close the Portfolio Lab app "
            "(stop it in the VS Code terminal with Ctrl-C) and run this again."
        ) from None
    _apply(con, MARKET_MIGRATIONS)
    return con


def open_user_snapshot(path: Path) -> duckdb.DuckDBPyConnection:
    """Read the portfolio store even while the app holds it.

    DuckDB's write lock is exclusive against readers too, and across processes — "one
    writer or many readers" means many readers only when there is no writer at all. So a
    scheduled job cannot simply open the file read-only while the app is running; it was
    measured failing exactly that way.

    Copying sidesteps the question: a file copy knows nothing about database locks. The
    store is small, and a scheduled briefing runs at an hour when nobody is clicking, so
    the window in which a copy could catch a half-written commit is negligible. If it
    does, opening the copy fails and the caller can carry on without the watchlist rather
    than dying — which is the right trade for a job nobody is watching.
    """
    import shutil
    import tempfile

    if not path.exists():
        raise RuntimeError(f"No portfolio store at {path}. Open the app once to create it.")
    tmp = Path(tempfile.gettempdir()) / f"plab-snapshot-{os.getpid()}.duckdb"
    shutil.copy2(path, tmp)

    # The write-ahead log has to come too. DuckDB commits into `<db>.wal` and only folds
    # it back into the main file at a checkpoint — typically when the last connection
    # closes. While the app is running, everything written since it started up lives in
    # the WAL and NOT in the file we just copied.
    #
    # Copying the file alone therefore yields a database frozen at the last checkpoint,
    # silently missing every recent watchlist entry, portfolio and tracked position. It
    # was measured doing exactly that: a store whose file was 17 hours old.
    wal = path.with_suffix(path.suffix + ".wal")
    tmp_wal = tmp.with_suffix(tmp.suffix + ".wal")
    tmp_wal.unlink(missing_ok=True)
    if wal.exists():
        shutil.copy2(wal, tmp_wal)
    try:
        # Read-WRITE on purpose: replaying a WAL is a write, and DuckDB refuses to open a
        # database that needs replaying in read-only mode. This is a private copy in the
        # temp directory, so writing to it cannot touch the real store.
        return duckdb.connect(str(tmp))
    except duckdb.Error as exc:
        tmp.unlink(missing_ok=True)
        tmp_wal.unlink(missing_ok=True)
        raise RuntimeError(
            "Could not read a snapshot of the portfolio store — it was probably being "
            "written at that moment. Nothing is damaged; try again."
        ) from exc


def open_market_for_rebuild(path: Path) -> tuple[duckdb.DuckDBPyConnection, Path | None]:
    """A writable market database, even while the app is reading the live one.

    DuckDB gives a writer exclusive access to a file, so a build could only run when the
    app was closed. In practice the app is open, which meant the scheduled morning job
    silently skipped its price refresh and wrote the briefing on yesterday's closes.

    So the build works on a copy and swaps it in when it is finished:

      * nothing holds a lock the app needs, so the app keeps serving all the way through;
      * a build that crashes leaves the live file untouched — the swap is the last step
        and `os.replace` is atomic, so there is no moment with a half-written store;
      * the copy costs about 0.4s here, because APFS clones rather than duplicates bytes.

    Returns the connection and, if a copy is in use, the path to swap in afterwards via
    `finish_rebuild`. When the live file happens to be free, it is used directly and the
    second element is None.
    """
    import shutil

    try:
        con = duckdb.connect(str(path))
    except duckdb.Error:
        con = None  # Held by the app. Work on a copy instead of refusing to run.
    if con is not None:
        _apply(con, MARKET_MIGRATIONS)
        return con, None

    work = path.with_suffix(path.suffix + ".building")
    work_wal = work.with_suffix(work.suffix + ".wal")
    for stale in (work, work_wal):
        stale.unlink(missing_ok=True)
    shutil.copy2(path, work)
    wal = path.with_suffix(path.suffix + ".wal")
    if wal.exists():
        shutil.copy2(wal, work_wal)
    con = duckdb.connect(str(work))
    # The copy carries the schema the live file had. A build that adds a column has to
    # migrate the copy too, or it writes against yesterday's table and fails — which is
    # exactly how the high/low columns would have been lost on first use.
    _apply(con, MARKET_MIGRATIONS)
    return con, work


def finish_rebuild(con: duckdb.DuckDBPyConnection, work: Path | None, live: Path) -> bool:
    """Close the build connection and, if it was a copy, put it in place. True if swapped.

    CHECKPOINT first so everything is folded into the single file being moved: replacing
    the main file while recent commits still sat in a separate WAL would lose them.
    """
    if work is not None:
        con.execute("CHECKPOINT")
    con.close()
    if work is None:
        return False
    os.replace(work, live)               # atomic: readers see one file or the other
    work.with_suffix(work.suffix + ".wal").unlink(missing_ok=True)
    return True


def open_user(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the portfolios/chat/tracking store.

    Read-write is the default because this is the half the app writes to, which is also
    why only one app instance can run at a time.

    Anything that only READS it must pass read_only=True — above all the scheduled
    morning briefing, which needs the watchlist and holdings but changes nothing. Without
    it, leaving the app open overnight makes the 07:15 job fail every single day, and the
    careful error message written for a human at a screen is read by nobody.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        if not path.exists():
            raise RuntimeError(
                f"No portfolio store at {path}. Open the app once to create it."
            )
        return duckdb.connect(str(path), read_only=True)
    try:
        con = duckdb.connect(str(path))
    except duckdb.IOException as exc:
        if "lock" not in str(exc).lower():
            raise
        raise RuntimeError(
            "Portfolio Lab is already running somewhere else. Only one instance can "
            "hold your portfolios at a time, so that two windows cannot overwrite each "
            "other's saves. Close the other one and reload this page."
        ) from None
    _apply(con, USER_MIGRATIONS)
    return con
