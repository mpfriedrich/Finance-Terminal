"""What the app and the morning brief show about the six forward books (read-only).

Performance is the book's own daily EUR value after costs against SPY bought with the
same 1,000 EUR at the same first open, both in EUR. No single-trade averages are
compared with buy-and-hold. Annualised figures are only given from a year of
observations on; before that they would be noise presented as an expectation.
"""

from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from .. import filing_butterfly as cal
from . import strategies as stg


def series(con, book_id: str) -> pd.DataFrame:
    m = con.execute("SELECT session, equity, cash, positions, gross, net, fees, borrow, "
                    "realized, unrealized, fx, open_positions, stale FROM fw_marks WHERE "
                    "book_id=? ORDER BY session", [book_id]).fetch_df()
    b = con.execute("SELECT session, value_eur FROM fw_bench WHERE book_id=? ORDER BY "
                    "session", [book_id]).fetch_df()
    if m.empty:
        return m
    m = m.merge(b.rename(columns={"value_eur": "spy"}), on="session", how="left")
    m["session"] = pd.to_datetime(m["session"]).dt.date
    return m


def drawdown(values: pd.Series, start: float) -> pd.Series:
    full = pd.concat([pd.Series([start]), values.reset_index(drop=True)])
    peak = full.cummax()
    return (full / peak - 1).iloc[1:].set_axis(values.index)


def years(m: pd.DataFrame, capital: float) -> pd.DataFrame:
    """Calendar-year returns of book and SPY; the running and the first, incomplete
    year are marked as partial."""
    rows = []
    prev_b = prev_s = capital
    for y, g in m.groupby(pd.to_datetime(m["session"]).dt.year):
        rb = g["equity"].iloc[-1] / prev_b - 1
        rs = (g["spy"].iloc[-1] / prev_s - 1) if g["spy"].notna().all() else float("nan")
        first_day = g["session"].iloc[0]
        complete = (first_day <= cal.sessions_from(dt.date(y, 1, 1), 1)[0]
                    and g["session"].iloc[-1] >= cal.prev_session(dt.date(y + 1, 1, 1)))
        rows.append({"year": int(y), "book": rb, "spy": rs, "gap_pp": 100 * (rb - rs),
                     "partial": not complete})
        prev_b, prev_s = g["equity"].iloc[-1], g["spy"].iloc[-1]
    return pd.DataFrame(rows)


def summary(con, book_id: str) -> dict:
    b = con.execute("SELECT * FROM fw_books WHERE id=?", [book_id]).fetch_df().iloc[0]
    out = {"id": book_id, "code": b["code"], "name": b["name"],
           "description": b["description"], "version": b["version"],
           "started_at": b["started_at"], "first_session": b["first_session"],
           "capital": float(b["capital"])}
    m = series(con, book_id)
    out["marks"] = m
    if m.empty:
        out["state"] = "noch keine Bewertung"
    else:
        last = m.iloc[-1]
        prev_eq = m["equity"].iloc[-2] if len(m) > 1 else out["capital"]
        prev_spy = m["spy"].iloc[-2] if len(m) > 1 else out["capital"]
        out.update({
            "as_of": last["session"], "equity": last["equity"], "cash": last["cash"],
            "day_eur": last["equity"] - prev_eq, "day_pct": last["equity"] / prev_eq - 1,
            "total_eur": last["equity"] - out["capital"],
            "total_pct": last["equity"] / out["capital"] - 1,
            "spy_day_pct": (last["spy"] / prev_spy - 1) if pd.notna(last["spy"]) else None,
            "spy_total_pct": (last["spy"] / out["capital"] - 1)
            if pd.notna(last["spy"]) else None,
            "gross": last["gross"], "net": last["net"],
            "open_positions": int(last["open_positions"]), "stale": int(last["stale"]),
            "fees": last["fees"], "borrow": last["borrow"],
            "max_dd": float(drawdown(m["equity"], out["capital"]).min()),
            "days": len(m)})
        if out.get("spy_total_pct") is not None:
            out["vs_spy_pp"] = 100 * (out["total_pct"] - out["spy_total_pct"])
        out["years"] = years(m, out["capital"])
    model = con.execute("SELECT source, outcomes_before, latest_outcome, next_retrain, "
                        "trained_at FROM fw_models WHERE book_id=? AND active",
                        [book_id]).fetchone()
    if model:
        out["model"] = dict(zip(("source", "outcomes_before", "latest_outcome",
                                 "next_retrain", "trained_at"), model, strict=True))
    out["signals"] = con.execute(
        "SELECT signal_session, exec_session, kind, status, note FROM fw_signals WHERE "
        "book_id=? ORDER BY signal_session DESC LIMIT 12", [book_id]).fetch_df()
    out["orders"] = con.execute(
        "SELECT exec_session, ticker, side, action, rule, status, note, price_usd, fx, "
        "stake_usd, fee_eur, whole_shares, borrow FROM fw_orders WHERE book_id=? ORDER BY "
        "exec_session DESC, action, side, ticker", [book_id]).fetch_df()
    out["positions"] = con.execute(
        "SELECT ticker, side, rule, opened_session, entry_usd, stake_eur, mark_usd, "
        "mark_session, planned_exit, borrow_eur FROM fw_positions WHERE book_id=? AND "
        "status='open' ORDER BY side, ticker", [book_id]).fetch_df()
    out["next"] = _next_step(con, b)
    return out


