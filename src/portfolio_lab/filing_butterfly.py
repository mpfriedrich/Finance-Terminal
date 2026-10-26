"""SEC Filing Butterfly: a prospective paper tracker, two books, one set of signals.

What it does, each time `run()` is called (scripts/butterfly_tracker.py, by hand or on a
schedule during US hours):

  1. Reads EDGAR's daily form index for every day since the last scan (never earlier
     than the day the books were opened — nothing is booked retroactively) and keeps the
     regular 10-K and 10-Q filings, one event per accession number. Amendments (10-K/A,
     10-Q/A) and late notices (NT 10-K) are not events.
  2. An event becomes a candidate from the first US session AFTER its filing date, and
     stays one for `ENTRY_SESSIONS` sessions. A filing date is NOT an earnings date: the
     earnings release usually comes days or weeks before the 10-Q (the research study in
     results/filing_options says so too), and nothing here looks earnings dates up.
  3. During the regular session (with a margin at open and close, Yahoo quotes are
     delayed ~15 minutes) each candidate is decided ONCE: universe checks on the stored
     prices, then a long call butterfly — buy the call below, sell two calls at the
     money, buy the call above — on listed strikes of one listed expiry 25-40 calendar
     days out, as close to 30 as possible, wings as close to +-10 % as the listed strikes
     allow while staying symmetric. Every leg's quote is checked; buys fill at the ask,
     sells at the bid. The observed quotes, the decision and its reasons are stored and
     never changed.
  4. Each book sizes the same structure on its own: at most 1 % of its own value as the
     maximum loss, whole units only (one unit = +1/-2/+1 = four contracts of 100 shares),
     at most 10 units, at most 5 % of any leg's open interest, 10 % of the free money
     held back, one open position per company. A candidate can open in the big book and
     be refused in the small one.
  5. Open positions are marked conservatively (what closing would bring: longs at the
     bid, shorts at the ask, closing fees and FX cost off) and closed on the last
     session before expiry. A position that could not be closed in time is settled as
     American calls are: exercise and assignment, with fees and stock costs — never as a
     free European cash settlement.

Money: every amount is a row in `fb_ledger`; a book's cash is the sum of its rows, so
the tracker and the PORTFOLIOS view read one balance. The EUR book converts every USD
amount at the EUR/USD rate of that moment plus `FX_COST`; the USD book does not convert.

What is assumed and not measured (shown in the app): 0.65 USD per contract and leg on
every trade, exercise or assignment; 0.25 % per currency conversion; 0.05 % stock
spread on a physical delivery; Yahoo's quotes are delayed and carry no quote timestamp
of their own (the fetch time and each contract's last trade time are stored instead).

The historical model study (results/filing_options/REPORT.md) priced options with
Black-Scholes and no real quotes; none of its numbers enter these books. Its 30-day
holding period becomes "the listed expiry 25-40 days out" here, because only listed
expiries can be traded.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd

from . import ui

ET = ZoneInfo("America/New_York")
FORMS = ("10-K", "10-Q")

# -- rules (fixed before the first book was opened, 18.10.2026) --------------------------
MIN_HISTORY = 252
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME_20D = 10e6
ENTRY_SESSIONS = 3            # the first session after the filing and the two after it
DTE_RANGE = (25, 40)
DTE_TARGET = 30
WING = 0.10
WING_RANGE = (0.07, 0.13)     # a listed symmetric wing outside this is not "about 10 %"
RISK_FRACTION = 0.01
RESERVE_FRACTION = 0.10
MAX_UNITS = 10
MAX_OI_SHARE = 0.05
MULTIPLIER = 100
# quote gates per leg
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 1
MAX_SPREAD = 0.35             # (ask - bid) / mid; wider is no price to trade at
MAX_LAST_TRADE_AGE_DAYS = 3
SESSION_MARGIN_MIN = 15       # no decisions in the first/last 15 minutes of the session
# costs (assumptions)
FEE_PER_CONTRACT = 0.65
FX_COST = 0.0025
STOCK_SPREAD = 0.0005

BOOKS = (
    {"id": "fb-eur-1000", "name": "SEC Filing Butterfly – 1.000 EUR", "currency": "EUR",
     "capital": 1_000.0},
    {"id": "fb-usd-100000", "name": "SEC Filing Butterfly – Vollbudget 100.000 USD",
     "currency": "USD", "capital": 100_000.0},
)


# -- US trading calendar ------------------------------------------------------------------

def _easter(year: int) -> dt.date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return dt.date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    if n > 0:
        return first + dt.timedelta(days=shift + 7 * (n - 1))
    last = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1) if d.weekday() == 5 else (
        d + dt.timedelta(days=1) if d.weekday() == 6 else d)


def nyse_holidays(year: int) -> set[dt.date]:
    """Full-day NYSE closures by the exchange's rules (no ad-hoc closures)."""
    days = {
        _nth_weekday(year, 1, 0, 3),                    # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                    # Presidents' Day
        _easter(year) - dt.timedelta(days=2),           # Good Friday
        _nth_weekday(year, 5, 0, -1),                   # Memorial Day
        _observed(dt.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),                    # Labor Day
        _nth_weekday(year, 11, 3, 4),                   # Thanksgiving
        _observed(dt.date(year, 12, 25)),
    }
    if year >= 2022:
        days.add(_observed(dt.date(year, 6, 19)))       # Juneteenth
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() != 5:                         # a Saturday 1 Jan is not moved
        days.add(_observed(new_year))
    return days


