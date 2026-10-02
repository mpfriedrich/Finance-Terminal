"""Macro data from FRED (Federal Reserve Bank of St. Louis) — the economic database.

FRED publishes every series as a plain CSV without a key, so it fits the rest of the app:
download once, store with dates, read offline. Each series is shown the way it is read
in practice, not as the raw level: inflation as the change on a year earlier, GDP as the
annualised change on the previous quarter, payrolls as the monthly change in jobs.

What is NOT here, and cannot be for free: consensus forecasts and the "surprise" against
them. The calendar of releases with expectations is a paid dataset; showing a surprise
without a consensus would be inventing one.

Dates are FRED's observation dates (the period the figure describes), not release dates.
A figure for June is published in July; nothing here is used in a backtest, and the
table says which period each number belongs to.

A few series come from Eurostat instead (EUROSTAT below), where FRED's copy stopped:
the OECD euro-area unemployment rate on FRED ends in January 2023. Eurostat's JSON API
needs no key either.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import urllib.parse

import duckdb
import pandas as pd

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
EUROSTAT_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"

# Series read from Eurostat: id -> (dataset, filters). The euro area's code follows its
# membership (EA20 until 2025, EA21 from 2026 with Bulgaria), so when it grows the old
# code stops updating — the board's `period` column then shows the series ageing, as it
# did for the FRED copy (stuck at 2023-01 until 02.10.2026).
EUROSTAT: dict[str, tuple[str, dict[str, str]]] = {
    "ESTAT_UNE_RT_M_EA21": ("une_rt_m", {"geo": "EA21", "s_adj": "SA", "age": "TOTAL",
                                         "sex": "T", "unit": "PC_ACT"}),
}

# id -> (label, group, how it is read, unit)
#   yoy   % change on the same period a year earlier
#   qoq_ann  % change on the previous quarter, annualised
#   diff  change on the previous observation (unit given)
#   level the value itself
SERIES: dict[str, tuple[str, str, str, str]] = {
    "CPIAUCSL": ("CPI inflation", "US inflation", "yoy", "%"),
    "CPILFESL": ("Core CPI (ex food & energy)", "US inflation", "yoy", "%"),
    "PCEPILFE": ("Core PCE (the Fed's measure)", "US inflation", "yoy", "%"),
    "PPIACO": ("Producer prices", "US inflation", "yoy", "%"),
    "GDPC1": ("Real GDP", "US growth", "qoq_ann", "%"),
    "INDPRO": ("Industrial production", "US growth", "yoy", "%"),
    "RSAFS": ("Retail sales", "US growth", "yoy", "%"),
    "HOUST": ("Housing starts", "US growth", "level", "k/yr"),
    "UMCSENT": ("Consumer sentiment (Michigan)", "US growth", "level", "index"),
    "UNRATE": ("Unemployment rate", "US labour", "level", "%"),
    "PAYEMS": ("Nonfarm payrolls", "US labour", "diff", "k jobs"),
    "CES0500000003": ("Average hourly earnings", "US labour", "yoy", "%"),
    "ICSA": ("Initial jobless claims", "US labour", "level", "claims"),
    "FEDFUNDS": ("Fed funds rate", "US money", "level", "%"),
    "M2SL": ("M2 money supply", "US money", "yoy", "%"),
    "T10Y2Y": ("10y − 2y Treasury spread", "US money", "level", "pts"),
    # Freight and passengers actually moving (Bureau of Transportation Statistics,
    # monthly, two to three months behind; checked 02.10.2026). Free daily train or flight
    # counts with a history do not exist; ships are in shipping.py.
    "RAILFRTCARLOADSD11": ("Rail freight carloads", "US transport", "yoy", "%"),
    "RAILFRTINTERMODALD11": ("Rail intermodal (containers)", "US transport", "yoy", "%"),
    "ENPLANED11": ("Airline passengers", "US transport", "yoy", "%"),
    "LOADFACTOR": ("Airline load factor", "US transport", "level", "%"),
    "AUSCPIALLQINMEI": ("Australia CPI", "Australia", "yoy", "%"),
    "LRUNTTTTAUM156S": ("Australia unemployment", "Australia", "level", "%"),
    "CP0000EZ19M086NEST": ("Euro area HICP inflation", "Euro area", "yoy", "%"),
    "ESTAT_UNE_RT_M_EA21": ("Euro area unemployment", "Euro area", "level", "%"),
}


def parse_csv(text: str, series: str) -> pd.DataFrame:
    """FRED's CSV: a date column and one value column; '.' marks a missing value."""
    frame = pd.read_csv(io.StringIO(text))
    if frame.empty or len(frame.columns) < 2:
        return pd.DataFrame(columns=["series_id", "date", "value"])
    frame.columns = ["date", "value"]
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame = frame.dropna(subset=["value"])
    frame.insert(0, "series_id", series)
    return frame


