"""The morning job re-warms the free pit cache after the daily update (Befund 01.10.2026)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "morning_briefing.py"


@pytest.fixture()
def job():
    spec = importlib.util.spec_from_file_location("morning_briefing", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_free_cache_is_warmed_and_pruned_after_a_good_update(job):
    command = job.warm_command("ok")
    assert command[1].endswith("warm_pit_cache.py")
    assert "--source=free" in command and "--prune" in command


def test_no_warm_when_the_prices_did_not_update(job):
    # The fingerprint did not change, so the existing frames are still the right ones.
    assert job.warm_command("failed: timeout") is None


def test_the_warm_runs_even_when_the_briefing_fails(job, monkeypatch, tmp_path):
    warmed = []
    monkeypatch.setattr(job, "OUT_DIR", tmp_path)
    monkeypatch.setattr(job, "refresh_prices", lambda: "ok")
    monkeypatch.setattr(job, "warm_cache", warmed.append)

    def broken(today, note):
        raise RuntimeError("no network")

    monkeypatch.setattr(job, "deliver", broken)
    with pytest.raises(RuntimeError):
        job.main()
    assert warmed == ["ok"]


# -- audit 24.10.2026 F14 and low items 5, 13 ---------------------------------------------

class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


URL = "https://data.nasdaq.com/api/v3/x?api_key=SECRET123"


def test_the_forward_runs_stderr_reaches_run_log_without_urls(job, monkeypatch, capsys):
    """F14: capture_output kept the child's stderr from run.log. Now the stdout before
    the section and the last 4,000 characters of stderr are printed, URLs cut (R24)."""
    head = "x" * 5_000                                    # pushed out of the tail
    err = (head + "\nTraceback (most recent call last):\n  ...\n"
           f"requests.exceptions.ConnectionError: {URL}\n")
    out = "booking A ... ok\n# Paper-Portfolios A–F\n\nA: 1.000 EUR\n"
    monkeypatch.setattr(job.subprocess, "run", lambda *a, **k: _Done(1, out, err))
    section = job.forward_books()
    assert section == "# Paper-Portfolios A–F\n\nA: 1.000 EUR"
    log = capsys.readouterr().out
    assert "booking A ... ok" in log and "A: 1.000 EUR" not in log
    assert "Traceback (most recent call last)" in log and "ConnectionError" in log
    assert "SECRET123" not in log and "nasdaq.com" not in log and "<address>" in log
    assert "x" * 4_000 not in log                        # only the last 4,000 characters
    assert "…x" in log                                   # and the cut is shown


def test_a_failed_forward_run_says_so_in_the_brief_without_its_url(job, monkeypatch):
    """Low item 5: the exception text goes into the mail through ui.error_text."""
    def broken(*a, **k):
        raise OSError(f"cannot reach {URL}")

    monkeypatch.setattr(job.subprocess, "run", broken)
    section = job.forward_books()
    assert section.startswith("# Paper-Portfolios A–F") and "OSError" in section
    assert "SECRET123" not in section and "nasdaq.com" not in section


def test_a_failed_price_update_note_carries_no_url(job, monkeypatch, capsys):
    """Low item 5: the price note is printed in the mail's header line."""
    monkeypatch.setattr(job.subprocess, "run",
                        lambda *a, **k: _Done(1, "", f"HTTPError: 401 for url: {URL}"))
    note = job.refresh_prices()
    assert note.startswith("failed: HTTPError: 401") and "SECRET123" not in note
    assert "SECRET123" not in capsys.readouterr().out


def test_a_brief_is_written_with_nothing_followed(job, monkeypatch, tmp_path):
    """Low item 13: forward_books() always returns a section, so the old "Nothing
    followed, no briefing" exit could never run and is gone. With nothing followed,
    the brief holds the paper books alone."""
    import datetime as dt

    class Store:
        def __init__(self, con):
            pass

        def everything_followed(self):
            return []

    monkeypatch.setattr(job, "OUT_DIR", tmp_path)
    monkeypatch.setattr(job.subprocess, "run", lambda *a, **k: _Done())
    monkeypatch.setattr(job, "forward_books", lambda: "# Paper-Portfolios A–F\n\nA")
    monkeypatch.setattr(job, "open_market", lambda *a, **k: None)
    monkeypatch.setattr(job, "open_user_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(job, "Store", Store)
    monkeypatch.setattr(job, "send", lambda *a: "not configured")
    monkeypatch.setattr(job, "notify", lambda *a: None)
    assert job.deliver(dt.date(2026, 10, 26), "ok") == 0
    assert (tmp_path / "2026-10-26.md").read_text() == "# Paper-Portfolios A–F\n\nA"
