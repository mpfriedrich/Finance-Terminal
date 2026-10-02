"""Ship traffic through the maritime chokepoints, from IMF PortWatch. No key needed.

PortWatch (IMF with the University of Oxford) counts the ships passing 28 chokepoints
each day — Hormuz, Suez, Bab el-Mandeb, Panama, Malacca, ... — split by ship type, with
their deadweight tonnage. The counts come from satellite and coastal AIS combined, so the
open-ocean gap of free terrestrial AIS does not apply. Checked 02.10.2026: daily since
2019-01-01, 79,156 rows, newest day 2026-09-27 (about five days behind).

Honest about what the numbers miss:
- Ships that switch their transponder off are not counted. Tankers carrying sanctioned
  Russian, Iranian or Venezuelan oil do this, so tanker counts through Hormuz and the
  Black Sea understate the real flow — systematically, not at random.
- Daily counts are noisy (a dozen ships either way is normal), so everything is read as a
  seven-day average, the way PortWatch shows it itself.
- PortWatch revises recent days. `chokepoint_transits` holds the latest value;
  `chokepoint_first` keeps the first value seen and the day it was seen (R13). Rows from
  the first download all carry that download's date, so the point-in-time record only
  starts then. Nothing here feeds a backtest.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse

import duckdb
import pandas as pd

URL = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
       "Daily_Chokepoints_Data/FeatureServer/0/query")
PAGE = 1000                     # the service's maxRecordCount
# Recent days are re-fetched on every update so revisions are picked up, as with prices.
OVERLAP_DAYS = 30
FIELDS = ["portid", "portname", "date", "n_total", "n_tanker", "n_container",
          "n_dry_bulk", "n_cargo", "capacity", "capacity_tanker"]
COLUMNS = ["chokepoint", "name", "date", "n_total", "n_tanker", "n_container",
           "n_dry_bulk", "n_cargo", "capacity", "capacity_tanker"]
# Shown first: the ones oil, container rates and grain prices react to.
KEY = ["Strait of Hormuz", "Suez Canal", "Bab el-Mandeb Strait", "Cape of Good Hope",
       "Panama Canal", "Malacca Strait", "Bosporus Strait", "Taiwan Strait"]
# Where PortWatch counts each chokepoint (lat, lon), from its PortWatch_chokepoints_database
# service on 02.10.2026. Stored here rather than fetched, so the map works offline; a
# chokepoint PortWatch adds later is left off the map until it is added here.
COORDS: dict[str, tuple[float, float]] = {
    "Suez Canal": (30.593, 32.437), "Panama Canal": (9.121, -79.767),
    "Bosporus Strait": (41.169, 29.092), "Bab el-Mandeb Strait": (12.789, 43.350),
    "Malacca Strait": (1.517, 102.665), "Strait of Hormuz": (26.297, 56.860),
    "Cape of Good Hope": (-34.927, 20.883), "Gibraltar Strait": (35.942, -5.755),
    "Dover Strait": (51.030, 1.506), "Oresund Strait": (55.508, 12.851),
    "Taiwan Strait": (24.724, 119.831), "Korea Strait": (34.131, 129.209),
    "Tsugaru Strait": (41.328, 140.353), "Luzon Strait": (20.489, 121.352),
    "Lombok Strait": (-8.419, 115.801), "Ombai Strait": (-8.399, 125.091),
    "Bohai Strait": (38.373, 120.900), "Torres Strait": (-9.863, 142.248),
    "Sunda Strait": (-5.967, 105.775), "Makassar Strait": (0.352, 119.257),
    "Magellan Strait": (-52.640, -69.595), "Yucatan Channel": (21.815, -85.647),
    "Windward Passage": (19.986, -73.698), "Mona Passage": (18.449, -67.711),
    "Balabac Strait": (7.414, 117.115), "Bering Strait": (65.966, -165.550),
    "Mindoro Strait": (12.468, 120.403), "Kerch Strait": (45.267, 36.544),
}


def parse(payload: dict) -> pd.DataFrame:
    """One page of the ArcGIS query answer -> rows in the store's layout."""
    if "error" in payload:
        raise RuntimeError(f"PortWatch: {payload['error'].get('message', 'query failed')}")
    rows = [f["attributes"] for f in payload.get("features", [])]
    if not rows:
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.DataFrame(rows)[FIELDS]
    frame.columns = COLUMNS
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    return frame


