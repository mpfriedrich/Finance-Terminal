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
) -> dict[str, int]:
    """Download in chunks and upsert. Safe to re-run; existing bars are replaced."""
    stats = {"requested": len(tickers), "rows": 0, "tickers_with_data": 0}
    for i in range(0, len(tickers), chunk):
        batch = tickers[i : i + chunk]
        frame = _download(batch, start, end)
        if frame.empty:
            continue
        con.register("_px", frame)
        con.execute(
            "DELETE FROM prices WHERE ticker IN (SELECT DISTINCT ticker FROM _px) "
            "AND date BETWEEN ? AND ?",
            [start, end],
        )
        con.execute(
            "INSERT INTO prices (ticker, date, open, high, low, close, close_adj, "
            "volume, split) SELECT ticker, date, open, high, low, close, close_adj, "
            "volume, split FROM _px"
        )
        con.unregister("_px")
        stats["rows"] += len(frame)
        stats["tickers_with_data"] += frame.ticker.nunique()
        print(
            f"  prices {i + len(batch):>6}/{len(tickers)} · {stats['rows']:>9} rows",
            flush=True,
        )
    return stats


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