def is_session(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def next_session(d: dt.date) -> dt.date:
    """The first session strictly after `d`."""
    d += dt.timedelta(days=1)
    while not is_session(d):
        d += dt.timedelta(days=1)
    return d


def prev_session(d: dt.date) -> dt.date:
    """The last session strictly before `d`."""
    d -= dt.timedelta(days=1)
    while not is_session(d):
        d -= dt.timedelta(days=1)
    return d


def sessions_from(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start if is_session(start) else next_session(start)
    while len(out) < n:
        out.append(d)
        d = next_session(d)
    return out


def in_session(now: dt.datetime, margin_min: int = SESSION_MARGIN_MIN) -> bool:
    """Inside the regular session, away from its first and last minutes."""
    local = now.astimezone(ET)
    if not is_session(local.date()):
        return False
    minutes = local.hour * 60 + local.minute
    return 9 * 60 + 30 + margin_min <= minutes <= 16 * 60 - margin_min


# -- EDGAR daily form index ---------------------------------------------------------------

def form_index_url(day: dt.date) -> str:
    q = (day.month - 1) // 3 + 1
    return (f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/"
            f"form.{day:%Y%m%d}.idx")


_INDEX_LINE = re.compile(r"^(\S+(?: \S+)*?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(\S+)\s*$")


def parse_form_index(text: str) -> pd.DataFrame:
    """Regular 10-K and 10-Q rows of a daily form.idx: form, company, cik, filed,
    accession. The accession number is the event's identity."""
    rows = []
    for line in text.splitlines():
        m = _INDEX_LINE.match(line)
        if not m:
            continue
        form, company, cik, filed, path = m.groups()
        if form not in FORMS:
            continue
        acc = path.rsplit("/", 1)[-1].removesuffix(".txt")
        rows.append({"form": form, "company": company.strip(), "cik": int(cik),
                     "filed": dt.datetime.strptime(filed, "%Y%m%d").date(),
                     "accession": acc})
    return pd.DataFrame(rows, columns=["form", "company", "cik", "filed", "accession"])


# -- the store ----------------------------------------------------------------------------

# Append-only and named, like data/schema.py: a store records the names it has applied
# in fb_migrations, so a new entry (an ALTER TABLE ... ADD COLUMN) runs exactly once.
# The old list was replayed whole on every open and only worked because each entry was
# CREATE TABLE IF NOT EXISTS; a new column would never have reached an existing store
# (audit 24.10.2026, low item 4). Never edit or reorder an entry; append a new one.
# A store from before the names applies the ten originals once more: they are all
# IF NOT EXISTS, so that only records their names.
MIGRATIONS: list[tuple[str, str]] = [
    ("001_fb_books",
     """CREATE TABLE IF NOT EXISTS fb_books (
        id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, currency VARCHAR NOT NULL,
        capital DOUBLE NOT NULL, opened_at TIMESTAMP NOT NULL, rules_json VARCHAR NOT NULL)"""),
    ("002_fb_ledger",
     """CREATE TABLE IF NOT EXISTS fb_ledger (
        id VARCHAR PRIMARY KEY, book_id VARCHAR NOT NULL, booked_at TIMESTAMP NOT NULL,
        kind VARCHAR NOT NULL, amount DOUBLE NOT NULL, amount_usd DOUBLE,
        fx DOUBLE, position_id VARCHAR, note VARCHAR)"""),
    ("003_fb_scan",
     """CREATE TABLE IF NOT EXISTS fb_scan (day DATE PRIMARY KEY, scanned_at TIMESTAMP,
        filings INTEGER)"""),
    ("004_fb_events",
     """CREATE TABLE IF NOT EXISTS fb_events (
        accession VARCHAR PRIMARY KEY, cik BIGINT NOT NULL, company VARCHAR,
        form VARCHAR NOT NULL, filed DATE NOT NULL, first_session DATE NOT NULL,
        detected_at TIMESTAMP NOT NULL, status VARCHAR NOT NULL, decided_at TIMESTAMP,
        ticker VARCHAR, reason VARCHAR, quotes_ok BOOLEAN, structure_json VARCHAR,
        line_json VARCHAR)"""),
    ("005_fb_decisions",
     """CREATE TABLE IF NOT EXISTS fb_decisions (
        accession VARCHAR NOT NULL, book_id VARCHAR NOT NULL, decided_at TIMESTAMP NOT NULL,
        opened BOOLEAN NOT NULL, reason VARCHAR, units INTEGER, max_loss DOUBLE,
        risk_budget DOUBLE, spendable DOUBLE, fx DOUBLE, position_id VARCHAR,
        PRIMARY KEY (accession, book_id))"""),
    ("006_fb_positions",
     """CREATE TABLE IF NOT EXISTS fb_positions (
        id VARCHAR PRIMARY KEY, book_id VARCHAR NOT NULL, accession VARCHAR NOT NULL,
        cik BIGINT NOT NULL, ticker VARCHAR NOT NULL, expiry DATE NOT NULL,
        close_on DATE NOT NULL, units INTEGER NOT NULL, multiplier INTEGER NOT NULL,
        opened_at TIMESTAMP NOT NULL, debit_usd DOUBLE NOT NULL, fees_usd DOUBLE NOT NULL,
        fx_open DOUBLE, reserved DOUBLE NOT NULL, max_loss DOUBLE NOT NULL,
        status VARCHAR NOT NULL, closed_at TIMESTAMP, close_usd DOUBLE,
        close_fees_usd DOUBLE, fx_close DOUBLE, close_reason VARCHAR,
        mark_value DOUBLE, mark_at TIMESTAMP, mark_note VARCHAR,
        UNIQUE (book_id, accession))"""),
    ("007_fb_legs",
     """CREATE TABLE IF NOT EXISTS fb_legs (
        position_id VARCHAR NOT NULL, leg INTEGER NOT NULL, contract VARCHAR NOT NULL,
        strike DOUBLE NOT NULL, qty INTEGER NOT NULL, open_price DOUBLE NOT NULL,
        bid DOUBLE, ask DOUBLE, last DOUBLE, volume DOUBLE, open_interest DOUBLE,
        last_trade VARCHAR, close_price DOUBLE, PRIMARY KEY (position_id, leg))"""),
    ("008_fb_marks",
     """CREATE TABLE IF NOT EXISTS fb_marks (
        book_id VARCHAR NOT NULL, day DATE NOT NULL, marked_at TIMESTAMP NOT NULL,
        equity DOUBLE NOT NULL, cash DOUBLE NOT NULL, reserved DOUBLE NOT NULL,
        open_value DOUBLE NOT NULL, uncertain INTEGER NOT NULL, fx DOUBLE,
        PRIMARY KEY (book_id, day))"""),
    ("009_fb_benchmark",
     """CREATE TABLE IF NOT EXISTS fb_benchmark (day DATE PRIMARY KEY, spy DOUBLE,
        eurusd DOUBLE)"""),
    ("010_fb_runs",
     """CREATE TABLE IF NOT EXISTS fb_runs (run_at TIMESTAMP PRIMARY KEY, summary_json VARCHAR)"""),
]


def connect(path: Path, read_only: bool = False, tries: int = 30) -> duckdb.DuckDBPyConnection:
    """Open the options paper store. A lock held by the other side is waited out
    rather than reported. The tracker writes only after all its fetches (`run`, audit
    24.10.2026 F13), so a wait lasts the seconds of its database work, not the minutes
    of EDGAR and Yahoo requests.

    A reader of a store with pending migrations (an older file, or none yet) opens it
    once for writing to apply them, so the app never reads a table without a column
    the code expects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(tries):
        try:
            if read_only and (not path.exists() or _pending(path)):
                con = duckdb.connect(str(path))
                _migrate(con)
                con.close()
            con = duckdb.connect(str(path), read_only=read_only)
            if not read_only:
                _migrate(con)
            return con
        except duckdb.IOException as exc:
            if "lock" not in str(exc).lower() or attempt == tries - 1:
                raise
            time.sleep(1.0)
    raise RuntimeError("unreachable")


def _applied(con) -> set[str]:
    has = con.execute("SELECT 1 FROM information_schema.tables "
                      "WHERE table_name = 'fb_migrations'").fetchone()
    return {r[0] for r in con.execute("SELECT name FROM fb_migrations").fetchall()} \
        if has else set()


def _pending(path: Path) -> bool:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return bool({name for name, _sql in MIGRATIONS} - _applied(con))
    finally:
        con.close()


def _migrate(con) -> None:
    """Apply the migrations this store has not recorded yet, each once, in order."""
    con.execute("CREATE TABLE IF NOT EXISTS fb_migrations (name VARCHAR PRIMARY KEY, "
                "applied_at TIMESTAMP)")
    done = _applied(con)
    for name, sql in MIGRATIONS:
        if name in done:
            continue
        con.execute(sql)
        con.execute("INSERT INTO fb_migrations VALUES (?, current_timestamp)", [name])


def rules() -> dict:
    """The rules every decision was taken under, stored with each book."""
    return {k: v for k, v in globals().items()
            if k.isupper() and isinstance(v, (int, float, tuple, str))
            and k not in ("ET",)}


def ensure_books(con, now: dt.datetime) -> None:
    """Open both books once: a deposit of the starting capital, nothing else."""
    for b in BOOKS:
        if con.execute("SELECT 1 FROM fb_books WHERE id = ?", [b["id"]]).fetchone():
            continue
        con.execute("INSERT INTO fb_books VALUES (?,?,?,?,?,?)",
                    [b["id"], b["name"], b["currency"], b["capital"], now,
                     json.dumps(rules(), default=str, sort_keys=True)])
        _book(con, b["id"], now, "deposit", b["capital"], None, None, None,
              "starting capital")


def _book(con, book_id, at, kind, amount, amount_usd, fx, position_id, note) -> None:
    con.execute("INSERT INTO fb_ledger VALUES (?,?,?,?,?,?,?,?,?)",
                [uuid.uuid4().hex[:16], book_id, at, kind, float(amount),
                 None if amount_usd is None else float(amount_usd),
                 None if fx is None else float(fx), position_id, note])


# -- money ------------------------------------------------------------------------------

def pay(usd: float, currency: str, fx: float | None) -> float:
    """What paying `usd` costs the book in its own currency (conversion cost included).
    `fx` is dollars per euro."""
    if currency == "USD":
        return usd
    if not fx or fx <= 0:
        raise ValueError("no EUR/USD rate")
    return usd / fx * (1.0 + FX_COST)


def receive(usd: float, currency: str, fx: float | None) -> float:
    """What receiving `usd` brings the book in its own currency. A negative amount is a
    payment and costs the conversion like one."""
    if usd < 0:
        return -pay(-usd, currency, fx)
    if currency == "USD":
        return usd
    if not fx or fx <= 0:
        raise ValueError("no EUR/USD rate")
    return usd / fx * (1.0 - FX_COST)


# -- universe: which line of the company, and is it eligible -------------------------------

def pick_line(market, cik: int, day: dt.date) -> tuple[dict | None, str]:
    """The company's share class with the highest average traded value over the 20
    sessions BEFORE `day`, and whether it clears the universe rules. Prices of `day`
    itself are not used: the decision is taken during that session.

    Only lines still trading compete: a last bar at most 6 days before `day`, the same
    limit the stale-price rule below applies. Without that, an ended line of the same
    CIK (an old CRSP line in the world store) could win on its turnover from years ago,
    and the event fell out as "Kursdaten veraltet" although the live line was fine
    (audit 24.10.2026 F20). With no current line, the stale one is still reported."""
    tickers = [r[0] for r in market.execute(
        "SELECT DISTINCT ticker FROM securities WHERE cik = ?", [int(cik)]).fetchall()]
    if not tickers:
        return None, "keine Aktie zur CIK im Bestand"
    best = best_stale = None
    for t in tickers:
        bars = market.execute(
            "SELECT date, COALESCE(close_raw, close) AS px, volume FROM prices "
            "WHERE ticker = ? AND date < ? AND close > 0 ORDER BY date", [t, day]).fetch_df()
        if bars.empty:
            continue
        last20 = bars.tail(20)
        dv = float((last20["px"] * last20["volume"].fillna(0)).mean())
        line = {"ticker": t, "history": len(bars), "price": float(bars["px"].iloc[-1]),
                "last_date": str(bars["date"].iloc[-1])[:10], "dollar_volume_20d": dv,
                "classes": len(tickers)}
        current = (day - dt.date.fromisoformat(line["last_date"])).days <= 6
        if current and (best is None or dv > best["dollar_volume_20d"]):
            best = line
        if not current and (best_stale is None or line["last_date"] >
                            best_stale["last_date"]):
            best_stale = line
    best = best or best_stale
    if best is None:
        return None, "keine Kurse vor dem Entscheidungstag"
    last = dt.date.fromisoformat(best["last_date"])
    if (day - last).days > 6:
        return best, f"Kursdaten veraltet (letzter Kurs {last})"
    if best["history"] < MIN_HISTORY:
        return best, f"nur {best['history']} Tageskurse (< {MIN_HISTORY})"
    if best["price"] < MIN_PRICE:
        return best, f"Kurs {best['price']:.2f} USD < {MIN_PRICE:.0f} USD"
    if best["dollar_volume_20d"] < MIN_DOLLAR_VOLUME_20D:
        return best, (f"20-Tage-Umsatz {best['dollar_volume_20d'] / 1e6:.1f} Mio. USD "
                      f"< {MIN_DOLLAR_VOLUME_20D / 1e6:.0f} Mio. USD")
    return best, ""


# -- construction ---------------------------------------------------------------------

def choose_expiry(expiries: list[str], day: dt.date) -> tuple[str | None, str]:
    """The listed expiry 25-40 calendar days out, closest to 30 (earlier one on a tie)."""
    ok = []
    for e in expiries:
        d = dt.date.fromisoformat(e)
        dte = (d - day).days
        if DTE_RANGE[0] <= dte <= DTE_RANGE[1]:
            ok.append((abs(dte - DTE_TARGET), dte, e))
    if not ok:
        return None, f"kein gelisteter Verfall mit {DTE_RANGE[0]}–{DTE_RANGE[1]} Tagen"
    return min(ok)[2], ""


def choose_strikes(strikes: list[float], spot: float) -> tuple[tuple | None, str]:
    """Middle = listed strike nearest the spot; wings = the symmetric listed pair whose
    width is closest to 10 % of the spot and between 7 % and 13 %."""
    ks = sorted({float(k) for k in strikes})
    if len(ks) < 3 or spot <= 0:
        return None, "zu wenige gelistete Strikes"
    mid = min(ks, key=lambda k: (abs(k - spot), k))
    have = set(ks)
    best = None
    for low in ks:
        if low >= mid:
            break
        width = mid - low
        high = round(mid + width, 6)
        if high not in have:
            continue
        frac = width / spot
        if WING_RANGE[0] <= frac <= WING_RANGE[1]:
            score = (abs(frac - WING), -width)
            if best is None or score < best[0]:
                best = (score, (low, mid, high))
    if best is None:
        return None, "keine symmetrischen gelisteten Flügel bei rund 10 %"
    return best[1], ""


def standard_contract(symbol: str, ticker: str) -> bool:
    """A standard 100-share contract: OCC root equal to the ticker (Yahoo drops '-' and
    '.'), six date digits, C, eight strike digits. Adjusted contracts after splits or
    mergers carry another root (AAPL1...) and another deliverable."""
    root = re.sub(r"[^A-Z]", "", ticker.upper())
    return bool(re.fullmatch(rf"{root}\d{{6}}C\d{{8}}", str(symbol)))


def _num(x) -> float:
    """A quote field as a float; missing, None and NaN all become NaN."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def check_leg(row: dict, ticker: str, now: dt.datetime, side: str) -> str:
    """Empty string when the leg can be traded on this quote; otherwise why not."""
    k = f"{row['strike']:g}C"
    if not standard_contract(row.get("contractSymbol", ""), ticker) or \
            str(row.get("contractSize", "REGULAR")).upper() != "REGULAR":
        return f"{k}: kein Standardkontrakt (Multiplikator nicht als {MULTIPLIER} bestätigt)"
    bid, ask = _num(row.get("bid")), _num(row.get("ask"))
    if not bid > 0:                      # NaN fails every comparison
        return f"{k}: kein Bid"
    if not ask > 0:
        return f"{k}: kein Ask"
    if ask < bid:
        return f"{k}: Ask unter Bid"
    mid = (bid + ask) / 2
    if (ask - bid) / mid > MAX_SPREAD:
        return f"{k}: Spanne {(ask - bid) / mid:.0%} > {MAX_SPREAD:.0%}"
    oi = _num(row.get("openInterest"))
    oi = 0.0 if math.isnan(oi) else oi
    if oi < MIN_OPEN_INTEREST:
        return f"{k}: Open Interest {int(oi)} < {MIN_OPEN_INTEREST}"
    vol = _num(row.get("volume"))
    if math.isnan(vol) or vol < MIN_VOLUME:
        return f"{k}: heute kein Umsatz"
    last = pd.to_datetime(row.get("lastTradeDate"), errors="coerce", utc=True)
    if pd.isna(last):
        return f"{k}: kein Zeitstempel des letzten Handels"
    if (pd.Timestamp(now).tz_convert("UTC") - last).days > MAX_LAST_TRADE_AGE_DAYS:
        return f"{k}: letzter Handel {last.date()} zu alt"
    return ""


def build(calls: pd.DataFrame, ticker: str, spot: float, expiry: str,
          now: dt.datetime) -> tuple[dict | None, str]:
    """The butterfly on this expiry's calls, priced to trade: or why not."""
    if calls is None or calls.empty:
        return None, "keine Calls für diesen Verfall"
    strikes, why = choose_strikes(calls["strike"].tolist(), spot)
    if strikes is None:
        return None, why
    rows = calls.set_index("strike")
    legs = []
    for n, (k, qty) in enumerate(zip(strikes, (1, -2, 1)), start=1):
        r = rows.loc[k]
        r = (r.iloc[0] if isinstance(r, pd.DataFrame) else r).to_dict()
        r["strike"] = k
        why = check_leg(r, ticker, now, "buy" if qty > 0 else "sell")
        if why:
            return None, why
        price = float(r["ask"]) if qty > 0 else float(r["bid"])
        legs.append({"leg": n, "contract": r["contractSymbol"], "strike": k, "qty": qty,
                     "price": price, "bid": float(r["bid"]), "ask": float(r["ask"]),
                     "last": float(r.get("lastPrice") or 0),
                     "volume": float(r.get("volume") or 0),
                     "open_interest": float(r.get("openInterest") or 0),
                     "last_trade": str(r.get("lastTradeDate")),
                     "model_iv": float(r.get("impliedVolatility") or float("nan"))})
    width = strikes[1] - strikes[0]
    debit = sum(leg["qty"] * leg["price"] for leg in legs) * MULTIPLIER
    if debit <= 0:
        return None, "Quote-Anomalie: Butterfly ohne Nettokosten"
    if debit >= width * MULTIPLIER:
        return None, "Nettokosten ≥ maximaler Auszahlung"
    spread_reserve = sum(abs(leg["qty"]) * (leg["ask"] - leg["bid"]) for leg in legs) \
        * MULTIPLIER
    return {"ticker": ticker, "spot": spot, "expiry": expiry,
            "close_on": str(prev_session(dt.date.fromisoformat(expiry))),
            "strikes": list(strikes), "width": width, "legs": legs,
            "debit_usd": debit, "max_payoff_usd": width * MULTIPLIER,
            "fees_open_usd": 4 * FEE_PER_CONTRACT, "fees_close_usd": 4 * FEE_PER_CONTRACT,
            "spread_reserve_usd": spread_reserve, "observed_at": now.isoformat()}, ""


# -- the books' state ---------------------------------------------------------------------

def book_row(con, book_id: str) -> dict:
    r = con.execute("SELECT id, name, currency, capital, opened_at FROM fb_books "
                    "WHERE id = ?", [book_id]).fetchone()
    return dict(zip(("id", "name", "currency", "capital", "opened_at"), r))


def state(con, book_id: str, fx: float | None) -> dict:
    """Cash from the ledger, money held back for closing, open positions at their last
    conservative mark, and the account value. Read by the tracker and the app alike."""
    b = book_row(con, book_id)
    cash = float(con.execute("SELECT COALESCE(SUM(amount), 0) FROM fb_ledger "
                             "WHERE book_id = ?", [book_id]).fetchone()[0])
    pos = con.execute("SELECT * FROM fb_positions WHERE book_id = ? AND status = 'open'",
                      [book_id]).fetch_df()
    reserved = float(pos["reserved"].sum()) if len(pos) else 0.0
    open_value, uncertain = 0.0, 0
    for p in pos.itertuples():
        try:
            open_value += receive(float(p.mark_value), b["currency"], fx)
        except ValueError:                       # EUR book without a rate: last FX known
            open_value += receive(float(p.mark_value), b["currency"], p.fx_open)
            uncertain += 1
            continue
        if p.mark_note:
            uncertain += 1
    return {"book": b, "cash": cash, "reserved": reserved, "available": cash - reserved,
            "open_value": open_value, "equity": cash + open_value, "open": pos,
            "uncertain": uncertain}


def size(structure: dict, st: dict, fx: float | None) -> dict:
    """How many whole butterflies this book may open, and why not if none."""
    ccy = st["book"]["currency"]
    unit_loss_usd = (structure["debit_usd"] + structure["fees_open_usd"]
                     + structure["fees_close_usd"] + structure["spread_reserve_usd"])
    try:
        unit_loss = pay(unit_loss_usd, ccy, fx)
    except ValueError:
        return {"units": 0, "reason": "kein aktueller EUR/USD-Kurs", "fx": fx}
    risk_budget = RISK_FRACTION * st["equity"]
    spendable = (1.0 - RESERVE_FRACTION) * max(st["available"], 0.0)
    by_risk = math.floor(risk_budget / unit_loss) if unit_loss > 0 else 0
    by_cash = math.floor(spendable / unit_loss) if unit_loss > 0 else 0
    by_liquidity = min(math.floor(MAX_OI_SHARE * leg["open_interest"] / abs(leg["qty"]))
                       for leg in structure["legs"])
    units = max(min(by_risk, by_cash, by_liquidity, MAX_UNITS), 0)
    out = {"units": units, "unit_max_loss": unit_loss, "max_loss": unit_loss * units,
           "risk_budget": risk_budget, "spendable": spendable, "fx": fx, "reason": ""}
    sym = "€" if ccy == "EUR" else "$"
    if by_risk < 1:
        out["reason"] = (f"Budget reicht nicht: Maximalverlust je Butterfly "
                         f"{unit_loss:,.2f} {sym} > Risikogrenze {risk_budget:,.2f} {sym} "
                         f"(1 % von {st['equity']:,.2f} {sym})")
    elif by_cash < 1:
        out["reason"] = (f"Budget reicht nicht: frei nach 10 % Reserve {spendable:,.2f} "
                         f"{sym} < {unit_loss:,.2f} {sym} je Butterfly")
    elif by_liquidity < 1:
        out["reason"] = "Optionsliquidität zu gering: 5 % des Open Interest < 1 Butterfly"
    return out


def liquidation_usd(legs: list[dict]) -> float | None:
    """What closing brings now: longs sold at the bid, shorts bought back at the ask.
    None when the quote is incomplete: a short leg without an ask (it cannot be bought
    back) or a long leg without any bid. A quoted bid of 0 is a price (the call is
    worth nothing to a buyer) and counts as 0; a MISSING bid is no price at all. It
    used to count as 0 as well, which dropped the mark and cleared its note, so a gap
    in Yahoo's chain looked like a loss (audit 24.10.2026 F18). The caller keeps the
    last mark and flags it uncertain."""
    total = 0.0
    for leg in legs:
        if leg["qty"] > 0:
            px = _num(leg.get("bid"))
            if math.isnan(px) or px < 0:
                return None
        else:
            px = _num(leg.get("ask"))
            if not px > 0:
                return None
        total += leg["qty"] * px * MULTIPLIER
    return total


def open_position(con, book_id: str, event: dict, structure: dict, sized: dict,
                  now: dt.datetime) -> str:
    b = book_row(con, book_id)
    ccy, fx, units = b["currency"], sized["fx"], sized["units"]
    pid = uuid.uuid4().hex[:12]
    debit = structure["debit_usd"] * units
    fees = structure["fees_open_usd"] * units
    reserve_usd = (structure["fees_close_usd"] + structure["spread_reserve_usd"]) * units
    legs = [dict(leg, qty=leg["qty"] * units) for leg in structure["legs"]]
    mark = liquidation_usd(legs) - structure["fees_close_usd"] * units
    con.execute(
        "INSERT INTO fb_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [pid, book_id, event["accession"], int(event["cik"]), structure["ticker"],
         structure["expiry"], structure["close_on"], units, MULTIPLIER, now, debit, fees,
         fx, pay(reserve_usd, ccy, fx), sized["max_loss"], "open", None, None, None, None,
         None, mark, now, ""])
    for leg in legs:
        con.execute("INSERT INTO fb_legs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [pid, leg["leg"], leg["contract"], leg["strike"], leg["qty"],
                     leg["price"], leg["bid"], leg["ask"], leg["last"], leg["volume"],
                     leg["open_interest"], leg["last_trade"], None])
    _book(con, book_id, now, "premium", -pay(debit, ccy, fx), -debit, fx, pid,
          f"{structure['ticker']} Butterfly {structure['strikes']} {structure['expiry']} "
          f"×{units}")
    _book(con, book_id, now, "fee", -pay(fees, ccy, fx), -fees, fx, pid,
          f"{4 * units} Kontrakte × {FEE_PER_CONTRACT} USD")
    return pid


