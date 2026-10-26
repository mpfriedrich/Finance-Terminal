"""Hand-made files in the CRSP / Compustat North America layout, and a small free store,
for testing data/crsp.py. CRSP ends on 2024-12-31 (the end of a subscription).

  * permno 10001 — Alpha Inc, CRSP ticker ALP, still trading; the free store knows the
    company (CIK 1001) as ALPH today and has its bars into 2025, on a split basis twice
    CRSP's. A 2:1 split on 2024-06-03 inside CRSP. Returns 0.1% a day.
  * permno 10002 — Bust Corp (CIK 1002), stopped trading 2023-06-30; CRSP records a
    delisting return of -85%. Compustat has its 2022 quarters; the SEC data starts with
    Q1 2023, so the SEC takes over from there.
  * permno 10003 — an old company that last traded as ALPH, ended 2023-12-29, no link
    to Compustat (so no SEC CIK). Its symbol collides with Alpha's current one.
  * permno 10004 — Merged Co (CIK 1004), taken over 2024-03-28 at +25%.
  * NEWCO (CIK 1005) — listed in 2025, after CRSP ends: only the free store has it.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd

CRSP_END = dt.date(2024, 12, 31)
DAYS = pd.bdate_range("2023-01-02", CRSP_END).date
FREE_DAYS = pd.bdate_range("2024-12-02", "2025-03-31").date
SPLIT_ON = dt.date(2024, 6, 3)
# Merged Co has no opening price on this day: CRSP leaves `openprc` blank when no open
# was recorded, and the store must keep it blank (audit 24.10.2026 F9).
NO_OPEN = DAYS[40]

LINES = {
    # permno: (ticker, name, sic, last day, delisting return, delisting code)
    10001: ("ALP", "ALPHA INC", "3550", CRSP_END, None, None),
    10002: ("BST", "BUST CORP", "5400", dt.date(2023, 6, 30), -0.85, "574"),
    10003: ("ALPH", "OLDCO INC", "2000", dt.date(2023, 12, 29), None, "100"),
    10004: ("MRG", "MERGED CO", "3670", dt.date(2024, 3, 28), 0.25, "233"),
}


def names() -> pd.DataFrame:
    return pd.DataFrame([
        {"permno": p, "namedt": dt.date(2000, 1, 3), "nameendt": last, "ticker": t,
         "comnam": n, "shrcd": 11, "exchcd": 1 if p != 10004 else 3, "siccd": sic}
        for p, (t, n, sic, last, *_ ) in LINES.items()
    ])


def dsf() -> pd.DataFrame:
    rows = []
    for permno, (_, _, _, last, *_) in LINES.items():
        for i, d in enumerate(DAYS):
            if d > last:
                break
            if permno == 10001:
                pre = d < SPLIT_ON
                # A 0.1% daily return, compounded, on a basis that halves at the split.
                base = 40.0 * 1.001 ** i
                prc, fac = (base, 2.0) if pre else (base / 2, 1.0)
                shrout = 50_000 if pre else 100_000          # thousands
            else:
                prc, fac, shrout = 20.0, 1.0, 10_000
            rows.append({"permno": permno, "date": d, "prc": prc if i % 7 else -prc,
                         "ret": 0.001 if (permno == 10001 and i) else (0.0 if i else None),
                         "vol": 100_000.0, "shrout": shrout, "cfacpr": fac,
                         "openprc": None if (permno == 10004 and d == NO_OPEN)
                                    else abs(prc), "askhi": abs(prc) * 1.01,
                         "bidlo": abs(prc) * 0.99})
    return pd.DataFrame(rows)


def delist() -> pd.DataFrame:
    return pd.DataFrame([
        {"permno": p, "dlstdt": last, "dlret": ret, "dlstcd": code}
        for p, (_, _, _, last, ret, code) in LINES.items() if code
    ])


def links() -> pd.DataFrame:
    return pd.DataFrame([
        {"gvkey": f"00{p - 9000}", "lpermno": p, "linktype": "LU", "linkprim": "P",
         "linkdt": dt.date(1990, 1, 1), "linkenddt": None}
        for p in (10001, 10002, 10004)
    ])


def company() -> pd.DataFrame:
    return pd.DataFrame([
        {"gvkey": "001001", "cik": "0000001001", "conm": "ALPHA INC", "sic": "3550"},
        {"gvkey": "001002", "cik": "0000001002", "conm": "BUST CORP", "sic": "5400"},
        {"gvkey": "001004", "cik": "0000001004", "conm": "MERGED CO", "sic": "3670"},
    ])


def fundq() -> pd.DataFrame:
    return pd.DataFrame([
        {"gvkey": "001002", "datadate": end, "rdq": end + dt.timedelta(days=30),
         "fqtr": (end.month - 1) // 3 + 1, "fyearq": end.year, "curcdq": "USD",
         "saleq": 50.0, "niq": 2.0, "ceqq": 300.0, "atq": 900.0, "cshoq": 10.0}
        for end in (dt.date(2022, 9, 30), dt.date(2022, 12, 31), dt.date(2023, 3, 31))
    ])


def write_files(directory: Path) -> Path:
    for folder, frame in (("crsp_dsenames", names()), ("crsp_dsf", dsf()),
                          ("crsp_dsedelist", delist()), ("crsp_ccmxpf_lnkhist", links()),
                          ("comp_company", company()), ("comp_fundq", fundq())):
        (directory / folder).mkdir(parents=True, exist_ok=True)
        frame.to_parquet(directory / folder / f"{folder}_000.parquet", index=False)
    return directory


def write_ciz_files(directory: Path) -> Path:
    """The same lines in CRSP's current format (CIZ): security-info periods, the v2
    daily file with price-only returns, and each delisting on a row of its own."""
    names_v2 = pd.DataFrame([
        {"permno": r.permno, "secinfostartdt": r.namedt, "secinfoenddt": r.nameendt,
         "ticker": r.ticker, "issuernm": r.comnam, "siccd": r.siccd,
         "primaryexch": "N" if r.exchcd == 1 else "Q", "sharetype": "NS",
         "securitytype": "EQTY", "securitysubtype": "COM", "usincflg": "Y",
         "issuertype": "CORP", "conditionaltype": "RW", "tradingstatusflg": "A"}
        for r in names().itertuples()])
    daily = dsf()
    daily_v2 = pd.DataFrame({
        "permno": daily.permno, "dlycaldt": daily.date, "dlyprc": daily.prc.abs(),
        "dlyret": daily.ret, "dlyretx": daily.ret, "dlyvol": daily.vol,
        "shrout": daily.shrout, "dlyopen": daily.openprc, "dlyhigh": daily.askhi,
        "dlylow": daily.bidlo, "dlydelflg": "N"})
    gone = pd.DataFrame([
        {"permno": p, "dlycaldt": (pd.Timestamp(last) + pd.offsets.BDay(1)).date(),
         "dlyprc": None, "dlyret": ret, "dlyretx": ret, "dlyvol": None, "shrout": None,
         "dlyopen": None, "dlyhigh": None, "dlylow": None, "dlydelflg": "Y"}
        for p, (_, _, _, last, ret, code) in LINES.items() if ret is not None])
    for folder, frame in (("crsp_stksecurityinfohist", names_v2),
                          ("crsp_dsf_v2", pd.concat([daily_v2, gone], ignore_index=True)),
                          ("crsp_ccmxpf_lnkhist", links()), ("comp_company", company()),
                          ("comp_fundq", fundq())):
        (directory / folder).mkdir(parents=True, exist_ok=True)
        frame.to_parquet(directory / folder / f"{folder}_000.parquet", index=False)
    return directory


def free_store(path: Path) -> Path:
    """The free US store: Alpha as ALPH (split basis twice CRSP's), NEWCO, the benchmark,
    and SEC fundamentals for Alpha and Bust."""
    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    con = duckdb.connect(str(path))
    _apply(con, MARKET_MIGRATIONS)
    con.execute("INSERT INTO securities (cik, ticker, name) VALUES "
                "(1001, 'ALPH', 'Alpha Inc'), (1005, 'NEWCO', 'New Co')")
    last_crsp = 40.0 * 1.001 ** (len(DAYS) - 1) / 2
    for d in FREE_DAYS:
        # Alpha: 102 throughout on the free store's basis (two of CRSP's), traded at 102.
        con.execute("INSERT INTO prices (ticker, date, open, high, low, close, close_raw, "
                    "close_adj, volume) VALUES ('ALPH', ?, 102, 102, 102, 102, 102, 204, 1000)",
                    [d])
        if d.year == 2025:
            con.execute("INSERT INTO prices (ticker, date, close, close_raw, close_adj, "
                        "volume) VALUES ('NEWCO', ?, 10, 10, 10, 5000)", [d])
    for d in pd.bdate_range("2023-01-02", "2025-03-31").date:
        con.execute("INSERT INTO prices (ticker, date, close, close_adj) "
                    "VALUES ('^SP500TR', ?, 10000, 10000)", [d])
    con.execute(
        "INSERT INTO fundamentals (cik, metric, value, period_end, filed, accession, "
        "xbrl_tag, tag_rank) VALUES "
        "(1001, 'book_value', 5e9, DATE '2024-09-30', DATE '2024-11-01', 'a', 'sec:x', 0), "
        "(1002, 'book_value', 3e8, DATE '2023-03-31', DATE '2023-05-10', 'b', 'sec:x', 0)")
    con.close()
    del last_crsp
    return path


def build(tmp: Path, ciz: bool = False):
    """A world store with CRSP ingested and the free store copied in."""
    from portfolio_lab.data import crsp, wrds
    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    market = free_store(tmp / "market.duckdb")
    source = (write_ciz_files if ciz else write_files)(tmp / "wrds")
    con = duckdb.connect(str(tmp / "world.duckdb"))
    _apply(con, MARKET_MIGRATIONS)
    counts = crsp.ingest(con, source, market)
    copied = wrds.copy_us(con, market)
    return wrds.WorldSource(con), counts, copied, market
