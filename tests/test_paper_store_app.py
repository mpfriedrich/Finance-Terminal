"""The paper-book stores in PORTFOLIOS while their writer is at work (audit 24.10.2026).

F13: the app opened paper_forward.duckdb with the ledger's 60 lock retries and
paper_options.duckdb with the tracker's 30, one second apart, so PORTFOLIOS froze for up
to ~90 s while the morning job or the tracker held the write lock. Low 6: every read
error, a schema error included, was then called "busy". Low 5: stored run notes and
book errors reached the screen as raw exception text.

A real cross-process lock is not needed to test this: DuckDB's lock conflict is an
IOException whose message says "lock", and the fakes below raise exactly that.
"""
import shutil
from pathlib import Path

import duckdb
import pytest

from portfolio_lab import filing_butterfly as fbt
from portfolio_lab import ui
from portfolio_lab.forward import ledger as fwl
from tests.app_fixture import build_data, errors, run, texts

ROOT = Path(__file__).resolve().parents[1]
LOCKED = ('IO Error: Could not set lock on file "paper_forward.duckdb": Conflicting '
          "lock is held in python (PID 4242)")


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    return build_data(tmp_path_factory.mktemp("paperstore") / "data")


@pytest.fixture()
def fresh(data, tmp_path):
    from portfolio_lab.data.schema import open_user
    from portfolio_lab.store import Store

    copy = tmp_path / "data"
    shutil.copytree(data, copy)
    store = Store(open_user(copy / "user.duckdb"))
    store.create("Demo")
    store.con.close()
    # Both stores exist, so the views get as far as opening them.
    (copy / "paper_forward.duckdb").touch()
    (copy / "paper_options.duckdb").touch()
    return copy


def _fake_connect(raise_, calls):
    def connect(path, read_only=False, tries=None):
        calls.append(tries)
        raise raise_
    return connect


def test_a_locked_store_is_said_at_once_not_waited_out(monkeypatch, fresh):
    calls = []
    monkeypatch.setattr(fwl, "connect", _fake_connect(duckdb.IOException(LOCKED), calls))
    monkeypatch.setattr(fbt, "connect", _fake_connect(duckdb.IOException(LOCKED), calls))
    app = run(monkeypatch, fresh, query={"cmd": "PORT"})
    assert not app.exception
    assert len(calls) == 2 and all(t is not None and t <= 2 for t in calls), calls
    body = texts(app)
    assert "The forward tracker is updating its books right now" in body
    assert "The options tracker is updating its books right now" in body
    assert errors(app) == [], errors(app)          # a lock is not a fault


def test_a_schema_error_is_shown_as_an_error_not_as_busy(monkeypatch, fresh):
    calls = []
    broken = duckdb.CatalogException("Catalog Error: Table with name fw_books does not "
                                     "exist!")
    monkeypatch.setattr(fwl, "connect", _fake_connect(broken, calls))
    (fresh / "paper_options.duckdb").unlink()        # only the forward store is broken
    app = run(monkeypatch, fresh, query={"cmd": "PORT"})
    assert not app.exception
    shown = errors(app)
    assert shown and all("forward tracker's store could not be read" in e
                         for e in shown), shown
    assert "CatalogException" in shown[0]
    assert "forward tracker is updating" not in texts(app)


def test_a_damaged_file_is_shown_as_an_error_not_as_busy(monkeypatch, fresh):
    """The real connect on a file that is not a DuckDB store: an IOException, but not a
    lock."""
    (fresh / "paper_forward.duckdb").unlink()
    app = run(monkeypatch, fresh, query={"cmd": "PORT"})
    assert not app.exception
    shown = errors(app)
    assert len(shown) == 1 and "options tracker's store could not be read" in shown[0]
    assert "updating its books" not in texts(app)


def test_a_lock_is_told_apart_from_other_errors():
    assert ui.store_is_busy(duckdb.IOException(LOCKED))
    assert not ui.store_is_busy(duckdb.IOException("IO Error: No space left on device"))
    assert not ui.store_is_busy(duckdb.CatalogException("Table fw_books does not exist"))
    assert not ui.store_is_busy(RuntimeError("lock"))


def test_stored_exception_text_is_scrubbed_before_it_is_shown():
    """The forward engine stores `str(exc)[:120]` in its run notes (low 5)."""
    note = ("fw-a: Training failed (ConnectionError: GET https://query1.example/v8?"
            "crumb=x&token=abc123 refused)")
    shown = ui.scrubbed(note)
    assert "example" not in shown and "abc123" not in shown and "<address>" in shown
    assert ui.scrubbed("api_key=s3cr3t in the URL") == "api_key=… in the URL"
    source = (ROOT / "app.py").read_text()
    assert 'f" · {n}" for n in last.get("notes"' not in source
    assert source.count('f" · {ui.scrubbed(n)}" for n in last.get("notes", [])') == 2
    assert '"error": str(exc)' not in source
