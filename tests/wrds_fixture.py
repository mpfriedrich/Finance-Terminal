"""Hand-made files in the Compustat Global layout, for testing the WRDS translation.

The build environment cannot reach WRDS, so this is the only Compustat data the tests
see. Each company is there to catch one rule in data/wrds.py:

  * DMA.DEU — German, prices in EUR, a 2:1 split on 2026-03-02, a dividend on
    2025-06-02, annual reports only (FY2024 book 4,000m, FY2025 book 5,000m). Also a
    preferred line (tpci '1') that must not be loaded.
  * 6701.JPN — Japanese, prices in JPY, quarterly reports with a report date (rdq)
    45 days after the quarter.
  * BPP.GBR — British, prices in pence (GBX): 250 pence is 2.50 pounds.
  * GONE.FRA — French, stopped trading on 2025-09-30.

Rates are quoted per pound, as in g_exrt_dly: USD 1.25, EUR 1.15, JPY 190.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

END = dt.date(2026, 6, 30)
DAYS = pd.bdate_range("2025-01-02", END).date
SPLIT_ON = dt.date(2026, 3, 2)
DIVIDEND_ON = dt.date(2025, 6, 2)
GONE_ON = dt.date(2025, 9, 30)
PER_GBP = {"USD": 1.25, "EUR": 1.15, "JPY": 190.0, "GBP": 1.0}
EURUSD = PER_GBP["USD"] / PER_GBP["EUR"]
JPYUSD = PER_GBP["USD"] / PER_GBP["JPY"]
GBPUSD = PER_GBP["USD"]


def company() -> pd.DataFrame:
    return pd.DataFrame([
        {"gvkey": "100001", "conm": "DEUTSCHE MASCHINEN AG", "loc": "DEU", "fic": "DEU",
         "sic": "3550", "prirow": "01W"},
        {"gvkey": "100002", "conm": "NIPPON DENKI KK", "loc": "JPN", "fic": "JPN",
         "sic": "3670", "prirow": "01W"},
        {"gvkey": "100003", "conm": "BRITISH PENCE PLC", "loc": "GBR", "fic": "GBR",
         "sic": "5400", "prirow": "01W"},
        {"gvkey": "100004", "conm": "GONE SA", "loc": "FRA", "fic": "FRA",
         "sic": "1040", "prirow": "01W"},
    ])


def security() -> pd.DataFrame:
    rows = [
        ("100001", "01W", "DMA", "DE0000000001", "DEU", "0", "A", None),
        ("100001", "02W", "DMAP", "DE0000000002", "DEU", "1", "A", None),
        ("100002", "01W", "6701", "JP0000000001", "JPN", "0", "A", None),
        ("100003", "01W", "BPP", "GB0000000001", "GBR", "0", "A", None),
        ("100004", "01W", "GONE", "FR0000000001", "FRA", "0", "I", GONE_ON),
    ]
    return pd.DataFrame(rows, columns=["gvkey", "iid", "tic", "isin", "excntry", "tpci",
                                       "secstat", "dldtei"])


def secd() -> pd.DataFrame:
    rows = []
    for d in DAYS:
        # DMA: 100 EUR before the split, 50 after; ajexdi 2 before, 1 after, so the
        # split-adjusted price is 50 throughout. A 2% dividend lifts trfd once.
        pre = d < SPLIT_ON
        rows.append({"gvkey": "100001", "iid": "01W", "datadate": d,
                     "prccd": 100.0 if pre else 50.0, "prcod": 100.0 if pre else 50.0,
                     "prchd": 101.0 if pre else 50.5, "prcld": 99.0 if pre else 49.5,
                     "cshtrd": 1_000_000.0, "ajexdi": 2.0 if pre else 1.0,
                     "trfd": 1.02 if d >= DIVIDEND_ON else 1.0, "curcdd": "EUR",
                     "cshoc": 1e9 if pre else 2e9, "qunit": 1})
        rows.append({"gvkey": "100001", "iid": "02W", "datadate": d, "prccd": 80.0,
                     "prcod": 80.0, "prchd": 80.0, "prcld": 80.0, "cshtrd": 1000.0,
                     "ajexdi": 1.0, "trfd": 1.0, "curcdd": "EUR", "cshoc": 1e6,
                     "qunit": 1})
        rows.append({"gvkey": "100002", "iid": "01W", "datadate": d, "prccd": 3000.0,
                     "prcod": 3000.0, "prchd": 3000.0, "prcld": 3000.0,
                     "cshtrd": 2_000_000.0, "ajexdi": 1.0, "trfd": 1.0, "curcdd": "JPY",
                     "cshoc": 500e6, "qunit": 1})
        rows.append({"gvkey": "100003", "iid": "01W", "datadate": d, "prccd": 250.0,
                     "prcod": 250.0, "prchd": 250.0, "prcld": 250.0,
                     "cshtrd": 3_000_000.0, "ajexdi": 1.0, "trfd": 1.0, "curcdd": "GBX",
                     "cshoc": 200e6, "qunit": 1})
        if d <= GONE_ON:
            rows.append({"gvkey": "100004", "iid": "01W", "datadate": d, "prccd": 20.0,
                         "prcod": 20.0, "prchd": 20.0, "prcld": 20.0,
                         "cshtrd": 500_000.0, "ajexdi": 1.0, "trfd": 1.0,
                         "curcdd": "EUR", "cshoc": 100e6, "qunit": 1})
    return pd.DataFrame(rows)


def funda() -> pd.DataFrame:
    base = {"curcd": "EUR", "sale": 8000.0, "ni": 600.0, "oancf": 900.0, "capx": 300.0,
            "oiadp": 800.0, "dp": 200.0, "xint": 50.0, "txt": 150.0, "pi": 750.0,
            "gp": 3000.0, "at": 12000.0, "lt": 7000.0, "dltt": 2000.0, "dlc": 300.0,
            "che": 500.0, "act": 4000.0, "lct": 2500.0}
    return pd.DataFrame([
        {**base, "gvkey": "100001", "datadate": dt.date(2024, 12, 31), "fyear": 2024,
         "ceq": 4000.0, "csho": 1000.0},
        {**base, "gvkey": "100001", "datadate": dt.date(2025, 12, 31), "fyear": 2025,
         "ceq": 5000.0, "csho": 1000.0},
        {**base, "gvkey": "100004", "datadate": dt.date(2024, 12, 31), "fyear": 2024,
         "ceq": 1000.0, "csho": 100.0},
    ])


def fundq() -> pd.DataFrame:
    rows = []
    for end in pd.date_range("2025-03-31", "2026-03-31", freq="QE").date:
        rows.append({"gvkey": "100002", "datadate": end, "fyearq": end.year,
                     "fqtr": (end.month - 1) // 3 + 1, "curcdq": "JPY",
                     "rdq": end + dt.timedelta(days=45), "cshoq": 500.0,
                     "saleq": 250_000.0, "niq": 10_000.0, "oiadpq": 15_000.0,
                     "dpq": 5_000.0, "ceqq": 2_000_000.0, "atq": 4_000_000.0,
                     "ltq": 2_000_000.0, "dlttq": 300_000.0, "dlcq": 50_000.0,
                     "cheq": 400_000.0})
    return pd.DataFrame(rows)


def exrt() -> pd.DataFrame:
    return pd.DataFrame([
        {"datadate": d, "fromcurd": "GBP", "tocurd": cur, "exratd": rate}
        for d in DAYS for cur, rate in PER_GBP.items()
    ])


def write_files(directory: Path) -> Path:
    for table, frame in (("g_company", company()), ("g_security", security()),
                         ("g_secd", secd()), ("g_funda", funda()), ("g_fundq", fundq()),
                         ("g_exrt_dly", exrt())):
        folder = directory / table
        folder.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(folder / f"{table}_000.parquet", index=False)
    return directory


def build_store(directory: Path):
    """A WorldSource over a store translated from the files above."""
    import duckdb

    from portfolio_lab.data import wrds
    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    source = write_files(directory / "wrds")
    con = duckdb.connect(str(directory / "world.duckdb"))
    _apply(con, MARKET_MIGRATIONS)
    counts = wrds.ingest(con, source)
    return wrds.WorldSource(con), counts
