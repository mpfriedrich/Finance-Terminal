"""The world beyond the US screen: a market monitor, and prices for any Yahoo ticker.

Stage 2 of the world-market plan (PLAN.md). Two things, both price-only:

  * **The monitor** — equity indices on every continent, the major currency pairs,
    the US curve, commodities and crypto, each with its moves over a day, a week, a
    month, the year to date and a year. Stored daily like every other series
    (scripts/build_world.py, refreshed by build_prices.py), so it works offline and
    every number on it has a date.
  * **Any Yahoo ticker** — BHP.AX, SAP.DE, 7203.T — for the research chart and the lab.
    Fetched on demand and held in memory, not written to the market store: the store
    is the screen's universe, and a name typed once into the research box is not part
    of it. There are no filings behind these, so no fundamentals, and the price is in
    the listing's own currency.

Moves are computed from closes on or before each horizon's start date, never after —
the "1W" of a Monday compares with the previous Monday's close or the last close
before it, not with whatever came next.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

# (symbol, label). Order is display order.
MONITOR: dict[str, list[tuple[str, str]]] = {
    "Americas": [
        ("^GSPC", "S&P 500"), ("^NDX", "Nasdaq 100"), ("^DJI", "Dow Jones"),
        ("^RUT", "Russell 2000"), ("^GSPTSE", "S&P/TSX Canada"), ("^BVSP", "Bovespa"),
    ],
    "Europe": [
        ("^STOXX50E", "Euro Stoxx 50"), ("^GDAXI", "DAX"), ("^FTSE", "FTSE 100"),
        ("^FCHI", "CAC 40"), ("^SSMI", "SMI"), ("FTSEMIB.MI", "FTSE MIB"),
    ],
    "Asia-Pacific": [
        ("^AXJO", "S&P/ASX 200"), ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"),
        ("000001.SS", "Shanghai"), ("^KS11", "KOSPI"), ("^BSESN", "Sensex"),
    ],
    "Currencies": [
        ("AUDUSD=X", "AUD/USD"), ("EURUSD=X", "EUR/USD"), ("GBPUSD=X", "GBP/USD"),
        ("JPY=X", "USD/JPY"), ("CHF=X", "USD/CHF"), ("CNY=X", "USD/CNY"),
        ("EURAUD=X", "EUR/AUD"), ("DX-Y.NYB", "US dollar index"),
    ],
    "US rates (yield, %)": [
        ("^IRX", "3 months"), ("^FVX", "5 years"), ("^TNX", "10 years"),
        ("^TYX", "30 years"),
    ],
    "Commodities": [
        ("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "Crude oil WTI"),
        ("BZ=F", "Crude oil Brent"), ("NG=F", "Natural gas"), ("HG=F", "Copper"),
        ("ZW=F", "Wheat"), ("ZC=F", "Corn"),
    ],
    "Crypto": [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ether")],
}

# Yields move in points, not percent: a 10-year going from 4.00 to 4.10 is +10 basis
# points, and "+2.5%" would be a meaningless number to put next to it.
YIELD_SYMBOLS = {s for s, _ in MONITOR["US rates (yield, %)"]}

HORIZONS = (("1D", 1), ("1W", 7), ("1M", 30), ("1Y", 365))

# The currency each index is quoted in. Everything not listed is in dollars.
CURRENCY = {
    "^GSPTSE": "CAD", "^BVSP": "BRL", "^STOXX50E": "EUR", "^GDAXI": "EUR",
    "^FTSE": "GBP", "^FCHI": "EUR", "^SSMI": "CHF", "FTSEMIB.MI": "EUR", "^AXJO": "AUD",
    "^N225": "JPY", "^HSI": "HKD", "000001.SS": "CNY", "^KS11": "KRW", "^BSESN": "INR",
}
# Dollar rates the gold view needs beyond the ones the monitor shows: an index in yen
# has to become dollars before it can become ounces. Stored by build_world.py.
FX_FOR_GOLD = ("CADUSD=X", "BRLUSD=X", "CHFUSD=X", "JPYUSD=X", "HKDUSD=X", "CNYUSD=X",
               "KRWUSD=X", "INRUSD=X")
GOLD_SYMBOL = "GC=F"
# Numbers without a currency: a yield, an index of the dollar against other currencies,
# a cross rate between two non-dollar currencies. Priced in gold they would mean nothing,
# so the gold view leaves them as they are.
UNITLESS = YIELD_SYMBOLS | {"DX-Y.NYB", "EURAUD=X"}


def all_symbols() -> list[str]:
    return [s for group in MONITOR.values() for s, _ in group]


def stored_symbols() -> list[str]:
    """What build_world.py stores: the monitor plus the rates behind its gold view."""
    return all_symbols() + [s for s in FX_FOR_GOLD if s not in all_symbols()]


def _on_dates(other: pd.Series, dates: pd.Index) -> pd.Series:
    """`other` carried forward onto `dates`: markets keep different calendars, and a
    missing bar on one side must not delete the other side's day."""
    o = other.copy()
    o.index = pd.to_datetime(pd.Index(o.index))
    o = o[~o.index.duplicated(keep="last")].sort_index()
    want = pd.to_datetime(pd.Index(dates))
    return o.reindex(want.union(o.index)).ffill().reindex(want)


