"""Phase 7: the universe as it was, and an honest measure of how little of it survives.

Today's ticker file is a list of companies that still exist. Screening on it is fine;
backtesting on it is not, because everything that went bankrupt or was taken over is
simply absent, and a strategy tested only on survivors looks better than it was. The
error is large — it is the single biggest reason home-made backtests beat the market.

This module builds `ticker_history` from what the free stack actually knows, and then
measures the gap rather than papering over it:

  * **filing window** — from the SEC submissions file, the first and last filing of each
    CIK. That is a real observation of when a company was alive, available for dead and
    living filers alike.
  * **symbol** — from the same file's `tickers` field, which holds only CURRENT symbols.
    For a company that vanished it is empty, and no free source fills it: the cover-page
    tag `dei:TradingSymbol` the plan hoped for is a text fact, so it is not in the
    numeric companyfacts bulk file, and pulling it per filing means tens of thousands of
    requests.
  * **observed prices** — first and last bar actually stored for a symbol. This is what
    the price source gave us, not what the exchange recorded.

Every row says which of those it came from, because a validity window derived from
filings means something different from one derived from bars.

The measurement is the point of the phase. `recovery_rate()` draws CIKs that filed
around 2014 and nothing since 2019 — the vanished world — and reports two fractions: how
many get a symbol at all, and how many of those have prices. If the second is under 50%,
stage B runs on a survivor-based universe and the label has to say so, rather than
claiming a correction that never happened.
"""

from __future__ import annotations

import datetime as dt
import json
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

SOURCE_FILINGS = "sec_submissions_filing_window"
SOURCE_PRICES = "observed_prices"


def _iter_submissions(zip_path: Path, only: set[int] | None = None):
    """Yield (cik, document) for the main submission file of each filer.

    The archive also holds continuation files (`-submissions-001.json`) for filers with
    long histories; those carry older filings but no company-level fields, so the main
    file is the one to read.
    """
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.endswith(".json") or "-submissions-" in name:
                continue
            try:
                cik = int(name[3:].split(".")[0])
            except ValueError:
                continue
            if only is not None and cik not in only:
                continue
            try:
                yield cik, json.loads(archive.read(name))
            except (json.JSONDecodeError, KeyError):
                continue


def _filing_window(doc: dict) -> tuple[dt.date | None, dt.date | None]:
    """Earliest and latest filing date this filer has, across all its files.

    The continuation entries carry `filingFrom`/`filingTo`, so the full span is available
    without opening them.
    """
    dates: list[str] = []
    recent = doc.get("filings", {}).get("recent", {})
    dates += [d for d in recent.get("filingDate", []) if d]
    for extra in doc.get("filings", {}).get("files", []):
        dates += [d for d in (extra.get("filingFrom"), extra.get("filingTo")) if d]
    if not dates:
        return None, None
    return (dt.date.fromisoformat(min(dates)), dt.date.fromisoformat(max(dates)))


def build_ticker_history(
    con: duckdb.DuckDBPyConnection, submissions_zip: Path, only: set[int] | None = None
) -> dict[str, int]:
    """Fill `ticker_history`, one row per (cik, ticker, source).

    Deliberately NOT built from today's ticker file alone: each row carries the window it
    was observed over and the source that observed it, so a later query can ask "was this
    symbol valid in 2016" instead of assuming that today's list applied then.
    """
    if only is None:
        only = {int(r[0]) for r in con.execute(
            "SELECT DISTINCT cik FROM fundamentals").fetchall()}

    rows: list[tuple] = []
    seen_filers = 0
    with_symbol = 0
    for cik, doc in _iter_submissions(submissions_zip, only):
        seen_filers += 1
        first, last = _filing_window(doc)
        tickers = [t for t in (doc.get("tickers") or []) if t]
        if tickers:
            with_symbol += 1
        for ticker in tickers:
            rows.append((cik, ticker.upper(), first, last, SOURCE_FILINGS))

    con.execute("DELETE FROM ticker_history WHERE source = ?", [SOURCE_FILINGS])
    if rows:
        frame = pd.DataFrame(
            rows, columns=["cik", "ticker", "valid_from", "valid_to", "source"])
        con.register("_th", frame)
        con.execute(
            "INSERT INTO ticker_history (cik, ticker, valid_from, valid_to, source) "
            "SELECT cik, ticker, valid_from, valid_to, source FROM _th"
        )
        con.unregister("_th")

    refresh_price_windows(con)

    total = con.execute("SELECT COUNT(*) FROM ticker_history").fetchone()[0]
    return {
        "filers_read": seen_filers,
        "filers_with_a_current_symbol": with_symbol,
        "rows": int(total),
    }


