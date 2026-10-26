"""The testable parts of book E's historical simulation (scripts/e_backtest.py): frame
choice, the year-earlier frame, delisting exits and the survivorship label. Audit
24.10.2026 F12 and F22. Hand-computed values, no real stores."""

from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from portfolio_lab.data import history
from portfolio_lab.forward import ledger as lg
from portfolio_lab.forward import simulation as sim
from portfolio_lab.pit_cache import FrameCache

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e_backtest.py"


def test_a_delisted_long_takes_the_real_return_where_the_store_has_one():
    con = duckdb.connect()
    con.execute("CREATE TABLE delisting_returns (ticker VARCHAR, ret DOUBLE)")
    con.execute("INSERT INTO delisting_returns VALUES ('GONE', -0.5), ('BOUGHT', 0.2)")
    real = history.delisting_returns(con, ["GONE", "BOUGHT", "NOROW"])
    # stake 100 bought at 10, last close 8: real -50 % -> 4; value 100 x 4/10 = 40
    x = sim.delisting_exit(100.0, 10.0, 8.0, "long", real.get("GONE"))
    assert x["value"] == pytest.approx(40.0) and x["how"] == "real"
    assert x["fee"] == pytest.approx(lg.COST * 40.0)
    # a takeover at +20 % on the last close 8: 9.60; the short gets 100 x (1 + 0.04)
    s = sim.delisting_exit(100.0, 10.0, 8.0, "short", real.get("BOUGHT"))
    assert s["value"] == pytest.approx(104.0)
    # no row: a long at -30 %, a short at its last close
    a = sim.delisting_exit(100.0, 10.0, 8.0, "long", real.get("NOROW"))
    assert a["value"] == pytest.approx(56.0) and a["how"] == "assumed"
    b = sim.delisting_exit(100.0, 10.0, 8.0, "short", None)
    assert b["value"] == pytest.approx(120.0) and b["return"] == 0.0


def test_the_departures_are_counted_by_how_they_were_valued():
    trades = [("A", "long", None, None, -5.0, "delisted (real return)"),
              ("B", "short", None, None, 1.0, "delisted (assumed return)"),
              ("C", "long", None, None, 2.0, "rebalance")]
    assert sim.departures_line(trades).startswith(
        "departures 2 (real delisting return 1, assumed 1")


def test_fewer_than_five_frames_stop_the_run(tmp_path):
    # F22: after the cache was pruned the script printed a flat 1,000 curve.
    for d in pd.date_range("2025-03-31", periods=4, freq="QE"):
        (tmp_path / f"{d.date()}.parquet").write_bytes(b"")
    with pytest.raises(sim.TooFewFrames, match="4 pit-cache frame"):
        sim.frame_dates(tmp_path)
    (tmp_path / "2026-03-31.parquet").write_bytes(b"")
    assert len(sim.frame_dates(tmp_path)) == 5


def test_the_year_earlier_frame_is_chosen_by_date_not_position():
    # Monthly frames: frames[k - 4] is four months back, not a year.
    frames = list(pd.date_range("2024-01-31", "2026-06-30", freq="ME"))
    k = sim.frame_on(frames, pd.Timestamp("2026-06-15"))
    assert k == pd.Timestamp("2026-05-31")
    assert sim.year_before(frames, k) == pd.Timestamp("2025-05-31")
    # quarterly frames: the one a year before
    q = list(pd.date_range("2024-03-31", "2026-06-30", freq="QE"))
    assert sim.year_before(q, pd.Timestamp("2026-06-30")) == pd.Timestamp("2025-06-30")
    # nothing within a year and a quarter: no share growth, not an old frame
    assert sim.year_before(q[-3:], q[-1]) is None


def test_the_cache_folder_follows_the_live_fingerprint(tmp_path):
    src = SimpleNamespace(snapshot=lambda: SimpleNamespace(fingerprint=lambda: "abc"))
    assert sim.cache_root(src, tmp_path) == FrameCache(src, tmp_path, "abc").root


def test_the_script_says_survivorship_is_not_corrected():
    text = SCRIPT.read_text()
    assert "SURVIVORSHIP IS NOT CORRECTED" in text           # the docstring (R12)
    assert "print(sim.SURVIVORSHIP" in text and "sim.departures_line(trades)" in text
    assert "cache/pit/v" not in text                         # no hard-coded folder
    assert "NOT corrected" in sim.SURVIVORSHIP
    compile(text, str(SCRIPT), "exec")
