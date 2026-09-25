"""Alerts on the stored daily data: a price crossing a level, a large day, a volume spike,
a new SEC filing.

End-of-day by design, like the rest of the store: a check runs after the prices are
refreshed (the morning job does it; scripts/check_alerts.py and the app's button do it on
demand). A level alert fires when the close CROSSES the level, not on every day spent
beyond it — an alert that repeats daily is one people learn to ignore.

The rules live in the portfolio store, edited in the app. What has fired lives in a small
JSON file the checker owns (data/alerts_state.json): the app holds a write lock on the
portfolio store while it runs, and the morning job must still be able to record that it
already told you.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

import duckdb
import pandas as pd

KINDS = {
    "price_above": "close crosses above",
    "price_below": "close crosses below",
    "move_up": "day's gain at least (%)",
    "move_down": "day's loss at least (%)",
    "volume_spike": "volume at least × its 20-day average",
    "new_filing": "new SEC filing (8-K, 10-Q, 10-K, 4, …)",
}

# -- rules ------------------------------------------------------------------------------

def add(con, ticker: str, kind: str, threshold: float | None, note: str = "") -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind: {kind}")
    if kind != "new_filing" and (threshold is None or threshold <= 0):
        raise ValueError("this alert needs a positive threshold")
    alert_id = uuid.uuid4().hex[:10]
    con.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?)",
                [alert_id, ticker.strip().upper(), kind, threshold, note,
                 dt.datetime.now()])
    return alert_id


def remove(con, alert_id: str) -> None:
    con.execute("DELETE FROM alerts WHERE id = ?", [alert_id])


def rules(con) -> pd.DataFrame:
    return con.execute(
        "SELECT id, ticker, kind, threshold, note, created_at FROM alerts "
        "ORDER BY created_at").fetch_df()


# -- evaluating --------------------------------------------------------------------------

def evaluate(rule: dict, bars: pd.DataFrame) -> str | None:
    """The message if `rule` fires on the latest bar, else None.

    `bars`: date, close (the traded price), volume; oldest first. Price levels compare
    with the traded close — the number a person sets a level against.
    """
    if bars.empty:
        return None
    last = bars.iloc[-1]
    prev = bars.iloc[-2] if len(bars) > 1 else None
    t, kind, x = rule["ticker"], rule["kind"], rule.get("threshold")
    close = float(last["close"])
    if kind == "price_above" and close >= x and (prev is None or float(prev["close"]) < x):
        return f"{t} closed at {close:,.2f}, above {x:,.2f}"
    if kind == "price_below" and close <= x and (prev is None or float(prev["close"]) > x):
        return f"{t} closed at {close:,.2f}, below {x:,.2f}"
    if prev is not None and kind in ("move_up", "move_down"):
        move = close / float(prev["close"]) - 1.0
        if kind == "move_up" and move * 100 >= x:
            return f"{t} rose {move:+.1%} to {close:,.2f}"
        if kind == "move_down" and -move * 100 >= x:
            return f"{t} fell {move:+.1%} to {close:,.2f}"
    if kind == "volume_spike" and len(bars) > 20:
        average = bars["volume"].iloc[-21:-1].mean()
        if average and float(last["volume"]) >= x * average:
            return (f"{t} traded {float(last['volume']) / average:.1f}× its 20-day "
                    f"average volume")
    return None


def _bars(market, ticker: str) -> pd.DataFrame:
    return market.execute(
        "SELECT date, COALESCE(close_raw, close) AS close, volume FROM prices "
        "WHERE upper(ticker) = upper(?) ORDER BY date DESC LIMIT 30", [ticker],
    ).fetch_df().iloc[::-1].reset_index(drop=True)


def _filings_since(market, ticker: str, since: str, getter) -> list[dict]:
    row = market.execute("SELECT cik FROM securities WHERE upper(ticker) = upper(?)",
                         [ticker]).fetchone()
    if not row:
        return []
    doc = json.loads(getter(f"https://data.sec.gov/submissions/CIK{int(row[0]):010d}.json"))
    recent = (doc.get("filings") or {}).get("recent") or {}
    return [{"form": f, "filed": d} for f, d in
            zip(recent.get("form", []), recent.get("filingDate", [])) if d > since]


def run(user_con, market: duckdb.DuckDBPyConnection, state: dict,
        getter=None, today: dt.date | None = None) -> tuple[list[dict], dict]:
    """Check every rule once. Returns the new events and the updated state; an event is
    never reported twice for the same bar (or, for filings, the same filing date)."""
    today = today or dt.date.today()
    state = {"fired": dict(state.get("fired", {})),
             "filings_seen": dict(state.get("filings_seen", {})),
             "log": list(state.get("log", []))}
    events = []
    for rule in rules(user_con).to_dict("records"):
        if rule["kind"] == "new_filing":
            if getter is None:
                continue
            seen = state["filings_seen"].get(rule["id"])
            if seen is None:          # first check: remember where we are, report nothing
                state["filings_seen"][rule["id"]] = today.isoformat()
                continue
            try:
                new = _filings_since(market, rule["ticker"], seen, getter)
            except Exception:                                      # noqa: BLE001
                continue
            if new:
                forms = ", ".join(sorted({n["form"] for n in new}))
                events.append({"id": rule["id"], "ticker": rule["ticker"],
                               "message": f"{rule['ticker']} filed {forms}",
                               "on": max(n["filed"] for n in new)})
                state["filings_seen"][rule["id"]] = max(n["filed"] for n in new)
            continue
        bars = _bars(market, rule["ticker"])
        message = evaluate(rule, bars)
        if not message:
            continue
        on = str(pd.Timestamp(bars.iloc[-1]["date"]).date())
        key = f"{rule['id']}|{on}"
        if key in state["fired"]:
            continue
        state["fired"][key] = dt.datetime.now().isoformat(timespec="seconds")
        events.append({"id": rule["id"], "ticker": rule["ticker"], "message": message,
                       "on": on})
    for e in events:
        state["log"].append({**e, "at": dt.datetime.now().isoformat(timespec="seconds")})
    state["log"] = state["log"][-200:]
    return events, state


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    """Written to a temporary file and moved into place, so a crash mid-write cannot
    leave half a file that forgets everything that fired."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(path)