def legs_of(con, position_id: str) -> pd.DataFrame:
    return con.execute("SELECT * FROM fb_legs WHERE position_id = ? ORDER BY leg",
                       [position_id]).fetch_df()


def close_position(con, position: dict, quotes: dict, fx: float | None,
                   now: dt.datetime, reason: str) -> str:
    """Close at the quote: longs at the bid, shorts at the ask. `quotes` maps contract
    symbol to {bid, ask}. Returns '' or why it could not be closed."""
    b = book_row(con, position["book_id"])
    legs = legs_of(con, position["id"]).to_dict("records")
    priced = []
    for leg in legs:
        q = quotes.get(leg["contract"])
        if q is None:
            return f"keine Quote für {leg['contract']}"
        priced.append(dict(leg, bid=q.get("bid"), ask=q.get("ask")))
    credit = liquidation_usd(priced)
    if credit is None:
        return ("Quote unvollständig (Short-Call ohne Ask oder Long-Call ohne Bid): "
                "Schließung auf dieser Quote nicht möglich")
    if b["currency"] == "EUR" and not fx:
        return "kein aktueller EUR/USD-Kurs"
    fees = FEE_PER_CONTRACT * sum(abs(leg["qty"]) for leg in legs)
    for leg in priced:
        px = leg["bid"] if leg["qty"] > 0 else leg["ask"]
        con.execute("UPDATE fb_legs SET close_price = ? WHERE position_id = ? AND leg = ?",
                    [float(px or 0.0), position["id"], int(leg["leg"])])
    _finish(con, position, b, credit, fees, fx, now, reason)
    return ""


