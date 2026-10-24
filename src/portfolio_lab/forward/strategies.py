"""The six forward books: names, rules and how each scores the universe.

Fixed on 22.10.2026 before the first book opened. A change of rules gets a new version
number and its own observation period (fw_versions); nothing here is tuned afterwards.
Version 2 (24.10.2026, before any session was booked) adds the delisting rule of the
audit's F4 to every book; nothing else changed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import features as fx
from . import models as md

RARE = {"jump_short": ("short", "ret5", 2.92252964071333),
        "vol_short": ("short", "vol60", 1.999262392887066),
        "drop_long": ("long", "ret1", -0.4680557667480151)}

# What each version number changed, written into fw_versions when a book moves to it.
VERSION_NOTES = {
    "2": "Delisting-Regel (Audit 24.10.2026 F4): keine Kurse an 5 vollständigen Sitzungen "
         "→ Abgang realisiert, echte Delisting-Rendite sonst Long −30 %, Short 0 %",
}

BOOKS = [
    {"code": "A", "id": "fw-a", "kind": "model", "variant": "v29", "version": "A2",
     "name": "A — CURRENT · Makro & Peers · 10 Jahre rollierend",
     "description": "Ridge (Alpha 1) auf 23 Kurs-/Marktmerkmalen, Peers, Beschäftigung, "
                    "Inflation und Fed-Zinsen (Faktorgruppe v29, ohne Gold). Trainiert auf "
                    "den zehn Jahren vor dem Start, jährlich rollierend neu. Vorab "
                    "festgelegte Hypothese, kein nachgewiesenes Optimum."},
    {"code": "B", "id": "fw-b", "kind": "model", "variant": "frozen", "version": "B2",
     "name": "B — LEGACY 2025 · Eingefrorenes Kursmodell",
     "description": "Gespeichertes Ridge-Modell (Alpha 1) auf 23 Kurs- und Marktmerkmalen, "
                    "trainiert bis Ende 2024. Koeffizienten, Reihenfolge, Skalierung und "
                    "Clipping exakt übernommen; nie neu trainiert."},
    {"code": "C", "id": "fw-c", "kind": "model", "variant": "v13", "version": "C2",
     "name": "C — RESEARCH WINNER · v13 · 10 Jahre mit Retraining",
     "description": "Ridge (Alpha 1) auf 23 Kurs-/Marktmerkmalen, Peers, Beschäftigung und "
                    "Inflation (v13). Start mit der Jahresversion 2026 aus dem Research "
                    "(Training 2016–2025), jedes Jahr im Januar neu. Rückblickend als "
                    "stärkste Gruppe ausgewählt — nicht unabhängig bestätigt."},
    {"code": "D", "id": "fw-d", "kind": "rule_d", "variant": "d", "version": "D2",
     "name": "D — CODEX CHOICE · Momentum & Peer-Reversion",
     "description": "Feste Regel: 0,5 × 12–1-Momentum + 0,3 × 6–1-Momentum − 0,2 × relative "
                    "20-Tage-Peer-Bewegung, je durch die 60-Tage-Volatilität geteilt, "
                    "standardisiert und auf ±3 begrenzt. Die spätere historische Prüfung "
                    "war schwach; die Gewichte bleiben unverändert."},
    {"code": "E", "id": "fw-e", "kind": "rule_e", "variant": "e", "version": "E2",
     "name": "E — CLAUDE CHOICE · Momentum, Profitabilität, Kapitaldisziplin",
     "description": "Monatlicher Long/Short-Korb großer US-Aktien nach dem Mittel dreier "
                    "Ränge: 12–1-Momentum, Bruttogewinn je Vermögen und wenig neue Aktien. "
                    "Puffer gegen Umschlag. Begründung und Regeln in PLAN.md (E1)."},
    {"code": "F", "id": "fw-f", "kind": "rare", "variant": "f", "version": "F2",
     "name": "F — CODEX EXTREME REVERSION · 75 %",
     "description": "Experimentell und rückblickend entwickelt. Short nach ≥292,25 % in fünf "
                    "Tagen oder 60-Tage-Volatilität ≥199,93 % (≤200 %), Long nach ≥46,81 % "
                    "Tagesverlust; Einstieg zur nächsten Eröffnung, Ausstieg nach fünf "
                    "Sitzungen, je Trade 75 % des Kontowerts. Historisch modelliert: 26,96 % "
                    "CAGR, −56,86 % max. Drawdown; ohne die fünf besten Trades 16,87 % — "
                    "keine Renditezusage."},
]
BY_CODE = {b["code"]: b for b in BOOKS}
TOP_N = 20
E_BUFFER = 40
E_MIN_CAP = 2e9
E_MIN_DOLLAR_VOLUME = 10e6
F_TARGET = 0.75
F_HOLD = 5


def ranking(scores: np.ndarray, ok: np.ndarray, tickers: list[str]) -> list[dict]:
    """Eligible shares from the highest score down; ties by universe order, as the
    research's lexsort((ids, -score))."""
    ids = np.flatnonzero(ok & np.isfinite(scores))
    order = ids[np.lexsort((ids, -scores[ids]))]
    return [{"ticker": tickers[j], "score": float(scores[j])} for j in order]


