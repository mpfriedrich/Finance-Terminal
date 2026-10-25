"""The forward books' store: one EUR ledger per book, positions in USD, daily marks.

Cash is the sum of a book's ledger rows, so the tracker, the app and the morning brief
read one balance. A position is a stake in USD at an entry price; its value is
stake x (1 + direction x (price / entry - 1)) — for a long that is the shares it bought,
for a short the collateral plus the gain or loss on the borrowed shares. Every USD amount
is converted at the EUR/USD rate of that moment: at an open, the previous session's close
(the rate known at the time); at a close, that day's close.

Hypothetical by design: fractional shares, no broker, no confirmed borrow. Each order
records whether whole shares would have fitted and that the borrow is unconfirmed, so
what could really have been executed is shown next to what is simulated.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
import uuid
from pathlib import Path

import duckdb
import pandas as pd

COST = 0.001            # per entry and exit notional (assumption, spec 22.10.2026)
BORROW = 0.03           # per year on the original short notional, per calendar day

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS fw_books (id VARCHAR PRIMARY KEY, code VARCHAR, name VARCHAR,
       description VARCHAR, capital DOUBLE, started_at TIMESTAMP, first_session DATE,
       version VARCHAR, config_json VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS fw_versions (book_id VARCHAR, version VARCHAR,
       started_at TIMESTAMP, config_json VARCHAR, note VARCHAR,
       PRIMARY KEY (book_id, version))""",
    """CREATE TABLE IF NOT EXISTS fw_models (id VARCHAR PRIMARY KEY, book_id VARCHAR,
       trained_at TIMESTAMP, source VARCHAR, window_start VARCHAR, outcomes_before VARCHAR,
       latest_outcome VARCHAR, samples INTEGER, sha256 VARCHAR, next_retrain DATE,
       active BOOLEAN, artifact_json VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS fw_ledger (id VARCHAR PRIMARY KEY, book_id VARCHAR,
       booked_at TIMESTAMP, session DATE, kind VARCHAR, amount DOUBLE, position_id VARCHAR,
       note VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS fw_signals (id VARCHAR PRIMARY KEY, book_id VARCHAR,
       created_at TIMESTAMP, signal_session DATE, exec_session DATE, kind VARCHAR,
       status VARCHAR, payload_json VARCHAR, note VARCHAR, UNIQUE (book_id, signal_session,
       kind))""",
    """CREATE TABLE IF NOT EXISTS fw_orders (id VARCHAR PRIMARY KEY, book_id VARCHAR,
       signal_id VARCHAR, created_at TIMESTAMP, exec_session DATE, ticker VARCHAR,
       side VARCHAR, action VARCHAR, rule VARCHAR, status VARCHAR, note VARCHAR,
       filled_at TIMESTAMP, price_usd DOUBLE, fx DOUBLE, stake_usd DOUBLE, fee_eur DOUBLE,
       whole_shares INTEGER, borrow VARCHAR, position_id VARCHAR, score DOUBLE,
       rank INTEGER)""",
    """CREATE TABLE IF NOT EXISTS fw_positions (id VARCHAR PRIMARY KEY, book_id VARCHAR,
       ticker VARCHAR, side VARCHAR, rule VARCHAR, opened_session DATE, entry_usd DOUBLE,
       entry_fx DOUBLE, stake_usd DOUBLE, stake_eur DOUBLE, entry_fee_eur DOUBLE,
       borrow_eur DOUBLE DEFAULT 0, planned_exit DATE, status VARCHAR,
       exit_session DATE, exit_usd DOUBLE, exit_fx DOUBLE, exit_fee_eur DOUBLE,
       pnl_eur DOUBLE, mark_usd DOUBLE, mark_session DATE, note VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS fw_marks (book_id VARCHAR, session DATE, marked_at TIMESTAMP,
       cash DOUBLE, positions DOUBLE, equity DOUBLE, gross DOUBLE, net DOUBLE,
       fees DOUBLE, borrow DOUBLE, realized DOUBLE, unrealized DOUBLE, fx DOUBLE,
       open_positions INTEGER, stale INTEGER, note VARCHAR, PRIMARY KEY (book_id, session))""",
    """CREATE TABLE IF NOT EXISTS fw_bench (book_id VARCHAR, session DATE, units DOUBLE,
       value_eur DOUBLE, fx DOUBLE, PRIMARY KEY (book_id, session))""",
    """CREATE TABLE IF NOT EXISTS fw_spy (day DATE PRIMARY KEY, open DOUBLE, close DOUBLE,
       close_adj DOUBLE)""",
    """CREATE TABLE IF NOT EXISTS fw_fx (day DATE PRIMARY KEY, eurusd DOUBLE, source VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS fw_macro (series_id VARCHAR, observation_date DATE,
       value DOUBLE, available_from DATE, available_through DATE,
       PRIMARY KEY (series_id, observation_date, available_from))""",
    """CREATE TABLE IF NOT EXISTS fw_runs (run_at TIMESTAMP PRIMARY KEY, summary_json VARCHAR)""",
]


