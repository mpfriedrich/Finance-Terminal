"""The daily pass of the six forward books (run after the US close, e.g. by the morning
job): refresh SPY/FX/macro, book what happened at each session's open and close, then
create the orders for the next open. Paper only — nothing here can reach a broker.

Order of events on a session S, the same as the research simulation:
  1. at S's open: planned exits, then entries (A-D: close everything, open the new
     baskets; F: exits first, entries from the money that is actually free; E: monthly);
  2. borrow accrues for every calendar day since the last mark;
  3. at S's close: every position is marked; SPY with the same 1,000 EUR is marked.
After the last session with complete data, new signals are created — but only while its
next open still lies in the future. A late run never creates an order that would have
been filled in the past; that signal is logged as missed.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .. import nyse as cal
from .. import ui
from ..data import history
from . import data as dd
from . import features as fx
from . import ledger as lg
from . import models as md
from . import strategies as stg

CAPITAL = 1_000.0
FAR = "9999-12-31"


def open_time(day: dt.date) -> dt.datetime:
    """09:30 New York on `day`, as naive UTC."""
    t = dt.datetime(day.year, day.month, day.day, 9, 30, tzinfo=cal.ET)
    return t.astimezone(dt.UTC).replace(tzinfo=None)


def close_time(day: dt.date) -> dt.datetime:
    """The end of `day`'s regular session (13:00 on an early close), as naive UTC."""
    t = dt.datetime(day.year, day.month, day.day, cal.close_hour(day), 0, tzinfo=cal.ET)
    return t.astimezone(dt.UTC).replace(tzinfo=None)


def first_session_after(moment: dt.datetime) -> dt.date:
    d = moment.replace(tzinfo=dt.UTC).astimezone(cal.ET).date()
    if not cal.is_session(d) or open_time(d) <= moment:
        d = cal.next_session(d)
    return d


# -- live inputs (network); tests pass a fake with the same methods ----------------------

class Live:
    def spy(self, start: dt.date) -> pd.DataFrame:
        return dd.yahoo_spy(start)

    def fx(self, start: dt.date) -> pd.Series:
        import yfinance as yf

        h = yf.Ticker("EURUSD=X").history(start=str(start), auto_adjust=False)
        if h.empty:
            return pd.Series(dtype=float)
        return pd.Series(h["Close"].to_numpy(),
                         index=pd.to_datetime(h.index).tz_localize(None).normalize().date)

    def fred(self) -> pd.DataFrame:
        return dd.fred_snapshot()

    def pit_frame(self, day: dt.date) -> pd.DataFrame:
        from ..config import CACHE_DIR, Settings
        from ..data.sources import open_selected
        from ..pit_cache import FrameCache

        src, _ = open_selected(Settings(data_source="free"))
        cache = FrameCache(src, CACHE_DIR / "pit", src.snapshot().fingerprint())
        return cache.frame_for(day)


# -- setup ------------------------------------------------------------------------------

def ensure_books(con, now: dt.datetime) -> None:
    for b in stg.BOOKS:
        config = {k: v for k, v in b.items() if k not in ("name", "description")}
        have = con.execute("SELECT version FROM fw_books WHERE id=?", [b["id"]]).fetchone()
        if have:
            if have[0] != b["version"]:
                # A new rule version starts its own observation period from here.
                con.execute("UPDATE fw_books SET version=?, config_json=? WHERE id=?",
                            [b["version"], json.dumps(config), b["id"]])
                con.execute("INSERT INTO fw_versions VALUES (?,?,?,?,?)",
                            [b["id"], b["version"], now, json.dumps(config),
                             stg.VERSION_NOTES.get(b["version"], stg.VERSION_NOTES.get(
                                b["version"][1:], "neue Version"))])
            continue
        first = first_session_after(now)
        # One transaction: a book without its deposit would start with no cash.
        con.execute("BEGIN")
        try:
            con.execute("INSERT INTO fw_books VALUES (?,?,?,?,?,?,?,?,?)",
                        [b["id"], b["code"], b["name"], b["description"], CAPITAL, now,
                         first, b["version"], json.dumps(config)])
            con.execute("INSERT INTO fw_versions VALUES (?,?,?,?,?)",
                        [b["id"], b["version"], now, json.dumps(config), "Start"])
            lg.book(con, b["id"], now, None, "deposit", CAPITAL, None,
                    "Startkapital 1.000 EUR")
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


