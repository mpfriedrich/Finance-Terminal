"""Single-company research.

The screener answers "which companies match these rules". This answers the next question:
"what is actually going on at this one". Same data, different shape — the screen looks
across companies at one moment, research looks at one company across time.

Three things here are deliberate and not what a commercial terminal would show:

1. **Figures are as originally reported**, matching the rest of the app. A terminal
   usually shows the latest restated numbers, which quietly rewrites history.
2. **Restatements are surfaced as findings**, not smoothed away. A company that revised
   its equity or earnings after the fact is telling you something, and the size of the
   revision is research material rather than an inconvenience.
3. **Valuation history is rebuilt from the price on each reporting date**, not from
   today's price. What a company's price-to-earnings looked like in 2019 is a fact about
   2019, and it only means something against the earnings that were public then.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

ANNUAL_MIN, ANNUAL_MAX = 330, 400


def find_ticker(con: duckdb.DuckDBPyConnection, query: str) -> pd.DataFrame:
    """Ticker or name lookup, so a half-remembered company still gets found."""
    q = query.strip()
    if not q:
        return pd.DataFrame()
    return con.execute(
        """
        SELECT ticker, cik, name, sector, exchange
        FROM securities
        WHERE upper(ticker) = upper(?) OR name ILIKE '%' || ? || '%'
        ORDER BY CASE WHEN upper(ticker) = upper(?) THEN 0 ELSE 1 END, ticker
        LIMIT 25
        """,
        [q, q, q],
    ).fetch_df()


def profile(con: duckdb.DuckDBPyConnection, ticker: str) -> dict | None:
    row = con.execute(
        "SELECT ticker, cik, name, sector, sic, exchange, first_seen, last_seen "
        "FROM securities WHERE upper(ticker) = upper(?)",
        [ticker],
    ).fetchone()
    if not row:
        return None
    keys = ("ticker", "cik", "name", "sector", "sic", "exchange", "first_seen", "last_seen")
    out = dict(zip(keys, row))
    px = con.execute(
        "SELECT date, close, close_adj, volume FROM prices "
        "WHERE upper(ticker) = upper(?) ORDER BY date DESC LIMIT 1",
        [ticker],
    ).fetchone()
    if px:
        out |= {"as_of": px[0], "price": px[1], "volume": px[3]}
    return out


def price_history(
    con: duckdb.DuckDBPyConnection, ticker: str, years: int = 5
) -> pd.DataFrame:
    since = dt.date.today() - dt.timedelta(days=int(years * 365.25))
    return con.execute(
        "SELECT date, open, high, low, close, close_adj, volume FROM prices "
        "WHERE upper(ticker) = upper(?) AND date >= ? ORDER BY date",
        [ticker, since],
    ).fetch_df()


def bollinger(
    px: pd.DataFrame,
    window: int = 20,
    num_std: float = 2.0,
    column: str = "close",
) -> pd.DataFrame:
    """Bollinger bands: a moving average with a volatility envelope around it.

    middle = the `window`-day simple moving average
    upper  = middle + num_std × the standard deviation of the same window
    lower  = middle − num_std × the same

    Two choices that change the numbers, so they are stated rather than buried:

      * The deviation is the POPULATION one (ddof=0). That is Bollinger's own
        definition; pandas defaults to the sample deviation (ddof=1), which on a
        20-day window runs about 2.6% wider — small, but it is the difference between
        a price touching the band and not.
      * The bands are computed on the SAME series the candles are drawn from, the
        unadjusted close. Mixing an adjusted average with unadjusted candles puts the
        envelope in the wrong place around every split and dividend.

    The first `window - 1` rows are NaN on purpose: a 20-day band computed from 3 days
    is not an early reading, it is a different and much narrower statistic.
    """
    out = pd.DataFrame(index=px.index)
    series = pd.to_numeric(px[column], errors="coerce")
    rolling = series.rolling(window=window, min_periods=window)
    middle = rolling.mean()
    spread = rolling.std(ddof=0) * float(num_std)
    out["middle"] = middle
    out["upper"] = middle + spread
    out["lower"] = middle - spread
    # Where in the envelope the price sits: 1 at the upper band, 0 at the lower. The
    # conventional %B, useful because "near the top of its range" is the actual question.
    width = out["upper"] - out["lower"]
    out["percent_b"] = (series - out["lower"]).where(width > 0) / width.where(width > 0)
    return out


def has_intraday_range(px: pd.DataFrame) -> bool:
    """True when high and low are present for enough rows to draw candles.

    Bars stored before the range columns existed carry NULLs. Drawing a candle from
    open and close alone would invent a wickless chart that looks like data.
    """
    if not {"high", "low"}.issubset(px.columns) or px.empty:
        return False
    both = px["high"].notna() & px["low"].notna()
    return bool(both.sum() >= 2)


def reporting_currency(con: duckdb.DuckDBPyConnection, cik: int) -> str:
    """The currency this filer's amounts are stored in — 'USD' for domestic filers.

    The history tables show figures as filed, in this currency; only the screen and the
    valuation convert to dollars, at the rate of their date.
    """
    row = con.execute(
        "SELECT arg_max(currency, filed) FROM fundamentals "
        "WHERE cik = ? AND currency IS NOT NULL", [cik],
    ).fetchone()
    return (row[0] if row and row[0] else "USD")


def annual_history(con: duckdb.DuckDBPyConnection, cik: int) -> pd.DataFrame:
    """One row per fiscal year, as first reported.

    Balance-sheet items are taken at the year-end date; income and cash-flow items from
    the ~12-month span ending there. Mixing those up is the standard way to produce a
    margin that never existed.
    """
    flows = con.execute(
        """
        WITH first_filed AS (
            SELECT cik, metric, value, period_start, period_end, filed,
                   ROW_NUMBER() OVER (
                       -- period_end only: the WHERE already fixes the length class,
                       -- and a restatement may shift the start by days (see pit.py).
                       PARTITION BY cik, metric, period_end
                       ORDER BY filed ASC, tag_rank ASC
                   ) AS rn
            FROM fundamentals
            WHERE cik = ? AND period_start IS NOT NULL
              AND date_diff('day', period_start, period_end) BETWEEN ? AND ?
        )
        SELECT metric, value, period_end, filed FROM first_filed WHERE rn = 1
        """,
        [cik, ANNUAL_MIN, ANNUAL_MAX],
    ).fetch_df()

    stocks = con.execute(
        """
        WITH first_filed AS (
            SELECT cik, metric, value, period_end, filed,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik, metric, period_end
                       ORDER BY filed ASC, tag_rank ASC
                   ) AS rn
            FROM fundamentals
            WHERE cik = ? AND period_start IS NULL
        )
        SELECT metric, value, period_end, filed FROM first_filed WHERE rn = 1
        """,
        [cik],
    ).fetch_df()

    if flows.empty and stocks.empty:
        return pd.DataFrame()

    both = pd.concat([flows, stocks], ignore_index=True)
    both["period_end"] = pd.to_datetime(both["period_end"])
    both["year"] = both.period_end.dt.year

    wide = both.pivot_table(
        index="year", columns="metric", values="value", aggfunc="last"
    ).sort_index()
    filed = both.groupby("year")["filed"].min().rename("first_filed")
    out = wide.join(filed)

    for name in ("revenue", "net_income", "operating_cash_flow", "capex",
                 "book_value", "total_assets", "total_liabilities", "cash",
                 "shares_outstanding"):
        if name not in out:
            out[name] = np.nan

    out["free_cash_flow"] = out["operating_cash_flow"] - out["capex"].abs().fillna(0)
    out["net_margin"] = np.where(out.revenue > 0, out.net_income / out.revenue, np.nan)
    out["return_on_equity"] = np.where(
        out.book_value > 0, out.net_income / out.book_value, np.nan
    )
    out["debt_to_assets"] = np.where(
        out.total_assets > 0, out.total_liabilities / out.total_assets, np.nan
    )
    return out.reset_index()


def restatements(con: duckdb.DuckDBPyConnection, cik: int, min_pct: float = 0.02) -> pd.DataFrame:
    """Where a later filing changed a previously reported figure.

    Not an accusation — revisions are routine and often tiny. But a large one, or a
    pattern of them, is exactly the kind of thing a screen cannot see and a person
    researching a company would want to know.
    """
    df = con.execute(
        """
        SELECT metric, period_end, value, filed, accession
        FROM fundamentals
        WHERE cik = ? AND value IS NOT NULL
        ORDER BY metric, period_end, filed
        """,
        [cik],
    ).fetch_df()
    if df.empty:
        return df

    rows = []
    for (metric, period), grp in df.groupby(["metric", "period_end"], sort=False):
        grp = grp.drop_duplicates(subset=["value"], keep="first")
        if len(grp) < 2:
            continue
        first, last = grp.iloc[0], grp.iloc[-1]
        if not first.value:
            continue
        change = (last.value - first.value) / abs(first.value)
        if abs(change) >= min_pct:
            rows.append({
                "metric": metric, "period_end": period,
                "first_reported": first.value, "first_filed": first.filed,
                "later_value": last.value, "later_filed": last.filed,
                "change": change,
            })
    out = pd.DataFrame(rows)
    return out.sort_values("change", key=abs, ascending=False) if not out.empty else out


def valuation_history(
    con: duckdb.DuckDBPyConnection, ticker: str, cik: int
) -> pd.DataFrame:
    """Multiples as they stood on each reporting date.

    Uses the unadjusted close nearest each fiscal year end and the figures public at that
    time. Computing a 2019 price-to-earnings from today's price would describe nothing
    that ever happened.
    """
    hist = annual_history(con, cik)
    if hist.empty:
        return hist
    px = con.execute(
        "SELECT date, close FROM prices WHERE upper(ticker) = upper(?) ORDER BY date",
        [ticker],
    ).fetch_df()
    if px.empty:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"])

    # A foreign filer: its figures are in its own currency and its share count is in
    # ordinary shares, while the price is in dollars per LISTED share. Both have to be
    # put on the price's basis, or a Toyota-style ADR shows a multiple ten times too
    # high. Without a rate or a measured ratio, no multiple rather than a wrong one.
    from .data import listing, pit

    currency = reporting_currency(con, cik)
    form = con.execute(
        "SELECT arg_max(form, filed) FROM fundamentals WHERE cik = ? AND form IS NOT NULL",
        [cik]).fetchone()
    ratio = 1.0
    if form and form[0] in listing.FOREIGN_FORMS:
        ratio = listing.stored_ratios(con).get(ticker.upper())
        if ratio is None:
            return pd.DataFrame()

    rows = []
    for r in hist.itertuples():
        target = pd.Timestamp(year=int(r.year), month=12, day=31)
        near = px[px.date <= target]
        if near.empty or pd.isna(r.shares_outstanding):
            continue
        fx = pit.fx_to_usd(con, [currency], target.date()).get(currency)
        if fx is None:
            continue
        r = r._replace(net_income=r.net_income * fx, book_value=r.book_value * fx,
                       revenue=r.revenue * fx)
        price = near.iloc[-1].close
        cap = price * r.shares_outstanding / ratio
        rows.append({
            "year": int(r.year), "price": price, "market_cap": cap,
            "price_to_earnings": cap / r.net_income if r.net_income and r.net_income > 0 else np.nan,
            "price_to_book": cap / r.book_value if r.book_value and r.book_value > 0 else np.nan,
            "price_to_sales": cap / r.revenue if r.revenue and r.revenue > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def peers(frame: pd.DataFrame, ticker: str, limit: int = 12) -> pd.DataFrame:
    """Same industry, closest in size.

    Size matters as much as industry: a $400m miner and a $90bn miner do not trade on
    comparable multiples, and lining them up side by side invites a wrong conclusion.
    """
    row = frame[frame.ticker.astype(str).str.upper() == ticker.upper()]
    if row.empty:
        return pd.DataFrame()
    row = row.iloc[0]
    if pd.isna(row.get("sector")):
        return pd.DataFrame()
    same = frame[
        (frame.sector == row.sector)
        & (frame.ticker.astype(str).str.upper() != ticker.upper())
        & frame.market_cap.notna()
    ].copy()
    if same.empty:
        return same
    same["size_gap"] = (np.log10(same.market_cap) - np.log10(row.market_cap)).abs()
    return same.nsmallest(limit, "size_gap")


# --------------------------------------------------------------------------- live quote
def live_quote(ticker: str) -> dict | None:
    """Fetch the current price for ONE company, on demand.

    Deliberately not used by the screener. A screen has to value every company at the
    same moment or the comparison is meaningless — mixing a live quote for one name with
    Friday's close for the rest would quietly rank that one differently. Research looks
    at a single company, so there is nothing to keep comparable.
    """
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).fast_info
        out = {
            "price": info.get("lastPrice"),
            "previous_close": info.get("previousClose"),
            "open": info.get("open"),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "year_high": info.get("yearHigh"),
            "year_low": info.get("yearLow"),
            "volume": info.get("lastVolume"),
            "currency": info.get("currency"),
        }
    except Exception:
        return None
    if not out.get("price"):
        return None
    prev = out.get("previous_close")
    out["change"] = (out["price"] / prev - 1.0) if prev else None
    return out


# ----------------------------------------------------------------- return and risk
def price_stats(
    con: duckdb.DuckDBPyConnection, ticker: str, benchmark: str = "^SP500TR"
) -> dict:
    """Trailing returns, volatility, drawdown and beta — the numbers a terminal leads with.

    Beta is measured against the same total-return index the tracking view uses, on daily
    returns over the stored window. It is a description of the past, not a forecast, and
    a short window makes it noisy — the number of days used is reported alongside.
    """
    px = con.execute(
        "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) ORDER BY date",
        [ticker],
    ).fetch_df()
    if len(px) < 30:
        return {}
    px["date"] = pd.to_datetime(px["date"])
    s = px.set_index("date")["close_adj"].astype(float)
    rets = s.pct_change().dropna()

    out: dict = {"days": len(s), "from": s.index[0].date(), "to": s.index[-1].date()}
    for label, days in (("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)):
        out[f"return_{label}"] = (s.iloc[-1] / s.iloc[-days] - 1.0) if len(s) > days else np.nan

    out["volatility"] = rets.std() * np.sqrt(252) if len(rets) > 20 else np.nan
    running_max = s.cummax()
    out["max_drawdown"] = float((s / running_max - 1.0).min())
    out["high"] = float(s.max())
    out["low"] = float(s.min())

    bench = con.execute(
        "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date", [benchmark]
    ).fetch_df()
    if len(bench) > 30:
        bench["date"] = pd.to_datetime(bench["date"])
        b = bench.set_index("date")["close_adj"].astype(float).pct_change().dropna()
        joined = pd.concat([rets.rename("a"), b.rename("b")], axis=1).dropna()
        if len(joined) > 30 and joined.b.var() > 0:
            out["beta"] = float(joined.a.cov(joined.b) / joined.b.var())
            out["beta_days"] = len(joined)
    # Trend levels every chart-watcher checks first.
    out["ma50"] = float(s.tail(50).mean()) if len(s) >= 50 else np.nan
    out["ma200"] = float(s.tail(200).mean()) if len(s) >= 200 else np.nan
    return out


# ----------------------------------------------------------------------- SEC filings
FILING_LABELS = {
    "10-K": "annual report", "10-Q": "quarterly report", "8-K": "material event",
    "4": "insider trade", "3": "insider initial", "5": "insider annual",
    "DEF 14A": "proxy", "S-1": "registration", "S-3": "shelf registration",
    "SC 13D": "activist stake", "SC 13G": "passive stake", "424B5": "offering",
}


def filings(
    zip_path, cik: int, forms: list[str] | None = None, limit: int = 40
) -> pd.DataFrame:
    """Recent SEC filings for one company, straight out of the local archive.

    This is the part of research a screen can never do: the numbers tell you what a
    company looked like, the filing list tells you what it has been doing lately —
    a shelf registration, a sudden run of insider sales, an activist stake.
    """
    import json
    import zipfile

    member = f"CIK{int(cik):010d}.json"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if member not in set(zf.namelist()):
                return pd.DataFrame()
            with zf.open(member) as fh:
                doc = json.load(fh)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
        return pd.DataFrame()

    recent = (doc.get("filings") or {}).get("recent") or {}
    if not recent.get("form"):
        return pd.DataFrame()
    df = pd.DataFrame({
        "form": recent["form"],
        "filed": recent["filingDate"],
        "period": recent.get("reportDate", [""] * len(recent["form"])),
        "description": recent.get("primaryDocDescription", [""] * len(recent["form"])),
        "accession": recent["accessionNumber"],
        "document": recent.get("primaryDocument", [""] * len(recent["form"])),
    })
    if forms:
        df = df[df.form.isin(forms)]
    df = df.head(limit).copy()
    df["what"] = df.form.map(FILING_LABELS).fillna("")
    df["url"] = [
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{a.replace('-', '')}/{d}"
        for a, d in zip(df.accession, df.document)
    ]
    return df


def company_details(zip_path, cik: int) -> dict:
    """Registration facts the price data cannot tell you."""
    import json
    import zipfile

    member = f"CIK{int(cik):010d}.json"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if member not in set(zf.namelist()):
                return {}
            with zf.open(member) as fh:
                doc = json.load(fh)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
        return {}
    fy = doc.get("fiscalYearEnd") or ""
    return {
        "entity_type": doc.get("entityType"),
        "incorporated_in": doc.get("stateOfIncorporationDescription")
                           or doc.get("stateOfIncorporation"),
        "fiscal_year_end": f"{fy[:2]}-{fy[2:]}" if len(fy) == 4 else None,
        "phone": doc.get("phone"),
        "website": doc.get("website") or doc.get("investorWebsite"),
        "former_names": [n.get("name") for n in (doc.get("formerNames") or [])],
        "has_insider_filings": bool(doc.get("insiderTransactionForIssuerExists")),
    }


def quarterly_trend(con: duckdb.DuckDBPyConnection, cik: int, quarters: int = 12) -> pd.DataFrame:
    """Revenue and earnings by quarter — the shape annual figures hide.

    A company can grow every year while shrinking for three straight quarters. Annual
    rows cannot show that; this can.
    """
    df = con.execute(
        """
        WITH first_filed AS (
            SELECT metric, value, period_start, period_end, filed,
                   ROW_NUMBER() OVER (
                       PARTITION BY metric, period_end
                       ORDER BY filed ASC, tag_rank ASC
                   ) AS rn
            FROM fundamentals
            WHERE cik = ? AND period_start IS NOT NULL
              AND metric IN ('revenue', 'net_income')
              AND date_diff('day', period_start, period_end) BETWEEN 80 AND 100
        )
        SELECT metric, value, period_end FROM first_filed WHERE rn = 1
        ORDER BY period_end
        """,
        [cik],
    ).fetch_df()
    if df.empty:
        return df
    wide = df.pivot_table(index="period_end", columns="metric", values="value",
                          aggfunc="last").sort_index().tail(quarters)
    if "revenue" in wide and "net_income" in wide:
        wide["margin"] = np.where(wide.revenue > 0, wide.net_income / wide.revenue, np.nan)
    return wide.reset_index()


# ------------------------------------------------------------------ market intelligence
def _ticker(symbol: str):
    import yfinance as yf

    return yf.Ticker(symbol)


def news(symbol: str, limit: int = 12) -> pd.DataFrame:
    """Recent headlines. Titles and links only — the article text is not ours to copy."""
    try:
        raw = _ticker(symbol).news or []
    except Exception:
        return pd.DataFrame()
    rows = []
    for item in raw[:limit]:
        c = item.get("content") or item
        provider = (c.get("provider") or {}).get("displayName") or c.get("publisher") or ""
        link = ((c.get("canonicalUrl") or {}).get("url")
                or (c.get("clickThroughUrl") or {}).get("url") or c.get("link") or "")
        rows.append({
            "when": (c.get("pubDate") or c.get("displayTime") or "")[:10],
            "title": c.get("title") or "",
            "summary": (c.get("summary") or "")[:220],
            "source": provider,
            "url": link,
        })
    return pd.DataFrame(rows)


def analyst_view(symbol: str) -> dict:
    """What the sell side currently says. Opinions, not facts — labelled as such."""
    out: dict = {}
    t = _ticker(symbol)
    try:
        recs = t.recommendations
        if recs is not None and len(recs):
            latest = recs.iloc[0]
            out["ratings"] = {
                "strong buy": int(latest.get("strongBuy", 0)),
                "buy": int(latest.get("buy", 0)),
                "hold": int(latest.get("hold", 0)),
                "sell": int(latest.get("sell", 0)),
                "strong sell": int(latest.get("strongSell", 0)),
            }
            out["ratings_period"] = str(latest.get("period", ""))
    except Exception:
        pass
    try:
        out["targets"] = t.analyst_price_targets or {}
    except Exception:
        pass
    try:
        ud = t.upgrades_downgrades
        if ud is not None and len(ud):
            recent = ud.sort_index(ascending=False).head(8).reset_index()
            out["changes"] = recent
    except Exception:
        pass
    return out


def holders(symbol: str) -> pd.DataFrame:
    """Largest institutional positions, as last filed with the SEC."""
    try:
        h = _ticker(symbol).institutional_holders
    except Exception:
        return pd.DataFrame()
    return h if h is not None else pd.DataFrame()


def dividend_history(symbol: str, years: int = 10) -> pd.DataFrame:
    try:
        d = _ticker(symbol).dividends
    except Exception:
        return pd.DataFrame()
    if d is None or not len(d):
        return pd.DataFrame()
    df = d.reset_index()
    df.columns = ["date", "dividend"]
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=int(years * 365.25))
    return df[df.date >= cutoff].sort_values("date")


def earnings_history(symbol: str, limit: int = 12) -> pd.DataFrame:
    """Reported earnings against what was expected.

    The surprise column is the part a screen cannot see: a company can beat every quarter
    and still be expensive, or miss every quarter and still be cheap.
    """
    try:
        e = _ticker(symbol).earnings_dates
    except Exception:
        return pd.DataFrame()
    if e is None or not len(e):
        return pd.DataFrame()
    df = e.reset_index()
    df.columns = [str(c) for c in df.columns]
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], utc=True).dt.tz_localize(None)
    return df.sort_values(date_col, ascending=False).head(limit)


# ----------------------------------------------------------------------- commodities
# Front-month futures and two macro series. Chosen because they are the inputs that show
# up in real cost structures: energy, industrial metals, precious metals, grains, plus
# the dollar and the long rate that price everything else.
CRYPTO = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
    "XRP-USD": "XRP", "ADA-USD": "Cardano", "DOGE-USD": "Dogecoin",
    "LTC-USD": "Litecoin", "LINK-USD": "Chainlink",
}


def is_equity(con: duckdb.DuckDBPyConnection, symbol: str) -> bool:
    """Does this symbol have filings behind it?

    The dividing line the whole app turns on. A share has a balance sheet, so book value,
    earnings and every ratio built on them mean something. A token has a price and
    nothing else — not because the data is missing, but because there is no issuer
    filing accounts. Asking for Bitcoin's price-to-book is not a gap to fill; the
    quantity does not exist.
    """
    row = con.execute(
        "SELECT 1 FROM securities WHERE upper(ticker) = upper(?) LIMIT 1", [symbol]
    ).fetchone()
    return bool(row)


def price_only_symbols(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Everything in the price store that is not a share: crypto, commodities, indices."""
    labels = {**CRYPTO, **COMMODITIES, "^SP500TR": "S&P 500 total return"}
    rows = []
    for symbol, label in labels.items():
        r = con.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM prices WHERE ticker = ?", [symbol]
        ).fetchone()
        if r and r[0]:
            kind = ("crypto" if symbol in CRYPTO
                    else "index" if symbol.startswith("^") and symbol not in COMMODITIES
                    else "commodity / macro")
            rows.append({"symbol": symbol, "name": label, "kind": kind,
                         "bars": r[0], "from": r[1], "to": r[2]})
    # The columns even when there are no rows: a store without any index or commodity
    # series returned a frame with no `symbol` column, and the research tab's lookup on
    # it raised for every ticker typed.
    return pd.DataFrame(rows, columns=["symbol", "name", "kind", "bars", "from", "to"])