def settle_at_expiry(con, position: dict, stock_close: float, fx: float | None,
                     now: dt.datetime) -> None:
    """A position still open after expiry is settled the way American calls settle:
    in-the-money calls are exercised or assigned, the shares delivered and sold or
    bought back. Fees per exercised/assigned contract and the stock spread on every
    delivered share are charged — no free European cash settlement."""
    b = book_row(con, position["book_id"])
    legs = legs_of(con, position["id"]).to_dict("records")
    payoff, fees, stock_cost = 0.0, 0.0, 0.0
    for leg in legs:
        itm = max(stock_close - leg["strike"], 0.0)
        payoff += leg["qty"] * itm * MULTIPLIER
        if itm > 0:
            fees += FEE_PER_CONTRACT * abs(leg["qty"])
            stock_cost += STOCK_SPREAD * stock_close * MULTIPLIER * abs(leg["qty"])
        con.execute("UPDATE fb_legs SET close_price = ? WHERE position_id = ? AND leg = ?",
                    [itm, position["id"], int(leg["leg"])])
    _finish(con, position, b, payoff - stock_cost, fees, fx or position["fx_open"], now,
            f"verfallen ohne Schließung: Ausübung/Zuteilung zum Schlusskurs "
            f"{stock_close:.2f} USD")