def refresh_inputs(con, market, live, today: dt.date, notes: list, now=None) -> None:
    """SPY and EUR/USD from Yahoo, today's FRED vintage. A failure is a note, not a stop:
    the books then wait for the data instead of using made-up values."""
    try:
        spy = live.spy(dt.date(2009, 1, 1))
        for d, r in spy.iterrows():
            con.execute("INSERT OR REPLACE INTO fw_spy VALUES (?,?,?,?)",
                        [pd.Timestamp(d).date(), float(r["open"]), float(r["close"]),
                         float(r["close_adj"])])
    except Exception as exc:                                       # noqa: BLE001
        notes.append(f"SPY nicht geladen: {type(exc).__name__}")
    store = market.execute("SELECT date, close FROM prices WHERE ticker='EURUSD=X' AND "
                           "date >= '2026-01-01'").fetchall()
    try:
        for d, v in live.fx(today - dt.timedelta(days=30)).items():
            if v and not math.isnan(v):
                lg.store_fx(con, pd.Timestamp(d).date(), float(v), "yahoo", now)
    except Exception as exc:                                       # noqa: BLE001
        notes.append(f"EUR/USD live nicht geladen: {type(exc).__name__}")
    for d, v in store:             # only where Yahoo gave nothing (lg.store_fx)
        if v and not math.isnan(v):
            lg.store_fx(con, pd.Timestamp(d).date(), float(v), "store", None)
    try:
        snap = live.fred()
        alfred = _alfred()
        if alfred is None:
            alfred = pd.DataFrame(columns=VINTAGE_COLUMNS)
        for r in snap.itertuples():
            obs = pd.Timestamp(r.observation_date).date()
            cur = con.execute("SELECT value FROM fw_macro WHERE series_id=? AND "
                              "observation_date=? AND available_through=?",
                              [r.series_id, obs, FAR]).fetchone()
            if cur is not None and abs(cur[0] - r.value) < 1e-12:
                continue
            if cur is None:
                known = alfred[(alfred.series_id == r.series_id)
                               & (alfred.observation_date.astype(str) == str(obs))
                               & (alfred.available_through.astype(str) >= str(today))]
                if len(known) and abs(float(known.value.iloc[-1]) - r.value) < 1e-12:
                    continue
            con.execute("UPDATE fw_macro SET available_through=? WHERE series_id=? AND "
                        "observation_date=? AND available_through=?",
                        [today - dt.timedelta(days=1), r.series_id, obs, FAR])
            con.execute("INSERT OR REPLACE INTO fw_macro VALUES (?,?,?,?,?)",
                        [r.series_id, obs, float(r.value), today, FAR])
    except Exception as exc:                                       # noqa: BLE001
        notes.append(f"FRED nicht geladen: {type(exc).__name__}")


_ALFRED = None          # tests set a frame here; otherwise read from the stored copy
_ALFRED_READ: dict = {}
VINTAGE_COLUMNS = ["available_from", "available_through", "observation_date", "value",
                   "series_id"]


def _alfred() -> pd.DataFrame | None:
    """ALFRED's vintages from the stored copy, or None when there is none (audit
    24.10.2026 F11: a missing research folder used to stop all six books, although
    only A and C use macro inputs)."""
    if _ALFRED is not None:
        return _ALFRED
    try:
        path = md.stored("alfred")
    except (md.MissingInput, ValueError):
        return None
    key = (str(path), path.stat().st_mtime_ns)
    if key not in _ALFRED_READ:
        _ALFRED_READ.clear()
        _ALFRED_READ[key] = pd.read_parquet(path)
    return _ALFRED_READ[key]


def vintages(con) -> pd.DataFrame:
    """ALFRED's vintages, cut where one of our own FRED snapshots begins, plus ours.
    Without ALFRED only ours; the books that need macro inputs (A, C) are then not
    scored at all (make_signals), so this never feeds them a shortened history."""
    a = _alfred()
    a = pd.DataFrame(columns=VINTAGE_COLUMNS) if a is None else a.copy()
    for col in ("available_from", "available_through", "observation_date"):
        a[col] = a[col].astype(str).str[:10]
    ours = con.execute("SELECT series_id, observation_date, value, available_from, "
                       "available_through FROM fw_macro").fetch_df()
    if len(ours):
        for col in ("available_from", "available_through", "observation_date"):
            ours[col] = ours[col].astype(str).str[:10]
        first = ours.groupby(["series_id", "observation_date"])["available_from"].min()
        key = list(zip(a.series_id, a.observation_date, strict=True))
        cut = pd.Series([first.get(k) for k in key], index=a.index)
        has = cut.notna()
        day_before = pd.to_datetime(cut[has]) - pd.Timedelta(days=1)
        a.loc[has, "available_through"] = np.minimum(
            a.loc[has, "available_through"].to_numpy(),
            day_before.dt.strftime("%Y-%m-%d").to_numpy())
        a = a[a.available_from <= a.available_through]
    parts = [x for x in (a[VINTAGE_COLUMNS], ours[VINTAGE_COLUMNS]) if len(x)]
    if not parts:
        return pd.DataFrame(columns=VINTAGE_COLUMNS)
    return pd.concat(parts, ignore_index=True)


def spy_frame(con) -> pd.DataFrame:
    s = con.execute("SELECT day, open, close, close_adj FROM fw_spy ORDER BY day").fetch_df()
    s.index = pd.to_datetime(s.pop("day"))
    return s


def fx_on(con, day: dt.date) -> float | None:
    """EUR/USD (dollars per euro) on `day`, or the latest of the four days before."""
    row = con.execute("SELECT day, eurusd FROM fw_fx WHERE day <= ? ORDER BY day DESC "
                      "LIMIT 1", [day]).fetchone()
    if row and (day - row[0]).days <= 4:
        return float(row[1])
    return None


def active_model(con, book_id: str) -> dict | None:
    row = con.execute("SELECT id, artifact_json, next_retrain, trained_at FROM fw_models "
                      "WHERE book_id=? AND active ORDER BY trained_at DESC LIMIT 1",
                      [book_id]).fetchone()
    if not row:
        return None
    a = json.loads(row[1])
    a["_id"], a["_next"], a["_trained"] = row[0], row[2], row[3]
    return a


