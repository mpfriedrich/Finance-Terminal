"""World stage 3: Compustat Global through WRDS, behind the same seam as FreeSource.

What it adds is what no free source has: listed companies outside the US, with their
home-market prices, share counts and fundamentals, including companies that stopped
trading. The screener, the point-in-time assembly and the backtest do not change — the
Compustat tables are translated into the market schema once, like the paid source.

How it is wired, and why:

  * **Download** (`download`): straight SQL against the WRDS PostgreSQL server. The
    official `wrds` package is not used because it pins pandas below 2.3 and this project
    runs on pandas 3. Each table lands as parquet under data/cache/wrds/, so a rebuild
    or a test never needs the server; closed years of daily prices are not fetched twice.
  * **One line per company**: its primary home-market issue (`g_company.prirow`), the
    stock a local investor would buy. Ticker = Compustat ticker + "." + the listing
    country (`7203.JPN`, `SAP.DEU`) — NOT the Yahoo symbol, and unique across countries.
  * **Everything priced in USD** at that day's rate (`g_exrt_dly`, quoted against the
    pound, crossed to the dollar), so market caps, turnover floors and returns are on the
    same footing as the US names. Returns are therefore dollar returns: what a dollar
    investor earned, currency included. Fundamentals keep their reporting currency and
    go through `pit.to_usd` at the screen date, like the SEC foreign filers.
  * **Filing dates are estimated.** Compustat Global does not say when an annual report
    was published. `filed` is the period end plus the LEGAL DEADLINE (four months for
    annual reports in the EU, the UK and Japan; three for interim reports), i.e. the
    latest date a compliant company could have published — never earlier than the truth.
    Where the quarterly table carries the report date (`rdq`), that is used instead.
    A late filer would still be seen too early; that is the remaining look-ahead, and
    the build prints how many facts rest on an estimate.
  * **CIK namespace**: `GVKEY_OFFSET + gvkey`, so a Compustat company can never collide
    with an SEC CIK (those stay below ten million) when both live in one store.

Written against the published Compustat Global layout WITHOUT access to WRDS from the
build environment: the translation is tested on hand-made files in that layout; the
download is not tested at all. PLAN.md records both.
"""

from __future__ import annotations

import datetime as dt
import decimal
from pathlib import Path

import duckdb
import pandas as pd

from . import history, industry
from .free import FreeSource

KEY_USER = "WRDS_USERNAME"
KEY_PASSWORD = "WRDS_PASSWORD"
# Optional second account for CRSP, when the first one's university does not license it
# (the FU's does not). Unset, CRSP is tried with the first account.
KEY_CRSP_USER = "WRDS_CRSP_USERNAME"
KEY_CRSP_PASSWORD = "WRDS_CRSP_PASSWORD"
HOST = "wrds-pgdata.wharton.upenn.edu"
PORT = 9737
DBNAME = "wrds"

GVKEY_OFFSET = 1_000_000_000
# The Compustat Global range ends here; CRSP lines without an SEC CIK live above it
# (data/crsp.py), and a Global rebuild must not touch them.
GLOBAL_LAST = 2 * GVKEY_OFFSET - 1

# Developed markets outside North America (Compustat Global has no US or Canadian
# companies; those are in Compustat North America). ISO-3 codes of the headquarters.
DEFAULT_COUNTRIES = ("AUS", "AUT", "BEL", "CHE", "DEU", "DNK", "ESP", "FIN", "FRA", "GBR",
                     "HKG", "IRL", "ISR", "ITA", "JPN", "NLD", "NOR", "NZL", "PRT", "SGP",
                     "SWE")
DEFAULT_SINCE = dt.date(2010, 1, 1)

ANNUAL_LAG_MONTHS = 4
INTERIM_LAG_MONTHS = 3
# A report date this far from the period end is a data error, not a report date.
MAX_RDQ_DAYS = 366
# The rate used for a price may be at most this old (holidays differ between markets).
FX_MAX_AGE_DAYS = 10

# Prices quoted in a currency's hundredth: London in pence, Johannesburg in cents,
# Tel Aviv in agorot.
SUBUNITS = {"GBX": ("GBP", 100.0), "GBP_P": ("GBP", 100.0), "ZAC": ("ZAR", 100.0),
            "ILA": ("ILS", 100.0)}

