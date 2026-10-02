"""AISStream live ships: reading messages, the subscription, the listen loop — no network.

Messages are hand-made in AISStream's layout (documentation, 02.10.2026): MessageType,
MetaData {MMSI, ShipName, Latitude, Longitude, time_utc}, Message {<type>: {...}}.
"""
import json

import pandas as pd
import pytest

from portfolio_lab import ais

KEY = "test-key-not-real-0123456789"


def _pos(mmsi, lat, lon, name="ATLANTIC STAR@@@", sog=12.3, status=0):
    return {"MessageType": "PositionReport",
            "MetaData": {"MMSI": mmsi, "ShipName": name, "Latitude": lat,
                         "Longitude": lon, "time_utc": "2026-10-02 06:00:00"},
            "Message": {"PositionReport": {"Sog": sog, "Cog": 90.0,
                                           "NavigationalStatus": status}}}


def _static(mmsi, type_code=80, destination="ROTTERDAM   ", draught=14.2, imo=9321483):
    return {"MessageType": "ShipStaticData",
            "MetaData": {"MMSI": mmsi, "ShipName": "ATLANTIC STAR"},
            "Message": {"ShipStaticData": {
                "Type": type_code, "Destination": destination,
                "MaximumStaticDraught": draught, "ImoNumber": imo, "CallSign": "ABCD@",
                "Name": "ATLANTIC STAR", "Dimension": {"A": 200, "B": 50, "C": 20,
                                                       "D": 24}}}}


def test_ship_type_codes_read_as_words():
    assert ais.ship_type(80) == "tanker" and ais.ship_type(84) == "tanker"
    assert ais.ship_type(70) == "cargo" and ais.ship_type(60) == "passenger"
    assert ais.ship_type(30) == "fishing" and ais.ship_type(35) == "military"
    assert ais.ship_type(0) == "unknown" and ais.ship_type(None) == "unknown"


def test_the_latest_position_wins_and_static_data_is_joined():
    ships, static = ais.read([
        _pos(211000001, 51.9, 4.0, sog=10.0),
        _static(211000001),
        _pos(211000001, 51.95, 4.1, sog=11.0, status=1),
        _pos(244000002, 52.0, 3.9, name=None, sog=0.0, status=5),
    ])
    assert len(ships) == 2
    a = ships.set_index("mmsi").loc[211000001]
    assert (a.lat, a.lon, a.speed_kn, a.status) == (51.95, 4.1, 11.0, "at anchor")
    assert a["name"] == "ATLANTIC STAR"                      # '@' padding stripped
    assert (a.type, a.destination, a.draught_m, a.length_m, a.imo) == (
        "tanker", "ROTTERDAM", 14.2, 250, 9321483)
    b = ships.set_index("mmsi").loc[244000002]
    assert b.type == "unknown" and pd.isna(b.destination) and b.status == "moored"
    assert set(static) == {211000001}


def test_static_data_from_an_earlier_listen_fills_a_later_one():
    _, static = ais.read([_static(211000001, type_code=70, destination="SINGAPORE")])
    ships, static2 = ais.read([_pos(211000001, 1.2, 103.8)], static)
    assert ships.type.iloc[0] == "cargo" and ships.destination.iloc[0] == "SINGAPORE"
    assert static2 == static


def test_static_without_a_position_is_kept_but_not_drawn():
    ships, static = ais.read([_static(211000009)])
    assert ships.empty and 211000009 in static


def test_the_subscription_carries_the_region_box_and_both_message_types():
    sub = json.loads(ais.subscription(KEY, "Strait of Hormuz"))
    assert sub["APIKey"] == KEY
    assert sub["BoundingBoxes"] == [[[25.2, 55.4], [27.2, 57.6]]]
    assert sub["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]


class _FakeSocket:
    """Stands in for a websockets connection: records what is sent, replays frames."""

    def __init__(self, frames, fail=False):
        self.frames, self.sent, self.fail = list(frames), [], fail

    async def __aenter__(self):
        if self.fail:
            raise OSError(f"handshake failed for {ais.URL}?key={KEY}")
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text):
        self.sent.append(text)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        import asyncio
        await asyncio.sleep(3600)


def test_listen_subscribes_once_and_returns_every_frame_until_the_time_is_up():
    sock = _FakeSocket([json.dumps(_pos(1, 26.5, 56.5)).encode(),
                        json.dumps(_static(1)).encode()])
    got = ais.listen(KEY, "Strait of Hormuz", seconds=0.2, connect=lambda url: sock)
    assert [m["MessageType"] for m in got] == ["PositionReport", "ShipStaticData"]
    assert len(sock.sent) == 1 and json.loads(sock.sent[0])["APIKey"] == KEY


def test_a_failed_connection_never_shows_the_key_or_the_address():
    sock = _FakeSocket([], fail=True)
    with pytest.raises(ais.ListenFailed) as caught:
        ais.listen(KEY, "Strait of Hormuz", seconds=0.2, connect=lambda url: sock)
    text = str(caught.value) + repr(caught.value)
    assert KEY not in text and "aisstream" not in text
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_a_refused_subscription_is_reported_as_such():
    sock = _FakeSocket([json.dumps({"error": "Api Key Is Not Valid"}).encode()])
    with pytest.raises(ais.ListenFailed, match="refused"):
        ais.listen(KEY, "Strait of Hormuz", seconds=0.5, connect=lambda url: sock)


def test_an_unknown_region_is_refused_before_connecting():
    with pytest.raises(ValueError):
        ais.listen(KEY, "Atlantis", seconds=1, connect=lambda url: None)


def test_summary_counts_ships_and_moving_ones_by_type():
    ships, _ = ais.read([_static(1, 80), _pos(1, 0, 0, sog=10.0),
                         _static(2, 80), _pos(2, 0, 0, sog=0.0),
                         _static(3, 70), _pos(3, 0, 0, sog=14.0)])
    s = ais.summary(ships).set_index("type")
    assert s.at["tanker", "ships"] == 2 and s.at["tanker", "under way"] == 1
    assert s.at["cargo", "mean speed kn"] == 14.0


def test_every_region_box_runs_south_west_to_north_east():
    for (lat0, lon0), (lat1, lon1) in ais.REGIONS.values():
        assert -90 <= lat0 < lat1 <= 90 and -180 <= lon0 < lon1 <= 180


def test_the_live_map_draws_one_trace_per_ship_class():
    from portfolio_lab import charts

    ships, _ = ais.read([_static(1, 80), _pos(1, 26.5, 56.5),
                         _static(2, 70), _pos(2, 26.6, 56.6), _pos(3, 26.7, 56.7)])
    fig = charts.live_ship_map(ships, ais.REGIONS["Strait of Hormuz"])
    assert sorted(t.name for t in fig.data) == ["cargo", "other", "tanker"]
    assert "MMSI 3" in fig.data[[t.name for t in fig.data].index("other")].hovertext[0]