def _store_model(con, book_id, now, artifact, source, sha, next_retrain) -> None:
    con.execute("UPDATE fw_models SET active=FALSE WHERE book_id=?", [book_id])
    con.execute("INSERT INTO fw_models VALUES (?,?,?,?,?,?,?,?,?,?,TRUE,?)",
                [lg.uid(), book_id, now, source, artifact.get("window_start"),
                 artifact.get("outcomes_before"),
                 artifact.get("latest_outcome") or artifact.get("latest_training_label"),
                 artifact.get("samples") or artifact.get("training_samples"), sha,
                 next_retrain, json.dumps(artifact)])


def ensure_models(con, market, today: dt.date, now, log, notes) -> None:
    """B and C from the stored research inputs, A trained at the start; retraining
    when due. Each book on its own: a missing or changed input only stops that book and
    leaves a note (audit 24.10.2026 F11). The source is recorded without the local
    folder, so no full path reaches the app or the brief."""
    if active_model(con, "fw-b") is None:
        try:
            path = md.stored("b")
            b = md.load_b(path)
            _store_model(con, "fw-b", now, b, f"forward_inputs/{path.name}",
                         md.sha256(path), None)
        except Exception as exc:                                   # noqa: BLE001
            notes.append(f"fw-b: Modell nicht geladen ({ui.error_text(exc)})")
    if active_model(con, "fw-c") is None:
        try:
            path = md.stored("c")
            c = md.load_c_2026(path)
            c["outcomes_before"] = "2026-01-01"
            _store_model(con, "fw-c", now, c, f"forward_inputs/{path.name} [2026][v13]",
                         md.sha256(path), cal.sessions_from(dt.date(2027, 1, 1), 1)[0])
        except Exception as exc:                                   # noqa: BLE001
            notes.append(f"fw-c: Modell nicht geladen ({ui.error_text(exc)})")
    if _alfred() is None:
        notes.append("ALFRED-Vintages fehlen: A und C werden weder trainiert noch "
                     "bewertet, D, E und F laufen weiter")
        return
    due = []
    a = active_model(con, "fw-a")
    if a is None or (a["_next"] and today >= a["_next"]):
        due.append(("fw-a", fx.V29, today - pd.DateOffset(years=10), today,
                    (pd.Timestamp(today) + pd.DateOffset(years=1)).date(), "v29"))
    c = active_model(con, "fw-c")
    if c is not None and c["_next"] and today >= c["_next"]:
        y = today.year
        due.append(("fw-c", fx.V13, dt.date(y - 10, 1, 1), dt.date(y, 1, 1),
                    cal.sessions_from(dt.date(y + 1, 1, 1), 1)[0], "v13"))
    for book_id, cols, start, cutoff, nxt, variant in due:
        start = pd.Timestamp(start).date()
        log(f"Training {book_id} ({variant}) {start} .. {cutoff}")
        try:
            panel = dd.Panel(market, start - dt.timedelta(days=400), cutoff,
                             spy_frame(con), vintages(con))
            art = md.train_window(panel, cols, start, cutoff, variant, log)
            _store_model(con, book_id, now, art, "trained in the tracker", None, nxt)
        except Exception as exc:                                   # noqa: BLE001
            notes.append(f"{book_id}: Training fehlgeschlagen ({ui.error_text(exc)})")


# -- prices of one session ---------------------------------------------------------------

def prices_at(market, day: dt.date) -> pd.DataFrame:
    df = market.execute(
        "SELECT ticker, open, close, close_adj, close_raw FROM prices WHERE date = ? "
        "AND close > 0", [day]).fetch_df().set_index("ticker")
    df["open_adj"] = df["open"] * df["close_adj"] / df["close"]
    df["open_raw"] = df["open"] * df["close_raw"] / df["close"]
    return df


def adj_open(market, ticker: str, day: dt.date) -> float | None:
    r = market.execute("SELECT open * close_adj / close FROM prices WHERE ticker=? AND "
                       "date=? AND close > 0", [ticker, day]).fetchone()
    return float(r[0]) if r and _usable(r[0]) else None


def _usable(price) -> bool:
    """A real, positive price. NaN passes `not x` and `x <= 0` alike (audit 24.10.2026
    F5: `prices.open` may be NULL, and a NaN open became NaN proceeds, NaN cash and a
    NaN book for good), so finiteness is checked explicitly."""
    try:
        return price is not None and math.isfinite(float(price)) and float(price) > 0
    except (TypeError, ValueError):
        return False


def equivalent(market, pos: dict, adj_now: float | None) -> float | None:
    """The price on the entry price's basis: entry x (adjusted now / adjusted entry),
    both read today, so dividends between entry and now count. None when either price
    is missing, NaN or not positive: the caller then keeps the last mark or leaves the
    order unresolved, never books a made-up value."""
    if not _usable(adj_now):
        return None
    base = adj_open(market, pos["ticker"], pos["opened_session"])
    if not _usable(base):
        return None
    out = pos["entry_usd"] * adj_now / base
    return out if _usable(out) else None


