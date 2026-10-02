"""Aircraft in the air right now, from the OpenSky Network. No key needed.

OpenSky collects ADS-B positions from volunteer receivers and serves the current state of
every aircraft it hears. Anonymous use costs 4 of 400 daily credits per worldwide call
(about 100 calls a day), is for non-commercial use, and asks for attribution. Checked
02.10.2026 at 03:11 UTC (night in Europe): 7,300 aircraft in one call, 6,372 of them
airborne with a position — 3,564 over North America, 792 over Europe — in ~3 s.

There is no free history of flight counts, so this keeps its own: the daily price build
stores one count per region (`air_traffic`). It starts the day it was first run, and the
build runs at roughly the same time each weekday, so counts compare across days — not
across times of day, which differ several-fold.

What the counts miss: coverage follows the receivers. Europe and North America are dense;
oceans, much of Africa, Russia and China are thin, so a low count there may mean no
receiver, not no aircraft. Military and some private aircraft do not broadcast. A change
in OpenSky's receiver network moves the counts too, so read a sharp step with suspicion.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

URL = "https://opensky-network.org/api/states/all"
ATTRIBUTION = "The OpenSky Network, opensky-network.org"
COLUMNS = ["icao24", "callsign", "country", "lon", "lat", "altitude_m", "velocity_ms"]
# Boxes (lat from, lat to, lon from, lon to); the first that contains a position wins, so
# the Middle East is tested before Africa and Asia. Coarse on purpose: the counts are a
# traffic gauge, not a geography lesson.
REGIONS: dict[str, tuple[float, float, float, float]] = {
    "Europe": (35, 72, -25, 45),
    "Middle East": (12, 42, 34, 63),
    "Africa": (-36, 37, -20, 52),
    "Asia-Pacific": (-50, 56, 60, 180),
    "North America": (14, 72, -170, -50),
    "Latin America": (-56, 14, -120, -30),
}
OTHER = "Oceans and elsewhere"
# Cargo airlines by the ICAO designator that starts their callsign (FDX123 = FedEx). What
# an aircraft carries is never public; whose freighter it is, is. Passenger airlines carry
# about half of all air cargo in their holds, and those flights are not counted here, so
# this measures dedicated freighters only. Some of these also fly charters or passengers.
CARGO_AIRLINES: dict[str, str] = {
    "FDX": "FedEx", "UPS": "UPS", "GTI": "Atlas Air", "CLX": "Cargolux",
    "ICV": "Cargolux Italia", "BCS": "DHL (European Air Transport)", "DHK": "DHL Air UK",
    "BOX": "AeroLogic (DHL)", "ABX": "ABX Air", "ATN": "Air Transport International",
    "CKS": "Kalitta Air", "PAC": "Polar Air Cargo", "GEC": "Lufthansa Cargo",
    "NCA": "Nippon Cargo Airlines", "CAO": "Air China Cargo", "CKK": "China Cargo Airlines",
    "CSS": "SF Airlines", "AJT": "Amerijet", "WGN": "Western Global",
    "MPH": "Martinair Cargo", "TAY": "ASL Airlines Belgium", "SQC": "Singapore Airlines Cargo",
}


def parse(payload: dict) -> tuple[dt.datetime, pd.DataFrame]:
    """OpenSky's state vectors -> (snapshot time in UTC, airborne aircraft with a position).

    A state vector is a list: [0] icao24, [1] callsign, [2] origin country, [5] longitude,
    [6] latitude, [7] barometric altitude, [8] on ground, [9] velocity, ...
    """
    taken_at = dt.datetime.fromtimestamp(int(payload["time"]), dt.UTC).replace(tzinfo=None)
    rows = [
        (s[0], (s[1] or "").strip(), s[2], s[5], s[6], s[7], s[9])
        for s in payload.get("states") or []
        if not s[8] and s[5] is not None and s[6] is not None
    ]
    return taken_at, pd.DataFrame(rows, columns=COLUMNS)


def fetch(getter=None, timeout: int = 60) -> tuple[dt.datetime, pd.DataFrame]:
    if getter is None:
        import requests

        def getter(url):
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
    return parse(getter(URL))


def region_of(lat: float, lon: float) -> str:
    for name, (lat0, lat1, lon0, lon1) in REGIONS.items():
        if lat0 <= lat <= lat1 and lon0 <= lon <= lon1:
            return name
    return OTHER


def by_region(aircraft: pd.DataFrame) -> pd.Series:
    """Airborne aircraft per region, every region listed (zero where none were heard)."""
    names = [*REGIONS, OTHER]
    if aircraft.empty:
        return pd.Series(0, index=names, dtype=int)
    counts = pd.Series([region_of(a, o) for a, o in zip(aircraft.lat, aircraft.lon)])
    return counts.value_counts().reindex(names, fill_value=0).astype(int)


def grid(aircraft: pd.DataFrame, step: float = 3.0) -> pd.DataFrame:
    """Aircraft counted per `step`-degree cell, at the cell's centre — six thousand single
    dots would bury the map and the browser, a few hundred cells show where traffic is."""
    if aircraft.empty:
        return pd.DataFrame(columns=["lat", "lon", "aircraft"])
    cell_lat = (aircraft.lat // step) * step + step / 2
    cell_lon = (aircraft.lon // step) * step + step / 2
    return (pd.DataFrame({"lat": cell_lat, "lon": cell_lon})
            .groupby(["lat", "lon"]).size().rename("aircraft").reset_index())


def cargo_airline(callsign: str) -> str | None:
    """The cargo airline flying under `callsign`, or None for anyone else. A missing
    callsign arrives from pandas as NaN, a float, not as an empty string."""
    if not isinstance(callsign, str):
        return None
    return CARGO_AIRLINES.get(callsign[:3].upper())


def cargo_flights(aircraft: pd.DataFrame) -> pd.DataFrame:
    """The airborne aircraft flying for a cargo airline, with the airline's name."""
    if aircraft.empty:
        return aircraft.assign(airline=pd.Series(dtype=str))
    named = aircraft.assign(airline=aircraft.callsign.map(cargo_airline))
    return named[named.airline.notna()].reset_index(drop=True)