def _next_step(con, b) -> str:
    pending = con.execute("SELECT exec_session, kind FROM fw_signals WHERE book_id=? AND "
                          "status='pending' ORDER BY exec_session", [b["id"]]).fetchone()
    if pending:
        return f"Auftrag zur Eröffnung am {pending[0]} ({pending[1]})"
    book = stg.BY_CODE[b["code"]]
    if book["kind"] == "rare":
        exits = con.execute("SELECT ticker, planned_exit FROM fw_positions WHERE book_id=? "
                            "AND status='open' ORDER BY planned_exit", [b["id"]]).fetchall()
        return ("Ausstiege: " + ", ".join(f"{t} {d}" for t, d in exits)) if exits else \
            "täglicher Scan nach Handelsschluss"
    last = con.execute("SELECT MAX(signal_session) FROM fw_signals WHERE book_id=? AND "
                       "kind IN ('rebalance','monthly') AND status <> 'missed'",
                       [b["id"]]).fetchone()[0]
    if last is None:
        return "erstes Signal nach der nächsten vollständigen Sitzung"
    if book["kind"] == "rule_e":
        d = last
        while cal.next_session(d).month == d.month or d == last:
            d = cal.next_session(d)
        return f"nächstes Monatssignal nach Schluss {d}"
    return f"nächstes Signal nach Schluss {cal.sessions_from(last, 6)[-1]}"


def last_run(con) -> tuple:
    row = con.execute("SELECT run_at, summary_json FROM fw_runs ORDER BY run_at DESC "
                      "LIMIT 1").fetchone()
    return (row[0], json.loads(row[1])) if row else (None, {})


def _pct(v, digits=2) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:+.{digits}%}".replace(".", ",")


def _eur(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def brief_section(con) -> str:
    """The six books for the morning brief, as plain text (no model writes these
    numbers: they come straight from the ledger)."""
    at, last = last_run(con)
    lines = ["# Paper-Portfolios A–F (Forward, 1.000 € je Buch, nur Papier)", ""]
    if at is None:
        return "\n".join(lines + ["Noch nicht gestartet."])
    lines.append(f"Letzter Lauf {at:%d.%m.%Y %H:%M} UTC · neueste vollständige Sitzung "
                 f"{last.get('latest_complete')} · neueste Kursdaten "
                 f"{last.get('latest_data')}")
    for n in last.get("notes", []):
        lines.append(f"Hinweis: {n}")
    lines.append("")
    for b in stg.BOOKS:
        s = summary(con, b["id"])
        lines.append(f"## {s['name']} ({s['version']})")
        status = last.get("status", {}).get(b["code"])
        if "equity" not in s:
            lines.append(f"Status: {status or s.get('state')}. Start {s['first_session']}.")
            lines.append("")
            continue
        lines.append(
            f"Kontowert {_eur(s['equity'])} · heute {_eur(s['day_eur'])} "
            f"({_pct(s['day_pct'])}) · seit Start {_eur(s['total_eur'])} "
            f"({_pct(s['total_pct'])}) · Stand Schluss {s['as_of']}")
        lines.append(
            f"SPY gleiche Tage: heute {_pct(s.get('spy_day_pct'))}, seit Start "
            f"{_pct(s.get('spy_total_pct'))} · Differenz "
            + (f"{s['vs_spy_pp']:+.2f} Pp." if s.get("vs_spy_pp") is not None else "—"))
        lines.append(
            f"Cash {_eur(s['cash'])} · offene Positionen {s['open_positions']} · Brutto "
            f"{_pct(s['gross'], 0) if s['gross'] is not None else '—'} / Netto "
            f"{_pct(s['net'], 0) if s['net'] is not None else '—'} · max. Drawdown "
            f"{_pct(s['max_dd'])} · Gebühren {_eur(s['fees'])} · Leihe {_eur(s['borrow'])}")
        o = s["orders"]
        if len(o):
            day = o["exec_session"].max()
            today = o[o["exec_session"] == day]
            counts = today["status"].value_counts().to_dict()
            lines.append(f"Aufträge {day}: " + ", ".join(
                f"{k} {v}" for k, v in sorted(counts.items())))
        if status:
            lines.append(f"Signal: {status}")
        lines.append(f"Nächster Schritt: {s['next']}")
        if s.get("model"):
            m = s["model"]
            lines.append(f"Modell: Ziele bekannt bis {m['latest_outcome']}, nächstes "
                         f"Retraining {m['next_retrain'] or 'nie (eingefroren)'}")
        if s["stale"]:
            lines.append(f"Achtung: {s['stale']} Position(en) ohne aktuellen Kurs")
        if s["days"] < 252:
            lines.append(f"Erst {s['days']} Handelstage: keine annualisierten Werte.")
        lines.append("")
    lines.append("Hypothetische Simulation mit Bruchstücken; Leerverkäufe ohne bestätigte "
                 "Leihbarkeit; Kosten 0,10 % je Seite und 3 % p. a. Leihe sind Annahmen.")
    return "\n".join(lines)
