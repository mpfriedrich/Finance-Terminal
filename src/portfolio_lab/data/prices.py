"""Daily price bars from the free source, cached locally.

Two closes are stored and they are not interchangeable:

  close      unadjusted — what the stock actually traded at that day. Market cap and every
             valuation ratio must use this. An adjusted price times a historical share
             count produces a market cap that never existed.
  close_adj  adjusted for splits and dividends — total return. Performance must use this.

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
            actions=False,
            progress=False,
            threads=True,
            group_by="column",
        )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "close_adj", "volume"])

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
                                      "Volume")
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
            }
        ).dropna(subset=["close"])
        frames.append(out)

    if not frames:
        return pd.DataFrame(columns=["ticker", "date", "open", "high", "low", "close", "close_adj", "volume"])
    result = pd.concat(frames, ignore_index=True)
    result["volume"] = result["volume"].fillna(0).astype("int64")
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
            "volume) SELECT ticker, date, open, high, low, close, close_adj, volume "
            "FROM _px"
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
    """Liquidity screen input (PLAN.md R15)."""
    return con.execute(
        """
        WITH recent AS (
            SELECT ticker, close * volume AS dollar_volume,
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
