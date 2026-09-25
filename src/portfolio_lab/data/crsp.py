"""The US half without survivorship bias: CRSP through WRDS.

The free US data (SEC + Yahoo) holds today's listed companies and almost none of the
ones that went bankrupt or were taken over (Phase 7 measured 0 of 20). CRSP holds every
US common stock with its daily prices until the day it stopped trading, and the return
holders actually received at that point (the delisting return). This module puts that
history into the world store in place of the free US prices.

How it is wired, and why:

  * **Prices from CRSP** (legacy daily file `crsp.dsf` with `dsenames`, `dsedelist`):
    common shares (share code 10/11) on NYSE, NYSE American and Nasdaq. Traded close
    `|prc|` (a negative price is CRSP's bid/ask midpoint), split-adjusted through
    `cfacpr`, total return compounded from `ret`.
  * **After CRSP ends** — a subscription covers up to its last update, often the end of
    the previous year — the lines still trading then continue with the free store's
    daily prices, spliced at the last CRSP day so neither the split basis nor the
    return series jumps (`extend`). `build_prices.py` does this every day.
  * **Company key = the SEC CIK** where the CRSP–Compustat link and Compustat give one
    (almost every company that files with the SEC), so the SEC fundamentals, filed on
    real dates, apply unchanged — including those of companies that no longer exist,
    which the SEC keeps. A line with no CIK gets `CRSP_OFFSET + permno`.
  * **Compustat North America** fills only what the SEC data lacks: periods before a
    company's first XBRL filing (small companies began in mid-2011). Quarterly figures
    are dated by `rdq` (the earnings announcement) where it exists, otherwise by the
    filing deadline (45 days for a 10-Q, 90 for a 10-K).
  * **Tickers**: a line still trading carries the ticker the free store knows it by
    today (so META, not FB); a line that ended carries its last CRSP ticker, suffixed
    with the permno if a current line uses that ticker.
  * **Delisting returns** go into `delisting_returns`; the backtests use them instead of
    the assumed loss. CRSP leaves the return empty for many performance delistings;
    those keep the assumption, as the literature does.

Written against the published CRSP and Compustat layouts WITHOUT access from the build
environment: the translation is tested on hand-made files in those layouts only.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pandas as pd

from . import industry
from .wrds import GVKEY_OFFSET, _fetch, available_columns

CRSP_OFFSET = 2_000_000_000
# Share codes 10 and 11: ordinary common shares of US companies. Exchange codes 1, 2, 3:
# NYSE, NYSE American (AMEX), Nasdaq.
SHARE_CODES = (10, 11)
EXCHANGE_CODES = (1, 2, 3)
EXCHANGES = {1: "NYSE", 2: "AMEX", 3: "NASDAQ"}
# A line whose last CRSP bar is this close to the end of CRSP was still trading.
ACTIVE_DAYS = 10
# Filing deadlines used when Compustat has no announcement date.
DEADLINE_10Q_DAYS = 45
DEADLINE_10K_DAYS = 90
# Facts this module writes carry these tag prefixes; the rest of the US half belongs to
# the free store and is replaced whenever that is copied in.
OWN_TAGS = ("crsp:%", "compustat-na:%")

WANTED = {
    ("crsp", "dsenames"): ("permno", "namedt", "nameendt", "ticker", "comnam", "shrcd",
                           "exchcd", "siccd"),
    ("crsp", "dsf"): ("permno", "date", "prc", "ret", "vol", "shrout", "cfacpr",
                      "openprc", "askhi", "bidlo"),
    ("crsp", "dsedelist"): ("permno", "dlstdt", "dlret", "dlstcd"),
    ("crsp", "ccmxpf_lnkhist"): ("gvkey", "lpermno", "linktype", "linkprim", "linkdt",
                                 "linkenddt"),
    ("comp", "company"): ("gvkey", "cik", "conm", "sic"),
    ("comp", "fundq"): ("gvkey", "datadate", "rdq", "fqtr", "fyearq", "curcdq", "saleq",
                        "niq", "oiadpq", "dpq", "xintq", "txtq", "piq", "cogsq", "ceqq",
                        "atq", "ltq", "dlttq", "dlcq", "cheq", "actq", "lctq", "cshoq"),
    ("comp", "funda"): ("gvkey", "datadate", "fyear", "curcd", "sale", "ni", "oancf",
                        "capx", "oiadp", "dp", "xint", "txt", "pi", "gp", "cogs", "ceq",
                        "at", "lt", "dltt", "dlc", "che", "act", "lct", "csho"),
}
# Stored metric -> Compustat NA item (annual; quarterly adds "q"). Quarterly cash flow is
# year-to-date in Compustat and is not read.
DURATION = {"revenue": "sale", "net_income": "ni", "operating_cash_flow": "oancf",
            "capex": "capx", "operating_income": "oiadp", "depreciation_amortization": "dp",
            "interest_expense": "xint", "income_tax": "txt", "pretax_income": "pi",
            "gross_profit": "gp"}
INSTANT = {"book_value": "ceq", "total_assets": "at", "total_liabilities": "lt",
           "long_term_debt": "dltt", "current_debt": "dlc", "cash": "che",
           "current_assets": "act", "current_liabilities": "lct"}
QUARTERLY_SKIP = ("operating_cash_flow", "capex", "gross_profit")


def _folder(schema: str, table: str) -> str:
    return f"{schema}_{table}"


# -- download ----------------------------------------------------------------------------

def download(con, dest: Path, since: dt.date, log=print) -> dict[str, int]:
    """Fetch CRSP daily prices, names, delistings, the CRSP–Compustat link and Compustat
    North America fundamentals into parquet under `dest`. Closed years of daily prices
    already on disk are not fetched again. Raises with a clear message when the
    subscription does not include CRSP."""
    cols = {key: available_columns(con, key[1], wanted, schema=key[0])
            for key, wanted in WANTED.items()}
    if not cols[("crsp", "dsf")] or not cols[("crsp", "dsenames")]:
        raise RuntimeError(
            "CRSP's daily stock file (crsp.dsf) is not available to this WRDS account. "
            "Check the subscription on the WRDS website, or build without it: "
            "--us=free.")
    for key, wanted in WANTED.items():
        missing = set(wanted) - set(cols[key])
        if missing:
            log(f"  {key[0]}.{key[1]}: not in WRDS, skipped: {', '.join(sorted(missing))}")

    common = ("SELECT DISTINCT permno FROM crsp.dsenames WHERE shrcd IN %s "
              "AND exchcd IN %s")
    common_params = (SHARE_CODES, EXCHANGE_CODES)
    counts: dict[str, int] = {}

    def fresh(key) -> Path:
        folder = dest / _folder(*key)
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.parquet"):
            old.unlink()
        return folder / f"{_folder(*key)}.parquet"

    def select(key, alias="") -> str:
        prefix = f"{alias}." if alias else ""
        return ", ".join(prefix + c for c in cols[key])

    key = ("crsp", "dsenames")
    counts["names"] = _fetch(
        con, f"SELECT {select(key)} FROM crsp.dsenames WHERE permno IN ({common})",
        common_params, fresh(key))
    key = ("crsp", "dsedelist")
    if cols[key]:
        counts["delistings"] = _fetch(
            con, f"SELECT {select(key)} FROM crsp.dsedelist WHERE dlstdt >= %s "
                 f"AND permno IN ({common})", (since, *common_params), fresh(key))
    key = ("crsp", "ccmxpf_lnkhist")
    if cols[key]:
        counts["links"] = _fetch(
            con, f"SELECT {select(key)} FROM crsp.ccmxpf_lnkhist "
                 "WHERE linktype IN ('LU', 'LC') AND linkprim IN ('P', 'C')", (),
            fresh(key))
    key = ("comp", "company")
    if cols[key]:
        counts["companies"] = _fetch(con, f"SELECT {select(key)} FROM comp.company", (),
                                     fresh(key))
    std = "indfmt = 'INDL' AND datafmt = 'STD' AND consol = 'C' AND popsrc = 'D'"
    fund_since = since - dt.timedelta(days=2 * 366)
    for table in ("fundq", "funda"):
        key = ("comp", table)
        if cols[key]:
            counts[table] = _fetch(
                con, f"SELECT {select(key)} FROM comp.{table} WHERE {std} "
                     "AND datadate >= %s", (fund_since,), fresh(key))

    key = ("crsp", "dsf")
    folder = dest / _folder(*key)
    folder.mkdir(parents=True, exist_ok=True)
    this_year = dt.date.today().year
    counts["daily"] = 0
    for year in range(since.year, this_year + 1):
        stem = f"dsf_{year}"
        done = list(folder.glob(f"{stem}_*.parquet"))
        if done and year < this_year:
            continue
        for old in done:
            old.unlink()
        n = _fetch(con, f"SELECT {select(key)} FROM crsp.dsf WHERE date BETWEEN %s AND %s "
                        f"AND permno IN ({common})",
                   (max(since, dt.date(year, 1, 1)), dt.date(year, 12, 31), *common_params),
                   folder / f"{stem}.parquet")
        counts["daily"] += n
        log(f"  CRSP daily {year}: {n:,} rows")
    return counts


# -- translation -------------------------------------------------------------------------

def _load(con, source: Path, schema: str, table: str) -> set[str]:
    files = sorted((source / _folder(schema, table)).glob("*.parquet"))
    name = f"_{schema}_{table}"
    if not files:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT NULL AS permno LIMIT 0")
        return set()
    con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM "
                "read_parquet(?, union_by_name = true)", [[str(f) for f in files]])
    return {r[0].lower() for r in con.execute(f"DESCRIBE {name}").fetchall()}


def ingest(con: duckdb.DuckDBPyConnection, source: Path,
           market_db: Path | None = None) -> dict[str, int]:
    """Translate the CRSP / Compustat NA files under `source` into the market schema.

    `market_db` (the free store) supplies today's tickers for lines still trading.
    Replaces what a previous CRSP build wrote, and any free-store rows for the companies
    CRSP now covers. One transaction.
    """
    have = {key: _load(con, source, *key) for key in WANTED}
    if not have[("crsp", "dsf")]:
        raise RuntimeError(f"No CRSP daily prices under {source}.")
    if market_db is not None and Path(market_db).exists():
        con.execute(f"ATTACH '{market_db}' AS us_names (READ_ONLY)")
        free_tickers = con.execute(
            """SELECT s.cik, s.ticker, MAX(p.date) AS last_bar
               FROM us_names.securities s JOIN us_names.prices p USING (ticker)
               WHERE s.cik < ? GROUP BY 1, 2""", [GVKEY_OFFSET]).fetch_df()
        con.execute("DETACH us_names")
    else:
        free_tickers = pd.DataFrame(columns=["cik", "ticker", "last_bar"])
    con.register("_free_tickers", free_tickers)

    con.execute("BEGIN")
    try:
        crsp_end = con.execute("SELECT MAX(CAST(date AS DATE)) FROM _crsp_dsf").fetchone()[0]

        # One row per permno: its last name record, its span in the daily file.
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE _lines AS
            WITH last_name AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY permno
                    ORDER BY CAST(nameendt AS DATE) DESC NULLS FIRST,
                             CAST(namedt AS DATE) DESC) AS rn
                FROM _crsp_dsenames
            ), span AS (
                SELECT permno, MIN(CAST(date AS DATE)) AS first_date,
                       MAX(CAST(date AS DATE)) AS last_date
                FROM _crsp_dsf WHERE prc IS NOT NULL AND prc <> 0 GROUP BY 1
            )
            SELECT CAST(n.permno AS BIGINT) AS permno,
                   upper(trim(COALESCE(n.ticker, ''))) AS crsp_ticker,
                   n.comnam AS name, CAST(n.siccd AS VARCHAR) AS sic,
                   CAST(n.exchcd AS INTEGER) AS exchcd,
                   s.first_date, s.last_date
            FROM last_name n JOIN span s ON s.permno = n.permno
            WHERE n.rn = 1
            """
        )
        # permno -> gvkey (the primary link that covers the line's last day, else its
        # latest primary link) -> CIK.
        if have[("crsp", "ccmxpf_lnkhist")] and have[("comp", "company")]:
            con.execute(
                """
                CREATE OR REPLACE TEMP TABLE _gv AS
                SELECT permno, gvkey, primary_link FROM (
                    SELECT CAST(l.lpermno AS BIGINT) AS permno, CAST(l.gvkey AS VARCHAR) AS gvkey,
                           l.linkprim = 'P' AS primary_link,
                           ROW_NUMBER() OVER (PARTITION BY l.lpermno ORDER BY
                               (x.last_date BETWEEN CAST(l.linkdt AS DATE)
                                 AND COALESCE(CAST(l.linkenddt AS DATE), DATE '9999-12-31')) DESC,
                               l.linkprim = 'P' DESC,
                               COALESCE(CAST(l.linkenddt AS DATE), DATE '9999-12-31') DESC) AS rn
                    FROM _crsp_ccmxpf_lnkhist l JOIN _lines x
                      ON x.permno = CAST(l.lpermno AS BIGINT)
                ) WHERE rn = 1
                """
            )
            con.execute(
                """
                CREATE OR REPLACE TEMP TABLE _key AS
                SELECT x.*, g.gvkey, COALESCE(g.primary_link, TRUE) AS primary_link,
                       TRY_CAST(c.cik AS BIGINT) AS sec_cik
                FROM _lines x LEFT JOIN _gv g USING (permno)
                LEFT JOIN _comp_company c ON CAST(c.gvkey AS VARCHAR) = g.gvkey
                """
            )
        else:
            con.execute("CREATE OR REPLACE TEMP TABLE _key AS SELECT x.*, NULL::VARCHAR "
                        "AS gvkey, TRUE AS primary_link, NULL::BIGINT AS sec_cik FROM _lines x")

        # Ticker: a line still trading takes the free store's current ticker for its
        # company — the same symbol if it has one, else (primary line only) the one
        # with the newest bar. Ended lines keep CRSP's last symbol.
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _named AS
            WITH k AS (
                SELECT *, last_date >= DATE '{crsp_end}' - {ACTIVE_DAYS} AS active,
                       COALESCE(sec_cik, {CRSP_OFFSET} + permno) AS cik
                FROM _key
            ), same AS (
                SELECT k.permno, f.ticker FROM k JOIN _free_tickers f
                  ON f.cik = k.sec_cik AND f.ticker = k.crsp_ticker
            ), newest AS (
                SELECT k.permno, arg_max(f.ticker, f.last_bar) AS ticker
                FROM k JOIN _free_tickers f ON f.cik = k.sec_cik
                WHERE k.primary_link GROUP BY 1
            )
            SELECT k.*, CASE WHEN k.active THEN
                             COALESCE(same.ticker, newest.ticker, k.crsp_ticker)
                        ELSE k.crsp_ticker END AS base_ticker
            FROM k LEFT JOIN same USING (permno) LEFT JOIN newest USING (permno)
            """
        )
        # A symbol names one line: current lines first, then the most recent.
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE _final AS
            SELECT *, CASE WHEN rn = 1 AND base_ticker <> '' THEN base_ticker
                           ELSE COALESCE(NULLIF(base_ticker, ''), 'CRSP') || '-'
                                || CAST(permno AS VARCHAR) END AS ticker
            FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY base_ticker
                        ORDER BY active DESC, last_date DESC, permno) AS rn FROM _named)
            """
        )

        # Out with the previous CRSP build, and with free-store rows for companies CRSP
        # now covers (their prices come from CRSP, extended by `extend`).
        con.execute("DELETE FROM prices WHERE ticker IN (SELECT ticker FROM crsp_link) "
                    "OR ticker IN (SELECT ticker FROM securities WHERE cik IN "
                    "(SELECT cik FROM _final)) OR ticker IN (SELECT ticker FROM _final)")
        con.execute("DELETE FROM securities WHERE cik IN (SELECT cik FROM crsp_link) "
                    "OR cik IN (SELECT cik FROM _final) "
                    "OR ticker IN (SELECT ticker FROM _final)")
        for pattern in OWN_TAGS:
            con.execute("DELETE FROM fundamentals WHERE xbrl_tag LIKE ?", [pattern])
        con.execute("DELETE FROM delisting_returns WHERE source = 'crsp'")
        con.execute("DELETE FROM crsp_link")

        # Daily bars. cfacpr puts every price on the line's last-day share basis; the
        # total-return series compounds CRSP's own return and ends at the last close.
        has_open = "openprc" in have[("crsp", "dsf")]
        has_hl = {"askhi", "bidlo"} <= have[("crsp", "dsf")]
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _bars AS
            WITH d AS (
                SELECT CAST(d.permno AS BIGINT) AS permno, CAST(d.date AS DATE) AS date,
                       abs(CAST(d.prc AS DOUBLE)) AS px,
                       COALESCE(NULLIF(CAST(d.cfacpr AS DOUBLE), 0), 1) AS fac,
                       {'abs(CAST(d.openprc AS DOUBLE))' if has_open else 'NULL'} AS op,
                       {'abs(CAST(d.askhi AS DOUBLE))' if has_hl else 'NULL'} AS hi,
                       {'abs(CAST(d.bidlo AS DOUBLE))' if has_hl else 'NULL'} AS lo,
                       CAST(d.ret AS DOUBLE) AS ret, CAST(d.vol AS DOUBLE) AS vol,
                       CAST(d.shrout AS DOUBLE) * 1000 AS shares
                FROM _crsp_dsf d
                WHERE d.prc IS NOT NULL AND d.prc <> 0
            ), w AS (
                SELECT d.*,
                       last_value(fac) OVER (PARTITION BY permno ORDER BY date
                           ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS fac_last,
                       exp(SUM(ln(greatest(1 + COALESCE(ret, 0), 1e-9))) OVER (
                           PARTITION BY permno ORDER BY date)) AS growth
                FROM d
            ), z AS (
                SELECT w.*,
                       last_value(px / fac * fac_last) OVER (PARTITION BY permno ORDER BY date
                           ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS close_last,
                       last_value(growth) OVER (PARTITION BY permno ORDER BY date
                           ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS growth_last
                FROM w
            )
            SELECT f.ticker, z.date,
                   z.op / z.fac * z.fac_last AS open, z.hi / z.fac * z.fac_last AS high,
                   z.lo / z.fac * z.fac_last AS low, z.px / z.fac * z.fac_last AS close,
                   z.px AS close_raw, z.growth / z.growth_last * z.close_last AS close_adj,
                   CAST(z.vol AS BIGINT) AS volume, z.shares, f.cik, f.primary_link
            FROM z JOIN _final f USING (permno)
            """
        )
        con.execute(
            """INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, close_raw,
                                              close_adj, volume)
               SELECT ticker, date, open, high, low, close, close_raw, close_adj, volume
               FROM _bars"""
        )
        con.execute(
            """INSERT INTO securities (cik, ticker, name, sector, sic, exchange, first_seen,
                                       last_seen)
               SELECT cik, ticker, name, NULL, sic,
                      CASE exchcd WHEN 1 THEN 'NYSE' WHEN 2 THEN 'AMEX' WHEN 3 THEN 'NASDAQ'
                           ELSE CAST(exchcd AS VARCHAR) END, first_date, last_date
               FROM _final"""
        )
        sics = [r[0] for r in con.execute(
            "SELECT DISTINCT sic FROM _final WHERE sic IS NOT NULL").fetchall()]
        if sics:
            con.register("_groups", pd.DataFrame(
                {"sic": sics, "grp": [industry.group_for(x) for x in sics]}))
            con.execute("UPDATE securities SET sector = g.grp FROM _groups g "
                        "WHERE securities.sic = g.sic AND securities.ticker IN "
                        "(SELECT ticker FROM _final) AND securities.sector IS NULL")
            con.unregister("_groups")
        con.execute("INSERT INTO crsp_link SELECT permno, cik, ticker, last_date, active "
                    "FROM _final")
        con.execute("INSERT OR REPLACE INTO source_windows VALUES ('crsp', ?)", [crsp_end])

        # Shares outstanding as the market saw them, month by month, known that day —
        # primary lines only, so a two-class company is not counted as either class.
        con.execute(
            """INSERT INTO fundamentals (cik, metric, value, period_start, period_end, fy,
                                         fp, filed, accession, xbrl_tag, tag_rank, currency)
               SELECT cik, 'shares_outstanding', shares, NULL, date, NULL, 'market', date,
                      'crsp:' || ticker || ':' || CAST(date AS VARCHAR), 'crsp:shrout', 0, NULL
               FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker, date_trunc('month', date)
                         ORDER BY date DESC) AS rn FROM _bars WHERE shares > 0 AND primary_link)
               WHERE rn = 1"""
        )

        # What holders received when a listing ended before CRSP does.
        if {"dlret", "dlstdt"} <= have[("crsp", "dsedelist")]:
            code = "CAST(d.dlstcd AS VARCHAR)" if "dlstcd" in have[("crsp", "dsedelist")] \
                else "NULL"
            con.execute(
                f"""INSERT OR REPLACE INTO delisting_returns (ticker, date, ret, code, source)
                    SELECT f.ticker, CAST(d.dlstdt AS DATE), CAST(d.dlret AS DOUBLE), {code},
                           'crsp'
                    FROM _crsp_dsedelist d JOIN _final f
                      ON f.permno = CAST(d.permno AS BIGINT)
                    WHERE d.dlret IS NOT NULL AND NOT f.active"""
            )

        _compustat_na(con, have)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.unregister("_free_tickers")

    return {
        "lines": con.execute("SELECT COUNT(*) FROM crsp_link").fetchone()[0],
        "still_trading": con.execute(
            "SELECT COUNT(*) FROM crsp_link WHERE active").fetchone()[0],
        "ended": con.execute("SELECT COUNT(*) FROM crsp_link WHERE NOT active").fetchone()[0],
        "with_delisting_return": con.execute(
            "SELECT COUNT(*) FROM delisting_returns WHERE source = 'crsp'").fetchone()[0],
        "without_sec_cik": con.execute(
            "SELECT COUNT(*) FROM crsp_link WHERE cik >= ?", [CRSP_OFFSET]).fetchone()[0],
        "crsp_end": crsp_end,
    }


def _compustat_na(con, have) -> None:
    """Compustat North America facts for the CRSP companies, keyed like them. Pruned
    later (`prune_compustat`) to the periods before the company's first SEC filing."""
    if not have[("comp", "fundq")] and not have[("comp", "funda")]:
        return
    con.execute("""CREATE OR REPLACE TEMP TABLE _gvcik AS
                   SELECT DISTINCT k.gvkey, f.cik FROM _key k JOIN _final f USING (permno)
                   WHERE k.gvkey IS NOT NULL AND f.primary_link""")
    insert = ("INSERT INTO fundamentals (cik, metric, value, period_start, period_end, fy, "
              "fp, filed, accession, xbrl_tag, tag_rank, currency) ")
    join = "JOIN _gvcik g ON g.gvkey = CAST(f.gvkey AS VARCHAR)"

    q = have[("comp", "fundq")]
    if q:
        est = f"CAST(f.datadate AS DATE) + {DEADLINE_10Q_DAYS}"
        if "rdq" in q:
            ok = ("f.rdq IS NOT NULL AND CAST(f.rdq AS DATE) >= CAST(f.datadate AS DATE) "
                  "AND CAST(f.rdq AS DATE) - CAST(f.datadate AS DATE) <= 366")
            filed, how = (f"CASE WHEN {ok} THEN CAST(f.rdq AS DATE) ELSE {est} END",
                          f"CASE WHEN {ok} THEN 'rdq' ELSE 'est' END")
        else:
            filed, how = est, "'est'"
        span = "CAST(CAST(f.datadate AS DATE) - INTERVAL 3 MONTH + INTERVAL 1 DAY AS DATE)"
        items = {m: f"{c}q" for m, c in {**DURATION, **INSTANT}.items()
                 if m not in QUARTERLY_SKIP}
        items["shares_outstanding"] = "cshoq"
        for metric, item in items.items():
            if item not in q:
                continue
            scale = "1e6"
            start = span if metric in DURATION else "NULL"
            cur = "NULL" if metric == "shares_outstanding" else (
                "upper(f.curcdq)" if "curcdq" in q else "'USD'")
            con.execute(insert + f"""
                SELECT g.cik, '{metric}', CAST(f.{item} AS DOUBLE) * {scale}, {start},
                       CAST(f.datadate AS DATE), NULL, {how}, CAST({filed} AS DATE),
                       'compustat-na:q:' || f.gvkey || ':' || CAST(f.datadate AS VARCHAR),
                       'compustat-na:{item}', 1, {cur}
                FROM _comp_fundq f {join} WHERE f.{item} IS NOT NULL""")

    a = have[("comp", "funda")]
    if a:
        # A 10-K's figures are public at the fourth-quarter announcement where Compustat
        # records one, otherwise at the 10-K deadline.
        q4 = ("LEFT JOIN (SELECT CAST(gvkey AS VARCHAR) AS gvkey, CAST(datadate AS DATE) AS dd, "
              "MIN(CAST(rdq AS DATE)) AS rdq FROM _comp_fundq WHERE rdq IS NOT NULL "
              "GROUP BY 1, 2) r ON r.gvkey = CAST(f.gvkey AS VARCHAR) "
              "AND r.dd = CAST(f.datadate AS DATE)") if "rdq" in q else ""
        rdq = ("CASE WHEN r.rdq >= CAST(f.datadate AS DATE) AND r.rdq - CAST(f.datadate AS DATE) "
               "<= 366 THEN r.rdq END") if q4 else "NULL"
        filed = f"COALESCE({rdq}, CAST(f.datadate AS DATE) + {DEADLINE_10K_DAYS})"
        how = f"CASE WHEN {rdq} IS NOT NULL THEN 'rdq' ELSE 'est' END"
        span = "CAST(CAST(f.datadate AS DATE) - INTERVAL 12 MONTH + INTERVAL 1 DAY AS DATE)"
        items = {**DURATION, **INSTANT, "shares_outstanding": "csho"}
        for metric, item in items.items():
            expr = f"f.{item}" if item in a else None
            if metric == "gross_profit" and expr is None and {"sale", "cogs"} <= a:
                expr = "(f.sale - f.cogs)"
            if expr is None:
                continue
            start = span if metric in DURATION else "NULL"
            cur = "NULL" if metric == "shares_outstanding" else (
                "upper(f.curcd)" if "curcd" in a else "'USD'")
            con.execute(insert + f"""
                SELECT g.cik, '{metric}', CAST({expr} AS DOUBLE) * 1e6, {start},
                       CAST(f.datadate AS DATE), NULL, {how}, CAST({filed} AS DATE),
                       'compustat-na:a:' || f.gvkey || ':' || CAST(f.datadate AS VARCHAR),
                       'compustat-na:{item}', 2, {cur}
                FROM _comp_funda f {join} {q4} WHERE {expr} IS NOT NULL""")