def usd_per_unit(currency: str, by_symbol: dict) -> pd.Series | None:
    """Dollars per unit of `currency`, by date, from whichever Yahoo quote is stored:
    'EURUSD=X' (dollars per euro) directly, or 'JPY=X' (yen per dollar) inverted."""
    if currency == "USD":
        return None
    direct = by_symbol.get(f"{currency}USD=X")
    if direct is not None and not direct.empty:
        return direct
    inverse = by_symbol.get(f"{currency}=X")
    if inverse is not None and not inverse.empty:
        return 1.0 / inverse[inverse > 0]
    return None


def in_gold(symbol: str, closes: pd.Series, by_symbol: dict) -> tuple[pd.Series | None, str]:
    """The series in ounces of gold, and how to read its value.

    Returns (series, kind): kind is 'oz' (ounces per unit of the index, share or
    commodity), 'mg' (milligrams of gold per unit of a currency — an ounce is far too
    big a unit for one yen), 'unitless' (left as it is), 'unit' (gold itself),
    'no_rate' (a currency the store has no dollar rate for) or 'missing' (no prices
    stored for the symbol at all); the series is None for the last three.
    """
    if closes is None or closes.empty:
        return None, "missing"
    if symbol in UNITLESS:
        return closes, "unitless"
    if symbol == GOLD_SYMBOL:
        return None, "unit"
    gold = by_symbol.get(GOLD_SYMBOL)
    if gold is None or gold.empty:
        return None, "no_rate"
    if symbol.endswith("=X"):
        # A currency: the dollar value of one unit of its non-dollar side.
        if symbol.endswith("USD=X"):
            usd = closes                                     # AUDUSD=X: $ per AUD
        else:
            usd = 1.0 / closes[closes > 0]                   # JPY=X: yen per $
        ounces = usd / _on_dates(gold, usd.index).to_numpy()
        return ounces * 31_103.4768, "mg"                    # 1 troy oz = 31,103.5 mg
    currency = CURRENCY.get(symbol, "USD")
    usd = closes
    if currency != "USD":
        rate = usd_per_unit(currency, by_symbol)
        if rate is None:
            return None, "no_rate"
        usd = closes * _on_dates(rate, closes.index).to_numpy()
    return (usd / _on_dates(gold, usd.index).to_numpy()).dropna(), "oz"


def gold_label(symbol: str, label: str) -> str:
    """How a currency row reads in the gold view: the currency whose value is shown —
    'AUD' for AUDUSD=X, and 'JPY' for JPY=X (yen per dollar, shown as the yen)."""
    if symbol.endswith("=X") and symbol not in UNITLESS:
        return symbol[:3]
    return label


