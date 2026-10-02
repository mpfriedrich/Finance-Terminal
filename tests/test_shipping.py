"""PortWatch chokepoint transits: parsing, paging, revisions, the readings — no network.

The payloads are hand-made in the layout of PortWatch's ArcGIS query answer (checked
against the live service on 02.10.2026: `features[].attributes`, dates as 'YYYY-MM-DD').
"""
import datetime as dt
import urllib.parse

import duckdb
import pandas as pd
import pytest

from portfolio_lab import shipping
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply


def _row(day, portid="chokepoint6", name="Strait of Hormuz", total=10, tankers=4):
    return {"attributes": {
        "date": day.isoformat(), "year": day.year, "month": day.month, "day": day.day,
        "portid": portid, "portname": name, "n_total": total, "n_tanker": tankers,
        "n_container": 2, "n_dry_bulk": 1, "n_cargo": total - tankers,
        "capacity": total * 50_000, "capacity_tanker": tankers * 100_000, "ObjectId": 1}}


def _store():
    con = duckdb.connect(":memory:")
    _apply(con, MARKET_MIGRATIONS)
    return con


def _frame(rows):
    return shipping.parse({"features": rows})


def test_parse_maps_the_service_fields_to_the_store_layout():
    frame = _frame([_row(dt.date(2026, 9, 27))])
    assert list(frame.columns) == shipping.COLUMNS
    assert frame.chokepoint.iloc[0] == "chokepoint6"
    assert frame.date.iloc[0] == dt.date(2026, 9, 27)
    assert frame.n_tanker.iloc[0] == 4


def test_a_service_error_is_raised_not_read_as_no_ships():
    with pytest.raises(RuntimeError):
        shipping.parse({"error": {"code": 400, "message": "Invalid query"}})


def test_download_pages_until_a_short_page_and_filters_by_date():
    asked = []
    day = dt.date(2026, 1, 1)

    def getter(url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        asked.append((int(q["resultOffset"][0]), q["where"][0]))
        n = shipping.PAGE if len(asked) < 3 else 7
        return {"features": [_row(day)] * n}

    frame = shipping.download(dt.date(2026, 9, 1), getter=getter)
    assert len(frame) == 2 * shipping.PAGE + 7
    assert [a[0] for a in asked] == [0, shipping.PAGE, 2 * shipping.PAGE]
    assert asked[0][1] == "date >= DATE '2026-09-01'"


def test_a_revision_replaces_the_latest_value_but_not_the_first_seen():
    con = _store()
    day = dt.date(2026, 9, 20)
    shipping.store(con, _frame([_row(day, total=30)]), seen_on=dt.date(2026, 9, 25))
    shipping.store(con, _frame([_row(day, total=34)]), seen_on=dt.date(2026, 10, 1))
    assert con.execute("SELECT COUNT(*), MAX(n_total) FROM chokepoint_transits"
                       ).fetchone() == (1, 34)
    assert con.execute("SELECT n_total, seen_on FROM chokepoint_first").fetchone() == (
        30, dt.date(2026, 9, 25))


def test_update_refetches_an_overlap_after_the_first_build():
    con = _store()
    wheres = []

    def getter(url):
        wheres.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["where"][0])
        return {"features": [_row(dt.date(2026, 9, 27))]}

    shipping.update(con, getter=getter)
    shipping.update(con, getter=getter)
    since = dt.date(2026, 9, 27) - dt.timedelta(days=shipping.OVERLAP_DAYS)
    assert wheres == ["1=1", f"date >= DATE '{since.isoformat()}'"]


def _two_years(con, now_total, now_tankers, end=dt.date(2026, 9, 27)):
    """10 ships (4 tankers) a day a year earlier, `now_*` in the last week before `end`."""
    rows = []
    for back in range(380):
        day = end - dt.timedelta(days=back)
        recent = back < 7
        rows.append(_row(day, total=now_total if recent else 10,
                         tankers=now_tankers if recent else 4))
    shipping.store(con, _frame(rows))