COMMODITIES = {
    "CL=F": "Crude oil (WTI)",
    "BZ=F": "Crude oil (Brent)",
    "NG=F": "Natural gas",
    "GC=F": "Gold",
    "SI=F": "Silver",
    "HG=F": "Copper",
    "ZW=F": "Wheat",
    "ZC=F": "Corn",
    "DX-Y.NYB": "US dollar index",
    "^TNX": "US 10-year yield",
}


def commodity_sensitivity(
    con: duckdb.DuckDBPyConnection, ticker: str, min_days: int = 120
) -> pd.DataFrame:
    """How much this share has moved with each commodity, measured on daily returns.

    Beta is the slope: 0.4 against crude means the share moved about 0.4% for each 1%
    move in oil, on average, over the window. Correlation says how tightly.

    Two warnings that belong next to the number, not in a footnote:
      * This is association, not causation. An airline and oil move together because fuel
        is a cost; a gold miner and gold move together because gold is the product. The
        arithmetic cannot tell those apart, and it will happily find a relationship
        between a software firm and corn if the window is short enough.
      * The window is whatever price history is stored, which is short. Treat a weak
        reading as noise.
    """
    px = con.execute(
        "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) ORDER BY date",
        [ticker],
    ).fetch_df()
    if len(px) < min_days:
        return pd.DataFrame()
    px["date"] = pd.to_datetime(px["date"])
    stock = px.set_index("date")["close_adj"].astype(float).pct_change().dropna()

    rows = []
    for symbol, label in COMMODITIES.items():
        c = con.execute(
            "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date", [symbol]
        ).fetch_df()
        if len(c) < min_days:
            continue
        c["date"] = pd.to_datetime(c["date"])
        other = c.set_index("date")["close_adj"].astype(float).pct_change().dropna()
        joined = pd.concat([stock.rename("a"), other.rename("b")], axis=1).dropna()
        if len(joined) < min_days or joined.b.var() == 0:
            continue
        rows.append({
            "commodity": label,
            "symbol": symbol,
            "correlation": float(joined.a.corr(joined.b)),
            "beta": float(joined.a.cov(joined.b) / joined.b.var()),
            "days": len(joined),
        })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.reindex(out.correlation.abs().sort_values(ascending=False).index)