def download(since: dt.date | None = None, getter=None, timeout: int = 60) -> pd.DataFrame:
    """Every row from `since` (all of them without), page by page."""
    if getter is None:
        import requests

        def getter(url):
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
    where = f"date >= DATE '{since.isoformat()}'" if since else "1=1"
    pages, offset = [], 0
    while True:
        query = urllib.parse.urlencode({
            "f": "json", "where": where, "outFields": ",".join(FIELDS),
            "orderByFields": "ObjectId", "resultOffset": offset, "resultRecordCount": PAGE,
        })
        page = parse(getter(f"{URL}?{query}"))
        pages.append(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return pd.concat(pages, ignore_index=True) if pages else pd.DataFrame(columns=COLUMNS)


def last_date(con: duckdb.DuckDBPyConnection) -> dt.date | None:
    return con.execute("SELECT MAX(date) FROM chokepoint_transits").fetchone()[0]


def store(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame,
          seen_on: dt.date | None = None) -> int:
    """Replace the latest values for these days; keep the first value ever seen."""
    if frame.empty:
        return 0
    seen_on = seen_on or dt.date.today()
    con.register("_c", frame)
    con.execute("""DELETE FROM chokepoint_transits t USING _c
                   WHERE t.chokepoint = _c.chokepoint AND t.date = _c.date""")
    con.execute("INSERT INTO chokepoint_transits BY NAME SELECT * FROM _c")
    con.execute("""INSERT OR IGNORE INTO chokepoint_first
                   SELECT chokepoint, date, n_total, n_tanker, ? FROM _c""", [seen_on])
    con.unregister("_c")
    return len(frame)


def update(con: duckdb.DuckDBPyConnection, getter=None) -> int:
    """The whole history the first time, then the days since the last one plus overlap."""
    last = last_date(con)
    since = last - dt.timedelta(days=OVERLAP_DAYS) if last else None
    return store(con, download(since, getter=getter))


def _weekly(con: duckdb.DuckDBPyConnection, field: str) -> pd.DataFrame:
    """Seven-day average of `field`, one column per chokepoint, indexed by date."""
    if field not in COLUMNS[3:]:
        raise ValueError(field)
    raw = con.execute(
        f"SELECT name, date, {field} AS v FROM chokepoint_transits ORDER BY date"
    ).fetch_df()
    if raw.empty:
        return pd.DataFrame()
    wide = raw.pivot_table(index="date", columns="name", values="v")
    wide.index = pd.to_datetime(wide.index)
    # A missing day is a gap in the data, not zero ships; the average skips it.
    wide = wide.asfreq("D")
    return wide.rolling(7, min_periods=5).mean()


def dashboard(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per chokepoint: the latest seven-day average of all ships and of tankers, and the
    change against the same seven days a year earlier (seasonality cancels out)."""
    total, tankers = _weekly(con, "n_total"), _weekly(con, "n_tanker")
    if total.empty:
        return pd.DataFrame()
    rows = []
    for name in total.columns:
        t, k = total[name].dropna(), tankers[name].dropna()
        if t.empty:
            continue
        end = t.index[-1]
        year_ago = end - pd.Timedelta(days=364)       # same weekday a year earlier
        prev_t, prev_k = t.get(year_ago), k.get(year_ago)
        rows.append({
            "chokepoint": name, "as_of": end.date(),
            "ships_per_day": float(t.iloc[-1]),
            "vs_year_ago": (float(t.iloc[-1]) / prev_t - 1.0) if prev_t else None,
            "tankers_per_day": float(k.iloc[-1]) if len(k) else None,
            "tankers_vs_year_ago": (float(k.iloc[-1]) / prev_k - 1.0)
            if prev_k and len(k) else None,
        })
    board = pd.DataFrame(rows)
    order = {n: i for i, n in enumerate(KEY)}
    board["_o"] = board.chokepoint.map(lambda n: order.get(n, len(KEY)))
    return (board.sort_values(["_o", "ships_per_day"], ascending=[True, False])
            .drop(columns="_o").reset_index(drop=True))


def on_map(board: pd.DataFrame) -> pd.DataFrame:
    """The board with each chokepoint's position; rows without a known position drop."""
    if board.empty:
        return board.assign(lat=pd.Series(dtype=float), lon=pd.Series(dtype=float))
    placed = board[board.chokepoint.isin(COORDS)].copy()
    placed["lat"] = placed.chokepoint.map(lambda n: COORDS[n][0])
    placed["lon"] = placed.chokepoint.map(lambda n: COORDS[n][1])
    return placed.reset_index(drop=True)


def history(con: duckdb.DuckDBPyConnection, names: list[str], field: str,
            since: dt.date) -> pd.DataFrame:
    """Seven-day averages of `field` for the chosen chokepoints, from `since`."""
    wide = _weekly(con, field)
    if wide.empty:
        return wide
    keep = [n for n in names if n in wide.columns]
    return wide.loc[wide.index >= pd.Timestamp(since), keep].dropna(how="all")
