"""Daily price bars from the free source, cached locally.

THREE closes are stored, and the reason there are three is a defect this module used to
carry. It claimed `close` was the unadjusted traded price. It is not: the free source
serves history already adjusted for splits, so `close` for Apple on 2020-08-28 is 124.81
where the stock actually traded at 499.23 — a factor of exactly the 4-for-1 split that
followed three days later. Nothing broke while the store only held recent bars, because
no split had happened since; on sixteen years of history it breaks every historical
market cap by the cumulative split factor, silently and always in the same direction.

  close      as delivered — adjusted for SPLITS but not dividends. Fine for returns
             between two dates, wrong for anything multiplied by a share count.
  close_raw  reconstructed traded price: `close` times the product of every split that
             happened AFTER that date. This is what the stock cost that day, and it is
             the only price that may meet a point-in-time share count.
  close_adj  adjusted for splits and dividends — total return. Performance uses this.

`split` keeps the event itself (4.0 on the day a 4-for-1 takes effect, NULL otherwise) so
the reconstruction can be redone or audited without another download.

`open` is stored because the rebalance fills at the NEXT day's open (PLAN.md R16).
Screening on a close and filling at that same close is look-ahead, and it is the default
mistake in almost every home-made backtest.
"""

from __future__ import annotations

import datetime as dt
import warnings

import duckdb
import pandas as pd

# Total-return index first; the plain S&P 500 index excludes dividends and would hand
# every portfolio ~2 points a year of fake outperformance (PLAN.md R11, A15).
BENCHMARK_CANDIDATES = ("^SP500TR", "SPY")
# What scripts/add_benchmark.py stores once and build_prices.py keeps current. They are
# not in `securities`, so the universe download never touched them: ^SP500TR and SPY
# stopped on 23.09.2026 (the last add_benchmark run) while every stock moved on.
BENCHMARK_SERIES = ("^SP500TR", "^GSPC", "SPY")


class YahooUnreachable(ConnectionError):
    """Yahoo did not answer: no connection, a timeout, a rate limit or a server error.

    Raised so that a cache wrapper does not store the failure as "no data" (review F6,
    03.10.2026). A ConnectionError, so `except ConnectionError` catches it too."""


def yahoo_answers(symbol: str, timeout: float = 10) -> None:
    """Return when Yahoo's chart endpoint answers for `symbol`; raise YahooUnreachable
    when it does not.

    Why a separate request: yfinance 1.7 never raises on a failed download. With its
    default `debug.hide_exceptions = True` a dropped connection during the timezone
    lookup is logged and turned into "possibly delisted; no timezone found" — the very
    same soft error an unknown symbol gets — and `download()` keeps that in a per-call
    context it does not return (`multi._DownloadCtx`; `shared._ERRORS` is no longer
    filled). `Ticker.dividends` is None in both cases. So the result of yfinance alone
    cannot tell "unreachable" from "no such symbol"; this probe can, at the cost of one
    small request that is only made after an empty answer.

    What counts as an answer: any HTTP status below 500 except 429 — 404 is Yahoo
    saying it has no such symbol, 200 a symbol that exists. What cannot be told apart:
    Yahoo failing for the download and recovering a moment later for the probe reads as
    "no data" (one cached empty answer, for the cache's lifetime); and a symbol Yahoo
    knows but serves no bars for in the window reads as "no data", which it is.

    The message names the error type only, never the address (R24).
    """
    try:
        from yfinance.const import _BASE_URL_
        from yfinance.data import YfData

        response = YfData().get(f"{_BASE_URL_}/v8/finance/chart/{symbol}",
                                params={"range": "5d", "interval": "1d"}, timeout=timeout)
        status = int(response.status_code)
    except Exception as exc:                                        # noqa: BLE001
        raise YahooUnreachable(f"Yahoo could not be reached ({type(exc).__name__})") \
            from None
    if status >= 500 or status == 429:
        raise YahooUnreachable(f"Yahoo could not be reached (HTTP {status})")