def _finish(con, position, b, credit_usd, fees_usd, fx, now, reason) -> None:
    ccy, pid = b["currency"], position["id"]
    _book(con, b["id"], now, "close", receive(credit_usd, ccy, fx), credit_usd, fx, pid,
          reason)
    _book(con, b["id"], now, "fee", -pay(fees_usd, ccy, fx), -fees_usd, fx, pid,
          "Schließungsgebühren")
    con.execute(
        "UPDATE fb_positions SET status = 'closed', closed_at = ?, close_usd = ?, "
        "close_fees_usd = ?, fx_close = ?, close_reason = ?, reserved = 0, "
        "mark_value = 0, mark_at = ?, mark_note = '' WHERE id = ?",
        [now, credit_usd, fees_usd, fx, reason, now, pid])


# -- live data (the only network code; tests pass a fake with the same methods) ----------

class EdgarError(RuntimeError):
    """EDGAR answered, but not with the index (403, 429, 5xx): the day stays open."""


class Live:
    """EDGAR's index and Yahoo's quotes through the project's existing access paths."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent

    def form_index(self, day: dt.date) -> str | None:
        """The day's form.idx, or None when EDGAR has none (404: not published yet, or
        no filings that day). Any other answer raises, so the day stays open and the
        next run asks again. A 403 used to count as an empty day too, but SEC answers
        403 to a rate limit or a rejected user agent, and that day's 10-K/10-Q were then
        never looked at again (audit 24.10.2026 F15). The error names the status only:
        requests' own message carries the URL (R24)."""
        import requests

        r = requests.get(form_index_url(day), headers={"User-Agent": self.user_agent},
                         timeout=30)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise EdgarError(f"HTTP {r.status_code} für den Index vom {day}")
        return r.text

    def expiries(self, symbol: str) -> list[str]:
        from . import options as op

        return op.expiries(symbol, strict=True)

    def calls(self, symbol: str, expiry: str) -> pd.DataFrame:
        from . import options as op

        return op.fetch_chain(symbol, expiry).calls

    def spot(self, symbol: str) -> float | None:
        """The last price. yfinance 1.7 answers fast_info["lastPrice"] while
        fast_info.get("last_price") quietly returns None (measured 18.10.2026): read
        by item, camel case first."""
        import yfinance as yf

        info = yf.Ticker(symbol).fast_info
        for key in ("lastPrice", "last_price"):
            try:
                px = info[key]
            except (KeyError, TypeError):
                continue
            if _num(px) > 0:
                return float(px)
        return None

    def fx(self) -> float | None:
        return self.spot("EURUSD=X")

    def ex_dividend(self, symbol: str) -> dt.date | None:
        import yfinance as yf

        cal = yf.Ticker(symbol).calendar or {}
        d = cal.get("Ex-Dividend Date") if isinstance(cal, dict) else None
        return pd.Timestamp(d).date() if d is not None and not pd.isna(d) else None

    def history(self, symbol: str, start: dt.date) -> pd.Series:
        import yfinance as yf

        h = yf.Ticker(symbol).history(start=str(start), auto_adjust=True)
        if h.empty:
            return pd.Series(dtype=float)
        return pd.Series(h["Close"].to_numpy(), index=pd.to_datetime(h.index).date)

    def close_on(self, symbol: str, day: dt.date) -> float | None:
        import yfinance as yf

        h = yf.Ticker(symbol).history(start=str(day), end=str(day + dt.timedelta(days=1)),
                                      auto_adjust=False)
        return float(h["Close"].iloc[-1]) if not h.empty else None


