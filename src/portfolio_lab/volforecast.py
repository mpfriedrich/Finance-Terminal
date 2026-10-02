"""How much a share is likely to move over the next 20 sessions: a pooled Log-HAR model.

This forecasts RISK, not direction. It says how wide the plausible range is, never which
way the price goes, and the range is centred on today's close on purpose. Three price
models were tested out of sample on 02.10.2026 and none forecast direction better than
"no change" (Kronos, TimesFM 3.0, a Claude agent; PLAN.md → „Geprüft und verworfen“).
The width is different. It is persistent and can be forecast, and this model does it
measurably better than the trailing 60-day volatility the app showed before.

The model (Corsi's HAR, in logs, plus a one-year term):

    log RV(next 20) = b0 + b1·log RV(1) + b2·log RV(5) + b3·log RV(22) + b4·log RV(250)

RV(n) is the mean squared daily log return over the last n sessions. Daily closes only:
no intraday data, so "realised variance" is the daily-return proxy. One set of
coefficients is fitted on ~200 liquid US shares at once. A per-share fit was not tested
and is not offered. Back from logs with Duan's smearing factor, so the forecast is a
mean variance and not a median.

The 80 % range does not use the normal curve's 1.28 standard deviations. It uses the
multiplier that held 80 % of 20-day moves in the fitting data, because real returns
have fatter tails. Measured 2013–2026 on 200 US shares (126,480 forecasts, origins every
5 sessions, refitted every 250 sessions on past data only):

    QLIKE 0.397 vs 0.482 for 60-day volatility (−18 %), rank correlation with the
    realised variance 0.736 vs 0.710, range held 78 % (78 % since 07/2024).

Untested: non-US lines (the world store's Compustat Global shares), and anything but a
20-session horizon.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import duckdb
import numpy as np
import pandas as pd

HORIZON = 20
WINDOWS = (1, 5, 22, 250)
EPS = 1e-8              # keeps log() finite on a day without a move
MIN_ROWS = 5000         # below this the pooled fit is not trusted
START = "2010-01-01"
STALE_DAYS = 5         # unchanged closes allowed in 22 sessions before a window is a gap


@dataclasses.dataclass(frozen=True)
class Model:
    beta: tuple[float, ...]     # intercept, then one coefficient per WINDOWS entry
    smear: float                # Duan's factor: E[exp(residual)]
    z80: float                  # multiplier that held 80 % of past 20-day moves
    rows: int
    series: int
    asof: dt.date


def features(closes: pd.Series) -> pd.DataFrame:
    """Per-date regressors, the 20-session target and the 20-session log return.

    The target and the forward return are NaN for the last 20 rows. A series cut at the
    as-of date can therefore never train on a window that ends after it.
    """
    s = closes.dropna().astype(float)
    s = s[s > 0]
    r = np.log(s).diff()
    r2 = r ** 2
    out = pd.DataFrame(index=s.index)
    for w in WINDOWS:
        out[f"l{w}"] = np.log(r2.rolling(w).mean() + EPS)
    ahead = r2[::-1].rolling(HORIZON).mean()[::-1].shift(-1)       # sessions t+1..t+20
    out["target"] = np.log(ahead + EPS)
    out["fwd"] = np.log(s).shift(-HORIZON) - np.log(s)
    # A price that does not move for days is a data gap (pre-listing filler, a feed that
    # stopped), not a calm share. On 02.10.2026 one sampled line had 1,741 such days; at
    # the EPS floor their log variance pulled Duan's factor to 9.7 and AAPL's forecast to
    # 74 % a year. Such windows neither train the model nor get a forecast.
    flat = (r == 0).astype(float)
    behind = flat.rolling(22).sum() > STALE_DAYS
    ahead_flat = flat[::-1].rolling(HORIZON).sum()[::-1].shift(-1) > STALE_DAYS
    out["stale"] = behind
    out.loc[behind | ahead_flat, ["target", "fwd"]] = np.nan
    return out


def _design(f: pd.DataFrame) -> np.ndarray:
    return np.c_[np.ones(len(f)), f[[f"l{w}" for w in WINDOWS]].to_numpy()]


def fit(panel: dict[str, pd.Series], asof: dt.date) -> Model | None:
    """One pooled fit on every series, each cut at `asof` before anything is computed."""
    cut = pd.Timestamp(asof)
    rows = [features(s[s.index <= cut]) for s in panel.values()]
    rows = [f.drop(columns="stale").dropna() for f in rows if len(f)]
    train = pd.concat(rows) if rows else pd.DataFrame()
    if len(train) < MIN_ROWS:
        return None
    X = _design(train)
    beta = np.linalg.lstsq(X, train.target.to_numpy(), rcond=None)[0]
    resid = train.target.to_numpy() - X @ beta
    smear = float(np.mean(np.exp(resid)))
    var = np.exp(X @ beta) * smear
    z80 = float(np.quantile(np.abs(train.fwd.to_numpy()) / np.sqrt(var * HORIZON), 0.8))
    return Model(tuple(float(b) for b in beta), smear, z80, len(train),
                 sum(1 for f in rows if len(f)), asof)


def forecast(model: Model, closes: pd.Series, asof: dt.date | None = None) -> dict | None:
    """Volatility forecast and the 80 % range for the close 20 sessions after `asof`."""
    s = closes.dropna()
    if asof is not None:
        s = s[s.index <= pd.Timestamp(asof)]
    if len(s) <= max(WINDOWS):
        return None
    last = features(s).iloc[-1]
    if last["stale"] or last[[f"l{w}" for w in WINDOWS]].isna().any():
        return None
    x = np.r_[1.0, [last[f"l{w}"] for w in WINDOWS]]
    var = float(np.exp(x @ np.array(model.beta)) * model.smear)        # daily variance
    sigma_h = np.sqrt(var * HORIZON)
    price = float(s.iloc[-1])
    r = np.log(s.astype(float)).diff()
    return {
        "volatility": np.sqrt(var * 252),
        "trailing_60d": float(r.tail(60).std() * np.sqrt(252)),
        "low": price * np.exp(-model.z80 * sigma_h),
        "high": price * np.exp(model.z80 * sigma_h),
        "price": price,
        "date": s.index[-1].date(),
    }


def sample_tickers(con: duckdb.DuckDBPyConnection, asof: dt.date, n: int = 200) -> list[str]:
    """~n liquid US shares with three years of history, the same kind the model was
    measured on. Picked by a hash of the ticker, so the set is stable between runs."""
    return [t for (t,) in con.execute(f"""
        WITH recent AS (
            SELECT ticker, median(close_raw * volume) dv, arg_max(close_raw, date) px
            FROM prices WHERE date BETWEEN ?::DATE - INTERVAL 90 DAY AND ?::DATE
            GROUP BY ticker),
        span AS (SELECT ticker, min(date) d0 FROM prices GROUP BY ticker)
        SELECT r.ticker FROM recent r JOIN span USING (ticker)
        WHERE r.ticker IN (SELECT ticker FROM securities)
          AND r.ticker NOT LIKE '%.%' AND r.ticker NOT LIKE '^%' AND r.ticker NOT LIKE '%=%'
          AND r.dv > 5e6 AND r.px > 5 AND span.d0 <= ?::DATE - INTERVAL 3 YEAR
        ORDER BY hash(r.ticker) LIMIT {int(n)}
    """, [asof, asof, asof]).fetchall()]


def closes(con: duckdb.DuckDBPyConnection, tickers: list[str]) -> dict[str, pd.Series]:
    if not tickers:
        return {}
    px = con.execute(
        "SELECT ticker, date, close_adj FROM prices WHERE ticker IN (SELECT UNNEST(?)) "
        "AND date >= ? AND close_adj > 0 ORDER BY ticker, date", [tickers, START],
    ).fetch_df()
    px["date"] = pd.to_datetime(px["date"])
    return {t: g.set_index("date")["close_adj"] for t, g in px.groupby("ticker")}


def fit_from_store(con: duckdb.DuckDBPyConnection, asof: dt.date, n: int = 200) -> Model | None:
    return fit(closes(con, sample_tickers(con, asof, n)), asof)


def for_ticker(con: duckdb.DuckDBPyConnection, model: Model, ticker: str,
               asof: dt.date | None = None) -> dict | None:
    series = closes(con, [ticker]).get(ticker)
    return None if series is None else forecast(model, series, asof)