def test_the_board_reads_seven_day_averages_against_a_year_earlier():
    con = _store()
    _two_years(con, now_total=12, now_tankers=5)
    row = shipping.dashboard(con).iloc[0]
    assert row.chokepoint == "Strait of Hormuz"
    assert row.as_of == dt.date(2026, 9, 27)
    assert row.ships_per_day == pytest.approx(12.0)
    assert row.vs_year_ago == pytest.approx(0.20)
    assert row.tankers_per_day == pytest.approx(5.0)
    assert row.tankers_vs_year_ago == pytest.approx(0.25)


def test_a_missing_day_is_a_gap_not_zero_ships():
    con = _store()
    end = dt.date(2026, 9, 27)
    rows = [_row(end - dt.timedelta(days=b)) for b in range(10) if b != 3]
    shipping.store(con, _frame(rows))
    assert shipping.dashboard(con).iloc[0].ships_per_day == pytest.approx(10.0)


def test_the_key_chokepoints_come_first():
    con = _store()
    day = dt.date(2026, 9, 27)
    rows = [_row(day - dt.timedelta(days=b), portid=p, name=n, total=t)
            for b in range(7)
            for p, n, t in (("chokepoint9", "Dover Strait", 200),
                            ("chokepoint1", "Suez Canal", 35),
                            ("chokepoint6", "Strait of Hormuz", 90))]
    shipping.store(con, _frame(rows))
    assert shipping.dashboard(con).chokepoint.tolist() == [
        "Strait of Hormuz", "Suez Canal", "Dover Strait"]


def test_history_returns_the_chosen_chokepoints_from_the_start_date():
    con = _store()
    _two_years(con, now_total=12, now_tankers=5)
    h = shipping.history(con, ["Strait of Hormuz", "Nowhere"], "n_tanker",
                         dt.date(2026, 9, 1))
    assert list(h.columns) == ["Strait of Hormuz"]
    assert h.index[0] >= pd.Timestamp("2026-09-01")
    assert h.iloc[-1, 0] == pytest.approx(5.0)


def test_every_chokepoint_portwatch_counts_has_a_place_on_the_map():
    # The 28 names the live service returned on 02.10.2026.
    assert len(shipping.COORDS) == 28
    assert all(-90 <= lat <= 90 and -180 <= lon <= 180
               for lat, lon in shipping.COORDS.values())
    assert set(shipping.KEY) <= set(shipping.COORDS)


def test_the_map_places_known_chokepoints_and_drops_unknown_ones():
    con = _store()
    day = dt.date(2026, 9, 27)
    rows = [_row(day - dt.timedelta(days=b), portid=p, name=n)
            for b in range(7)
            for p, n in (("chokepoint6", "Strait of Hormuz"), ("chokepoint99", "New Strait"))]
    shipping.store(con, _frame(rows))
    placed = shipping.on_map(shipping.dashboard(con))
    assert placed.chokepoint.tolist() == ["Strait of Hormuz"]
    assert (placed.lat.iloc[0], placed.lon.iloc[0]) == shipping.COORDS["Strait of Hormuz"]
    assert shipping.on_map(pd.DataFrame()).empty


def test_the_map_caps_the_colour_and_greys_a_missing_comparison():
    from portfolio_lab import charts

    placed = pd.DataFrame({
        "chokepoint": ["Strait of Hormuz", "Suez Canal", "Bering Strait"],
        "as_of": [dt.date(2026, 9, 27)] * 3,
        "ships_per_day": [3.0, 40.0, 4.0], "vs_year_ago": [-0.96, 0.10, None],
        "tankers_per_day": [1.0, 15.0, 2.0], "tankers_vs_year_ago": [-0.97, -0.11, None],
        "lat": [26.3, 30.6, 66.0], "lon": [56.9, 32.4, -165.6]})
    marker = charts.chokepoint_map(placed).data[0].marker
    assert list(marker.color) == pytest.approx([-charts.MAP_COLOUR_CAP, 0.10, 0.0])
    # Area carries the count: above the base size the diameter grows with the square root,
    # so 40 ships against 4 is sqrt(10) times as wide.
    assert (marker.size[1] - 5) / (marker.size[2] - 5) == pytest.approx(10 ** 0.5)
    tank = charts.chokepoint_map(placed, tankers=True).data[0]
    assert "15 tankers/day" in tank.hovertext[1]
    assert "no year-earlier value" in tank.hovertext[2]