# -- the run ----------------------------------------------------------------------------

def _naive(now: dt.datetime) -> dt.datetime:
    return now.astimezone(dt.UTC).replace(tzinfo=None)


def _store_fx(market, day: dt.date) -> float | None:
    row = market.execute("SELECT date, close FROM prices WHERE ticker = 'EURUSD=X' "
                         "AND date <= ? ORDER BY date DESC LIMIT 1", [day]).fetchone()
    if row and (day - row[0]).days <= 4:
        return float(row[1])
    return None


def _scan(con, live, today: dt.date, start: dt.date, now, log,
          notes: list[str] | None = None) -> int:
    """Read every weekday's index since `start` that is not in fb_scan yet. Only a day
    that EDGAR answered for is recorded. A day whose request failed stays open with a
    note, and the days after it are still read: one day that keeps failing must not
    hold back every later filing (audit 24.10.2026 F15)."""
    new = 0
    day = start
    while day <= today:
        if day.weekday() < 5 and not con.execute(
                "SELECT 1 FROM fb_scan WHERE day = ?", [day]).fetchone():
            try:
                text = live.form_index(day)
            except Exception as exc:                               # noqa: BLE001
                if notes is not None:
                    notes.append(f"EDGAR {day} nicht gelesen ({ui.error_text(exc)}); "
                                 "der Tag bleibt offen")
                day += dt.timedelta(days=1)
                continue
            if text is None:
                if day >= today:
                    break                    # today's index appears after the day ends
                con.execute("INSERT INTO fb_scan VALUES (?,?,0)", [day, now])
            else:
                rows = parse_form_index(text)
                for r in rows.itertuples():
                    new += register(con, r._asdict(), now)
                con.execute("INSERT INTO fb_scan VALUES (?,?,?)", [day, now, len(rows)])
                log(f"EDGAR {day}: {len(rows)} 10-K/10-Q")
        day += dt.timedelta(days=1)
    return new


def register(con, filing: dict, now) -> int:
    """One event per accession; a second regular report of the same form by the same
    company within 20 days is a duplicate (a re-filed report), not a new signal."""
    if con.execute("SELECT 1 FROM fb_events WHERE accession = ?",
                   [filing["accession"]]).fetchone():
        return 0
    dup = con.execute(
        "SELECT accession FROM fb_events WHERE cik = ? AND form = ? "
        "AND abs(datediff('day', filed, ?)) <= 20", [filing["cik"], filing["form"],
                                                     filing["filed"]]).fetchone()
    first = next_session(filing["filed"])
    status, reason = ("duplicate", f"Duplikat von {dup[0]}") if dup else ("pending", None)
    con.execute("INSERT INTO fb_events (accession, cik, company, form, filed, first_session,"
                " detected_at, status, reason) VALUES (?,?,?,?,?,?,?,?,?)",
                [filing["accession"], int(filing["cik"]), filing.get("company"),
                 filing["form"], filing["filed"], first, now, status, reason])
    return 0 if dup else 1


def _decide_all(con, ev: dict, now, reason: str, status: str = "decided",
                quotes_ok: bool | None = None, line: dict | None = None,
                structure: dict | None = None) -> None:
    for b in BOOKS:
        con.execute("INSERT OR IGNORE INTO fb_decisions (accession, book_id, decided_at, "
                    "opened, reason) VALUES (?,?,?,?,?)",
                    [ev["accession"], b["id"], now, False, reason])
    con.execute("UPDATE fb_events SET status = ?, decided_at = ?, reason = ?, "
                "quotes_ok = ?, ticker = ?, line_json = ?, structure_json = ? "
                "WHERE accession = ? AND status = 'pending'",
                [status, now, reason, quotes_ok, (line or {}).get("ticker"),
                 json.dumps(line) if line else None,
                 json.dumps(structure, default=str) if structure else None,
                 ev["accession"]])


def _candidate(market, live, ev: dict, today: dt.date, now_aware) -> dict:
    """Universe, dividend and quote checks for one event. Shared by both books."""
    line, why = pick_line(market, ev["cik"], today)
    out = {"line": line, "structure": None, "reason": why, "quotes_ok": None}
    if why:
        return out
    t = line["ticker"]
    from . import options as op

    if op.trailing_dividend_yield(market, t) > 0:
        ex = live.ex_dividend(t)
        if ex is None:
            out["reason"] = "Dividendenzahler ohne bekannten Ex-Tag: Zuteilungsrisiko"
            return out
        horizon = today + dt.timedelta(days=DTE_RANGE[1])
        nxt = ex if ex >= today else ex + dt.timedelta(days=80)
        if nxt <= horizon:
            out["reason"] = (f"Ex-Dividende {'am' if ex >= today else 'vermutlich ab'} "
                             f"{nxt} vor Verfall: vorzeitige Zuteilung der Short-Calls")
            return out
    expiry, why = choose_expiry(live.expiries(t), today)
    if not expiry:
        out["reason"] = why
        return out
    spot = live.spot(t)
    if not spot or abs(spot / line["price"] - 1) > 0.25:
        out["reason"] = "kein plausibler aktueller Aktienkurs"
        return out
    structure, why = build(live.calls(t, expiry), t, spot, expiry, now_aware)
    out["quotes_ok"] = structure is not None
    out["structure"], out["reason"] = structure, why
    return out


class _Recorded:
    """Stands in for `Live`: the first call of each method and arguments goes to the
    real source and is kept (its result or its exception); a repeat is answered from
    what was kept. A DataFrame is handed out as a copy, so no pass can change what the
    next one sees."""

    def __init__(self, live):
        self._live, self._kept = live, {}

    def __getattr__(self, name):
        method = getattr(self._live, name)

        def call(*args):
            key = (name, *args)
            if key not in self._kept:
                try:
                    self._kept[key] = (True, method(*args))
                except Exception as exc:                           # noqa: BLE001
                    self._kept[key] = (False, exc)
            ok, value = self._kept[key]
            if not ok:
                raise value
            return value.copy() if isinstance(value, (pd.DataFrame, pd.Series)) else value
        return call