def refresh_price_windows(con: duckdb.DuckDBPyConnection,
                          own_transaction: bool = True) -> int:
    """Rewrite the `observed_prices` rows from the bars currently stored.

    The second source: the bars we actually hold. Same symbol as the filing rows,
    different evidence — and where the two disagree, that disagreement is itself worth
    seeing. Called after every price update as well as by the Phase 7 build, because
    `universe_at` reads these windows: left at the date of the last Phase 7 build, every
    name would drop out of today's universe ten days later.
    """
    # One transaction: a failure after the DELETE must not leave the store without
    # windows, which would quietly switch the universe back to today's survivors. A
    # caller already inside its own transaction (the paid-source build) passes
    # own_transaction=False; DuckDB does not nest them.
    if own_transaction:
        con.execute("BEGIN")
    try:
        con.execute("DELETE FROM ticker_history WHERE source = ?", [SOURCE_PRICES])
        con.execute(
            """
            INSERT INTO ticker_history (cik, ticker, valid_from, valid_to, source)
            SELECT s.cik, p.ticker, p.lo, p.hi, ?
            FROM (SELECT ticker, MIN(date) lo, MAX(date) hi FROM prices GROUP BY 1) p
            JOIN securities s ON s.ticker = p.ticker
            """,
            [SOURCE_PRICES],
        )
        if own_transaction:
            con.execute("COMMIT")
    except Exception:
        if own_transaction:
            con.execute("ROLLBACK")
        raise
    return con.execute(
        "SELECT COUNT(*) FROM ticker_history WHERE source = ?", [SOURCE_PRICES]
    ).fetchone()[0]


# A last bar within this many days of the screen date still counts as listed: weekends,
# holidays and a lagging price source must not empty the universe for a day.
LISTING_GRACE_DAYS = 10


def windows_current(con: duckdb.DuckDBPyConnection) -> bool:
    """True when the stored price windows reach the newest bar we hold.

    False means `ticker_history` is empty or was built before the latest price update
    and never refreshed; reading it then would make living companies look delisted.
    """
    row = con.execute(
        "SELECT (SELECT MAX(valid_to) FROM ticker_history WHERE source = ?), "
        "(SELECT MAX(date) FROM prices p "
        " WHERE EXISTS (SELECT 1 FROM securities s WHERE s.ticker = p.ticker))",
        [SOURCE_PRICES],
    ).fetchone()
    windows_end, newest_bar = row
    if windows_end is None:
        return False
    return newest_bar is None or (newest_bar - windows_end).days <= LISTING_GRACE_DAYS


def universe_at(con: duckdb.DuckDBPyConnection, asof: dt.date) -> pd.DataFrame:
    """The securities tradable on `asof`, read from `ticker_history` (Phase 7).

    A symbol belongs to the universe when its observed price window covers the date.
    Name, sector and exchange come from `securities` where it still knows the filer;
    a vanished company that only `ticker_history` knows keeps its row with those fields
    empty rather than being dropped — dropping it is the survivorship this table exists
    to undo.

    A symbol with no price window at all is kept on every date, as the securities master
    always did: it cannot be priced, so the screener excludes and COUNTS it, instead of
    it disappearing from the arithmetic of the exclusions.
    """
    return con.execute(
        """
        WITH windows AS (
            SELECT cik, ticker, valid_from, valid_to FROM ticker_history WHERE source = ?
        ),
        listed AS (
            SELECT DISTINCT cik, ticker FROM windows
            WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)
        ),
        unpriced AS (
            SELECT cik, ticker FROM securities s
            WHERE NOT EXISTS (SELECT 1 FROM windows w WHERE w.ticker = s.ticker)
        ),
        members AS (SELECT * FROM listed UNION SELECT * FROM unpriced)
        SELECT m.ticker, m.cik, s.name, s.sector, s.sic, s.exchange
        FROM members m LEFT JOIN securities s ON s.cik = m.cik AND s.ticker = m.ticker
        """,
        [SOURCE_PRICES, asof, asof - dt.timedelta(days=LISTING_GRACE_DAYS)],
    ).fetch_df()


def symbol_at(con: duckdb.DuckDBPyConnection, cik: int, on: dt.date) -> str | None:
    """The symbol recorded for this filer on that date, if any."""
    row = con.execute(
        "SELECT ticker FROM ticker_history WHERE cik = ? "
        "AND (valid_from IS NULL OR valid_from <= ?) "
        "AND (valid_to IS NULL OR valid_to >= ?) "
        "ORDER BY CASE WHEN source = ? THEN 0 ELSE 1 END LIMIT 1",
        [cik, on, on, SOURCE_PRICES],
    ).fetchone()
    return row[0] if row else None