def _order(con, book_id, sig_id, now, S, ticker, side, action, rule, status, note,
           price=None, fx_rate=None, stake_usd=None, raw_price=None, pid=None, score=None,
           rank=None) -> None:
    whole = (math.floor(stake_usd / raw_price) if stake_usd and raw_price and raw_price > 0
             else None)
    con.execute("INSERT INTO fw_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [lg.uid(), book_id, sig_id, now, S, ticker, side, action, rule, status, note,
                 now if status in ("filled", "resized") else None, price, fx_rate,
                 stake_usd, lg.COST * stake_usd / fx_rate if stake_usd and fx_rate else None,
                 whole, "unbestätigt (keine Brokerdaten)" if side == "short" else None,
                 pid, score, rank])


def _value_at(con, market, book_id, px_col: str, prices: pd.DataFrame, fx_rate) -> tuple:
    """Open positions valued on a session's open or close: (value EUR, exposure EUR)."""
    value = expo = 0.0
    for p in lg.open_positions(con, book_id).to_dict("records"):
        px = equivalent(market, p, prices[px_col].get(p["ticker"])) or p["mark_usd"]
        value += lg.value_usd(p["stake_usd"], p["entry_usd"], px, p["side"]) / fx_rate
        expo += p["stake_usd"] * px / p["entry_usd"] / fx_rate
    return value, expo


def _close_at_open(con, market, book_id, pos, S, px, fx_open, now, reason, sig_id) -> bool:
    eq = equivalent(market, pos, px["open_adj"].get(pos["ticker"]))
    if eq is None:
        _order(con, book_id, sig_id, now, S, pos["ticker"], pos["side"], "close",
               pos["rule"], "unresolved", "kein Eröffnungskurs: Position bleibt offen")
        return False
    lg.close_position(con, pos, now, S, eq, fx_open, reason)
    _order(con, book_id, sig_id, now, S, pos["ticker"], pos["side"], "close", pos["rule"],
           "filled", reason, eq, fx_open, pos["stake_usd"] * eq / pos["entry_usd"],
           px["open_raw"].get(pos["ticker"]), pos["id"])
    return True


def _open_at(con, book_id, sig_id, now, S, t, side, rule, px, fx_open, stake_eur, note,
             planned_exit=None, score=None, rank=None, status="filled") -> str:
    price = float(px.at[t, "open"])
    stake_usd = stake_eur * fx_open
    pid = lg.open_position(con, book_id, now, S, t, side, rule, price, fx_open, stake_usd,
                           planned_exit, note)
    _order(con, book_id, sig_id, now, S, t, side, "open", rule, status, note, price,
           fx_open, stake_usd, float(px.at[t, "open_raw"]), pid, score, rank)
    return pid


def _valid_open(px: pd.DataFrame, t: str) -> bool:
    return t in px.index and bool(np.isfinite(px.at[t, "open_adj"])) and \
        px.at[t, "open_adj"] > 0