def _download(tickers: list[str], start: dt.date, end: dt.date) -> pd.DataFrame:
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(
            tickers,
            start=start.isoformat(),
            end=(end + dt.timedelta(days=1)).isoformat(),
            auto_adjust=False,  # we want BOTH Close and Adj Close
            actions=True,       # and the split events, to rebuild the traded price
            progress=False,
            threads=True,
            group_by="column",
        )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "close_adj",
                 "volume", "split"])

    frames: list[pd.DataFrame] = []
    single = not isinstance(raw.columns, pd.MultiIndex)
    for ticker in tickers:
        try:
            if single:
                sub = raw
            else:
                sub = pd.DataFrame(
                    {
                        field: raw[field][ticker]
                        for field in ("Open", "High", "Low", "Close", "Adj Close",
                                      "Volume", "Stock Splits")
                        if (field, ticker) in raw.columns
                    }
                )
        except KeyError:
            continue
        if sub.empty or "Close" not in sub:
            continue
        out = pd.DataFrame(
            {
                "ticker": ticker,
                "date": pd.to_datetime(sub.index).date,
                "open": sub.get("Open"),
                "high": sub.get("High"),
                "low": sub.get("Low"),
                "close": sub.get("Close"),
                "close_adj": sub.get("Adj Close", sub.get("Close")),
                "volume": sub.get("Volume"),
                "split": sub.get("Stock Splits"),
            }
        ).dropna(subset=["close"])
        frames.append(out)

    if not frames:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "close_adj",
                 "volume", "split"])
    result = pd.concat(frames, ignore_index=True)
    result["volume"] = result["volume"].fillna(0).astype("int64")
    # A split column of 0.0 means "no split that day"; store NULL so the reconstruction
    # cannot accidentally multiply a whole history by zero.
    if "split" in result:
        result["split"] = result["split"].replace(0.0, float("nan"))
    return result


def fetch_into_store(
    con: duckdb.DuckDBPyConnection,
    tickers: list[str],
    start: dt.date,
    end: dt.date,
    chunk: int = 200,
    download=None,
) -> dict:
    """Download in chunks and upsert. Safe to re-run; existing bars are replaced.

    A ticker without data is a note (`no_data`), never a failure: on any day a few of
    5,000 names are halted, delisted or renamed. A chunk that fails anyway is rolled back
    on its own, noted in `failed` with its error, and the next chunk goes on.

    The incident behind this (21.10.2026): Yahoo answers a halted ticker with its LAST
    bar even when that bar lies before `start` (BRR and SVA, both 14.10. for a window
    from 15.10.). The old delete covered only the window, so the insert hit the stored
    14.10. bar, the primary key raised, and the build died after chunk 3 of 27 —
    600 tickers got the new session, 4,764 did not. Its first chunks had committed, the
    window's delete had already run, and the yfinance "possibly delisted" lines on
    stderr (BLFS, BRNS) were taken for the cause. Rows are now replaced by their own
    keys as well as by the window, and each chunk is one transaction.
    """
    download = download or _download
    stats: dict = {"requested": len(tickers), "rows": 0, "tickers_with_data": 0,
                   "no_data": [], "failed": [], "refetched": []}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i : i + chunk]
        restated: list[str] = []
        try:
            frame = download(batch, start, end)
            if not frame.empty:
                frame = frame.drop_duplicates(["ticker", "date"], keep="last")
                restated = _restated(con, frame, start)
                _upsert(con, frame, start, end)
        except Exception as exc:                                    # noqa: BLE001
            stats["failed"].append(
                f"{batch[0]}..{batch[-1]}: {type(exc).__name__}: {str(exc)[:160]}")
            print(f"  prices {i + len(batch):>6}/{len(tickers)} · chunk "
                  f"{batch[0]}..{batch[-1]} failed: {type(exc).__name__}", flush=True)
            continue
        if restated:
            again = refetch_history(con, restated, end, download=download)
            stats["refetched"] += again["refetched"]
            stats["failed"] += again["failed"]
        got = set(frame.ticker) if not frame.empty else set()
        stats["no_data"] += [t for t in batch if t not in got]
        stats["rows"] += len(frame)
        stats["tickers_with_data"] += len(got)
        print(
            f"  prices {i + len(batch):>6}/{len(tickers)} · {stats['rows']:>9} rows",
            flush=True,
        )
    return stats


# Two answers for the same bar that differ by more than this are two bases, not noise.
RESTATED_TOLERANCE = 1e-6


