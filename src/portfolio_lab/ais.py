"""Single ships, live, from AISStream (aisstream.io). Free, needs AISSTREAM_API_KEY.

AISStream relays AIS messages from coastal receivers over a WebSocket: you send a key and
one or more bounding boxes, it streams every message from ships inside them. There is no
history and no snapshot, so the app *listens* to one region for a minute or so and shows
the ships it heard.

What a listen returns, and why it is never complete:
- Positions come every few seconds to minutes, so a minute catches most moving ships.
- Name, type, destination and draught come in a separate "static" message only every
  six minutes. After a minute most ships have a position and a name (AISStream fills the
  name from its own cache) but only some have a destination or draught. Static data heard
  in earlier listens is kept for the rest of the app session.
- Coastal receivers only: tens of kilometres out at sea there is nothing. A ship with its
  transponder off is not there at all — the tankers around Hormuz and Yanbu in 2026.
- Destination and draught are typed in by the crew and are often stale or free text.

The key is a secret (R24): it goes only into the subscription message, never into a URL,
a log line or an error. A failed connection is reported without its details.

Limits (AISStream documentation, 02.10.2026): 3 connections per account and per IP, the
subscription within 3 seconds of connecting, at most one subscription update a second.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

import pandas as pd

URL = "wss://stream.aisstream.io/v0/stream"
KEY_NAME = "AISSTREAM_API_KEY"
MESSAGE_TYPES = ["PositionReport", "ShipStaticData"]

# Regions to listen to: ((lat south, lon west), (lat north, lon east)). Small on purpose —
# a minute of a busy box is thousands of messages, and the map has to stay readable.
REGIONS: dict[str, tuple[tuple[float, float], tuple[float, float]]] = {
    "Strait of Hormuz": ((25.2, 55.4), (27.2, 57.6)),
    "Bab el-Mandeb": ((11.8, 42.6), (13.8, 44.2)),
    "Suez Canal (south)": ((29.4, 32.2), (30.2, 32.8)),
    "Yanbu (Red Sea)": ((23.4, 37.6), (24.6, 38.6)),
    "Singapore Strait": ((0.9, 103.4), (1.6, 104.6)),
    "Bosporus": ((40.9, 28.8), (41.4, 29.3)),
    "Rotterdam approaches": ((51.8, 3.4), (52.2, 4.3)),
    "Dover Strait": ((50.6, 0.9), (51.4, 2.0)),
    "Houston Ship Channel": ((29.2, -95.2), (29.8, -94.6)),
    "Panama Canal": ((8.8, -80.0), (9.5, -79.4)),
    "Taiwan Strait": ((23.5, 118.8), (25.5, 120.8)),
    "Port Hedland": ((-20.5, 118.2), (-19.8, 118.9)),
}

# AIS ship type codes (ITU-R M.1371): the tens digit is the class.
_TYPE_CLASSES = {
    3: "fishing/tug/special", 4: "high-speed craft", 5: "pilot/tug/service",
    6: "passenger", 7: "cargo", 8: "tanker", 9: "other",
}
_TYPE_SPECIAL = {30: "fishing", 31: "tug", 32: "tug", 35: "military", 36: "sailing",
                 37: "pleasure craft", 52: "tug", 50: "pilot"}
# AIS navigational status codes, the ones that say something.
NAV_STATUS = {0: "under way", 1: "at anchor", 2: "not under command", 3: "restricted",
              5: "moored", 6: "aground", 7: "fishing", 8: "sailing"}


def ship_type(code) -> str:
    """The AIS type code as a word; 0 or missing is 'unknown'."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "unknown"
    if code in _TYPE_SPECIAL:
        return _TYPE_SPECIAL[code]
    return _TYPE_CLASSES.get(code // 10, "unknown")


def subscription(key: str, region: str) -> str:
    (lat0, lon0), (lat1, lon1) = REGIONS[region]
    return json.dumps({
        "APIKey": key,
        "BoundingBoxes": [[[lat0, lon0], [lat1, lon1]]],
        "FilterMessageTypes": MESSAGE_TYPES,
    })


def _text(value) -> str | None:
    """AIS pads text fields with spaces and '@'; an empty field is None."""
    if not isinstance(value, str):
        return None
    cleaned = value.replace("@", " ").strip()
    return cleaned or None


def read(messages: list[dict], static: dict[int, dict] | None = None
         ) -> tuple[pd.DataFrame, dict[int, dict]]:
    """Messages as AISStream sends them -> one row per ship (its latest position), plus the
    static data per MMSI, merged into `static` from earlier listens."""
    static = dict(static or {})
    latest: dict[int, dict] = {}
    for m in messages:
        meta = m.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if mmsi is None:
            continue
        kind = m.get("MessageType")
        body = (m.get("Message") or {}).get(kind) or {}
        if kind == "ShipStaticData":
            dims = body.get("Dimension") or {}
            length = (dims.get("A") or 0) + (dims.get("B") or 0)
            static[mmsi] = {
                "imo": body.get("ImoNumber") or None,
                "callsign": _text(body.get("CallSign")),
                "type": ship_type(body.get("Type")),
                "destination": _text(body.get("Destination")),
                "draught_m": body.get("MaximumStaticDraught") or None,
                "length_m": length or None,
                "static_name": _text(body.get("Name")),
            }
        elif kind == "PositionReport":
            lat, lon = meta.get("Latitude"), meta.get("Longitude")
            if lat is None or lon is None:
                continue
            latest[mmsi] = {
                "mmsi": mmsi, "name": _text(meta.get("ShipName")),
                "lat": lat, "lon": lon,
                "speed_kn": body.get("Sog"), "course": body.get("Cog"),
                "status": NAV_STATUS.get(body.get("NavigationalStatus"), ""),
                "seen": meta.get("time_utc"),
            }
    rows = []
    for mmsi, pos in latest.items():
        info = static.get(mmsi, {})
        rows.append({**pos,
                     "name": pos["name"] or info.get("static_name"),
                     "type": info.get("type", "unknown"),
                     "destination": info.get("destination"),
                     "draught_m": info.get("draught_m"),
                     "length_m": info.get("length_m"),
                     "imo": info.get("imo")})
    columns = ["mmsi", "name", "type", "speed_kn", "course", "status", "destination",
               "draught_m", "length_m", "imo", "lat", "lon", "seen"]
    return pd.DataFrame(rows, columns=columns), static


class ListenFailed(RuntimeError):
    """The stream could not be read. Carries no URL, key or server text (R24)."""


async def _collect(key: str, region: str, seconds: float, connect) -> list[dict]:
    messages: list[dict] = []
    deadline = time.monotonic() + seconds
    async with connect(URL) as socket:
        await socket.send(subscription(key, region))
        while (left := deadline - time.monotonic()) > 0:
            try:
                frame = await asyncio.wait_for(socket.recv(), timeout=left)
            except TimeoutError:
                break
            data = json.loads(frame.decode() if isinstance(frame, bytes) else frame)
            if "error" in data:
                raise ListenFailed("the stream refused the subscription")
            messages.append(data)
    return messages


def listen(key: str, region: str, seconds: float = 60, connect=None) -> list[dict]:
    """Every message heard from `region` for `seconds`. `connect` is injectable for tests;
    the default is websockets with permessage-deflate (the service's own advice)."""
    if region not in REGIONS:
        raise ValueError(region)
    if connect is None:
        import websockets

        def connect(url):
            return websockets.connect(url, compression="deflate", max_size=2**22)
    try:
        return asyncio.run(_collect(key, region, seconds, connect))
    except ListenFailed:
        raise
    except Exception:                                                # noqa: BLE001
        # Deliberately no `from`: the original exception can carry the subscription.
        raise ListenFailed("could not read the AIS stream") from None


def summary(ships: pd.DataFrame) -> pd.DataFrame:
    """Ships heard, by type: how many, how many under way, their mean speed."""
    if ships.empty:
        return pd.DataFrame(columns=["type", "ships", "under way", "mean speed kn"])
    moving = ships.speed_kn.fillna(0) > 0.5
    grouped = ships.assign(moving=moving).groupby("type")
    out = pd.DataFrame({
        "ships": grouped.size(),
        "under way": grouped.moving.sum().astype(int),
        "mean speed kn": grouped.speed_kn.mean().round(1),
    }).sort_values("ships", ascending=False)
    return out.reset_index()


def listened_at() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)
