"""Ridge models of portfolios A, B and C: research artifacts in, live scores out.

The numeric core (`scaler_fit`, `transform`, `ridge_fit`, `predict`, `sample_training`)
is copied from the research copy's experiments/price_ml/model.py and train.py without
change: 1 %/99 % clipping estimated on the training rows only, standardisation, ridge
with the target centred, at most 256 training rows per signal date (seed 62025).

  * B loads results/price_ml_inputs/frozen_model.json exactly (features, impute,
    scaler, coefficients). It is never refitted.
  * C starts from results/rolling_windows/10y/annual_models.json, year 2026, variant v13
    (trained on 2016-2025, outcomes before 2026-01-01). Each January it is refitted on
    the previous ten calendar years by `train_window`, the same recipe.
  * A (v29) is fitted at the start on the ten years before the implementation date and
    refitted every twelve months by the same function.
The three research inputs (B's model, C's yearly models, the ALFRED vintages) are copied
on first use into config.FORWARD_INPUTS (data/forward_inputs/) with their SHA-256 in
manifest.json, and read from there afterwards: a later change or removal of the research
folder cannot change or stop a running book, and a copy that no longer matches its hash
is refused. Audit 24.10.2026 F11: every run read the research folder directly, and the
docstring claimed a copy that did not exist.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import features as fx

SEED = 62025
# The research copy. Read only to make the first copy of INPUTS (and by the app for the
# historical research curves, which are display only).
RESEARCH = Path("/Users/moritzfriedrich/.codex/.chatgpt-projects/"
                "g-p-6acf80a14d708191bee8657f87aa1ab2/portfolio-lab-research")
INPUTS = {"b": "results/price_ml_inputs/frozen_model.json",
          "c": "results/rolling_windows/10y/annual_models.json",
          "alfred": "results/macro_factors/alfred/macro_realtime_periods.parquet"}
# Which inputs each model book needs; D, E and F need none.
NEEDS = {"fw-a": ("alfred",), "fw-b": ("b",), "fw-c": ("c", "alfred")}


class MissingInput(FileNotFoundError):
    """A research input is neither copied yet nor in the research folder."""


def inputs_dir() -> Path:
    from .. import config

    return Path(config.FORWARD_INPUTS)


def stored(name: str) -> Path:
    """The local copy of research input `name`, made on first use.

    The copy goes through a temporary name and a rename, so an interrupted copy never
    looks complete; its SHA-256 is recorded then, and checked on every later use."""
    rel = INPUTS[name]
    d = inputs_dir()
    dst = d / Path(rel).name
    manifest = d / "manifest.json"
    known = json.loads(manifest.read_text()) if manifest.exists() else {}
    if dst.exists():
        want = known.get(name, {}).get("sha256")
        if want and sha256(dst) != want:
            raise ValueError(f"{name}: the stored copy no longer matches its SHA-256")
        return dst
    src = RESEARCH / rel
    if not src.exists():
        raise MissingInput(f"{name}: research input {Path(rel).name} is missing "
                           "(no stored copy, not in the research folder)")
    d.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partial")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)
    known[name] = {"file": dst.name, "from": rel, "sha256": sha256(dst),
                   "copied_utc": dt.datetime.now(dt.UTC).isoformat()}
    manifest.write_text(json.dumps(known, indent=1))
    return dst


def available(name: str) -> bool:
    try:
        stored(name)
        return True
    except (MissingInput, ValueError):
        return False


# -- numeric core, unchanged from the research ------------------------------------------

def scaler_fit(X: np.ndarray) -> dict:
    lo = np.quantile(X, .01, axis=0)
    hi = np.quantile(X, .99, axis=0)
    z = np.clip(X, lo, hi)
    return {"lo": lo.tolist(), "hi": hi.tolist(), "mean": z.mean(axis=0).tolist(),
                "std": np.maximum(z.std(axis=0), 1e-6).tolist()}


def transform(X: np.ndarray, s: dict) -> np.ndarray:
    return (np.clip(X, np.asarray(s["lo"]), np.asarray(s["hi"])) - s["mean"]) / s["std"]


def ridge_fit(X: np.ndarray, y: np.ndarray, alpha: float) -> dict:
    center = float(y.mean())
    coef = np.linalg.solve(X.T @ X / len(X) + alpha * np.eye(X.shape[1]),
                           X.T @ (y - center) / len(X))
    return {"kind": "ridge", "alpha": alpha, "intercept": center, "coef": coef.tolist()}


def predict(model: dict, X: np.ndarray) -> np.ndarray:
    return model["intercept"] + X @ np.asarray(model["coef"])


def sample_training(ids: np.ndarray, dates: np.ndarray, max_per_date: int = 256
                    ) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    chosen = []
    for day in np.unique(dates[ids]):
        available = ids[dates[ids] == day]
        chosen.extend(rng.choice(available, min(max_per_date, len(available)),
                                 replace=False))
    return np.sort(chosen)


# -- artifacts --------------------------------------------------------------------------

def _check(condition: bool, message: str) -> None:
    """An explicit raise, not `assert`: asserts vanish under `python -O`, and these guard
    the books against a wrong artefact and against look-ahead (audit 24.10.2026 F21)."""
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_b(path: Path | None = None) -> dict:
    a = json.loads((path or stored("b")).read_text())
    _check(a["features"] == fx.BASE23, "B: feature order differs from the 23 inputs")
    _check(a["model"]["kind"] == "ridge" and a["model"]["alpha"] == 1.0,
           "B: not the ridge model with alpha 1")
    _check(a["latest_training_label"] < "2025-01-01" and not a["post2024_prices_loaded"],
           "B: trained on data after 2024")
    return a


def load_c_2026(path: Path | None = None) -> dict:
    years = json.loads((path or stored("c")).read_text())
    y = years["2026"]
    v = y["models"]["v13"]
    _check(v["features"] == fx.V13, "C: v13 feature order differs from the research spec")
    _check(y["latest_outcome"] < "2026-01-01", "C: an outcome of 2026 or later is in it")
    return {"features": v["features"], "scaler": v["scaler"], "model": v["model"],
            "window_start": y["window_start"], "latest_outcome": y["latest_outcome"],
            "samples": y["samples"], "source_year": "2026", "variant": "v13"}


def score(artifact: dict, X: np.ndarray) -> np.ndarray:
    """Model score for rows of features in `artifact['features']` order. B imputes only
    its regime inputs (the research did the same); every other gap was excluded before."""
    if "impute" in artifact:
        imp = {k: v for k, v in zip(artifact["features"], artifact["impute"], strict=True)
               if k not in fx.PRICE16}
        X = X.copy()
        for j, name in enumerate(artifact["features"]):
            if name in imp:
                X[:, j] = np.where(np.isfinite(X[:, j]), X[:, j], imp[name])
    return predict(artifact["model"], transform(X, artifact["scaler"]))


# -- training (A at the start and yearly, C each January) -----------------------------

def training_rows(panel, columns: list[str], window_start: dt.date, cutoff: dt.date,
                  log=print) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Weekly signal rows (every 5th session from the window start) with the research
    target: the share's open-to-open return from session +1 to +6 minus SPY's, known in
    full before `cutoff`. Macro inputs come from vintages known the day before."""
    cal = panel.calendar
    first = int(np.searchsorted(cal, pd.Timestamp(window_start)))
    sig = np.arange(first, len(cal), 5)
    sig = sig[sig + 6 < len(cal)]
    sig = sig[cal[sig + 6] < pd.Timestamp(cutoff)]
    spy = panel.spy_close
    bo = panel.spy_open_adj
    by = (bo.shift(-6) / bo.shift(-1)).to_numpy()
    X, ys, ds, ends = [], [], [], []
    for n, t in enumerate(panel.tickers):
        f = panel.model_frame(t, columns, spy)
        ok = panel.eligible(t).to_numpy()[sig] & np.isfinite(f.to_numpy()[sig]).all(axis=1)
        opening = panel.open_adj(t)
        y = (opening.shift(-6) / opening.shift(-1)).to_numpy()[sig] - by[sig]
        ok &= np.isfinite(y) & (opening.to_numpy()[sig + 1] > 0) & \
            (opening.to_numpy()[sig + 6] > 0)
        if ok.any():
            X.append(f.to_numpy(dtype=np.float32)[sig][ok])
            ys.append(y[ok])
            ds.append(cal[sig[ok]].to_numpy())
            ends.append(cal[sig[ok] + 6].to_numpy())
        if n % 1000 == 0:
            log(f"training features {n}/{len(panel.tickers)}")
    return (np.concatenate(X), np.concatenate(ys), np.concatenate(ds),
            np.concatenate(ends))


def train_window(panel, columns: list[str], window_start: dt.date, cutoff: dt.date,
                 variant: str, log=print) -> dict:
    X, y, dates, ends = training_rows(panel, columns, window_start, cutoff, log)
    # The look-ahead guard of the training: an explicit raise, not `assert`, which
    # `python -O` drops (audit 24.10.2026 F21).
    _check(bool(len(ends)) and ends.max() < np.datetime64(cutoff),
           "a training outcome is not yet known at the cutoff")
    ids = sample_training(np.arange(len(y)), dates)
    sc = scaler_fit(X[ids])
    m = ridge_fit(transform(X[ids], sc), np.clip(y[ids], -.25, .25), 1.0)
    return {"features": columns, "scaler": sc, "model": m, "variant": variant,
            "window_start": str(window_start), "outcomes_before": str(cutoff),
            "earliest_signal": str(pd.Timestamp(dates[ids].min()).date()),
            "latest_outcome": str(pd.Timestamp(ends[ids].max()).date()),
            "samples": len(ids), "rows": len(y),
            "created_utc": dt.datetime.now(dt.UTC).isoformat()}