def moves(closes: pd.Series, symbol: str = "") -> dict:
    """Last value and its moves. `closes` is indexed by date, oldest first.

    Each horizon compares with the last close ON OR BEFORE the start date — 1D is the
    previous close. Yields report the change in basis points; everything else in
    percent. YTD compares with the last close of the previous year.
    """
    s = closes.dropna()
    s = s[s > 0] if symbol not in YIELD_SYMBOLS else s
    if s.empty:
        return {}
    s.index = pd.to_datetime(s.index)
    last_date, last = s.index[-1], float(s.iloc[-1])
    out = {"last": last, "as_of": last_date.date()}

    def change(ref: float | None):
        if ref is None or pd.isna(ref):
            return None
        if symbol in YIELD_SYMBOLS:
            return (last - ref) * 100.0            # basis points
        return last / ref - 1.0 if ref else None

    prior = s.iloc[:-1]
    out["1D"] = change(float(prior.iloc[-1]) if len(prior) else None)
    for label, days in HORIZONS[1:]:
        before = s[s.index <= last_date - pd.Timedelta(days=days)]
        out[label] = change(float(before.iloc[-1]) if len(before) else None)
    year_end = s[s.index < pd.Timestamp(year=last_date.year, month=1, day=1)]
    out["YTD"] = change(float(year_end.iloc[-1]) if len(year_end) else None)
    return out


def monitor(con: duckdb.DuckDBPyConnection, unit: str = "USD") -> dict[str, pd.DataFrame]:
    """One table per group: label, symbol, last, the moves, and the date of the last
    close. A symbol not stored yet is listed with empty moves, not left out.

    `unit="gold"` prices every row in gold instead (see `in_gold`); each row then also
    carries `kind`, which says how to read its value.
    """
    symbols = stored_symbols()
    frame = con.execute(
        "SELECT ticker, date, close FROM prices WHERE ticker IN (SELECT UNNEST(?)) "
        "AND date >= ? ORDER BY date",
        [symbols, dt.date.today() - dt.timedelta(days=400)],
    ).fetch_df()
    by_symbol = {t: g.set_index("date")["close"] for t, g in frame.groupby("ticker")}
    out = {}
    for group, members in MONITOR.items():
        rows = []
        for symbol, label in members:
            closes = by_symbol.get(symbol, pd.Series(dtype=float))
            if unit == "gold":
                converted, kind = in_gold(symbol, closes, by_symbol)
                m = moves(converted, symbol) if converted is not None else {}
                rows.append({"label": gold_label(symbol, label), "symbol": symbol,
                             "kind": kind, **m})
                continue
            m = moves(closes, symbol)
            rows.append({"label": label, "symbol": symbol, **m})
        out[group] = pd.DataFrame(rows)
    return out


def closes_in(con: duckdb.DuckDBPyConnection, symbols: list[str], start: dt.date,
              unit: str = "USD") -> tuple[pd.DataFrame, dict[str, str]]:
    """Daily closes of `symbols` from `start`, one column each, in dollars or in gold.

    Returns the frame and the symbols left out with why ('no prices', or in gold
    'no_rate' / 'unit'). Unitless series (yields, cross rates) stay as they are.
    """
    wanted = list(symbols)
    if unit == "gold":
        currencies = {CURRENCY.get(sym, "USD") for sym in symbols} - {"USD"}
        wanted += [GOLD_SYMBOL, *(f"{c}USD=X" for c in currencies),
                   *(f"{c}=X" for c in currencies)]
    frame = con.execute(
        "SELECT ticker, date, close FROM prices WHERE ticker IN (SELECT UNNEST(?)) "
        "AND date >= ? AND close > 0 ORDER BY date",
        [sorted(set(wanted)), start - dt.timedelta(days=15)],
    ).fetch_df()
    by_symbol = {t: g.set_index(pd.to_datetime(g["date"]))["close"]
                 for t, g in frame.groupby("ticker")}
    columns, skipped = {}, {}
    for sym in symbols:
        closes = by_symbol.get(sym)
        if closes is None or closes.empty:
            skipped[sym] = "no prices"
            continue
        if unit == "gold":
            closes, kind = in_gold(sym, closes, by_symbol)
            if closes is None:
                skipped[sym] = kind
                continue
        columns[sym] = closes[closes.index >= pd.Timestamp(start)]
    if not columns:
        return pd.DataFrame(), skipped
    wide = pd.concat(columns, axis=1, sort=True)
    return wide, skipped


def rebased(con: duckdb.DuckDBPyConnection, symbols: list[str],
            start: dt.date, unit: str = "USD") -> pd.DataFrame:
    """Several series on one scale: each starts at 100 on its first close on or after
    `start`. The only fair way to put an index, a currency and gold on one chart."""
    wide, _ = closes_in(con, symbols, start, unit)
    if wide.empty:
        return wide
    wide = wide.ffill()
    return 100.0 * wide / wide.bfill().iloc[0]


