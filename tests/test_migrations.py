"""Migration bookkeeping.

The bug this guards against was silent and total: two migrations inserted in the middle
of the list shifted every later one, a database that had recorded "the first six are
done" treated the two new entries as done, and the tables were simply never created.
Nothing raised, nothing warned. Only a query against a missing table gave it away.
"""
import duckdb

from portfolio_lab.data.schema import _apply, _name_of

A = 'CREATE TABLE IF NOT EXISTS alpha (id INTEGER);'
B = 'CREATE TABLE IF NOT EXISTS beta (id INTEGER);'
INSERTED = 'CREATE TABLE IF NOT EXISTS gamma (id INTEGER);'
IDX = 'CREATE INDEX IF NOT EXISTS idx_alpha ON alpha (id);'
COL = 'ALTER TABLE alpha ADD COLUMN IF NOT EXISTS note VARCHAR;'


def tables(con):
    return {r[0].lower() for r in con.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()}


def test_a_migration_inserted_in_the_middle_still_runs():
    """The regression itself. Order must not decide identity."""
    con = duckdb.connect(":memory:")
    _apply(con, [A, B])
    assert {"alpha", "beta"} <= tables(con)

    _apply(con, [A, INSERTED, B])          # gamma squeezed in between
    assert "gamma" in tables(con)


def test_nothing_is_applied_twice():
    con = duckdb.connect(":memory:")
    _apply(con, [A, IDX, COL])
    _apply(con, [A, IDX, COL])
    _apply(con, [A, IDX, COL])
    cols = [r[0] for r in con.execute("DESCRIBE alpha").fetchall()]
    assert cols.count("note") == 1


def test_names_come_from_what_the_statement_creates():
    assert _name_of(A) == "alpha"
    assert _name_of(IDX) == "idx_alpha"
    assert _name_of(COL) == "alpha.note"


def test_an_unrecognised_statement_still_gets_a_stable_name():
    odd = "PRAGMA something_unusual;"
    assert _name_of(odd) == _name_of(odd)
    assert len(_name_of(odd)) == 16


def test_an_older_database_is_adopted_without_rebuilding_it():
    """A store created under the old positional scheme must not have its existing tables
    re-created, nor its missing ones skipped."""
    con = duckdb.connect(":memory:")
    con.execute(A)
    con.execute("INSERT INTO alpha VALUES (1)")

    _apply(con, [A, B])
    assert con.execute("SELECT COUNT(*) FROM alpha").fetchone()[0] == 1   # data intact
    assert "beta" in tables(con)                                          # gap filled


def test_snapshot_sees_writes_still_sitting_in_the_write_ahead_log(tmp_path):
    """A snapshot must not be frozen at the last checkpoint.

    DuckDB commits into `<db>.wal` and folds it into the main file only at a checkpoint,
    which for a long-running app means "when you quit it". Copying the main file alone
    produced a store that silently missed everything written since startup — measured on
    the real store as a 17-hour-old view of the user's own portfolios.
    """
    from portfolio_lab.data.schema import open_user, open_user_snapshot

    db = tmp_path / "user.duckdb"
    live = open_user(db)
    live.execute("CREATE TABLE IF NOT EXISTS probe (k VARCHAR)")
    live.execute("INSERT INTO probe VALUES ('written-while-running')")
    # Deliberately NOT closed: this is the state the app is in all day.

    assert db.with_suffix(".duckdb.wal").exists(), "no WAL, so this test proves nothing"

    snap = open_user_snapshot(db)
    rows = snap.execute("SELECT k FROM probe").fetchall()
    assert rows == [("written-while-running",)]
    snap.close()
    live.close()