def connect(path: Path, read_only: bool = False, tries: int = 60):
    """Short visits only: the app, the brief and the tracker take turns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and not path.exists():
        c = duckdb.connect(str(path))
        for sql in SCHEMA:
            c.execute(sql)
        c.close()
    for attempt in range(tries):
        try:
            con = duckdb.connect(str(path), read_only=read_only)
            if not read_only:
                for sql in SCHEMA:
                    con.execute(sql)
            return con
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower() or attempt == tries - 1:
                raise
            time.sleep(1.0)
    raise RuntimeError("unreachable")


def uid() -> str:
    return uuid.uuid4().hex[:14]


def book(con, book_id, at, session, kind, amount, position_id=None, note="") -> None:
    """One ledger row. A non-finite amount is refused: cash is the SUM of these rows, so
    a single NaN would make the book's cash and value NaN for good (audit 24.10.2026
    F5). This one gate covers every booking path; the caller's transaction then rolls
    the whole session back and the next run tries again."""
    if amount is None or not math.isfinite(float(amount)):
        raise ValueError(f"{book_id}: {kind} amount is not a finite number ({amount})")
    con.execute("INSERT INTO fw_ledger VALUES (?,?,?,?,?,?,?,?)",
                [uid(), book_id, at, session, kind, float(amount), position_id, note])


def cash(con, book_id: str) -> float:
    return float(con.execute("SELECT COALESCE(SUM(amount),0) FROM fw_ledger WHERE "
                             "book_id = ?", [book_id]).fetchone()[0])


def direction(side: str) -> int:
    return 1 if side == "long" else -1


def value_usd(stake_usd: float, entry: float, mark: float, side: str) -> float:
    return stake_usd * (1 + direction(side) * (mark / entry - 1))


def open_position(con, book_id, at, session, ticker, side, rule, price_usd, fx,
                  stake_usd, planned_exit=None, note="") -> str:
    pid = uid()
    stake_eur = stake_usd / fx
    fee = COST * stake_eur
    con.execute("INSERT INTO fw_positions (id, book_id, ticker, side, rule, opened_session, "
                "entry_usd, entry_fx, stake_usd, stake_eur, entry_fee_eur, borrow_eur, "
                "planned_exit, status, mark_usd, mark_session, note) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,0,?,'open',?,?,?)",
                [pid, book_id, ticker, side, rule, session, price_usd, fx, stake_usd,
                 stake_eur, fee, planned_exit, price_usd, session, note])
    book(con, book_id, at, session, "stake", -stake_eur, pid, f"{side} {ticker}")
    book(con, book_id, at, session, "fee", -fee, pid, f"{COST:.2%} entry")
    return pid


def close_position(con, pos: dict, at, session, price_usd, fx, reason) -> float:
    proceeds_usd = value_usd(pos["stake_usd"], pos["entry_usd"], price_usd, pos["side"])
    notional_eur = pos["stake_usd"] * price_usd / pos["entry_usd"] / fx
    fee = COST * notional_eur
    proceeds_eur = proceeds_usd / fx
    book(con, pos["book_id"], at, session, "proceeds", proceeds_eur, pos["id"],
         f"close {pos['side']} {pos['ticker']}: {reason}")
    book(con, pos["book_id"], at, session, "fee", -fee, pos["id"], f"{COST:.2%} exit")
    pnl = proceeds_eur - fee - pos["stake_eur"] - pos["entry_fee_eur"] - pos["borrow_eur"]
    con.execute("UPDATE fw_positions SET status='closed', exit_session=?, exit_usd=?, "
                "exit_fx=?, exit_fee_eur=?, pnl_eur=?, mark_usd=?, mark_session=?, "
                "note=? WHERE id=?", [session, price_usd, fx, fee, pnl, price_usd, session,
                                      reason, pos["id"]])
    return pnl


def accrue_borrow(con, book_id, at, day_from: dt.date, day_to: dt.date, session) -> float:
    """3 % a year on each open short's original notional, per calendar day."""
    days = (day_to - day_from).days
    if days <= 0:
        return 0.0
    shorts = con.execute("SELECT id, stake_eur FROM fw_positions WHERE book_id=? AND "
                         "status='open' AND side='short'", [book_id]).fetchall()
    total = 0.0
    for pid, stake_eur in shorts:
        amt = BORROW * stake_eur * days / 365
        total += amt
        book(con, book_id, at, session, "borrow", -amt, pid, f"{days} Tag(e) Leihe")
        con.execute("UPDATE fw_positions SET borrow_eur = borrow_eur + ? WHERE id=?",
                    [amt, pid])
    return total


