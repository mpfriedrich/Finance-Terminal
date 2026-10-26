"""Features of the research models, ported line by line so live scores match them.

Source (read-only reference, never edited): the research copy's experiments/price_ml/
model.py (`features`), experiments/price_ml_inputs/study.py and experiments/
factor_combinations/run.py (`base_features`, `peer_features`, `macro_asof`), experiments/
model_d/run.py and experiments/rare_signals_2011/run.py. Formulas, windows, `min_periods`
and the order of columns are kept exactly; only the plumbing differs. Every function
here is a pure function of prices known at the signal date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PRICE16 = ["return_1", "return_5", "return_20", "return_60", "return_120", "return_252",
           "vol_20", "vol_60", "vol_ratio", "drawdown_60", "drawdown_252", "distance_ma20",
           "distance_ma150", "range_60", "downside_vol60", "trend_20_minus60"]
REGIME = ["vol5_to60", "skew60", "return_kurt60", "max_abs_return20", "negative_fraction60",
          "market_return20", "market_vol20"]
BASE23 = PRICE16 + REGIME
PEER = ["peer_return20", "relative_peer20", "peer_return60", "relative_peer60",
        "peer_dispersion20", "peer_relative_reversal5"]
MACRO_NAMES = ["employment_change3", "employment_change12", "cpi_yoy", "core_cpi_yoy",
               "core_pce_yoy", "fed_level", "fed_change3"]
MACRO = [f"{x}{s}" for x in MACRO_NAMES for s in ["", "_x_beta", "_x_momentum60"]]
BLOCKS = {
    "peers": PEER,
    "employment": [c for c in MACRO if c.startswith("employment_")],
    "inflation": [c for c in MACRO if c.startswith(("cpi_", "core_cpi_", "core_pce_"))],
    "rates": [c for c in MACRO if c.startswith("fed_")],
}
# The research bit mask: peers 1, gold 2, employment 4, inflation 8, rates 16.
V13 = BASE23 + BLOCKS["peers"] + BLOCKS["employment"] + BLOCKS["inflation"]
V29 = V13 + BLOCKS["rates"]
MACRO_SERIES = ["EMRATIO", "CPIAUCSL", "CPILFESL", "PCEPILFE", "FEDFUNDS"]


def price16(p: pd.Series) -> pd.DataFrame:
    """experiments/price_ml/model.py `features`."""
    p = p.where(p > 0)
    log = np.log(p / p.shift())
    f = pd.DataFrame(index=p.index)
    for k in [1, 5, 20, 60, 120, 252]:
        f[f"return_{k}"] = p / p.shift(k) - 1
    f["vol_20"] = log.rolling(20, min_periods=20).std() * np.sqrt(252)
    f["vol_60"] = log.rolling(60, min_periods=60).std() * np.sqrt(252)
    f["vol_ratio"] = f.vol_20 / f.vol_60
    for k in [60, 252]:
        f[f"drawdown_{k}"] = p / p.rolling(k, min_periods=k).max() - 1
    f["distance_ma20"] = p / p.rolling(20, min_periods=20).mean() - 1
    f["distance_ma150"] = p / p.rolling(150, min_periods=150).mean() - 1
    lo = p.rolling(60, min_periods=60).min()
    hi = p.rolling(60, min_periods=60).max()
    f["range_60"] = (p - lo) / (hi - lo)
    f["downside_vol60"] = np.sqrt(log.clip(upper=0).pow(2).rolling(60, min_periods=60)
                                  .mean()) * np.sqrt(252)
    f["trend_20_minus60"] = f.return_20 - f.return_60 / 3
    return f[PRICE16].replace([np.inf, -np.inf], np.nan)


def base23(p: pd.Series, spy: pd.Series) -> pd.DataFrame:
    """factor_combinations/run.py `base_features` (= the 23 inputs of model B)."""
    p = p.where(p > 0)
    f = price16(p)
    r = p.pct_change(fill_method=None)
    lr = np.log(p / p.shift())
    br = spy.pct_change(fill_method=None)
    f["vol5_to60"] = lr.rolling(5).std() / lr.rolling(60).std()
    f["skew60"] = r.rolling(60).skew()
    f["return_kurt60"] = r.rolling(60).kurt()
    f["max_abs_return20"] = r.abs().rolling(20).max()
    f["negative_fraction60"] = r.lt(0).astype(float).where(r.notna()).rolling(60).mean()
    f["market_return20"] = spy / spy.shift(20) - 1
    f["market_vol20"] = br.rolling(20).std() * np.sqrt(252)
    return f[BASE23].replace([np.inf, -np.inf], np.nan)


def eligible(p: pd.Series, raw: pd.Series, vol60: pd.Series,
             history: pd.Series | None = None) -> pd.Series:
    """The research eligibility: >= 252 observations, traded price >= 5, >= 45 changing
    closes in 60 sessions, 60-day volatility 1 %-200 %. `history` = observations counted
    from the start of the full price history (the panel may be a window of it)."""
    p = p.where(p > 0)
    raw = raw.where(raw > 0).fillna(p)
    count = p.notna().cumsum() if history is None else history
    changing = np.log(p / p.shift()).abs().gt(1e-8).rolling(60, min_periods=60).sum()
    return (count >= 252) & (raw >= 5) & changing.ge(45) & vol60.between(.01, 2)


def peer_features(close: pd.DataFrame, sic: dict[str, str]) -> dict:
    """factor_combinations/run.py `peer_features`: mean return of the OTHER shares in the
    same two-digit SIC group (at least 5 with values), and their dispersion."""
    labels = {t: str(sic.get(t, "") or "")[:2] for t in close.columns}
    result: dict = {}
    returns = {k: close / close.shift(k) - 1 for k in [5, 20, 60]}
    for label in sorted(set(labels.values())):
        names = [t for t in close if labels[t] == label]
        if len(names) < 6 or not label.isdigit():
            continue
        for k, r in returns.items():
            g = r[names]
            count = g.notna().sum(axis=1)
            total = g.sum(axis=1)
            sq = g.pow(2).sum(axis=1)
            for t in names:
                n = count - g[t].notna().astype(int)
                mean = (total - g[t].fillna(0)) / n
                mean = mean.where(n >= 5)
                result.setdefault(t, {})[k] = mean
                if k == 20:
                    result[t]["dispersion"] = np.sqrt(
                        ((sq - g[t].fillna(0).pow(2)) / n - mean.pow(2)).clip(lower=0))
    return result


def add_peers(f: pd.DataFrame, peers: dict | None) -> pd.DataFrame:
    if peers is None:
        for col in PEER:
            f[col] = np.nan
        return f
    f["peer_return20"] = peers[20]
    f["relative_peer20"] = f.return_20 - peers[20]
    f["peer_return60"] = peers[60]
    f["relative_peer60"] = f.return_60 - peers[60]
    f["peer_dispersion20"] = peers["dispersion"]
    f["peer_relative_reversal5"] = f.return_5 - peers[5]
    return f


def macro_asof(calendar: pd.DatetimeIndex, vintages: pd.DataFrame) -> pd.DataFrame:
    """factor_combinations/run.py `macro_asof`: on each day only the vintages available
    by the PREVIOUS calendar day (no same-day release use)."""
    v = vintages.copy()
    v["period"] = pd.to_datetime(v.observation_date)
    rows = []
    for day in calendar:
        cutoff = str((day - pd.Timedelta(days=1)).date())
        known = v[(v.available_from.astype(str) <= cutoff)
                  & (v.available_through.astype(str) >= cutoff)]
        series = {sid: g.set_index("period").value.sort_index()
                  for sid, g in known.groupby("series_id")}

        def change(sid, k, ratio=False, series=series):
            z = series.get(sid, pd.Series(dtype=float))
            if z.empty:
                return np.nan
            last = z.index[-1]
            previous = z.get(last - pd.DateOffset(months=k), np.nan)
            return (z.iloc[-1] / previous - 1) * 100 if ratio else z.iloc[-1] - previous

        rate = series.get("FEDFUNDS", pd.Series(dtype=float))
        rows.append([change("EMRATIO", 3), change("EMRATIO", 12),
                     change("CPIAUCSL", 12, True), change("CPILFESL", 12, True),
                     change("PCEPILFE", 12, True), rate.iloc[-1] if len(rate) else np.nan,
                     change("FEDFUNDS", 3)])
    return pd.DataFrame(rows, index=calendar, columns=MACRO_NAMES)


def add_macro(f: pd.DataFrame, p: pd.Series, spy: pd.Series,
              macro: pd.DataFrame) -> pd.DataFrame:
    """Macro levels and their interactions with the share's 126-day beta and 60-day
    return, as in factor_combinations/run.py."""
    r = p.where(p > 0).pct_change(fill_method=None)
    spyret = spy.pct_change(fill_method=None)
    beta = r.rolling(126).cov(spyret) / spyret.rolling(126).var()
    for col in MACRO_NAMES:
        f[col] = macro[col]
        f[col + "_x_beta"] = macro[col] * beta
        f[col + "_x_momentum60"] = macro[col] * f.return_60
    return f


# -- D: momentum and peer reversion (experiments/model_d/run.py) ------------------------

D_FEATURES = ["momentum252_ex21_vol60_z", "momentum126_ex21_vol60_z",
              "relative_peer20_vol60_z"]
D_WEIGHTS = np.array([.5, .3, -.2])


def d_raw(close: pd.DataFrame, sic: dict[str, str]) -> dict[str, pd.DataFrame]:
    """The three raw inputs of D, each divided by the annualised 60-day volatility."""
    p = close.where(close > 0)
    vol = np.log(p / p.shift()).rolling(60).std() * np.sqrt(252)
    a = (p.shift(21) / p.shift(252) - 1) / vol
    b = (p.shift(21) / p.shift(126) - 1) / vol
    returns = p / p.shift(20) - 1
    peers = pd.DataFrame(np.nan, index=p.index, columns=p.columns)
    labels = {t: str(sic.get(t, "") or "")[:2] for t in p.columns}
    for label in sorted(set(labels.values())):
        group = [t for t in p.columns if labels[t] == label]
        if len(group) < 6 or not label.isdigit():
            continue
        g = returns[group]
        n = g.notna().sum(axis=1)
        total = g.sum(axis=1)
        for t in group:
            others = n - g[t].notna().astype(int)
            peers[t] = ((total - g[t].fillna(0)) / others).where(others >= 5)
    c = (returns - peers) / vol
    return {"a": a, "b": b, "c": c, "vol": vol}


def d_scores(raw_row: np.ndarray, eligible_row: np.ndarray) -> np.ndarray:
    """Standardise each input over the eligible shares of the signal day, clip to +-3,
    weight 0.5 / 0.3 / -0.2 (model_d `standardize` and fixed weights)."""
    masked = np.where(eligible_row[:, None], raw_row, np.nan)
    count = np.sum(np.isfinite(masked), axis=0, keepdims=True)
    mu = np.nansum(masked, axis=0, keepdims=True) / np.maximum(count, 1)
    sd = np.sqrt(np.nansum((masked - mu) ** 2, axis=0, keepdims=True) / np.maximum(count, 1))
    z = np.clip((raw_row - mu) / np.maximum(sd, 1e-8), -3, 3)
    z = np.where(eligible_row[:, None], z, np.nan)
    return z @ D_WEIGHTS


# -- F: extreme reversion inputs (experiments/rare_signals_2011/run.py) ------------------

def rare_inputs(p: pd.Series) -> dict[str, pd.Series]:
    p = p.where(p > 0)
    lr = np.log(p / p.shift())
    vol = lr.rolling(60).std() * np.sqrt(252)
    return {"ret1": p / p.shift() - 1, "ret5": p / p.shift(5) - 1, "vol60": vol}


def rare_eligible(p: pd.Series, raw: pd.Series, history: pd.Series | None = None
                  ) -> pd.Series:
    """rare_signals_2011: 252 observations, 45 changing closes in 60, vol60 1 %-200 %,
    traded price proxy >= 5. Same as `eligible`, with rolling windows without
    min_periods as in that script."""
    p = p.where(p > 0)
    lr = np.log(p / p.shift())
    vol = lr.rolling(60).std() * np.sqrt(252)
    count = p.notna().cumsum() if history is None else history
    return ((count >= 252) & lr.abs().gt(1e-8).rolling(60).sum().ge(45)
            & vol.between(.01, 2) & raw.where(raw > 0).fillna(p).ge(5))