# ------------------------------------------------------------------ earnings calendar
QUARTERLY_FORMS = ("10-Q", "10-K")


def next_report_estimate(zip_path, cik: int) -> dict | None:
    """When this company is likely to report next, from its own filing rhythm.

    Companies file remarkably close to the same calendar dates year after year, so last
    year's filing for the same fiscal quarter, moved forward 364 days, lands within a few
    days of the real date and keeps the weekday. That is an ESTIMATE and is labelled as
    one everywhere it appears — a schedule presented as fact is worse than no schedule.

    Needs no network and works for every filer in the archive, which is what makes a
    calendar over thousands of companies possible at all.
    """
    import json
    import zipfile

    member = f"CIK{int(cik):010d}.json"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if member not in set(zf.namelist()):
                return None
            with zf.open(member) as fh:
                doc = json.load(fh)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
        return None

    recent = (doc.get("filings") or {}).get("recent") or {}
    if not recent.get("form"):
        return None
    df = pd.DataFrame({
        "form": recent["form"],
        "filed": pd.to_datetime(recent["filingDate"], errors="coerce"),
        "period": pd.to_datetime(recent.get("reportDate", []), errors="coerce"),
    })
    df = df[df.form.isin(QUARTERLY_FORMS) & df.filed.notna() & df.period.notna()]
    if df.empty:
        return None

    today = pd.Timestamp(dt.date.today())
    df["lag"] = (df.filed - df.period).dt.days
    # Project every past filing forward a year; the soonest future one is the next report.
    df["projected"] = df.filed + pd.Timedelta(days=364)
    future = df[df.projected >= today].sort_values("projected")
    if future.empty:
        return None
    row = future.iloc[0]

    last = df.sort_values("filed").iloc[-1]
    return {
        "date": row.projected.date(),
        "days_away": int((row.projected - today).days),
        "basis_form": row.form,
        "basis_filed": row.filed.date(),
        "typical_lag_days": int(df.lag.median()),
        "last_filed": last.filed.date(),
        "source": "estimated from filing history",
    }


