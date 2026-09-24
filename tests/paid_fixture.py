"""Hand-made files in the Sharadar layout, for testing the Phase 10 translation.

No subscription exists, so this is the only Sharadar data the tests will ever see. The
layout follows the published table definitions; the values are chosen so every rule in
paid.py has something to catch:

  * AAA, BBB, CCC, EEE — ordinary common stocks, liquid, recently filed.
  * DDD1 — delisted mid-window: the company the free stack cannot supply.
  * NEG — negative equity.
  * OLD — last filed eight months before the end: visible, but past the freshness floor.
  * ETF1 — a fund. Not a company; the translation must leave it out.
  * AAA's FY figures also appear under MRY with a restated value. MR* is "most recent",
    i.e. restated; loading it would be look-ahead.
"""
from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pandas as pd

END = dt.date(2026, 6, 30)
DAYS = pd.bdate_range("2025-01-02", END).date
DELISTED_ON = dt.date(2025, 9, 30)

COMPANIES = {
    # ticker: (permaticker, name, category, siccode, sicindustry, start price)
    "AAA": (101, "Alpha Corp", "Domestic Common Stock", 3550, "Special Industry Machinery", 20.0),
    "BBB": (102, "Beta Inc", "Domestic Common Stock Primary Class", 2810, "Industrial Inorganic Chemicals", 40.0),
    "CCC": (103, "Gamma Co", "Domestic Common Stock", 3550, "Special Industry Machinery", 12.0),
    "EEE": (105, "Epsilon Foods", "Domestic Common Stock", 2000, "Food and Kindred Products", 30.0),
    "DDD1": (104, "Delta Mining (delisted)", "Domestic Common Stock", 1040, "Gold and Silver Ores", 15.0),
    "NEG": (106, "Negative Equity Inc", "Domestic Common Stock", 3550, "Special Industry Machinery", 9.0),
    "OLD": (107, "Old Filer Corp", "Domestic Common Stock", 2000, "Food and Kindred Products", 25.0),
    "ETF1": (108, "Some Index Fund", "ETF", None, None, 50.0),
}

# (ticker, equity, shares (in sharefactor units), sharefactor, revenue per quarter,
#  net income per quarter, ocf per quarter, capex per quarter (negative, as Sharadar),
#  last quarter filed)
FUNDAMENTALS = [
    ("AAA", 900e6, 100e6, 1, 250e6, 30e6, 40e6, -10e6, dt.date(2026, 3, 31)),
    ("BBB", 5e9, 400e3, 1000, 800e6, 50e6, 70e6, -20e6, dt.date(2026, 3, 31)),
    ("CCC", 2e9, 200e6, 1, 300e6, 20e6, 25e6, -5e6, dt.date(2026, 3, 31)),
    ("EEE", 3e9, 250e6, 1, 500e6, 100e6, 120e6, -30e6, dt.date(2026, 3, 31)),
    ("DDD1", 800e6, 150e6, 1, 100e6, 5e6, 8e6, -2e6, dt.date(2025, 6, 30)),
    ("NEG", -200e6, 50e6, 1, 60e6, -10e6, -5e6, -1e6, dt.date(2026, 3, 31)),
    ("OLD", 700e6, 80e6, 1, 90e6, 9e6, 10e6, -3e6, dt.date(2025, 9, 30)),
]
RESTATED_AAA_EQUITY = 1_400e6


def _quarter_ends(last: dt.date, n: int = 8) -> list[dt.date]:
    ends = []
    for k in range(n):
        month = last.month - 3 * k
        year = last.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        ends.append((pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)).date())
    return sorted(ends)


def tickers_frame() -> pd.DataFrame:
    rows = []
    for ticker, (perma, name, category, sic, sicind, _) in COMPANIES.items():
        last = DELISTED_ON if ticker == "DDD1" else END
        for table in ("SEP", "SF1"):
            rows.append({
                "table": table, "permaticker": perma, "ticker": ticker, "name": name,
                "exchange": "NYSE", "isdelisted": "Y" if ticker == "DDD1" else "N",
                "category": category, "siccode": sic, "sicsector": None,
                "sicindustry": sicind, "firstpricedate": DAYS[0], "lastpricedate": last,
            })
    return pd.DataFrame(rows)


def sf1_frame() -> pd.DataFrame:
    rows = []
    for ticker, equity, shares, factor, rev, ni, ocf, capex, last in FUNDAMENTALS:
        for end in _quarter_ends(last):
            filed = end + dt.timedelta(days=40)
            base = {
                "ticker": ticker, "calendardate": end, "reportperiod": end,
                "datekey": filed, "fiscalperiod": f"{end.year}-Q", "lastupdated": filed,
                "equity": equity, "assets": abs(equity) * 2, "liabilities": abs(equity),
                "debtnc": 100e6, "debtc": 10e6, "cashneq": 50e6, "assetsc": 200e6,
                "liabilitiesc": 90e6, "sharesbas": shares, "sharefactor": factor,
            }
            rows.append({**base, "dimension": "ARQ", "revenue": rev, "netinc": ni,
                         "ncfo": ocf, "capex": capex})
            if end.month == 12:
                year = {**base, "dimension": "ARY", "revenue": 4 * rev, "netinc": 4 * ni,
                        "ncfo": 4 * ocf, "capex": 4 * capex}
                rows.append(year)
                if ticker == "AAA":
                    rows.append({**year, "dimension": "MRY", "equity": RESTATED_AAA_EQUITY,
                                 "datekey": filed})
    return pd.DataFrame(rows)


def sep_frame() -> pd.DataFrame:
    rows = []
    for ticker, (*_, start) in COMPANIES.items():
        price = start
        for i, d in enumerate(DAYS):
            if ticker == "DDD1" and d > DELISTED_ON:
                break
            close = price * (1.0 + 0.0002 + 0.003 * math.sin(i / 5.0))
            open_ = price * 1.0001
            rows.append({
                "ticker": ticker, "date": d, "open": round(open_, 4),
                "high": round(max(open_, close) * 1.01, 4),
                "low": round(min(open_, close) * 0.99, 4), "close": round(close, 4),
                "volume": 400_000, "closeadj": round(close, 4),
                "closeunadj": round(close, 4), "lastupdated": d,
            })
            price = close
    return pd.DataFrame(rows)


def write_files(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, frame in (("TICKERS", tickers_frame()), ("SF1", sf1_frame()),
                        ("SEP", sep_frame())):
        path = directory / f"{name}.csv"
        frame.to_csv(path, index=False)
        files[name] = path
    return files


def build_store(directory: Path):
    """A PaidSource over a store translated from the files above."""
    import duckdb

    from portfolio_lab.data import paid
    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    files = write_files(directory)
    con = duckdb.connect(str(directory / "paid.duckdb"))
    _apply(con, MARKET_MIGRATIONS)
    paid.ingest(con, files["TICKERS"], files["SF1"], files["SEP"])
    return paid.PaidSource(con)