def fetch_history(symbol: str, years: int = 5, downloader=None,
                  probe=None) -> pd.DataFrame:
    """Daily bars for any Yahoo symbol, on demand, not stored.

    Same columns as the price store plus `open_adj`, so the chart and the lab can use it
    unchanged. Empty when Yahoo answered and has nothing under that symbol; raises
    `prices.YahooUnreachable` when it did not answer. yf.download returns an empty
    frame in both cases, so an empty download is followed by `probe` (default
    `prices.yahoo_answers`), which raises when Yahoo is not reached (review F6).
    """
    from .data import prices

    if downloader is None:
        downloader = prices._download
    end = dt.date.today()
    bars = downloader([symbol], end - dt.timedelta(days=int(years * 365.25)), end)
    if bars is None or bars.empty:
        (probe or prices.yahoo_answers)(symbol)
        return pd.DataFrame()
    bars = bars.dropna(subset=["close"]).copy()
    if "close_adj" not in bars or bars["close_adj"].isna().all():
        bars["close_adj"] = bars["close"]
    factor = bars["close_adj"] / bars["close"].where(bars["close"] != 0)
    bars["open_adj"] = bars["open"] * factor
    return bars.sort_values("date").reset_index(drop=True)


def stats(bars: pd.DataFrame) -> dict:
    """Return and risk figures from bars alone — for a symbol that is not in the store."""
    if bars.empty or len(bars) < 30:
        return {}
    s = bars.set_index(pd.to_datetime(bars["date"]))["close_adj"].dropna()
    out = {}
    for label, days in (("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365)):
        before = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
        out[label] = float(s.iloc[-1] / before.iloc[-1] - 1.0) if len(before) else None
    daily = s.pct_change().dropna().tail(252)
    out["volatility"] = float(daily.std() * (252 ** 0.5)) if len(daily) > 20 else None
    running = s.tail(252)
    out["max_drawdown"] = float((running / running.cummax() - 1.0).min())
    return out


def compare(con: duckdb.DuckDBPyConnection, symbols: list[str],
            start: dt.date, benchmark: str = "^SP500TR", unit: str = "USD") -> dict:
    """Any stored series side by side: rebased curves, the correlation of their daily
    returns, and per-series return, volatility, drawdown and beta.

    Correlation is over the days all compared series traded — a Monday holiday in one
    market and not the other would otherwise pair a return with a gap. Returns are in
    each series' own currency and without dividends unless the series carries them —
    or, with unit="gold", everything (the benchmark too) in ounces of gold.
    """
    symbols = [s.strip().upper() for s in symbols if s.strip()]
    curves = rebased(con, symbols, start, unit)
    if curves.empty:
        return {}
    closes, _ = closes_in(con, list(dict.fromkeys(symbols + [benchmark])), start, unit)
    returns = closes.pct_change(fill_method=None)
    shared = returns[[s for s in symbols if s in returns]].dropna()
    rows = []
    for s in curves.columns:
        series = closes[s].dropna()
        r = returns[s].dropna()
        years = max((series.index[-1] - series.index[0]).days / 365.25, 1e-9)
        row = {"symbol": s, "return": float(series.iloc[-1] / series.iloc[0] - 1.0),
               "annualised": float((series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1.0),
               "volatility": float(r.std() * (252 ** 0.5)) if len(r) > 20 else None,
               "max_drawdown": float((series / series.cummax() - 1.0).min())}
        if benchmark in returns and s != benchmark:
            both = (pd.concat([r, returns[benchmark]], axis=1, sort=True)
                    .replace([float("inf"), float("-inf")], float("nan")).dropna())
            if len(both) > 20 and both.iloc[:, 1].var() > 0:
                row["beta"] = float(both.iloc[:, 0].cov(both.iloc[:, 1])
                                    / both.iloc[:, 1].var())
        rows.append(row)
    return {"curves": curves, "stats": pd.DataFrame(rows),
            "correlation": shared.corr() if len(shared) > 20 else pd.DataFrame(),
            "shared_days": len(shared),
            "missing": [s for s in symbols if s not in curves.columns]}