# -- port cargo ---------------------------------------------------------------------------

def _port_row(day, port="port955", export=1_500_000.0, imp=7_000.0,
              export_dry_bulk=None, export_tanker=0.0, import_container=0.0):
    return {"attributes": {
        "date": day.isoformat(), "portid": port, "portname": "Port Hedland",
        "portcalls": 9, "import": imp, "export": export,
        "import_tanker": 0.0, "import_container": import_container,
        "import_dry_bulk": imp - import_container,
        "export_tanker": export_tanker, "export_container": 0.0,
        "export_dry_bulk": export if export_dry_bulk is None else export_dry_bulk,
        "ObjectId": 1}}


def test_port_rows_keep_the_flows_by_ship_type():
    frame = shipping.parse_ports({"features": [_port_row(dt.date(2026, 9, 25))]})
    assert list(frame.columns) == shipping.PORT_COLUMNS
    assert frame.port.iloc[0] == "port955"
    assert frame.export_dry_bulk.iloc[0] == 1_500_000.0


def test_port_download_asks_only_for_the_watched_ports():
    asked = []

    def getter(url):
        asked.append(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["where"][0])
        return {"features": [_port_row(dt.date(2026, 9, 25))]}

    shipping.download_ports(dt.date(2026, 9, 1), getter=getter)
    assert asked[0].startswith("portid IN ('port1091', ")
    assert "'port955'" in asked[0] and "'port2164'" not in asked[0]      # not Kharg
    assert asked[0].endswith("AND date >= DATE '2026-09-01'")


def test_the_port_board_reads_tonnes_against_a_year_earlier_and_the_main_cargo():
    con = _store()
    end = dt.date(2026, 9, 25)
    rows = []
    for back in range(380):
        recent = back < 7
        rows.append(_port_row(end - dt.timedelta(days=back),
                              export=1_200_000.0 if recent else 1_000_000.0,
                              imp=5_000.0 if recent else 10_000.0))
    shipping.store_ports(con, shipping.parse_ports({"features": rows}))
    row = shipping.port_board(con).set_index("id").loc["port955"]
    assert row.port == "Port Hedland" and row.as_of == end
    assert row.export_t == pytest.approx(1_200_000.0)
    assert row.export_vs_year_ago == pytest.approx(0.20)
    assert row.import_vs_year_ago == pytest.approx(-0.50)
    assert row.total_vs_year_ago == pytest.approx(1_205_000 / 1_010_000 - 1)
    assert row.mostly == "dry bulk out"


def test_main_cargo_names_the_largest_flow():
    con = _store()
    day = dt.date(2026, 9, 25)
    rows = [_port_row(day, port="port1188", export=300_000.0, imp=900_000.0,
                      export_dry_bulk=0.0, import_container=800_000.0),
            _port_row(day, port="port1091", export=2_000_000.0, imp=0.0,
                      export_dry_bulk=0.0, export_tanker=2_000_000.0)]
    shipping.store_ports(con, shipping.parse_ports({"features": rows}))
    assert shipping.main_cargo(con) == {"port1188": "container in",
                                        "port1091": "tanker out"}


def test_a_port_revision_replaces_the_day():
    con = _store()
    day = dt.date(2026, 9, 25)
    for export in (1.0, 2.0):
        shipping.store_ports(con, shipping.parse_ports(
            {"features": [_port_row(day, export=export)]}))
    assert con.execute("SELECT COUNT(*), MAX(export) FROM port_trade").fetchone() == (1, 2.0)


def test_every_watched_port_has_a_place_and_a_reason():
    assert len(shipping.PORTS) == 26
    for name, iso3, lat, lon, known in shipping.PORTS.values():
        assert len(iso3) == 3 and known
        assert -90 <= lat <= 90 and -180 <= lon <= 180


def test_a_nearly_empty_port_is_not_given_a_main_cargo():
    con = _store()
    day = dt.date(2026, 9, 25)
    rows = [_port_row(day - dt.timedelta(days=b), port="port570", export=0.0, imp=300.0)
            for b in range(30)]
    shipping.store_ports(con, shipping.parse_ports({"features": rows}))
    assert shipping.main_cargo(con) == {"port570": "too little to tell"}