def _restated(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame,
              start: dt.date) -> list[str]:
    """Tickers whose answer for a day differs from the bar stored for that same day,
    while older bars lie before the window: Yahoo has restated the whole history.

    Yahoo divides every close before a split by it, and scales every adjusted close
    before an ex-dividend day. The increment replaced only its window, so the bars
    before it kept the old basis: VTAK's 1-for-10 of 05.10.2026 left the series falling
    to a tenth overnight, and rebuild_raw_closes then multiplied the old, unadjusted
    closes by the split once more. Seven such breaks were in market.duckdb on 24.10.2026
    (AIXI, GMEX, DLXY, VIVK, VTAK, KNDI, FRGT; audit F3). A dividend did the same on a
    smaller scale and dropped out of total return. A late correction of one bar also
    counts as a restatement; the cost is one needless download."""
    con.register("_chk", frame)
    try:
        rows = con.execute(
            """
            SELECT DISTINCT x.ticker FROM _chk x JOIN prices p USING (ticker, date)
            WHERE x.date >= ? AND x.close > 0 AND p.close > 0
              AND (abs(x.close / p.close - 1) > ?
                   OR abs((x.close_adj / x.close) / (p.close_adj / p.close) - 1) > ?)
              AND EXISTS (SELECT 1 FROM prices q WHERE q.ticker = x.ticker
                          AND q.date < ?)
            ORDER BY 1
            """,
            [start, RESTATED_TOLERANCE, RESTATED_TOLERANCE, start],
        ).fetchall()
    finally:
        con.unregister("_chk")
    return [r[0] for r in rows]


def refetch_history(con: duckdb.DuckDBPyConnection, tickers: list[str], end: dt.date,
                    chunk: int = 50, download=None) -> dict:
    """Download each ticker's whole stored history again and replace it, one ticker at
    a time. A ticker whose answer starts later than its stored bars keeps those older
    bars (they are not deleted on a guess); a failing chunk is noted and skipped."""
    download = download or _download
    stats: dict = {"refetched": [], "failed": []}
    if not tickers:
        return stats
    first = dict(con.execute(
        "SELECT ticker, MIN(date) FROM prices WHERE ticker IN (SELECT UNNEST(?)) "
        "GROUP BY 1", [list(tickers)]).fetchall())
    for i in range(0, len(tickers), chunk):
        batch = [t for t in tickers[i : i + chunk] if t in first]
        if not batch:
            continue
        try:
            frame = download(batch, min(first[t] for t in batch), end)
            for t, sub in frame.groupby("ticker", sort=True):
                sub = sub.drop_duplicates(["ticker", "date"], keep="last")
                _upsert(con, sub, min(sub["date"]), end)
                stats["refetched"].append(t)
        except Exception as exc:                                    # noqa: BLE001
            stats["failed"].append(
                f"history {batch[0]}..{batch[-1]}: {type(exc).__name__}")
    print(f"  history fetched again (split or dividend restated it): "
          f"{' '.join(stats['refetched']) or '-'}", flush=True)
    return stats