def open_positions(con, book_id: str) -> pd.DataFrame:
    return con.execute("SELECT * FROM fw_positions WHERE book_id=? AND status='open' "
                       "ORDER BY opened_session, ticker", [book_id]).fetch_df()


def mark(con, book_id, at, session, fx, prices: dict, note="") -> dict:
    """Value the book at the close of `session`. `prices` maps position id to its price
    on the entry price's basis (entry x adjusted close / adjusted entry, both read on the
    same day, so a dividend paid while held counts for a long and against a short). A
    share without a close keeps its last mark and is counted as stale — never given an
    invented price."""
    pos = open_positions(con, book_id)
    stale = 0
    value = gross = net = unreal = 0.0
    for p in pos.to_dict("records"):
        px = prices.get(p["id"])
        if px and px > 0:
            con.execute("UPDATE fw_positions SET mark_usd=?, mark_session=? WHERE id=?",
                        [px, session, p["id"]])
        else:
            px = p["mark_usd"]
            stale += 1
        v = value_usd(p["stake_usd"], p["entry_usd"], px, p["side"]) / fx
        exposure = p["stake_usd"] * px / p["entry_usd"] / fx
        value += v
        gross += exposure
        net += direction(p["side"]) * exposure
        unreal += v - p["stake_eur"] - p["entry_fee_eur"] - p["borrow_eur"]
    c = cash(con, book_id)
    led = con.execute("SELECT kind, SUM(amount) FROM fw_ledger WHERE book_id=? GROUP BY 1",
                      [book_id]).fetchall()
    sums = dict(led)
    realized = float(con.execute("SELECT COALESCE(SUM(pnl_eur),0) FROM fw_positions WHERE "
                                 "book_id=? AND status='closed'", [book_id]).fetchone()[0])
    equity = c + value
    row = {"cash": c, "positions": value, "equity": equity,
           "gross": gross / equity if equity > 0 else None,
           "net": net / equity if equity > 0 else None,
           "fees": -sums.get("fee", 0.0), "borrow": -sums.get("borrow", 0.0),
           "realized": realized, "unrealized": unreal, "fx": fx,
           "open_positions": len(pos), "stale": stale, "note": note}
    con.execute("INSERT OR REPLACE INTO fw_marks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [book_id, session, at, row["cash"], row["positions"], row["equity"],
                 row["gross"], row["net"], row["fees"], row["borrow"], row["realized"],
                 row["unrealized"], fx, row["open_positions"], stale, note])
    return row


def signal(con, book_id, at, signal_session, exec_session, kind, status, payload=None,
           note="") -> str | None:
    """One signal record per book, session and kind; a repeated run finds it."""
    row = con.execute("SELECT id FROM fw_signals WHERE book_id=? AND signal_session=? AND "
                      "kind=?", [book_id, signal_session, kind]).fetchone()
    if row:
        return None
    sid = uid()
    con.execute("INSERT INTO fw_signals VALUES (?,?,?,?,?,?,?,?,?)",
                [sid, book_id, at, signal_session, exec_session, kind, status,
                 json.dumps(payload or {}, default=str), note])
    return sid