# Stored metric -> Compustat items, in order of preference. Annual (g_funda) and
# quarterly (g_fundq, suffix q). Items are in millions of the reporting currency.
# Compustat Global names some items differently from the North American file: net
# income there is NICON (consolidated), shares CSHOI or CSHPRIA, and it has no gross
# profit item at all — that one is sales minus cost of goods sold (see GROSS_PROFIT).
# The first candidate the WRDS table actually carries is used.
DURATION = {
    "revenue": ("sale",),
    "net_income": ("ni", "nicon", "ib"),
    "operating_cash_flow": ("oancf",),
    "capex": ("capx",),
    "operating_income": ("oiadp",),
    "depreciation_amortization": ("dp",),
    "interest_expense": ("xint",),
    "income_tax": ("txt",),
    "pretax_income": ("pi",),
    "gross_profit": ("gp",),
}
INSTANT = {
    "book_value": ("ceq",),
    "total_assets": ("at",),
    "total_liabilities": ("lt",),
    "long_term_debt": ("dltt",),
    "current_debt": ("dlc",),
    "cash": ("che",),
    "current_assets": ("act",),
    "current_liabilities": ("lct",),
}
SHARES = ("csho", "cshoi", "cshpria")
SHARES_Q = ("cshoq", "cshoiq", "cshprq")
# Fallback when no gross-profit item exists: sales minus cost of goods sold.
GROSS_PROFIT = ("sale", "cogs")
# Cash-flow items are year-to-date in the quarterly table (oancfy, capxy); summing them
# as quarters would double-count, so quarterly cash flow is not read. Annual only.
QUARTERLY_SKIP = ("operating_cash_flow", "capex")


