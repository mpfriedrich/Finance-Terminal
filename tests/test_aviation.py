"""OpenSky aircraft counts: parsing, regions, the grid and the stored history — no network.

Payloads are hand-made in OpenSky's /states/all layout (checked against the live service
on 02.10.2026: {"time": unix seconds, "states": [[icao24, callsign, country, time_position,
last_contact, lon, lat, baro_altitude, on_ground, velocity, ...], ...]}).
"""
import datetime as dt

import duckdb
import pandas as pd
import pytest

from portfolio_lab import aviation
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

T = 1790910689                                   # 2026-10-02 03:11:29 UTC


def _state(icao, lat, lon, on_ground=False, callsign="DLH400  "):
    return [icao, callsign, "Germany", T, T, lon, lat, 10_000.0, on_ground, 230.0,
            90.0, 0.0, None, 10_200.0, None, False, 0]


PAYLOAD = {"time": T, "states": [
    _state("a1", 50.0, 8.6),                      # Frankfurt: Europe
    _state("a2", 51.5, -0.4),                     # London: Europe
    _state("a3", 25.3, 55.4),                     # Dubai: Middle East, not Asia or Africa
    _state("a4", 40.6, -73.8),                    # New York: North America
    _state("a5", 35.6, 139.8),                    # Tokyo: Asia-Pacific
    _state("a6", 30.0, -40.0),                    # mid-Atlantic: elsewhere
    _state("g1", 50.0, 8.6, on_ground=True),      # parked: not airborne
    _state("n1", None, None),                     # no position: cannot be placed
]}


def _store():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    return con


def test_parse_keeps_airborne_aircraft_with_a_position():
    taken_at, air = aviation.parse(PAYLOAD)
    assert taken_at == dt.datetime(2026, 10, 2, 3, 11, 29)
    assert list(air.columns) == aviation.COLUMNS
    assert sorted(air.icao24) == ["a1", "a2", "a3", "a4", "a5", "a6"]
    assert air.callsign.iloc[0] == "DLH400"


def test_an_empty_answer_is_no_aircraft_not_an_error():
    _, air = aviation.parse({"time": T, "states": None})
    assert air.empty and aviation.by_region(air).sum() == 0


def test_regions_count_every_aircraft_once_and_list_every_region():
    counts = aviation.by_region(aviation.parse(PAYLOAD)[1])
    assert counts.sum() == 6
    assert counts["Europe"] == 2 and counts["Middle East"] == 1
    assert counts["North America"] == 1 and counts["Asia-Pacific"] == 1
    assert counts[aviation.OTHER] == 1
    assert counts["Africa"] == 0 and counts["Latin America"] == 0


def test_the_grid_counts_aircraft_per_cell_at_its_centre():
    air = pd.DataFrame({"lat": [52.0, 53.0, 52.0, -0.5], "lon": [7.0, 8.0, 10.0, -0.5]})
    cells = aviation.grid(air, step=3.0).set_index(["lat", "lon"])["aircraft"]
    assert cells[(52.5, 7.5)] == 2              # 51..54 N, 6..9 E
    assert cells[(52.5, 10.5)] == 1             # 51..54 N, 9..12 E
    assert cells[(-1.5, -1.5)] == 1             # just south-west of 0/0
    assert cells.sum() == 4


def test_a_snapshot_is_stored_per_region_and_read_back_with_a_total():
    con = _store()
    total = aviation.snapshot(con, getter=lambda url: PAYLOAD)
    assert total == 6
    later = dict(PAYLOAD, time=T + 86_400)
    aviation.snapshot(con, getter=lambda url: later)
    aviation.snapshot(con, getter=lambda url: later)      # same moment again: replaced
    hist = aviation.history(con)
    assert len(hist) == 2
    assert hist["Total"].tolist() == [6, 6]
    assert hist.at[pd.Timestamp("2026-10-02 03:11:29"), "Europe"] == 2


def test_the_aircraft_map_sizes_by_count():
    from portfolio_lab import charts

    cells = pd.DataFrame({"lat": [52.5, 40.5], "lon": [7.5, -73.5], "aircraft": [100, 25]})
    marker = charts.aircraft_map(cells, dt.datetime(2026, 10, 2, 11, 11)).data[0].marker
    assert (marker.size[0] - 2) / (marker.size[1] - 2) == pytest.approx(2.0)