def model_scores(X_all: pd.DataFrame, artifact: dict, base_ok: np.ndarray
                 ) -> tuple[np.ndarray, np.ndarray]:
    """Scores of A, B or C on the signal session. `X_all`: one row per share, every
    model input (V29 superset) on that session; `base_ok`: research eligibility."""
    cols = artifact["features"]
    X = X_all[cols].to_numpy(dtype=float)
    if "impute" in artifact:                 # B: only the 16 price inputs must exist
        need = [cols.index(c) for c in fx.PRICE16]
        ok = base_ok & np.isfinite(X[:, need]).all(axis=1)
    else:
        ok = base_ok & np.isfinite(X).all(axis=1)
    s = np.full(len(X_all), np.nan)
    if ok.any():
        s[ok] = md.score(artifact, X[ok])
    return s, ok


def d_scores(panel, i: int, base_ok: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    raw = panel.d_inputs()
    row = np.column_stack([raw[k].iloc[i].reindex(panel.tickers).to_numpy(dtype=float)
                           for k in ("a", "b", "c")])
    ok = base_ok & np.isfinite(row).all(axis=1)
    return fx.d_scores(row, ok), ok


def e_scores(panel, i: int, frame: pd.DataFrame, frame_year_ago: pd.DataFrame,
             base_ok: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """E1: mean of the percentile ranks of 12-1 momentum, gross profit / total assets
    and minus the split-adjusted growth in shares over a year; large, liquid shares."""
    p = panel.close
    mom = (p.shift(21) / p.shift(252) - 1).iloc[i].reindex(panel.tickers)
    f = frame.drop_duplicates("ticker").set_index("ticker").reindex(panel.tickers)
    raw_px = panel.raw.iloc[i].reindex(panel.tickers)
    cap = pd.to_numeric(f["shares_outstanding"], errors="coerce") * raw_px
    gpa = pd.to_numeric(f["gross_profit_ttm"], errors="coerce") / \
        pd.to_numeric(f["total_assets"], errors="coerce").where(lambda v: v > 0)
    prev = frame_year_ago.drop_duplicates("cik").set_index("cik")["shares_outstanding"]
    sh_prev = pd.to_numeric(f["cik"].map(prev), errors="coerce")
    # Shares on today's split basis: count x close_raw / close on that date. After a
    # 2:1 split the old 1,000 shares were traded at 100 raw = 50 adjusted: 1,000 x 2.
    j_prev = max(i - 252, 0)
    basis_now = (panel.raw.iloc[i] / panel.close.iloc[i]).reindex(panel.tickers)
    basis_prev = (panel.raw.iloc[j_prev] / panel.close.iloc[j_prev]).reindex(panel.tickers)
    growth = (pd.to_numeric(f["shares_outstanding"], errors="coerce") * basis_now) / \
        (sh_prev * basis_prev) - 1
    issuance = -growth
    dv = pd.to_numeric(f.get("median_dollar_volume_60d"), errors="coerce")
    ok = base_ok & (cap >= E_MIN_CAP).to_numpy() & (dv >= E_MIN_DOLLAR_VOLUME).to_numpy()
    parts = pd.DataFrame({"mom": mom, "gpa": gpa, "iss": issuance})
    ok &= np.isfinite(parts.to_numpy(dtype=float)).all(axis=1)
    ranks = parts[ok].rank(pct=True)
    s = np.full(len(panel.tickers), np.nan)
    s[ok] = ranks.mean(axis=1).to_numpy()
    return s, ok


def rare_candidates(panel, i: int) -> list[dict]:
    """F: a rule fires only on the session its condition switches from off to on."""
    out = []
    for t in panel.tickers:
        if t in panel.exclude:
            continue
        p = panel.close[t]
        inputs = fx.rare_inputs(p)
        ok = fx.rare_eligible(p, panel.raw[t])
        for rule, (side, feature, threshold) in RARE.items():
            v = inputs[feature]
            on = (v.le(threshold) if side == "long" else v.ge(threshold)) & ok
            if bool(on.iloc[i]) and not bool(on.iloc[i - 1]):
                out.append({"ticker": t, "rule": rule, "side": side,
                            "value": float(v.iloc[i])})
    return out