def confirmed_report_date(symbol: str) -> dict | None:
    """The date the company has actually announced, where one exists."""
    try:
        cal = _ticker(symbol).calendar or {}
    except Exception:
        return None
    raw = cal.get("Earnings Date")
    if not raw:
        return None
    dates = raw if isinstance(raw, (list, tuple)) else [raw]
    parsed = [pd.Timestamp(d) for d in dates if d is not None]
    if not parsed:
        return None
    first = min(parsed)
    today = pd.Timestamp(dt.date.today())
    return {
        "date": first.date(),
        "days_away": int((first - today).days),
        "range_end": max(parsed).date() if len(parsed) > 1 else None,
        "eps_estimate": cal.get("Earnings Average"),
        "revenue_estimate": cal.get("Revenue Average"),
        "source": "confirmed by the company",
    }


def earnings_calendar(
    con: duckdb.DuckDBPyConnection,
    zip_path,
    tickers: list[str],
    weeks: int = 6,
    confirm: bool = True,
    confirm_limit: int = 40,
) -> pd.DataFrame:
    """Who reports in the next `weeks`, for a given list of companies.

    Estimates come first because they are free and local. Confirmed dates are then
    fetched for the soonest names only — one network call each, so the limit keeps a
    calendar over a large portfolio from taking minutes. A confirmed date always replaces
    its estimate, and the row says which it is.
    """
    if not tickers:
        return pd.DataFrame()
    rows = []
    meta = con.execute(
        "SELECT ticker, cik, name, sector FROM securities "
        "WHERE upper(ticker) IN (SELECT upper(UNNEST(?)))",
        [tickers],
    ).fetch_df()
    for r in meta.itertuples():
        est = next_report_estimate(zip_path, int(r.cik))
        if not est:
            continue
        rows.append({
            "ticker": r.ticker, "name": r.name, "sector": r.sector,
            "date": est["date"], "days_away": est["days_away"],
            "source": est["source"], "typical_lag_days": est["typical_lag_days"],
            "last_filed": est["last_filed"],
            "eps_estimate": None,
        })
    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("days_away")
    horizon = weeks * 7
    out = out[out.days_away.between(0, horizon)]
    if out.empty:
        return out

    if confirm:
        for i, r in enumerate(out.head(confirm_limit).itertuples()):
            actual = confirmed_report_date(r.ticker)
            if not actual:
                continue
            idx = out.index[out.ticker == r.ticker]
            out.loc[idx, "date"] = actual["date"]
            out.loc[idx, "days_away"] = actual["days_away"]
            out.loc[idx, "source"] = actual["source"]
            if actual.get("eps_estimate"):
                out.loc[idx, "eps_estimate"] = actual["eps_estimate"]
        out = out[out.days_away.between(0, horizon)]

    return out.sort_values(["days_away", "ticker"])


def market_news(symbols: list[str], per_symbol: int = 6, limit: int = 60) -> pd.DataFrame:
    """Headlines across several companies at once.

    The same story often carries several tickers — an industry piece, a rate decision,
    an index move. Deduplicating by headline and collecting which companies it was filed
    under turns that repetition into information: a story tagged to six of your holdings
    is not six stories, it is one thing happening to your portfolio.
    """
    frames = []
    for sym in symbols:
        one = news(sym, limit=per_symbol)
        if one.empty:
            continue
        one = one.copy()
        one["ticker"] = sym
        frames.append(one)
    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    grouped = (
        out.groupby("title", as_index=False)
        .agg({
            "when": "first", "summary": "first", "source": "first", "url": "first",
            "ticker": lambda s: ", ".join(sorted(set(s))),
        })
        .rename(columns={"ticker": "tickers"})
    )
    grouped["n_tickers"] = grouped.tickers.str.count(",") + 1
    return grouped.sort_values(["when", "n_tickers"], ascending=[False, False]).head(limit)