def store_snapshot(con: duckdb.DuckDBPyConnection, taken_at: dt.datetime,
                   aircraft: pd.DataFrame) -> int:
    counts = by_region(aircraft)
    con.execute("DELETE FROM air_traffic WHERE taken_at = ?", [taken_at])
    con.executemany("INSERT INTO air_traffic VALUES (?, ?, ?)",
                    [(taken_at, region, int(n)) for region, n in counts.items()])
    cargo = cargo_flights(aircraft).airline.value_counts()
    con.execute("DELETE FROM air_cargo WHERE taken_at = ?", [taken_at])
    if len(cargo):
        con.executemany("INSERT INTO air_cargo VALUES (?, ?, ?)",
                        [(taken_at, airline, int(n)) for airline, n in cargo.items()])
    return int(counts.sum())


def snapshot(con: duckdb.DuckDBPyConnection, getter=None) -> int:
    """Fetch the current traffic and store its regional counts; returns the total."""
    taken_at, aircraft = fetch(getter=getter)
    return store_snapshot(con, taken_at, aircraft)


def history(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Stored counts, one column per region plus the total, indexed by snapshot time."""
    raw = con.execute("SELECT taken_at, region, airborne FROM air_traffic").fetch_df()
    if raw.empty:
        return pd.DataFrame()
    wide = raw.pivot_table(index="taken_at", columns="region", values="airborne")
    wide = wide.reindex(columns=[c for c in [*REGIONS, OTHER] if c in wide.columns])
    wide["Total"] = wide.sum(axis=1)
    return wide.sort_index()


def cargo_history(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Freighters in the air at each stored snapshot, with their share of all airborne
    aircraft — the share moves less with the hour of the snapshot than the count does."""
    frame = con.execute(
        """SELECT t.taken_at, COALESCE(SUM(c.airborne), 0) AS freighters,
                  ANY_VALUE(t.total) AS airborne
           FROM (SELECT taken_at, SUM(airborne) AS total FROM air_traffic
                 GROUP BY taken_at) t
           LEFT JOIN air_cargo c USING (taken_at)
           GROUP BY t.taken_at ORDER BY t.taken_at""").fetch_df()
    if frame.empty:
        return frame
    frame["share"] = frame.freighters / frame.airborne
    return frame.set_index("taken_at")