def run(path: Path, market, live, now: dt.datetime | None = None, log=print) -> dict:
    """One pass: scan, decide, manage, mark. Safe to repeat: nothing is booked twice.

    Fetch first, then write briefly (audit 24.10.2026 F13). The tracker used to hold
    the store's write lock through every EDGAR and Yahoo request, so the app waited up
    to its 30 tries and then showed the store as busy. Now the whole pass runs once on
    a throwaway copy of the store, with every answer from `live` kept; then it runs
    again on the real store, answered from what was kept, without network. Both passes
    see the same store, the same `now` and the same answers, so they take the same
    decisions; the write lock is held only for the database work of the second."""
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    connect(path).close()                       # create or migrate, then let go
    kept = _Recorded(live)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / path.name
        shutil.copyfile(path, copy)
        wal = path.with_name(path.name + ".wal")
        if wal.exists():                        # not checkpointed yet: part of the store
            shutil.copyfile(wal, copy.with_name(copy.name + ".wal"))
        _run_once(copy, market, kept, now, lambda *_: None)
    return _run_once(path, market, kept, now, log)


def _run_once(path: Path, market, live, now: dt.datetime, log) -> dict:
    """The pass itself, on whichever store and source `run` hands it."""
    now_aware = now
    now = _naive(now_aware)
    today = now_aware.astimezone(ET).date()
    live_session = in_session(now_aware)
    con = connect(path)
    summary = {"at": now.isoformat(), "session_open": live_session, "opened": 0,
               "closed": 0, "rejected": 0, "new_events": 0, "notes": []}
    try:
        con.execute("BEGIN")
        ensure_books(con, now)
        start = min(r[0] for r in con.execute("SELECT opened_at FROM fb_books").fetchall())
        start_day = start.replace(tzinfo=dt.UTC).astimezone(ET).date()
        summary["new_events"] = _scan(con, live, today, start_day, now, log,
                                      summary["notes"])
        try:
            fx = live.fx()
        except Exception:                                          # noqa: BLE001
            fx = None
        if not fx:
            fx = _store_fx(market, today)
            summary["notes"].append("EUR/USD live nicht erreichbar" +
                                    (", Kurs aus dem Bestand" if fx else ", kein Kurs"))
        summary["fx"] = fx

        # 1. events whose entry window has passed
        pending = con.execute("SELECT * FROM fb_events WHERE status = 'pending' "
                              "ORDER BY filed, accession").fetch_df().to_dict("records")
        live_events = []
        for ev in pending:
            first = pd.Timestamp(ev["first_session"]).date()
            last_ok = sessions_from(first, ENTRY_SESSIONS)[-1]
            late = today > last_ok or (today == last_ok and not live_session and
                                       now_aware.astimezone(ET).hour >= 16)
            if late:
                _decide_all(con, ev, now, "Einstiegsfenster verpasst (kein Lauf während "
                            "der Handelszeit)", status="missed")
                summary["rejected"] += 1
            elif live_session and first <= today:
                live_events.append(ev)

        # 2. decide the live candidates, both books, in a fixed order
        cands = []
        for ev in live_events:
            try:
                c = _candidate(market, live, ev, today, now_aware)
            except Exception as exc:                               # noqa: BLE001
                summary["notes"].append(f"{ev['accession']}: {type(exc).__name__}")
                continue                   # a failed download is not a decision
            cands.append((ev, c))
        cands.sort(key=lambda p: (str(p[0]["filed"]),
                                  -((p[1]["line"] or {}).get("dollar_volume_20d") or 0),
                                  (p[1]["line"] or {}).get("ticker") or "", p[0]["accession"]))
        for ev, c in cands:
            if c["structure"] is None:
                _decide_all(con, ev, now, c["reason"], quotes_ok=c["quotes_ok"],
                            line=c["line"])
                summary["rejected"] += 1
                continue
            for b in BOOKS:
                held = con.execute("SELECT 1 FROM fb_positions WHERE book_id = ? AND "
                                   "cik = ? AND status = 'open'",
                                   [b["id"], int(ev["cik"])]).fetchone()
                st = state(con, b["id"], fx)
                sized = ({"units": 0, "reason": "bereits offene Position im Unternehmen"}
                         if held else size(c["structure"], st, fx))
                pid = None
                if sized["units"] >= 1:
                    pid = open_position(con, b["id"], ev, c["structure"], sized, now)
                    summary["opened"] += 1
                con.execute("INSERT OR IGNORE INTO fb_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            [ev["accession"], b["id"], now, pid is not None,
                             sized.get("reason") or "", sized.get("units"),
                             sized.get("max_loss"), sized.get("risk_budget"),
                             sized.get("spendable"), fx, pid])
            con.execute("UPDATE fb_events SET status = 'decided', decided_at = ?, "
                        "quotes_ok = TRUE, ticker = ?, line_json = ?, structure_json = ?, "
                        "reason = '' WHERE accession = ? AND status = 'pending'",
                        [now, c["line"]["ticker"], json.dumps(c["line"]),
                         json.dumps(c["structure"], default=str), ev["accession"]])

        # 3. open positions: close, settle or mark
        openp = con.execute("SELECT * FROM fb_positions WHERE status = 'open'").fetch_df()
        chains: dict = {}
        for p in openp.to_dict("records"):
            expiry = pd.Timestamp(p["expiry"]).date()
            close_on = pd.Timestamp(p["close_on"]).date()
            if today > expiry or (today == expiry and now_aware.astimezone(ET).hour >= 16):
                px = market.execute("SELECT close_raw FROM prices WHERE ticker = ? AND "
                                    "date = ?", [p["ticker"], expiry]).fetchone()
                px = float(px[0]) if px and px[0] else live.close_on(p["ticker"], expiry)
                if px:
                    settle_at_expiry(con, p, px, fx, now)
                    summary["closed"] += 1
                else:
                    summary["notes"].append(f"{p['ticker']}: Schlusskurs am Verfall fehlt")
                continue
            if not live_session:
                continue
            key = (p["ticker"], str(expiry))
            if key not in chains:
                try:
                    chains[key] = live.calls(p["ticker"], str(expiry))
                except Exception:                                  # noqa: BLE001
                    chains[key] = None
            calls = chains[key]
            quotes = ({} if calls is None else
                      {r["contractSymbol"]: {"bid": r.get("bid"), "ask": r.get("ask")}
                       for r in calls.to_dict("records")})
            if today >= close_on:
                why = close_position(con, p, quotes, fx, now,
                                     "planmäßig am letzten Handelstag vor Verfall")
                if not why:
                    summary["closed"] += 1
                    continue
                summary["notes"].append(f"{p['ticker']}: Schließung offen — {why}")
            legs = legs_of(con, p["id"]).to_dict("records")
            priced = [dict(leg, **quotes.get(leg["contract"], {"bid": None, "ask": None}))
                      for leg in legs]
            value = liquidation_usd(priced) if all(
                leg["contract"] in quotes for leg in legs) else None
            if value is None:
                con.execute("UPDATE fb_positions SET mark_note = ? WHERE id = ?",
                            ["keine handelbare Quote, letzte Bewertung gilt", p["id"]])
            else:
                fees = FEE_PER_CONTRACT * sum(abs(leg["qty"]) for leg in legs)
                con.execute("UPDATE fb_positions SET mark_value = ?, mark_at = ?, "
                            "mark_note = '' WHERE id = ?", [value - fees, now, p["id"]])

        # 4. benchmark and daily book values
        try:
            spy = live.history("SPY", start_day - dt.timedelta(days=7))
            eur = live.history("EURUSD=X", start_day - dt.timedelta(days=7))
            for d, v in spy.items():
                con.execute("INSERT OR REPLACE INTO fb_benchmark VALUES (?,?,?)",
                            [d, float(v), float(eur.get(d)) if d in eur.index else None])
        except Exception as exc:                                   # noqa: BLE001
            summary["notes"].append(f"SPY nicht erreichbar: {type(exc).__name__}")
        for b in BOOKS:
            st = state(con, b["id"], fx)
            con.execute("INSERT OR REPLACE INTO fb_marks VALUES (?,?,?,?,?,?,?,?,?)",
                        [b["id"], today, now, st["equity"], st["cash"], st["reserved"],
                         st["open_value"], st["uncertain"], fx])
        con.execute("INSERT INTO fb_runs VALUES (?,?)",
                    [now, json.dumps(summary, default=str)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    return summary


# -- reports (read-only; the app and the script share them) ----------------------------

def last_run(con) -> tuple[dt.datetime | None, dict]:
    row = con.execute("SELECT run_at, summary_json FROM fb_runs ORDER BY run_at DESC LIMIT 1"
                      ).fetchone()
    return (row[0], json.loads(row[1])) if row else (None, {})


def monthly(con) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per month of the first entry session: candidates found, candidates with tradable
    quotes, positions opened per book; and the refusals per book and reason."""
    ev = con.execute("SELECT accession, first_session, status, quotes_ok FROM fb_events "
                     "WHERE status <> 'duplicate'").fetch_df()
    dec = con.execute("SELECT d.accession, d.book_id, d.opened, d.reason, e.first_session "
                      "FROM fb_decisions d JOIN fb_events e USING (accession)").fetch_df()
    if ev.empty:
        return pd.DataFrame(), pd.DataFrame()
    ev["month"] = pd.to_datetime(ev["first_session"]).dt.strftime("%Y-%m")
    out = ev.groupby("month").agg(candidates=("accession", "count"),
                                  with_quotes=("quotes_ok", lambda s: int(s.fillna(False)
                                                                          .sum())))
    reasons = pd.DataFrame()
    if not dec.empty:
        dec["month"] = pd.to_datetime(dec["first_session"]).dt.strftime("%Y-%m")
        for b in BOOKS:
            sub = dec[dec["book_id"] == b["id"]]
            out[f"opened · {b['currency']}"] = sub[sub["opened"]].groupby("month").size()
        rej = dec[~dec["opened"]].copy()
        rej["reason"] = rej["reason"].fillna("").str.split(":").str[0].str.strip()
        reasons = (rej.groupby(["month", "book_id", "reason"]).size()
                   .rename("count").reset_index())
    return out.fillna(0).astype(int).reset_index(), reasons


def _metrics(series: pd.Series) -> dict:
    s = series.dropna()
    if len(s) < 2:
        return {"total": float("nan"), "annual": float("nan"), "max_dd": float("nan"),
                "days": 0}
    days = (pd.Timestamp(s.index[-1]) - pd.Timestamp(s.index[0])).days
    total = float(s.iloc[-1] / s.iloc[0] - 1)
    annual = float((1 + total) ** (365.25 / days) - 1) if days > 0 and total > -1 \
        else float("nan")
    return {"total": total, "annual": annual, "days": days,
            "max_dd": float((s / s.cummax() - 1).min())}


def performance(con, book_id: str) -> dict:
    """The book's daily values against SPY bought with the same money on the same day,
    dividends reinvested (Yahoo's adjusted close); for the EUR book in euros, so the
    EUR/USD move is in the comparison. Missing benchmark days are counted, not filled."""
    b = book_row(con, book_id)
    marks = con.execute("SELECT day, equity FROM fb_marks WHERE book_id = ? ORDER BY day",
                        [book_id]).fetch_df()
    bench = con.execute("SELECT day, spy, eurusd FROM fb_benchmark ORDER BY day").fetch_df()
    start = b["opened_at"].replace(tzinfo=dt.UTC).astimezone(ET).date()
    out = {"book": b, "start": start, "notes": []}
    if marks.empty:
        out["notes"].append("noch keine Bewertung")
        return out
    equity = pd.Series(marks["equity"].to_numpy(),
                       index=pd.to_datetime(marks["day"]).dt.date)
    equity = pd.concat([pd.Series([b["capital"]], index=[start]), equity])
    equity = equity[~equity.index.duplicated(keep="last")].sort_index()
    out["equity"] = equity
    days = pd.to_datetime(bench["day"]).dt.date
    # Only the span the book has a value for: a later benchmark close would compare the
    # book's last value with a market the book has not lived through yet.
    bench = bench[(days >= start) & (days <= equity.index[-1])]
    if b["currency"] == "EUR":
        bench = bench.dropna(subset=["eurusd"])
    if bench.empty or bench["spy"].isna().all():
        out["notes"].append("Benchmarkdaten fehlen (SPY/EUR-USD noch nicht geladen)")
    else:
        spy = pd.Series(bench["spy"].to_numpy(), index=pd.to_datetime(bench["day"]).dt.date)
        growth = spy / spy.iloc[0]
        if b["currency"] == "EUR":
            fx = pd.Series(bench["eurusd"].to_numpy(), index=growth.index)
            growth = growth * (fx.iloc[0] / fx)        # dollars back into euros
        bv = b["capital"] * growth
        out["benchmark"] = bv
        missing = sorted(set(equity.index[1:]) - set(bv.index))
        if missing:
            out["notes"].append(f"{len(missing)} Tag(e) ohne Benchmarkkurs "
                                f"(z. B. {missing[-1]})")
    out["metrics"] = _metrics(equity)
    if "benchmark" in out:
        out["benchmark_metrics"] = _metrics(out["benchmark"])
    if out["metrics"]["days"] < 365:
        out["notes"].append(f"erst {out['metrics']['days']} Tage beobachtet: annualisierte "
                            "Werte sind bei so kurzer Historie nicht aussagekräftig")
    rows = []
    for year in sorted({d.year for d in equity.index}):
        def yr(s, year=year):
            prior = s[[d.year < year for d in s.index]]
            inside = s[[d.year == year for d in s.index]]
            if inside.empty:
                return float("nan")
            base = prior.iloc[-1] if len(prior) else inside.iloc[0]
            return float(inside.iloc[-1] / base - 1)
        r = {"year": year, "strategy": yr(equity)}
        r["benchmark"] = yr(out["benchmark"]) if "benchmark" in out else float("nan")
        r["gap_pp"] = 100 * (r["strategy"] - r["benchmark"])
        r["partial"] = year == start.year or year == equity.index[-1].year
        rows.append(r)
    out["years"] = pd.DataFrame(rows)
    return out
