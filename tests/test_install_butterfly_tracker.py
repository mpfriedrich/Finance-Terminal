"""scripts/install_butterfly_tracker.sh: every scheduled run lies inside the decision
window all year, and the job is installed for the main checkout, not a worktree.

Audit 24.10.2026 F16: 01:30 Sydney was 09:30 New York from November to mid-March
(AEDT against EST), before the 09:45 window, so one of three runs a night never decided.
The script is parsed, not run for real: running it would call launchctl."""

import datetime as dt
import re
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from portfolio_lab import filing_butterfly as fb

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_butterfly_tracker.sh"
SYDNEY = ZoneInfo("Australia/Sydney")


def scheduled() -> tuple[list[int], list[tuple[int, int]]]:
    """The launchd weekdays and the (hour, minute) pairs the script installs."""
    text = SCRIPT.read_text()
    days = re.search(r"for DAY in ([\d ]+);", text).group(1).split()
    hms = re.search(r"for HM in ((?:\"\d+ \d+\" ?)+);", text).group(1)
    times = [tuple(int(x) for x in pair.split()) for pair in re.findall(r'"([^"]+)"', hms)]
    return [int(d) for d in days], times


def test_every_scheduled_run_is_inside_the_decision_window_all_year():
    days, times = scheduled()
    assert days == [2, 3, 4, 5, 6] and len(times) == 3
    offsets = set()
    day = dt.date(2026, 7, 1)
    while day <= dt.date(2028, 6, 30):          # two full years: every DST switch twice
        launchd_weekday = (day.weekday() + 1) % 7      # launchd: 0 = Sunday
        if launchd_weekday in days:
            for hour, minute in times:
                local = dt.datetime(day.year, day.month, day.day, hour, minute,
                                    tzinfo=SYDNEY)
                ny = local.astimezone(fb.ET)
                offsets.add(local.utcoffset() - ny.utcoffset())
                assert ny.weekday() < 5, (local, ny)
                minutes = ny.hour * 60 + ny.minute
                assert 9 * 60 + 45 <= minutes <= 15 * 60 + 45, (local, ny)
                if fb.is_session(ny.date()):
                    assert fb.in_session(local), (local, ny)
        day += dt.timedelta(days=1)
    # AEST/EDT, AEDT/EDT and AEDT/EST all occurred: 14, 15 and 16 hours ahead
    assert offsets == {dt.timedelta(hours=h) for h in (14, 15, 16)}


def test_the_comment_and_the_message_name_the_installed_times():
    _days, times = scheduled()
    text = SCRIPT.read_text()
    names = ", ".join(f"{h:02d}:{m:02d}" for h, m in times)
    assert names == "02:00, 03:00, 04:30"
    assert text.count(names) >= 2               # header line and the final message
    assert "01:30" not in text.replace("earlier 01:30", "")


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    *args], cwd=cwd, check=True, capture_output=True)


def _run(script: Path, tmp_path: Path, *args) -> subprocess.CompletedProcess:
    """Run the script with a fake `launchctl` first on PATH that only records its call
    and fails. An older script without --dry-run (or a broken one) would otherwise
    replace the user's real launchd job from inside a test."""
    fake = tmp_path / "fakebin"
    fake.mkdir(exist_ok=True)
    (fake / "launchctl").write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/launchctl.calls"\n'
                                    "exit 1\n")
    (fake / "launchctl").chmod(0o755)
    env = {"HOME": str(tmp_path / "home"), "PATH": f"{fake}:/usr/bin:/bin"}
    return subprocess.run(["bash", str(script), *args], capture_output=True, text=True,
                          env=env, check=False)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_called_from_a_worktree_it_installs_for_the_main_checkout(tmp_path):
    main = tmp_path / "portfolio-lab"
    (main / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, main / "scripts" / SCRIPT.name)
    (main / "scripts" / "butterfly_tracker.py").write_text("")
    _git("init", "-q", "-b", "main", cwd=main)
    _git("add", ".", cwd=main)
    _git("commit", "-q", "-m", "init", cwd=main)
    wt = tmp_path / "portfolio-lab-wt"
    _git("worktree", "add", "-q", "-b", "side", str(wt), cwd=main)
    out = _run(wt / "scripts" / SCRIPT.name, tmp_path, "--dry-run")
    assert not (tmp_path / "launchctl.calls").exists()     # a dry run touches no job
    assert not (tmp_path / "home").exists()                # and writes no plist
    assert out.returncode == 0, out.stderr
    real_main = str(main.resolve())
    assert f"project: {real_main}\n" in out.stdout
    assert "called from the worktree" in out.stdout
    assert f"<string>{real_main}/scripts/butterfly_tracker.py</string>" in out.stdout
    assert f"{wt.resolve()}/scripts/butterfly_tracker.py" not in out.stdout

    (main / "scripts" / "butterfly_tracker.py").unlink()  # main not merged yet
    out = _run(wt / "scripts" / SCRIPT.name, tmp_path, "--dry-run")
    assert out.returncode == 1 and "Merge the branch" in out.stderr