# -- after CRSP ends ----------------------------------------------------------------------

def crsp_end(con) -> dt.date | None:
    try:
        row = con.execute("SELECT last_date FROM source_windows WHERE source = 'crsp'"
                          ).fetchone()
    except duckdb.Error:
        return None
    return row[0] if row else None


def extend(con, free_alias: str = "us", max_gap_days: int = 7) -> dict[str, int]:
    """Continue the lines still trading at CRSP's end with the free store's daily bars.

    The free bars are put on CRSP's basis at the splice: each is divided by the ratio of
    the free close (and total-return close) to CRSP's on the last CRSP day — or the
    nearest free bar at most `max_gap_days` before it. The traded close is copied as is.
    Called with the free store attached as `free_alias`; re-run daily, it replaces what
    it appended before.
    """
    end = crsp_end(con)
    if end is None:
        return {"extended": 0, "not_found": 0}
    con.execute("""DELETE FROM prices WHERE date > ? AND ticker IN
                    (SELECT ticker FROM crsp_link WHERE active)""", [end])
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE _splice AS
        WITH anchor AS (
            SELECT l.ticker, p.close AS c_close, p.close_adj AS c_adj
            FROM crsp_link l JOIN prices p ON p.ticker = l.ticker AND p.date = l.last_date
            WHERE l.active
        ), free_anchor AS (
            SELECT f.ticker, arg_max(f.close, f.date) AS f_close,
                   arg_max(COALESCE(f.close_adj, f.close), f.date) AS f_adj
            FROM {free_alias}.prices f JOIN anchor a USING (ticker)
            WHERE f.date <= ? AND f.date >= ? GROUP BY 1
        )
        SELECT a.ticker, fa.f_close / a.c_close AS r_close, fa.f_adj / a.c_adj AS r_adj
        FROM anchor a JOIN free_anchor fa USING (ticker)
        WHERE a.c_close > 0 AND a.c_adj > 0 AND fa.f_close > 0 AND fa.f_adj > 0
        """,
        [end, end - dt.timedelta(days=max_gap_days)],
    )
    con.execute(
        f"""
        INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, close_raw,
                                       close_adj, volume)
        SELECT f.ticker, f.date, f.open / s.r_close, f.high / s.r_close, f.low / s.r_close,
               f.close / s.r_close, COALESCE(f.close_raw, f.close),
               COALESCE(f.close_adj, f.close) / s.r_adj, f.volume
        FROM {free_alias}.prices f JOIN _splice s USING (ticker)
        WHERE f.date > ?
        """,
        [end],
    )
    active = con.execute("SELECT COUNT(*) FROM crsp_link WHERE active").fetchone()[0]
    spliced = con.execute("SELECT COUNT(*) FROM _splice").fetchone()[0]
    con.execute("UPDATE securities SET last_seen = p.hi FROM (SELECT ticker, MAX(date) hi "
                "FROM prices GROUP BY 1) p WHERE securities.ticker = p.ticker "
                "AND securities.ticker IN (SELECT ticker FROM crsp_link WHERE active)")
    return {"extended": int(spliced), "not_found": int(active - spliced)}


def prune_compustat(con) -> int:
    """Drop Compustat NA facts from the period the SEC data covers for that company:
    where both exist, the SEC filing — dated by the SEC — is the better evidence."""
    before = con.execute("SELECT COUNT(*) FROM fundamentals WHERE xbrl_tag LIKE "
                         "'compustat-na:%'").fetchone()[0]
    con.execute(
        """
        DELETE FROM fundamentals WHERE xbrl_tag LIKE 'compustat-na:%' AND rowid IN (
            SELECT f.rowid FROM fundamentals f JOIN (
                SELECT cik, MIN(period_end) AS first_sec FROM fundamentals
                WHERE xbrl_tag NOT LIKE 'compustat-na:%' AND xbrl_tag NOT LIKE 'crsp:%'
                GROUP BY 1) s USING (cik)
            WHERE f.xbrl_tag LIKE 'compustat-na:%' AND f.period_end >= s.first_sec)
        """
    )
    after = con.execute("SELECT COUNT(*) FROM fundamentals WHERE xbrl_tag LIKE "
                        "'compustat-na:%'").fetchone()[0]
    return int(before - after)