def vanished_sample(con: duckdb.DuckDBPyConnection, size: int = 20) -> pd.DataFrame:
    """CIKs that filed around 2014 and nothing since 2019, drawn from our own data.

    Drawn by a hash of the CIK rather than at random or by hand: the same sample comes
    back every run, and nobody can be accused of having chosen convenient companies.
    """
    return con.execute(
        """
        WITH span AS (
            SELECT cik, MIN(filed) AS first_filed, MAX(filed) AS last_filed
            FROM fundamentals GROUP BY cik
        )
        SELECT cik, first_filed, last_filed FROM span
        WHERE first_filed <= DATE '2014-12-31'
          AND last_filed  >= DATE '2014-01-01'
          AND last_filed  <  DATE '2019-01-01'
        ORDER BY hash(cik) LIMIT ?
        """,
        [size],
    ).fetch_df()


def recovery_rate(con: duckdb.DuckDBPyConnection, size: int = 20) -> dict:
    """How much of the vanished world can be put back together (Phase 7's measurement).

    Two fractions, and the second is the one that decides what stage B may claim:
    a symbol without prices cannot be valued, so it cannot enter a backtest either.
    """
    sample = vanished_sample(con, size)
    if sample.empty:
        return {"n": 0, "with_ticker": 0, "with_prices": 0,
                "ticker_rate": None, "price_rate": None, "sample": sample}

    found: list[str | None] = []
    priced: list[bool] = []
    for row in sample.itertuples():
        symbol = symbol_at(con, int(row.cik), row.last_filed)
        found.append(symbol)
        if symbol is None:
            priced.append(False)
            continue
        n = con.execute(
            "SELECT COUNT(*) FROM prices WHERE ticker = ? AND date <= ?",
            [symbol, row.last_filed],
        ).fetchone()[0]
        priced.append(bool(n))

    sample = sample.assign(symbol=found, has_prices=priced)
    n = len(sample)
    with_ticker = int(sum(s is not None for s in found))
    with_prices = int(sum(priced))
    return {
        "n": n,
        "with_ticker": with_ticker,
        "with_prices": with_prices,
        "ticker_rate": with_ticker / n,
        "price_rate": with_prices / n,
        "sample": sample,
    }


def survivor_based(recovery: dict, threshold: float = 0.50) -> bool:
    """True when the recovered universe is too thin to claim a survivorship correction.

    The plan's decision point: below this line the app says stage B runs on survivors,
    which is honest, instead of implying a correction that did not happen.
    """
    rate = recovery.get("price_rate")
    return rate is None or rate < threshold


def stored_recovery(con: duckdb.DuckDBPyConnection) -> dict | None:
    """The last measured recovery rates, or None if nobody has measured yet.

    Read rather than recomputed: the answer belongs next to every backward-looking
    number on screen, and recomputing it means reading a million JSON files.
    """
    row = con.execute(
        "SELECT measured_at, n_sample, n_with_ticker, n_with_prices "
        "FROM universe_recovery ORDER BY measured_at DESC LIMIT 1"
    ).fetchone()
    if not row or not row[1]:
        return None
    return {
        "measured_at": row[0],
        "n": int(row[1]),
        "with_ticker": int(row[2]),
        "with_prices": int(row[3]),
        "ticker_rate": row[2] / row[1],
        "price_rate": row[3] / row[1],
    }


def universe_verdict(recovery: dict | None) -> str:
    """One sentence for the screen, stating what the universe actually is.

    Written from the measurement rather than from the intention, so it cannot claim a
    correction that did not happen (PLAN.md Phase 7, the <50% branch).
    """
    if not recovery:
        return ("The historical universe has not been measured yet, so every "
                "backward-looking number here is flattered by survivors.")
    n, found, priced = recovery["n"], recovery["with_ticker"], recovery["with_prices"]
    verdict = ("SURVIVOR-BASED" if survivor_based(recovery) else "point-in-time")
    return (
        f"Universe: {verdict}. Of {n} filers drawn from our own data that stopped filing "
        f"before 2019, {found} could be given a ticker at all and {priced} had prices "
        "stored. Companies that went bankrupt or were taken over are therefore missing "
        "from every backward-looking figure, which moves all of them upward."
    )


def delisting_returns(con: duckdb.DuckDBPyConnection, tickers) -> dict[str, float]:
    """What holders of these tickers actually received when the listing ended (CRSP's
    delisting return), where the store knows it. A ticker without a row falls back to
    the assumed loss (R18). Empty for a store without the table."""
    tickers = sorted({str(t) for t in tickers})
    if not tickers:
        return {}
    try:
        rows = con.execute("SELECT ticker, ret FROM delisting_returns "
                           "WHERE ticker IN (SELECT UNNEST(?))", [tickers]).fetchall()
    except duckdb.Error:
        return {}
    return {str(t): float(r) for t, r in rows if r is not None}
