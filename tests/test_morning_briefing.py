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