def _quarterly(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{c}q" for c in items)


TABLES = ("g_company", "g_security", "g_secd", "g_funda", "g_fundq", "g_exrt_dly")
WANTED = {
    "g_company": ("gvkey", "conm", "loc", "fic", "sic", "prirow"),
    "g_security": ("gvkey", "iid", "tic", "isin", "exchg", "excntry", "tpci", "secstat",
                   "dldtei", "dlrsni"),
    "g_secd": ("gvkey", "iid", "datadate", "prcod", "prchd", "prcld", "prccd", "cshtrd",
               "ajexdi", "trfd", "curcdd", "cshoc", "qunit"),
    "g_funda": ("gvkey", "datadate", "fyear", "curcd", *SHARES, *GROSS_PROFIT,
                *(c for cands in (*DURATION.values(), *INSTANT.values()) for c in cands)),
    "g_fundq": ("gvkey", "datadate", "fyearq", "fqtr", "curcdq", "rdq", *SHARES_Q,
                *_quarterly(GROSS_PROFIT),
                *(c for m, cands in DURATION.items() if m not in QUARTERLY_SKIP
                  for c in _quarterly(cands)),
                *(c for cands in INSTANT.values() for c in _quarterly(cands))),
    "g_exrt_dly": ("datadate", "fromcurd", "tocurd", "exratd"),
}
# A name can be wanted for two reasons (sales is revenue and half of gross profit);
# asked for twice, the query would return the column twice.
WANTED = {t: tuple(dict.fromkeys(cols)) for t, cols in WANTED.items()}
def item_report(present: set[str], quarterly: bool) -> list[str]:
    """One line per metric that is read from an alternative item or not at all — what
    the build prints, so a missing figure is named rather than silently absent."""
    lines = []
    for metric in {**DURATION, **INSTANT}:
        if quarterly and metric in QUARTERLY_SKIP:
            continue
        preferred = ({**DURATION, **INSTANT}[metric][0] + ("q" if quarterly else ""))
        found = item_expression(present, metric, quarterly)
        if found is None:
            lines.append(f"{metric}: not available")
        elif found[1] != preferred:
            lines.append(f"{metric}: from {found[1]} (no {preferred})")
    shares = pick(present, SHARES_Q if quarterly else SHARES)
    lines.append(f"shares_outstanding: from {shares}" if shares else
                 "shares_outstanding: not in the report (the daily file's count is used)")
    if quarterly and "rdq" not in present:
        lines.append("report date (rdq) absent: every quarter dated by the legal deadline")
    return lines


def pick(present: set[str], candidates: tuple[str, ...]) -> str | None:
    """The first candidate item the table has, or None."""
    return next((c for c in candidates if c in present), None)


def item_expression(present: set[str], metric: str, quarterly: bool) -> tuple[str, str] | None:
    """(SQL expression over alias f, item name for the record) for one metric, or None."""
    table = {**DURATION, **INSTANT}[metric]
    cands = _quarterly(table) if quarterly else table
    chosen = pick(present, cands)
    if chosen:
        return f"f.{chosen}", chosen
    if metric == "gross_profit":
        sale, cogs = _quarterly(GROSS_PROFIT) if quarterly else GROSS_PROFIT
        if sale in present and cogs in present:
            return f"(f.{sale} - f.{cogs})", f"{sale}-{cogs}"
    return None


class NotConfigured(RuntimeError):
    """WRDS was chosen but the login is missing. The message is the fix."""


def missing_login_message() -> str:
    return (
        f"The world source needs a WRDS login. Add '{KEY_USER}=...' to .env.local and "
        f"either '{KEY_PASSWORD}=...' there or the password in ~/.pgpass (the values are "
        "never shown). Then run scripts/build_wrds.py once. Or switch back with "
        "PORTFOLIO_LAB_SOURCE=free."
    )


# -- download ----------------------------------------------------------------------------

def connect(user: str, password: str | None):
    """A connection to the WRDS database. With two-factor authentication on the
    account, WRDS sends a Duo push to the phone at this moment — approve it."""
    import psycopg2

    # WRDS user names are lower case, and the database compares them exactly: typed as
    # "MPFriedrich" the login fails with a bare "PAM authentication failed".
    user = user.strip().lower()
    try:
        return psycopg2.connect(host=HOST, port=PORT, dbname=DBNAME, user=user,
                                password=password, sslmode="require", connect_timeout=60)
    except psycopg2.OperationalError as exc:
        # The message can echo connection parameters; keep only its first line, which
        # says what went wrong ("password authentication failed", "timeout expired").
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        hint = ""
        if "authentication failed" in first:
            hint = (" — check WRDS_USERNAME and WRDS_PASSWORD in .env.local (the WRDS "
                    "login, not the university one), and approve the Duo push within a "
                    "minute; a declined or expired push fails the same way.")
        raise RuntimeError(f"WRDS connection failed: {first}{hint}") from None


def available_columns(con, table: str, wanted=None, schema: str = "comp") -> list[str]:
    """Which of the wanted columns the WRDS table really has. Asking first means a
    column Compustat does not carry (or renamed) costs that one field, not the build.
    Empty when the table does not exist or the subscription does not include it."""
    with con.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s", (schema, table))
        have = {r[0].lower() for r in cur.fetchall()}
    return [c for c in (wanted or WANTED[table]) if c in have]


def _fetch(con, sql: str, params: tuple, dest: Path, chunk: int = 250_000) -> int:
    """Stream a query into parquet part files under `dest`. Returns the row count.
    A server-side cursor keeps a country's daily prices from landing in memory at once."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    part = 0
    with con.cursor(name=f"pl_{dest.stem}_{part}") as cur:
        cur.itersize = chunk
        cur.execute(sql, params)
        while True:
            batch = cur.fetchmany(chunk)
            if not batch:
                break
            if cur.description is None:
                break
            cols = [d[0] for d in cur.description]
            frame = pd.DataFrame(batch, columns=cols)
            # PostgreSQL NUMERIC arrives as Decimal; parquet would store a decimal type
            # whose precision can differ between parts. Money and prices are floats here.
            for c in frame.columns:
                if frame[c].dtype == object:
                    first = frame[c].dropna().head(1)
                    if len(first) and isinstance(first.iloc[0], decimal.Decimal):
                        frame[c] = frame[c].astype(float)
            frame.to_parquet(dest.with_name(f"{dest.stem}_{part:03d}.parquet"), index=False)
            rows += len(frame)
            part += 1
    return rows


def download(con, dest: Path, countries=DEFAULT_COUNTRIES, since: dt.date = DEFAULT_SINCE,
             log=print) -> dict[str, int]:
    """Fetch the six Compustat Global tables for `countries` into parquet under `dest`.

    Daily prices come per country and year; a year that has ended and is already on
    disk is not fetched again, so the second run only brings the current year.
    """
    dest.mkdir(parents=True, exist_ok=True)
    countries = tuple(sorted({c.upper() for c in countries}))
    cols = {t: available_columns(con, t) for t in TABLES}
    for t in TABLES:
        if t in ("g_funda", "g_fundq"):
            for line in item_report(set(cols[t]), quarterly=t == "g_fundq"):
                log(f"  {t}: {line}")
            continue
        missing = set(WANTED[t]) - set(cols[t])
        if missing:
            log(f"  {t}: not in WRDS, skipped: {', '.join(sorted(missing))}")
    firms = "SELECT gvkey FROM comp.g_company WHERE loc IN %s"
    counts: dict[str, int] = {}

    def fresh(table: str) -> Path:
        folder = dest / table
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.parquet"):
            old.unlink()
        return folder / f"{table}.parquet"

    counts["g_company"] = _fetch(
        con, f"SELECT {', '.join(cols['g_company'])} FROM comp.g_company WHERE loc IN %s",
        (countries,), fresh("g_company"))
    counts["g_security"] = _fetch(
        con, f"SELECT {', '.join(cols['g_security'])} FROM comp.g_security "
             f"WHERE gvkey IN ({firms})", (countries,), fresh("g_security"))
    # The standard screen of Compustat fundamentals: industrial format, consolidated,
    # historical standardised data (as first reported, not restated), international
    # population source.
    std = "indfmt = 'INDL' AND datafmt = 'HIST_STD' AND consol = 'C' AND popsrc = 'I'"
    fund_since = since - dt.timedelta(days=2 * 366)
    counts["g_funda"] = _fetch(
        con, f"SELECT {', '.join(cols['g_funda'])} FROM comp.g_funda WHERE {std} "
             f"AND datadate >= %s AND gvkey IN ({firms})", (fund_since, countries),
        fresh("g_funda"))
    counts["g_fundq"] = _fetch(
        con, f"SELECT {', '.join(cols['g_fundq'])} FROM comp.g_fundq WHERE {std} "
             f"AND datadate >= %s AND gvkey IN ({firms})", (fund_since, countries),
        fresh("g_fundq"))
    counts["g_exrt_dly"] = _fetch(
        con, f"SELECT {', '.join(cols['g_exrt_dly'])} FROM comp.g_exrt_dly "
             "WHERE fromcurd = 'GBP' AND datadate >= %s", (since,), fresh("g_exrt_dly"))

    secd_dir = dest / "g_secd"
    secd_dir.mkdir(parents=True, exist_ok=True)
    this_year = dt.date.today().year
    counts["g_secd"] = 0
    select = ", ".join(f"d.{c}" for c in cols["g_secd"])
    for country in countries:
        for year in range(since.year, this_year + 1):
            stem = f"{country}_{year}"
            done = list(secd_dir.glob(f"{stem}_*.parquet"))
            if done and year < this_year:
                continue
            for old in done:
                old.unlink()
            n = _fetch(
                con,
                f"SELECT {select} FROM comp.g_secd d JOIN comp.g_company c "
                "ON c.gvkey = d.gvkey AND c.prirow = d.iid "
                "WHERE c.loc = %s AND d.datadate BETWEEN %s AND %s",
                (country, max(since, dt.date(year, 1, 1)), dt.date(year, 12, 31)),
                secd_dir / f"{stem}.parquet")
            counts["g_secd"] += n
            log(f"  prices {country} {year}: {n:,} rows")
    return counts


# -- translation -------------------------------------------------------------------------

def load_parquet(con, name: str, files: list[Path], view: bool = False) -> set[str]:
    """Make the parquet parts available as `name`; returns its columns.

    Small tables are copied into a temp table. The daily price files are only put
    behind a VIEW, so they are streamed from disk each time they are read: held in
    memory, 8.8 million rows for two countries filled a 16 GB laptop (first real run).
    """
    if not files:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT NULL AS gvkey LIMIT 0")
        return set()
    if view:
        listed = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
        con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM "
                    f"read_parquet([{listed}], union_by_name = true)")
    else:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM "
                    "read_parquet(?, union_by_name = true)", [[str(f) for f in files]])
        untyped_as_text(con, name)
    return {r[0].lower() for r in con.execute(f"DESCRIBE {name}").fetchall()}


def _load(con, table: str, source: Path) -> set[str]:
    return load_parquet(con, f"_{table}", sorted((source / table).glob("*.parquet")),
                        view=table == "g_secd")


def limit_memory(con) -> None:
    """Settings for a build over tens of millions of rows on a laptop: no promise of
    insertion order (lets DuckDB stream and spill) and a memory ceiling below the
    machine's, so a large step spills to disk instead of failing."""
    con.execute("SET preserve_insertion_order = false")
    try:
        import os

        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        con.execute(f"SET memory_limit = '{max(int(total * 0.5 / 2**30), 2)}GB'")
    except (ValueError, OSError, AttributeError):
        pass


def untyped_as_text(con, table: str) -> None:
    """A column WRDS returned entirely empty (ISIN and SEDOL are blank without their own
    licence) arrives in parquet with no type, which DuckDB reads as INTEGER — and then
    cannot be combined with the text column beside it. Such a column is text."""
    for name, kind, *_ in con.execute(f"DESCRIBE {table}").fetchall():
        if kind.upper() in ("INTEGER", '"NULL"', "NULL"):
            empty = con.execute(f'SELECT COUNT("{name}") FROM {table}').fetchone()[0] == 0
            if empty:
                con.execute(f'ALTER TABLE {table} ALTER "{name}" TYPE VARCHAR')


def _col(present: set[str], name: str, default: str = "NULL") -> str:
    """`name` (optionally qualified, e.g. 'f.fyear') if the table has it, else `default`."""
    return name if name.split(".")[-1] in present else default


def _quiet(_message: str) -> None:
    pass


def ingest(con: duckdb.DuckDBPyConnection, source: Path, log=_quiet) -> dict[str, int]:
    """Translate the Compustat parquet files under `source` into the market schema.

    Adds to what is there (the build copies the US store in first) and replaces only
    Compustat rows. One transaction: an interrupted build keeps the previous contents.
    """
    log("reading the Compustat Global files")
    limit_memory(con)
    cols = {t: _load(con, t, source) for t in TABLES}
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM prices WHERE ticker IN "
                    "(SELECT ticker FROM securities WHERE cik BETWEEN ? AND ?)", [GVKEY_OFFSET, GLOBAL_LAST])
        con.execute("DELETE FROM securities WHERE cik BETWEEN ? AND ?", [GVKEY_OFFSET, GLOBAL_LAST])
        con.execute("DELETE FROM fundamentals WHERE cik BETWEEN ? AND ?", [GVKEY_OFFSET, GLOBAL_LAST])

        log("exchange rates")
        # Dollars per unit of every currency, per day: the rates are quoted as units per
        # pound, so USD per X = (USD per GBP) / (X per GBP).
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE _fx AS
            WITH r AS (
                SELECT CAST(datadate AS DATE) AS date, upper(tocurd) AS cur,
                       CAST(exratd AS DOUBLE) AS per_gbp
                FROM _g_exrt_dly WHERE upper(fromcurd) = 'GBP' AND exratd > 0
                UNION ALL
                SELECT DISTINCT CAST(datadate AS DATE), 'GBP', 1.0 FROM _g_exrt_dly
            ), usd AS (SELECT date, per_gbp AS usd_per_gbp FROM r WHERE cur = 'USD')
            SELECT DISTINCT r.cur, r.date, usd.usd_per_gbp / r.per_gbp AS usd_per_unit
            FROM r JOIN usd USING (date)
            """
        )

        # One primary issue per company, common equity only (tpci '0').
        tpci = ("AND COALESCE(CAST(s.tpci AS VARCHAR), '0') = '0'"
                if "tpci" in cols["g_security"] else "")
        excntry = _col(cols["g_security"], "s.excntry", "c.loc")
        isin = "CAST(s.isin AS VARCHAR)" if "isin" in cols["g_security"] else "NULL"
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _issue AS
            SELECT {GVKEY_OFFSET} + CAST(c.gvkey AS BIGINT) AS cik,
                   CAST(c.gvkey AS VARCHAR) AS gvkey, s.iid,
                   upper(regexp_replace(trim(COALESCE(CAST(s.tic AS VARCHAR),
                                                      {isin}, CAST(c.gvkey AS VARCHAR))),
                                        '[^A-Za-z0-9]', '', 'g'))
                       || '.' || upper(COALESCE(CAST({excntry} AS VARCHAR),
                                                CAST(c.loc AS VARCHAR))) AS ticker,
                   c.conm AS name, CAST(c.sic AS VARCHAR) AS sic,
                   upper(COALESCE(CAST({excntry} AS VARCHAR),
                                  CAST(c.loc AS VARCHAR))) AS exchange
            FROM _g_company c JOIN _g_security s
              ON CAST(s.gvkey AS VARCHAR) = CAST(c.gvkey AS VARCHAR)
             AND CAST(s.iid AS VARCHAR) = CAST(c.prirow AS VARCHAR)
            WHERE TRUE {tpci}
            """
        )
        # Two companies can share a ticker within a country (a reused symbol); the
        # second one gets its gvkey appended so a ticker still names one company.
        con.execute(
            """
            UPDATE _issue SET ticker = ticker || '-' || gvkey
            WHERE cik IN (SELECT cik FROM (
                SELECT cik, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY cik) AS rn
                FROM _issue) WHERE rn > 1)
            """
        )

        log("daily prices in dollars (the long step)")
        # Daily bars in dollars. qunit: prices quoted per 100 or 1000 shares in some
        # markets. ajexdi: the cumulative split factor (dividing by it puts every day on
        # today's share basis). trfd: the total-return factor (dividends reinvested).
        qunit = ("COALESCE(NULLIF(CAST(d.qunit AS DOUBLE), 0), 1)"
                 if "qunit" in cols["g_secd"] else "1")
        sub_cases = " ".join(f"WHEN '{k}' THEN '{v[0]}'" for k, v in SUBUNITS.items())
        div_cases = " ".join(f"WHEN '{k}' THEN {v[1]}" for k, v in SUBUNITS.items())
        con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW _bars AS
            SELECT i.ticker, CAST(d.datadate AS DATE) AS date,
                   CASE upper(CAST(d.curcdd AS VARCHAR)) {sub_cases}
                        ELSE upper(CAST(d.curcdd AS VARCHAR)) END AS cur,
                   CAST(d.prccd AS DOUBLE) / {qunit}
                       / CASE upper(CAST(d.curcdd AS VARCHAR)) {div_cases} ELSE 1 END
                       AS px_local,
                   CAST(d.prcod AS DOUBLE) / NULLIF(CAST(d.prccd AS DOUBLE), 0) AS open_rel,
                   CAST(d.prchd AS DOUBLE) / NULLIF(CAST(d.prccd AS DOUBLE), 0) AS high_rel,
                   CAST(d.prcld AS DOUBLE) / NULLIF(CAST(d.prccd AS DOUBLE), 0) AS low_rel,
                   COALESCE(NULLIF(CAST(d.ajexdi AS DOUBLE), 0), 1) AS ajexdi,
                   CAST({_col(cols['g_secd'], 'd.trfd')} AS DOUBLE) AS trfd,
                   CAST(d.cshtrd AS DOUBLE) AS volume,
                   CAST(d.cshoc AS DOUBLE) AS cshoc
            FROM _g_secd d JOIN _issue i
              ON CAST(i.gvkey AS VARCHAR) = CAST(d.gvkey AS VARCHAR)
             AND CAST(i.iid AS VARCHAR) = CAST(d.iid AS VARCHAR)
            WHERE CAST(d.prccd AS DOUBLE) > 0
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW _bars_usd AS
            SELECT b.*, f.usd_per_unit
            FROM _bars b ASOF JOIN _fx f ON f.cur = b.cur AND b.date >= f.date
            WHERE b.date - f.date <= {FX_MAX_AGE_DAYS}
            """
        )
        all_bars = con.execute("SELECT COUNT(*) FROM _bars").fetchone()[0]
        priced = con.execute("SELECT COUNT(*) FROM _bars_usd").fetchone()[0]
        no_fx = all_bars - priced

        # The total-return series is anchored to the split-adjusted close where the factor
        # exists; without it, adjusted = split-adjusted (dividends then missing, as the
        # coverage note in PLAN.md says).
        con.execute(
            """
            INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, close_raw,
                                           close_adj, volume)
            SELECT ticker, date,
                   px_local * open_rel / ajexdi * usd_per_unit,
                   px_local * high_rel / ajexdi * usd_per_unit,
                   px_local * low_rel / ajexdi * usd_per_unit,
                   px_local / ajexdi * usd_per_unit,
                   px_local * usd_per_unit,
                   px_local / ajexdi * COALESCE(trfd, 1) * usd_per_unit,
                   CAST(volume AS BIGINT)
            FROM _bars_usd
            """
        )
        con.execute(
            """
            INSERT INTO securities (cik, ticker, name, sector, sic, exchange,
                                    first_seen, last_seen)
            SELECT i.cik, i.ticker, i.name, NULL, i.sic, i.exchange, p.lo, p.hi
            FROM _issue i JOIN (SELECT ticker, MIN(date) lo, MAX(date) hi FROM prices
                                WHERE ticker IN (SELECT ticker FROM _issue)
                                GROUP BY 1) p USING (ticker)
            """
        )
        # The coarse industry group stands in for the SEC's industry wording.
        sics = con.execute("SELECT DISTINCT sic FROM securities WHERE cik BETWEEN ? AND ?",
                           [GVKEY_OFFSET, GLOBAL_LAST]).fetchall()
        groups = pd.DataFrame({"sic": [s[0] for s in sics],
                               "grp": [industry.group_for(s[0]) for s in sics]})
        if not groups.empty:
            con.register("_groups", groups)
            con.execute("UPDATE securities SET sector = g.grp FROM _groups g "
                        "WHERE securities.sic = g.sic AND securities.cik BETWEEN ? AND ?",
                        [GVKEY_OFFSET, GLOBAL_LAST])
            con.unregister("_groups")

        # The dollar rates go into the price table under the names pit.to_usd reads, so
        # fundamentals in any currency convert at the screen date exactly as the SEC
        # foreign filers do. A rate already there (from Yahoo) is left alone.
        con.execute(
            """
            INSERT OR IGNORE INTO prices (ticker, date, open, high, low, close, close_raw,
                                          close_adj, volume)
            SELECT cur || 'USD=X', date, usd_per_unit, usd_per_unit, usd_per_unit,
                   usd_per_unit, usd_per_unit, usd_per_unit, 0
            FROM _fx WHERE cur <> 'USD'
            """
        )

        log("annual and quarterly figures")
        _fundamentals(con, cols)
        log("listing windows")
        history.refresh_price_windows(con, own_transaction=False)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    counts = {
        "companies": con.execute("SELECT COUNT(*) FROM securities WHERE cik BETWEEN ? AND ?",
                                 [GVKEY_OFFSET, GLOBAL_LAST]).fetchone()[0],
        "price_rows": int(priced),
        "price_rows_without_rate": int(no_fx),
        "facts": con.execute("SELECT COUNT(*) FROM fundamentals WHERE cik BETWEEN ? AND ?",
                             [GVKEY_OFFSET, GLOBAL_LAST]).fetchone()[0],
        "facts_on_estimated_date": con.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE cik BETWEEN ? AND ? AND fp = 'est'",
            [GVKEY_OFFSET, GLOBAL_LAST]).fetchone()[0],
    }
    return counts


def _fundamentals(con, cols: dict[str, set[str]]) -> None:
    """Annual and quarterly items as point-in-time facts.

    `fp` records how `filed` was obtained: 'rdq' (the report date Compustat gives) or
    'est' (period end + the legal deadline). Money is in millions of `currency`.
    """
    insert = ("INSERT INTO fundamentals (cik, metric, value, period_start, period_end, fy, "
              "fp, filed, accession, xbrl_tag, tag_rank, currency) ")
    join = "JOIN _issue i ON CAST(i.gvkey AS VARCHAR) = CAST(f.gvkey AS VARCHAR)"

    a = cols["g_funda"]
    if a:
        filed = f"CAST(f.datadate AS DATE) + INTERVAL {ANNUAL_LAG_MONTHS} MONTH"
        span = "CAST(CAST(f.datadate AS DATE) - INTERVAL 12 MONTH + INTERVAL 1 DAY AS DATE)"
        for metric in {**DURATION, **INSTANT}:
            found = item_expression(a, metric, quarterly=False)
            if not found:
                continue
            expr, item = found
            start = span if metric in DURATION else "NULL"
            con.execute(
                insert +
                f"""SELECT i.cik, '{metric}', CAST({expr} AS DOUBLE) * 1e6, {start},
                           CAST(f.datadate AS DATE), {_col(a, 'f.fyear')}, 'est',
                           CAST({filed} AS DATE),
                           'compustat:funda:' || i.gvkey || ':' || CAST(f.datadate AS VARCHAR),
                           'compustat:{item}', 1, upper(f.curcd)
                    FROM _g_funda f {join} WHERE {expr} IS NOT NULL"""
            )
        shares = pick(a, SHARES)
        if shares:
            con.execute(
                insert +
                f"""SELECT i.cik, 'shares_outstanding', CAST(f.{shares} AS DOUBLE) * 1e6, NULL,
                           CAST(f.datadate AS DATE), {_col(a, 'f.fyear')}, 'est',
                           CAST({filed} AS DATE),
                           'compustat:funda:' || i.gvkey || ':' || CAST(f.datadate AS VARCHAR),
                           'compustat:{shares}', 1, NULL
                    FROM _g_funda f {join} WHERE f.{shares} > 0"""
            )

    q = cols["g_fundq"]
    if q:
        est = f"CAST(f.datadate AS DATE) + INTERVAL {INTERIM_LAG_MONTHS} MONTH"
        if "rdq" in q:
            good = (f"f.rdq IS NOT NULL AND CAST(f.rdq AS DATE) >= CAST(f.datadate AS DATE) "
                    f"AND CAST(f.rdq AS DATE) - CAST(f.datadate AS DATE) <= {MAX_RDQ_DAYS}")
            filed = f"CASE WHEN {good} THEN CAST(f.rdq AS DATE) ELSE {est} END"
            how = f"CASE WHEN {good} THEN 'rdq' ELSE 'est' END"
        else:
            filed, how = est, "'est'"
        span = "CAST(CAST(f.datadate AS DATE) - INTERVAL 3 MONTH + INTERVAL 1 DAY AS DATE)"
        for metric in {**DURATION, **INSTANT}:
            if metric in QUARTERLY_SKIP:
                continue
            found = item_expression(q, metric, quarterly=True)
            if not found:
                continue
            expr, item = found
            start = span if metric in DURATION else "NULL"
            con.execute(
                insert +
                f"""SELECT i.cik, '{metric}', CAST({expr} AS DOUBLE) * 1e6, {start},
                           CAST(f.datadate AS DATE), {_col(q, 'f.fyearq')}, {how},
                           CAST({filed} AS DATE),
                           'compustat:fundq:' || i.gvkey || ':' || CAST(f.datadate AS VARCHAR),
                           'compustat:{item}', 0, upper(f.curcdq)
                    FROM _g_fundq f {join} WHERE {expr} IS NOT NULL"""
            )
        shares = pick(q, SHARES_Q)
        if shares:
            con.execute(
                insert +
                f"""SELECT i.cik, 'shares_outstanding', CAST(f.{shares} AS DOUBLE) * 1e6, NULL,
                           CAST(f.datadate AS DATE), {_col(q, 'f.fyearq')}, {how},
                           CAST({filed} AS DATE),
                           'compustat:fundq:' || i.gvkey || ':' || CAST(f.datadate AS VARCHAR),
                           'compustat:{shares}', 1, NULL
                    FROM _g_fundq f {join} WHERE f.{shares} > 0"""
            )

    # The share count the market sees: the primary issue's shares outstanding from the
    # daily file, one per month (the last trading day), known on that day. It is newer
    # than any report, so it is the one the screen uses; for a company with two share
    # classes it counts the primary line only and the market cap is understated.
    con.execute(
        insert +
        """SELECT i.cik, 'shares_outstanding', b.cshoc, NULL, b.date, NULL, 'market',
                  b.date, 'compustat:secd:' || i.gvkey || ':' || CAST(b.date AS VARCHAR),
                  'compustat:cshoc', 0, NULL
           FROM (SELECT ticker, date, cshoc, ROW_NUMBER() OVER (
                     PARTITION BY ticker, date_trunc('month', date) ORDER BY date DESC) rn
                 FROM _bars_usd WHERE cshoc > 0) b
           JOIN _issue i USING (ticker) WHERE b.rn = 1"""
    )


# -- the US half -------------------------------------------------------------------------

# Tables copied whole from the free store. Company tables are keyed by CIK, which the
# Compustat Global rows never share (GVKEY_OFFSET), so the halves can be replaced
# separately. When CRSP supplies the US prices (data/crsp.py), the companies it covers
# keep their CRSP lines and take only their SEC fundamentals from the free store.
US_BY_CIK = ("ticker_history", "shares_outstanding")
US_WHOLE = ("listing_ratios", "macro_series", "chokepoint_transits", "chokepoint_first")


def copy_us(con: duckdb.DuckDBPyConnection, market_db: Path, log=_quiet) -> dict[str, int]:
    """Replace the free-store part of the world store with the current free store.

    Without CRSP: every US company, its prices and filings, plus the indices, futures and
    exchange rates. With CRSP: the same series, the SEC filings of every company, the
    free store's own lines only for companies CRSP does not cover (listed after CRSP
    ends), and CRSP's still-trading lines continued with today's prices (`crsp.extend`).
    The free store is refreshed daily by build_prices.py; this brings that in.
    """
    from . import crsp

    con.execute(f"ATTACH '{market_db}' AS us (READ_ONLY)")
    try:
        con.execute("BEGIN")
        try:
            owned_ciks = f"""(SELECT cik FROM crsp_link UNION ALL
                              SELECT cik FROM securities WHERE cik >= {GVKEY_OFFSET})"""
            owned_tickers = f"(SELECT ticker FROM securities WHERE cik IN {owned_ciks})"
            log("US prices")
            # Only the series the free store supplies are replaced. Everything else not
            # owned by a company line stays — above all Compustat's exchange rates
            # (EURUSD=X, JPYUSD=X, ...), without which every non-US company is excluded
            # for want of a rate. A rate Yahoo also has is taken from Yahoo.
            con.execute(f"""DELETE FROM prices WHERE ticker NOT IN {owned_tickers}
                            AND ticker IN (SELECT DISTINCT ticker FROM us.prices)""")
            con.execute("""DELETE FROM prices WHERE ticker IN (SELECT ticker FROM securities
                            WHERE cik < ? AND cik NOT IN (SELECT cik FROM crsp_link))""",
                        [GVKEY_OFFSET])
            con.execute(
                f"""INSERT INTO prices BY NAME SELECT * FROM us.prices
                    WHERE ticker NOT IN {owned_tickers}
                      AND ticker NOT IN (SELECT ticker FROM us.securities
                                         WHERE cik IN (SELECT cik FROM crsp_link))""")
            con.execute(f"DELETE FROM securities WHERE cik < ? AND cik NOT IN {owned_ciks}",
                        [GVKEY_OFFSET])
            con.execute(
                f"""INSERT INTO securities BY NAME SELECT * FROM us.securities
                    WHERE cik < ? AND cik NOT IN {owned_ciks}
                      AND ticker NOT IN {owned_tickers}""", [GVKEY_OFFSET])
            log("SEC filings")
            # SEC filings for every US company, CRSP-covered ones included: they are dated
            # by the SEC, which is the point. CRSP's own facts are kept.
            con.execute(
                "DELETE FROM fundamentals WHERE cik < ? AND xbrl_tag NOT LIKE 'crsp:%' "
                "AND xbrl_tag NOT LIKE 'compustat-na:%'", [GVKEY_OFFSET])
            con.execute("INSERT INTO fundamentals BY NAME SELECT * FROM us.fundamentals "
                        "WHERE cik < ?", [GVKEY_OFFSET])
            for table in US_BY_CIK:
                con.execute(f"DELETE FROM {table} WHERE cik < ?", [GVKEY_OFFSET])
                con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM us.{table} "
                            f"WHERE cik < ? AND cik NOT IN {owned_ciks}", [GVKEY_OFFSET])
            for table in US_WHOLE:
                con.execute(f"DELETE FROM {table}")
                con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM us.{table}")
            log("continuing CRSP lines and listing windows")
            spliced = crsp.extend(con, "us")
            pruned = crsp.prune_compustat(con)
            history.refresh_price_windows(con, own_transaction=False)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.execute("DETACH us")
    out = {"us_companies": con.execute(
        "SELECT COUNT(DISTINCT cik) FROM securities WHERE cik < ?",
        [GVKEY_OFFSET]).fetchone()[0]}
    if crsp.crsp_end(con) is not None:
        out.update({"crsp_lines_continued": spliced["extended"],
                    "crsp_lines_without_current_prices": spliced["not_found"],
                    "compustat_na_facts_replaced_by_sec": pruned})
    return out


# -- the source --------------------------------------------------------------------------

def is_compustat(cik) -> bool:
    """True for a company that came from Compustat — no SEC filings to link to."""
    try:
        return int(cik) >= GVKEY_OFFSET
    except (TypeError, ValueError):
        return False


class WorldSource(FreeSource):
    """FreeSource's queries over the US store plus Compustat Global."""

    name = "world (Compustat Global via WRDS + SEC/Yahoo for the US)"

    @property
    def survivorship_free(self) -> bool:
        """True when the US half comes from CRSP and really holds listings that ended —
        measured, like the paid source. Compustat Global keeps its ended companies; the
        free US store does not, so without CRSP the store does not claim the correction.
        """
        try:
            row = self.con.execute(
                "SELECT COUNT(*) FROM crsp_link WHERE NOT active").fetchone()
        except duckdb.Error:
            return False
        return bool(row and row[0])


def country_counts(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Companies per listing country, with how many are still trading — printed by the
    build so the coverage is a number, not an impression."""
    return con.execute(
        """
        SELECT exchange AS country, COUNT(*) AS companies,
               COUNT(*) FILTER (WHERE last_seen >= (SELECT MAX(last_seen) FROM securities
                                                    WHERE cik BETWEEN ? AND ?) - 30) AS trading
        FROM securities WHERE cik BETWEEN ? AND ? GROUP BY 1 ORDER BY 2 DESC
        """,
        [GVKEY_OFFSET, GLOBAL_LAST, GVKEY_OFFSET, GLOBAL_LAST],
    ).fetch_df()