def split_breaks(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Tickers whose stored close jumps by the split factor on a split day: the bars
    before it were never restated (stored before _restated existed, or a refetch that
    failed). A properly adjusted series barely moves at a split.

    Only splits of at least 3-for-2 (or 1-for-1.45) count: below that a real day's move
    and the factor cannot be told apart. Measured on market.duckdb, 24.10.2026: with a
    25 % floor BEP (1.251, 30.07.2020, an 8 % move) and FWONA (1.424, 18.04.2016, a jump
    that is in Yahoo's own history and comes back on every download) were flagged too."""
    return [r[0] for r in con.execute(
        """
        WITH s AS (SELECT ticker, date, split FROM prices
                   WHERE split IS NOT NULL AND abs(ln(split)) > ln(1.45)),
        p AS (SELECT ticker, date, close,
                     LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS before
              FROM prices WHERE ticker IN (SELECT ticker FROM s))
        SELECT DISTINCT ticker FROM s JOIN p USING (ticker, date)
        WHERE before > 0 AND close > 0 AND abs(before / close / split - 1) < 0.15
        ORDER BY 1
        """).fetchall()]


def _upsert(con: duckdb.DuckDBPyConnection, frame: pd.DataFrame,
            start: dt.date, end: dt.date) -> None:
    """Replace the window and every key in `frame` (see fetch_into_store), all or none.

    The window ends, per ticker, at its newest bar in `frame`. On 22.10.2026 around
    06:30 New York time Yahoo's batch answer left out the session of the 21st for most
    tickers while single requests still had it; a delete over the whole window threw
    away 21.10. bars already stored (SPY among them)."""
    con.register("_px", frame)
    con.begin()
    try:
        con.execute(
            "DELETE FROM prices p WHERE p.ticker IN (SELECT DISTINCT ticker FROM _px) "
            "AND (p.date BETWEEN ? AND LEAST(?, (SELECT MAX(x.date) FROM _px x "
            "WHERE x.ticker = p.ticker)) OR EXISTS (SELECT 1 FROM _px x "
            "WHERE x.ticker = p.ticker AND x.date = p.date))",
            [start, end],
        )
        con.execute(
            "INSERT INTO prices (ticker, date, open, high, low, close, close_adj, "
            "volume, split) SELECT ticker, date, open, high, low, close, close_adj, "
            "volume, split FROM _px"
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.unregister("_px")


def last_complete_session(
    con: duckdb.DuckDBPyConnection, share: float = 0.9, lookback: int = 20
) -> dt.date | None:
    """The newest session on which at least `share` of the usual number of equities has
    a bar; the usual number is the most seen on any of the last `lookback` sessions.

    The incremental build starts from here, not from MAX(date): after the aborted build
    of 21.10.2026, MAX(date) was the new session of the 600 tickers that got it, so the
    next run's window began after the session the other 4,764 were missing and never
    filled it. What this does not catch: one chunk failing day after day stays under
    the 10 % — that is what `failed` in the build's output is for.

    Sessions are counted on the equities in `securities`, as in session_coverage.
    """
    rows = con.execute(
        """
        SELECT p.date, COUNT(*) AS n FROM prices p
        WHERE EXISTS (SELECT 1 FROM securities s WHERE s.ticker = p.ticker)
        GROUP BY p.date ORDER BY p.date DESC LIMIT ?
        """,
        [lookback],
    ).fetchall()
    if not rows:
        return None
    usual = max(n for _, n in rows)
    return next(d for d, n in rows if n >= share * usual)


def fetch_benchmark(start: dt.date, end: dt.date) -> tuple[str, pd.DataFrame]:
    """Return (symbol_used, bars). Tries the total-return index before the ETF proxy."""
    for symbol in BENCHMARK_CANDIDATES:
        frame = _download([symbol], start, end)
        if not frame.empty and len(frame) > 200:
            return symbol, frame
    raise RuntimeError(
        "No benchmark series available from "
        f"{BENCHMARK_CANDIDATES}. Without a total-return benchmark the comparison "
        "would overstate the portfolio, so this is a hard stop rather than a fallback."
    )


def median_share_volume(
    con: duckdb.DuckDBPyConnection, asof: dt.date, window: int = 60
) -> pd.DataFrame:
    """Median daily SHARE volume — the input to the share-count sanity check."""
    return con.execute(
        """
        WITH recent AS (
            SELECT ticker, volume,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM prices WHERE date <= ?
        )
        SELECT ticker, MEDIAN(volume) AS median_share_volume_60d
        FROM recent WHERE rn <= ? GROUP BY ticker
        """,
        [asof, window],
    ).fetch_df()


def median_dollar_volume(
    con: duckdb.DuckDBPyConnection, asof: dt.date, window: int = 60
) -> pd.DataFrame:
    """Liquidity screen input (PLAN.md R15).

    The traded price times the traded volume. Both halves have to be on the same footing
    and they are not as delivered: the source adjusts prices for splits and leaves volume
    raw. Apple on 2020-08-28 traded 187.6m shares at 499.23, which is 93.7bn dollars;
    multiplying the split-adjusted 124.81 by that same volume gives 23.4bn, a quarter of
    the truth, for every company that has split since.
    """
    return con.execute(
        """
        WITH recent AS (
            SELECT ticker, COALESCE(close_raw, close) * volume AS dollar_volume,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM prices WHERE date <= ?
        )
        SELECT ticker, MEDIAN(dollar_volume) AS median_dollar_volume_60d
        FROM recent WHERE rn <= ? GROUP BY ticker
        """,
        [asof, window],
    ).fetch_df()


def suspicious_series(
    con: duckdb.DuckDBPyConnection,
    jump: float = 0.80,
    gap_days: int = 90,
) -> pd.DataFrame:
    """Tickers whose history looks like two different companies glued together.

    A symbol is reassigned after a company disappears, and a free price source will
    happily serve the old company's bars and the new one's under the same name. Splicing
    them produces a security that never existed, usually with a spectacular return across
    the join — which is exactly the kind of name a cheapness screen then falls in love
    with.

    Two signatures, both computed on the SPLIT-ADJUSTED close so that a 10-for-1 split
    (a −90% move in the raw close) is not mistaken for one:

      * a single-day move larger than `jump`
      * a gap of more than `gap_days` calendar days between consecutive bars

    Neither proves recycling on its own — a biotech can genuinely fall 85% in a day, and
    a halt can last months. This is a list to look at, not a list to delete.
    """
    return con.execute(
        """
        WITH steps AS (
            SELECT ticker, date, close_adj,
                   LAG(close_adj) OVER (PARTITION BY ticker ORDER BY date) AS prev,
                   date_diff('day', LAG(date) OVER (PARTITION BY ticker ORDER BY date),
                             date) AS gap
            FROM prices
            WHERE close_adj IS NOT NULL AND close_adj > 0
        )
        SELECT ticker,
               COUNT(*) FILTER (WHERE prev IS NOT NULL
                                AND abs(close_adj / prev - 1) > ?) AS big_moves,
               COUNT(*) FILTER (WHERE gap > ?) AS long_gaps,
               MAX(abs(close_adj / prev - 1)) FILTER (WHERE prev IS NOT NULL) AS worst_move,
               MAX(gap) AS longest_gap
        FROM steps
        GROUP BY ticker
        HAVING big_moves > 0 OR long_gaps > 0
        ORDER BY big_moves DESC, long_gaps DESC
        """,
        [jump, gap_days],
    ).fetch_df()


def session_coverage(
    con: duckdb.DuckDBPyConnection, asof: dt.date, window: int = 60
) -> pd.DataFrame:
    """On how many of the last `window` market sessions did each ticker actually trade?

    The liquidity floor asks how BIG the turnover is. This asks how OFTEN there is any —
    and the two come apart badly at the bottom of the market. Three names in the current
    universe traded on four days in the past year; the median of those four days cleared
    a million dollars, so a turnover floor waved them through. You cannot buy a stock on
    the 361 days it does not trade.

    Sessions are defined by the EQUITIES in the universe, not by every row in the price
    table: commodity futures and index series keep their own calendar, and counting their
    days as sessions would make every stock look like it missed some.
    """
    return con.execute(
        """
        WITH sessions AS (
            SELECT DISTINCT p.date FROM prices p
            WHERE p.date <= ?
              AND EXISTS (SELECT 1 FROM securities s WHERE s.ticker = p.ticker)
            ORDER BY p.date DESC LIMIT ?
        )
        SELECT p.ticker, COUNT(*) AS trading_days_60
        FROM prices p JOIN sessions USING (date)
        GROUP BY p.ticker
        """,
        [asof, window],
    ).fetch_df()


def rebuild_raw_closes(con: duckdb.DuckDBPyConnection) -> int:
    """Reconstruct the price each stock actually traded at, from the splits after it.

    `close_raw(t) = close(t) × Π split(d) for every d > t`

    Checked against Apple: 124.81 on 2020-08-28 times the 4-for-1 that took effect on
    2020-08-31 gives 499.23, which is what the stock cost that afternoon. Without this
    the market cap of every company that has split since is understated by exactly its
    cumulative split factor — Apple's 2020 market cap would come out four times too
    small, and a screen for cheap companies would find it.
    """
    con.execute(
        """
        UPDATE prices AS p SET close_raw = f.close_raw
        FROM (
            SELECT ticker, date,
                   close * exp(COALESCE(SUM(ln(split)) OVER (
                       PARTITION BY ticker ORDER BY date
                       ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING), 0)) AS close_raw
            FROM prices
        ) AS f
        WHERE f.ticker = p.ticker AND f.date = p.date
        """
    )
    return int(con.execute(
        "SELECT COUNT(*) FROM prices WHERE close_raw IS NOT NULL").fetchone()[0])


def non_prices(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Bars whose price is zero or negative, per ticker.

    Not a jump and therefore invisible to `suspicious_series`: the source carries long
    stretches of zero adjusted closes for some names (732 consecutive days for one name
    in this universe). A zero is not a collapse, it is an absent price — and dividing by
    one turns a whole backtest into an infinite return, which is how this was found.
    """
    return con.execute(
        """
        SELECT ticker, COUNT(*) AS days, MIN(date) AS first, MAX(date) AS last
        FROM prices
        WHERE close_adj IS NOT NULL AND close_adj <= 0
        GROUP BY ticker ORDER BY days DESC
        """
    ).fetch_df()