def fill_rebalance(con, market, book_id, sig, S, px, fx_open, now) -> str:
    """A-D: close everything at the open, then 20 long / 20 short, 50/50, equal weight —
    the research's `simulate` step, in EUR."""
    payload = json.loads(sig["payload_json"])
    for p in lg.open_positions(con, book_id).to_dict("records"):
        _close_at_open(con, market, book_id, p, S, px, fx_open, now, "Umschichtung",
                       sig["id"])
    remaining = lg.open_positions(con, book_id)
    held = set(remaining["ticker"])
    value, reserved = _value_at(con, market, book_id, "open_adj", px, fx_open)
    cash = lg.cash(con, book_id)
    budget = min(max(0.0, cash + value - reserved), cash) / (1 + lg.COST)
    ranked = [r for r in payload["ranking"] if _valid_open(px, r["ticker"])
              and r["ticker"] not in held]
    number = min(stg.TOP_N, len(ranked) // 2)
    if number == 0 or budget <= 0:
        return "kein Korb (zu wenige handelbare Aktien oder kein Kapital)"
    each = 0.5 * budget / number
    for k, r in enumerate(ranked[:number]):
        _open_at(con, book_id, sig["id"], now, S, r["ticker"], "long", "rebalance", px,
                 fx_open, each, "Long-Korb", score=r["score"], rank=k + 1)
    for k, r in enumerate(ranked[-number:]):
        _open_at(con, book_id, sig["id"], now, S, r["ticker"], "short", "rebalance", px,
                 fx_open, each, "Short-Korb", score=r["score"],
                 rank=len(payload["ranking"]) - number + k + 1)
    return f"{number} Long / {number} Short eröffnet"


def fill_rare(con, market, book_id, sig, S, px, fx_open, now, prev_equity) -> str:
    """F: planned exits first, then entries at 75 % of the previous close's value each,
    all shrunk alike when the free money does not cover them."""
    payload = json.loads(sig["payload_json"]) if sig is not None else {"candidates": []}
    held = lg.open_positions(con, book_id)
    held_keys = {(r.ticker, r.rule) for r in held.itertuples()}
    wanted, seen = [], set()
    for c in payload.get("candidates", []):
        if c.get("unresolved"):
            _order(con, book_id, sig["id"], now, S, c["ticker"], c["side"], "open",
                   c["rule"], "unresolved", c["unresolved"])
            continue
        if (c["ticker"], c["rule"]) in held_keys:
            _order(con, book_id, sig["id"], now, S, c["ticker"], c["side"], "open",
                   c["rule"], "skipped", "Trade derselben Regel und Aktie noch offen")
            continue
        if (c["ticker"], c["side"]) in seen:
            continue                     # same share, same side, same open: one order
        if not _valid_open(px, c["ticker"]):
            _order(con, book_id, sig["id"], now, S, c["ticker"], c["side"], "open",
                   c["rule"], "skipped", "kein Eröffnungskurs")
            continue
        seen.add((c["ticker"], c["side"]))
        wanted.append(c)
    if not wanted:
        return "keine Einstiege"
    cash = lg.cash(con, book_id)
    target = stg.F_TARGET * max(prev_equity, 0.0)
    stake = min(target, cash / (len(wanted) * (1 + lg.COST))) if cash > 0 else 0.0
    exit_on = cal.sessions_from(S, stg.F_HOLD + 1)[-1]
    if stake <= 0.01:
        for c in wanted:
            _order(con, book_id, sig["id"], now, S, c["ticker"], c["side"], "open",
                   c["rule"], "skipped", "kein freies Kapital")
        return "Signale ohne freies Kapital ausgelassen"
    status = "resized" if stake < target - 1e-9 else "filled"
    for c in wanted:
        _open_at(con, book_id, sig["id"], now, S, c["ticker"], c["side"], c["rule"], px,
                 fx_open, stake, f"{c['rule']} ({c['value']:.4f})", exit_on, c["value"],
                 None, status)
    return f"{len(wanted)} Einstieg(e) zu je {stake:,.2f} EUR" + \
        (" (verkleinert)" if status == "resized" else "")


def fill_monthly(con, market, book_id, sig, S, px, fx_open, now) -> str:
    """E: keep longs ranked in the top 40 and shorts in the bottom 40, close the rest,
    refill each side to 20 at 2.5 % of the value each, within the free money."""
    payload = json.loads(sig["payload_json"])
    ranking = [r["ticker"] for r in payload["ranking"]]
    pos_of = {t: k for k, t in enumerate(ranking)}
    n = len(ranking)
    for p in lg.open_positions(con, book_id).to_dict("records"):
        k = pos_of.get(p["ticker"])
        keep = k is not None and (k < stg.E_BUFFER if p["side"] == "long"
                                  else k >= n - stg.E_BUFFER)
        if not keep:
            _close_at_open(con, market, book_id, p, S, px, fx_open, now,
                           "Monatswechsel: außerhalb des Puffers", sig["id"])
    held = lg.open_positions(con, book_id)
    value, _ = _value_at(con, market, book_id, "open_adj", px, fx_open)
    cash = lg.cash(con, book_id)
    equity = cash + value
    each = equity * 0.5 / stg.TOP_N
    new = []
    # Each side at most half the ranking, and a name taken once is taken: with fewer
    # than 40 ranked names the top 20 and the bottom 20 overlap, and the same share
    # used to be opened long and short at once (audit 24.10.2026, low item 8; E3).
    per_side = min(stg.TOP_N, n // 2)
    taken = set(held["ticker"])
    for side, order in (("long", ranking), ("short", ranking[::-1])):
        have = set(held.loc[held["side"] == side, "ticker"])
        need = per_side - len(have)
        for t in order:
            if need <= 0:
                break
            if t in taken or not _valid_open(px, t):
                continue
            new.append((t, side, pos_of[t]))
            taken.add(t)
            need -= 1
    if not new:
        return "keine Änderungen"
    scale = min(1.0, cash / (len(new) * each * (1 + lg.COST))) if each > 0 else 0.0
    for t, side, k in new:
        _open_at(con, book_id, sig["id"], now, S, t, side, "monthly", px, fx_open,
                 each * scale, f"E3 {side}", score=payload["ranking"][k]["score"],
                 rank=k + 1, status="filled" if scale >= 1 else "resized")
    return f"{len(new)} neue Positionen" + (" (verkleinert)" if scale < 1 else "")


# A share with no bar on this many complete sessions in a row has left the market (rule
# 3, R18). Fixed with the rule versions A2-F2 on 24.10.2026, before any session was
# booked. Five is a week: a halt shorter than that keeps the position; a longer one is
# closed here and would be a new position if it came back.
GONE_AFTER = 5
# The return assumed for a long when the store knows no real delisting return (R18,
# Settings.delisting_return). A short is closed at its last mark: no gain is assumed
# for it, which is the conservative side for the book.
ASSUMED_DELISTING_RETURN = -0.30


def _sessions_without_bar(market, ticker: str, S: dt.date) -> int:
    last = market.execute("SELECT MAX(date) FROM prices WHERE ticker = ? AND close > 0 "
                          "AND date <= ?", [ticker, S]).fetchone()[0]
    if last is None:
        return GONE_AFTER
    n, d = 0, last
    while n < GONE_AFTER:
        d = cal.next_session(d)
        if d > S:
            break
        n += 1
    return n


def realise_delisted(con, market, book_id, S, px, fx_close, now) -> int:
    """Close, at session S, every open position whose share has had no bar on the last
    GONE_AFTER sessions (all of them complete: the run books nothing else). The price is
    the last mark on the entry basis times one plus the real delisting return where the
    store has one (CRSP), else the assumed -30 % for a long and 0 % for a short; the
    exit fee is charged as for any close. Audit 24.10.2026 F4: such a position used to
    stay open at its last mark forever, counted only as stale."""
    gone = []
    for p in lg.open_positions(con, book_id).to_dict("records"):
        if p["ticker"] in px.index:
            continue
        if _sessions_without_bar(market, p["ticker"], S) >= GONE_AFTER:
            gone.append(p)
    if not gone:
        return 0
    real = history.delisting_returns(market, [p["ticker"] for p in gone])
    for p in gone:
        r = real.get(p["ticker"])
        if r is None:
            r = ASSUMED_DELISTING_RETURN if p["side"] == "long" else 0.0
            how = "angenommen"
        else:
            how = "Delisting-Rendite der Daten"
        price = p["mark_usd"] * (1 + r)
        reason = (f"keine Kurse seit {GONE_AFTER} Sitzungen: als Abgang realisiert, "
                  f"{r:+.0%} ({how}) auf den letzten Kurs")
        lg.close_position(con, p, now, S, price, fx_close, reason)
        _order(con, book_id, None, now, S, p["ticker"], p["side"], "close", p["rule"],
               "delisted", reason, price=price, fx_rate=fx_close, pid=p["id"])
    return len(gone)


def process_session(con, market, S: dt.date, now, notes) -> bool:
    """Book session S for every book. False when the EUR/USD rate is missing or a book
    failed; the run then stops here, so no book skips a session another one booked.

    Each book's session is one transaction (audit 24.10.2026 F2). Whether a session is
    booked is decided by its mark alone, so the borrow, the fills, the signal status and
    the mark must land together: a failure in between used to leave the borrow and some
    fills in place, and the next run booked them a second time."""
    prev = cal.prev_session(S)
    fx_open, fx_close = fx_on(con, prev), fx_on(con, S)
    if not fx_open or not fx_close:
        notes.append(f"{S}: EUR/USD fehlt — Sitzung nicht gebucht")
        return False
    px = prices_at(market, S)
    for b in stg.BOOKS:
        con.execute("BEGIN")
        try:
            _book_session(con, market, b, S, px, fx_open, fx_close, now)
            con.execute("COMMIT")
        except Exception as exc:                                   # noqa: BLE001
            con.execute("ROLLBACK")
            notes.append(f"{b['code']}: {S} nicht gebucht ({type(exc).__name__}); "
                         "der nächste Lauf versucht es erneut")
            return False
    return True


def _book_session(con, market, b: dict, S: dt.date, px, fx_open, fx_close, now) -> None:
    bid = b["id"]
    first = con.execute("SELECT first_session FROM fw_books WHERE id=?",
                        [bid]).fetchone()[0]
    if S < first:
        return
    last = con.execute("SELECT MAX(session) FROM fw_marks WHERE book_id=?",
                       [bid]).fetchone()[0]
    if last is not None and S <= last:
        return
    lg.accrue_borrow(con, bid, now, last or S, S, S)
    gone = realise_delisted(con, market, bid, S, px, fx_close, now)
    prev_eq = con.execute("SELECT equity FROM fw_marks WHERE book_id=? ORDER BY "
                          "session DESC LIMIT 1", [bid]).fetchone()
    prev_eq = float(prev_eq[0]) if prev_eq else CAPITAL
    sigs = con.execute("SELECT * FROM fw_signals WHERE book_id=? AND exec_session=? "
                       "AND status='pending'", [bid, S]).fetch_df().to_dict("records")
    if b["kind"] == "rare":
        for p in lg.open_positions(con, bid).to_dict("records"):
            if p["planned_exit"] is not None and S >= p["planned_exit"]:
                _close_at_open(con, market, bid, p, S, px, fx_open, now,
                               "fünf Sitzungen gehalten", None)
    for sig in sigs:
        if pd.Timestamp(sig["created_at"]) >= pd.Timestamp(open_time(S)):
            con.execute("UPDATE fw_signals SET status='missed', note='Signal nach der "
                        "Eröffnung erstellt' WHERE id=?", [sig["id"]])
            continue
        if b["kind"] in ("model", "rule_d"):
            result = fill_rebalance(con, market, bid, sig, S, px, fx_open, now)
        elif b["kind"] == "rule_e":
            result = fill_monthly(con, market, bid, sig, S, px, fx_open, now)
        else:
            result = fill_rare(con, market, bid, sig, S, px, fx_open, now, prev_eq)
        con.execute("UPDATE fw_signals SET status='executed', note=? WHERE id=?",
                    [result, sig["id"]])
    # A signal for a session before S that is still pending was never filled: its
    # session had no bars at all and was passed over (a closure the calendar does not
    # know). It used to stay "pending" for good (audit 24.10.2026, low item 9); a
    # later session being booked bounds the wait, and E is then due again (F19).
    con.execute("UPDATE fw_signals SET status='missed', note='keine Kurse am "
                "Ausführungstag: nicht ausgeführt' WHERE book_id=? AND status='pending' "
                "AND exec_session < ?", [bid, S])
    prices = {}
    for p in lg.open_positions(con, bid).to_dict("records"):
        eq = equivalent(market, p, px["close_adj"].get(p["ticker"]))
        if eq:
            prices[p["id"]] = eq
    lg.mark(con, bid, now, S, fx_close, prices,
            note=f"{gone} Abgang/Abgänge realisiert" if gone else "")


def benchmark(con, book_id: str) -> pd.DataFrame:
    """SPY bought with the same 1,000 EUR at the first session's open, dividends in
    (adjusted close), valued in EUR at each close — recomputed from the stored series
    every time, so a later dividend adjustment cannot leave old values behind."""
    first = con.execute("SELECT first_session FROM fw_books WHERE id=?",
                        [book_id]).fetchone()[0]
    marks = con.execute("SELECT session FROM fw_marks WHERE book_id=? ORDER BY session",
                        [book_id]).fetch_df()
    spy = spy_frame(con)
    if marks.empty or spy.empty or pd.Timestamp(first) not in spy.index:
        return pd.DataFrame(columns=["session", "value_eur"])
    s0 = spy.loc[pd.Timestamp(first)]
    open_adj0 = s0["open"] * s0["close_adj"] / s0["close"]
    fx0 = fx_on(con, cal.prev_session(first))
    rows = []
    for d in marks["session"]:
        ts = pd.Timestamp(d)
        fxd = fx_on(con, pd.Timestamp(d).date())
        if ts not in spy.index or not fxd or not fx0:
            rows.append((d, None))
            continue
        rows.append((d, CAPITAL * fx0 * spy.at[ts, "close_adj"] / open_adj0 / fxd))
    return pd.DataFrame(rows, columns=["session", "value_eur"])


# -- signals after the close of L ---------------------------------------------------------

def _due(con, book: dict, L: dt.date) -> bool:
    last = con.execute("SELECT MAX(signal_session) FROM fw_signals WHERE book_id=? AND "
                       "kind IN ('rebalance','monthly') AND status <> 'missed'",
                       [book["id"]]).fetchone()[0]
    if book["kind"] in ("model", "rule_d"):
        if last is None:
            return True
        n, d = 0, last
        while d < L:
            d = cal.next_session(d)
            n += 1
        return n >= 5
    if book["kind"] == "rule_e":
        return last is None or last < month_anchor(L)
    return True


def month_anchor(L: dt.date) -> dt.date:
    """The month end E's next basket belongs to: L itself when L is the last session of
    its month, else the last session of the month before.

    E is due as long as no signal (pending or executed) exists from that session on. It
    used to be due only when L itself was the month's last session, so a month end that
    was incomplete or run too late moved the basket a whole month (audit 24.10.2026
    F19); now the next complete session catches up. The first basket is built at the
    start, mid-month (22.10.2026); the month ends follow from there."""
    if cal.next_session(L).month != L.month:
        return L
    return cal.prev_session(dt.date(L.year, L.month, 1))


def _rows(panel, i: int) -> tuple[pd.DataFrame, np.ndarray]:
    """Every model input of every share on session i, and the research eligibility."""
    rows, ok = [], []
    for t in panel.tickers:
        f = panel.model_frame(t, fx.V29)
        rows.append(f.iloc[i].to_numpy(dtype=float))
        ok.append(t not in panel.exclude and bool(panel.eligible(t).iloc[i]))
    return pd.DataFrame(rows, index=panel.tickers, columns=fx.V29), np.array(ok)


def _unresolved(panel, t: str, i: int) -> str:
    """An extreme move with a changing ratio of traded to split-adjusted close inside
    the window is an unexplained corporate action: not traded on a made-up price."""
    ratio = (panel.raw[t] / panel.close[t]).iloc[max(i - 6, 0):i + 1].dropna()
    if len(ratio) and (ratio.max() / ratio.min() - 1) > 1e-6:
        return "ungeklärte Kapitalmaßnahme (Verhältnis Kurs/Split-Kurs ändert sich)"
    return ""


def make_signals(con, market, live, L: dt.date, now, started: dt.datetime, log,
                 notes) -> dict:
    nxt = cal.next_session(L)
    status = {}
    due = [b for b in stg.BOOKS if _due(con, b, L)]
    if not due:
        return status
    if now >= open_time(nxt):
        for b in due:
            lg.signal(con, b["id"], now, L, nxt,
                      "rare" if b["kind"] == "rare" else
                      ("monthly" if b["kind"] == "rule_e" else "rebalance"), "missed",
                      note="Lauf erst nach der nächsten Eröffnung — kein rückwirkender "
                           "Auftrag")
            status[b["code"]] = "Signal verpasst"
        return status
    log(f"Signale nach Schluss {L} für {', '.join(b['code'] for b in due)}")
    panel = dd.Panel(market, L - dt.timedelta(days=900), L, spy_frame(con), vintages(con))
    if pd.Timestamp(L) not in panel.calendar:
        notes.append(f"{L}: SPY-Kurs fehlt — keine Signale")
        return status
    i = int(panel.calendar.get_loc(pd.Timestamp(L)))
    need_rows = any(b["kind"] in ("model", "rule_d", "rule_e") for b in due)
    X_all, base_ok = _rows(panel, i) if need_rows else (None, None)
    for b in due:
        try:
            if b["kind"] == "rare":
                if close_time(L) <= started:
                    lg.signal(con, b["id"], now, L, nxt, "rare", "observed",
                              {"candidates": stg.rare_candidates(panel, i)},
                              "Episoden vor dem Start: nur beobachtet")
                    status[b["code"]] = "beobachtet (vor Start)"
                    continue
                cands = stg.rare_candidates(panel, i)
                for c in cands:
                    c["unresolved"] = _unresolved(panel, c["ticker"], i)
                lg.signal(con, b["id"], now, L, nxt, "rare", "pending" if cands else "none",
                          {"candidates": cands}, f"{len(cands)} neue Schwellenereignis(se)")
                status[b["code"]] = f"{len(cands)} Signal(e)"
                continue
            if b["kind"] == "model":
                if "alfred" in md.NEEDS.get(b["id"], ()) and _alfred() is None:
                    status[b["code"]] = "wartet auf die ALFRED-Vintages"
                    continue
                art = active_model(con, b["id"])
                if art is None:
                    status[b["code"]] = "kein Modell"
                    continue
                s, ok = stg.model_scores(X_all, art, base_ok)
                meta = {"model_id": art["_id"],
                        "latest_outcome": art.get("latest_outcome")
                        or art.get("latest_training_label")}
            elif b["kind"] == "rule_d":
                s, ok = stg.d_scores(panel, i, base_ok)
                meta = {}
            else:
                prev = L - dt.timedelta(days=365)
                frame = stg.with_share_dates(live.pit_frame(L), market, L)
                year_ago = stg.with_share_dates(live.pit_frame(prev), market, prev)
                s, ok = stg.e_scores(panel, i, frame, year_ago, base_ok, L, prev)
                meta = {}
            rank = stg.ranking(s, ok, panel.tickers)
            kind = "monthly" if b["kind"] == "rule_e" else "rebalance"
            lg.signal(con, b["id"], now, L, nxt, kind, "pending",
                      {"ranking": rank, "eligible": int(ok.sum()), **meta},
                      f"{len(rank)} geeignete Aktien")
            status[b["code"]] = f"Korb für {nxt} geplant ({len(rank)} geeignet)"
        except Exception as exc:                                   # noqa: BLE001
            notes.append(f"{b['code']}: Signal fehlgeschlagen ({ui.error_text(exc)})")
    return status


# -- the run -----------------------------------------------------------------------------

def run(path: Path, market, live=None, now: dt.datetime | None = None, log=print) -> dict:
    live = live or Live()
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC).replace(tzinfo=None)
    today = now.replace(tzinfo=dt.UTC).astimezone(cal.ET).date()
    notes: list[str] = []
    con = lg.connect(path)
    summary = {"run_at": now.isoformat(), "notes": notes, "status": {}}
    try:
        ensure_books(con, now)
        started = con.execute("SELECT MIN(started_at) FROM fw_books").fetchone()[0]
        first = con.execute("SELECT MIN(first_session) FROM fw_books").fetchone()[0]
        refresh_inputs(con, market, live, today, notes, now)
        ensure_models(con, market, today, now, log, notes)
        complete = dd.complete_sessions(market, first - dt.timedelta(days=10))
        data_days = list(dd.coverage(market, first - dt.timedelta(days=10)).index)
        # Book every session from the first one on, in order, and stop at the first one
        # that cannot be booked yet (audit 24.10.2026 F1). A booked session is never
        # touched again, so a day with part of its prices (20./21.10.2026: 595 of
        # 5,364) must wait for the rest instead of trading a basket of whatever loaded.
        # A session whose close has not come yet waits too: its bar is not a close.
        # A session without any bar while later ones have them is a closure the
        # calendar does not know, and is passed over as before.
        complete_set, stop = set(complete), None
        if data_days:
            S = first
            while S <= max(data_days):
                if close_time(S) > now:
                    stop = S
                    break
                if S in data_days:
                    if S not in complete_set:
                        stop = S
                        notes.append(f"{S}: Kurse unvollständig (Abdeckung < 90 %) — "
                                     "Sitzung und alle folgenden warten")
                        break
                    if not process_session(con, market, S, now, notes):
                        stop = S
                        break
                S = cal.next_session(S)
        # Signals come only from sessions before that stop: a basket for a session after
        # a hole would be filled before the hole is booked.
        if stop is not None:
            complete = [d for d in complete if d < stop]
        latest_complete = max(complete) if complete else None
        summary["latest_complete"] = str(latest_complete)
        summary["latest_data"] = str(max(data_days)) if data_days else None
        if latest_complete is None or latest_complete < cal.prev_session(first):
            summary["status"] = {b["code"]: "wartet auf vollständige Kurse"
                                 for b in stg.BOOKS}
            notes.append("Kursdaten unvollständig: neueste vollständige Sitzung "
                         f"{latest_complete}")
        else:
            summary["status"] = make_signals(con, market, live, latest_complete, now,
                                             started, log, notes)
            if latest_complete < max(data_days):
                notes.append(f"{max(data_days)} unvollständig (Abdeckung < 90 %): "
                             "Signale nur bis zur letzten vollständigen Sitzung")
        for b in stg.BOOKS:
            bench = benchmark(con, b["id"])
            for r in bench.itertuples():
                con.execute("INSERT OR REPLACE INTO fw_bench VALUES (?,?,?,?,?)",
                            [b["id"], r.session, None, r.value_eur, None])
        con.execute("INSERT INTO fw_runs VALUES (?,?)",
                    [now, json.dumps(summary, default=str)])
    finally:
        con.close()
    return summary