def parse_eurostat(text: str, series: str) -> pd.DataFrame:
    """Eurostat's JSON-stat answer for one filtered series: `value` maps the position of
    each period in `dimension.time` to its figure; periods without a figure are absent."""
    payload = json.loads(text)
    periods = payload["dimension"]["time"]["category"]["index"]
    values = payload.get("value", {})
    rows = [(series, pd.Timestamp(period).date(), float(values[str(pos)]))
            for period, pos in sorted(periods.items(), key=lambda kv: kv[1])
            if str(pos) in values]
    return pd.DataFrame(rows, columns=["series_id", "date", "value"])


def url_for(series: str) -> str:
    if series in EUROSTAT:
        dataset, filters = EUROSTAT[series]
        return f"{EUROSTAT_URL.format(dataset=dataset)}?{urllib.parse.urlencode(filters)}"
    return URL.format(series=series)


def download(series: str, getter=None, timeout: int = 60) -> pd.DataFrame:
    if getter is None:
        import requests

        def getter(url):
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
    text = getter(url_for(series))
    if series in EUROSTAT:
        return parse_eurostat(text, series)
    return parse_csv(text, series)


def store(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    series = frame.series_id.iloc[0]
    con.execute("DELETE FROM macro_series WHERE series_id = ?", [series])
    con.register("_m", frame)
    con.execute("INSERT INTO macro_series SELECT series_id, date, value FROM _m")
    con.unregister("_m")
    return len(frame)


def _periods_per_year(dates: pd.Series) -> int:
    gaps = pd.to_datetime(pd.Series(dates)).diff().dt.days.dropna()
    step = gaps.median() if len(gaps) else 30
    return 52 if step <= 8 else 12 if step <= 35 else 4 if step <= 100 else 1


def transformed(values: pd.Series, how: str) -> pd.Series:
    """The reading of a series (see SERIES). `values` indexed by date, oldest first."""
    s = values.dropna().astype(float)
    if how == "level":
        return s
    if how == "diff":
        return s.diff()
    per_year = _periods_per_year(pd.Series(s.index))
    if how == "yoy":
        return (s / s.shift(per_year) - 1.0) * 100.0
    if how == "qoq_ann":
        return ((s / s.shift(1)) ** per_year - 1.0) * 100.0
    raise ValueError(how)


def dashboard(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per stored series: its latest reading, the one before, and the period."""
    frame = con.execute(
        "SELECT series_id, date, value FROM macro_series ORDER BY series_id, date"
    ).fetch_df()
    rows = []
    for sid, (label, group, how, unit) in SERIES.items():
        g = frame[frame.series_id == sid]
        if g.empty:
            rows.append({"group": group, "series": label, "id": sid, "unit": unit,
                         "latest": None, "previous": None, "period": None})
            continue
        t = transformed(g.set_index("date")["value"], how).dropna()
        rows.append({
            "group": group, "series": label, "id": sid, "unit": unit,
            "latest": float(t.iloc[-1]) if len(t) else None,
            "previous": float(t.iloc[-2]) if len(t) > 1 else None,
            "period": t.index[-1] if len(t) else None,
        })
    return pd.DataFrame(rows)


def history(con: duckdb.DuckDBPyConnection, series: str, since: dt.date) -> pd.Series:
    """The transformed series from `since`, for a chart."""
    label, _, how, _ = SERIES[series]
    g = con.execute(
        "SELECT date, value FROM macro_series WHERE series_id = ? ORDER BY date", [series]
    ).fetch_df()
    if g.empty:
        return pd.Series(dtype=float, name=label)
    t = transformed(g.set_index("date")["value"], how)
    t.index = pd.to_datetime(t.index)
    return t[t.index >= pd.Timestamp(since)].rename(label)
