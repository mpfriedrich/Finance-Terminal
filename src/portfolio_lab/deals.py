"""Precedent transactions: what acquirers paid, as multiples of the target's numbers.

The deal terms are entered by hand — a full M&A database is a paid product — but
everything computed from them comes from the target's SEC filings AS THEY STOOD ON THE
ANNOUNCEMENT DATE (pit.fundamentals_wide on that date). A multiple built on figures
published after the deal would describe something the buyer could not have known.

  equity value     entered, or offer price × the target's shares
  enterprise value equity value + the target's net debt at announcement
  multiples        EV/Revenue, EV/EBITDA, EV/EBIT (TTM), P/E, P/B
  premium          offer price over the unaffected price (entered, or the last close
                   at least one trading day before the announcement if stored)

Targets are often delisted by the time anyone builds a comps table, so a target can be
given by CIK: the SEC keeps a delisted company's filings.
"""

from __future__ import annotations

import datetime as dt
import uuid

import duckdb
import numpy as np
import pandas as pd

from .data import pit


def add(con, target: str, announced: dt.date, acquirer: str = "",
        equity_value: float | None = None, offer_price: float | None = None,
        unaffected_price: float | None = None, consideration: str = "",
        note: str = "") -> str:
    if not target.strip():
        raise ValueError("a deal needs a target (ticker or CIK)")
    if not (equity_value or offer_price):
        raise ValueError("give the equity value or the offer price per share")
    deal_id = uuid.uuid4().hex[:10]
    con.execute(
        "INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,?)",
        [deal_id, target.strip().upper(), acquirer.strip(), announced,
         equity_value, offer_price, unaffected_price, consideration, note,
         dt.datetime.now()])
    return deal_id


def remove(con, deal_id: str) -> None:
    con.execute("DELETE FROM deals WHERE id = ?", [deal_id])


def listing(con) -> pd.DataFrame:
    return con.execute(
        "SELECT id, target, acquirer, announced, equity_value, offer_price, "
        "unaffected_price, consideration, note FROM deals ORDER BY announced DESC"
    ).fetch_df()


def _resolve(market, target: str) -> tuple[int | None, str | None, str]:
    """(cik, ticker, name) from a ticker or a CIK."""
    if target.isdigit():
        row = market.execute("SELECT ticker, name FROM securities WHERE cik = ? LIMIT 1",
                             [int(target)]).fetchone()
        return int(target), (row[0] if row else None), (row[1] if row else f"CIK {target}")
    row = market.execute("SELECT cik, name FROM securities WHERE upper(ticker) = ? "
                         "LIMIT 1", [target.upper()]).fetchone()
    return (int(row[0]), target.upper(), row[1]) if row else (None, target.upper(), target)


def _unaffected(market, ticker: str | None, announced: dt.date) -> float | None:
    """The last traded close strictly before the announcement date."""
    if not ticker:
        return None
    row = market.execute(
        "SELECT COALESCE(close_raw, close) FROM prices WHERE upper(ticker) = ? "
        "AND date < ? ORDER BY date DESC LIMIT 1", [ticker, announced]).fetchone()
    return float(row[0]) if row and row[0] else None


def value(market: duckdb.DuckDBPyConnection, deal: dict) -> dict:
    """The deal's multiples from the target's point-in-time figures."""
    cik, ticker, name = _resolve(market, deal["target"])
    announced = pd.Timestamp(deal["announced"]).date()
    out = {"id": deal["id"], "target": name, "ticker": ticker or "", "cik": cik,
           "acquirer": deal.get("acquirer") or "", "announced": announced}
    if cik is None:
        return {**out, "error": "target not found — give its CIK"}
    wide = pit.fundamentals_wide(market, announced)
    row = wide[wide.cik == cik]
    if row.empty:
        return {**out, "error": "no SEC figures visible at the announcement date"}
    f = row.iloc[0]

    def get(col):
        v = f.get(col)
        return None if v is None or pd.isna(v) else float(v)

    shares = get("shares_outstanding")
    offer = deal.get("offer_price")
    offer = None if offer is None or pd.isna(offer) else float(offer)
    equity = deal.get("equity_value")
    equity = None if equity is None or pd.isna(equity) else float(equity)
    if equity is None and offer and shares:
        equity = offer * shares
    debt = (get("long_term_debt") or 0.0) + (get("current_debt") or 0.0)
    known_debt = get("long_term_debt") is not None or get("current_debt") is not None
    cash = get("cash")
    net_debt = debt - cash if known_debt and cash is not None else None
    ev = equity + net_debt if equity is not None and net_debt is not None else None
    ebit = get("operating_income_ttm")
    da = get("depreciation_amortization_ttm")
    ebitda = ebit + da if ebit is not None and da is not None else None

    def multiple(numer, denom):
        return numer / denom if numer is not None and denom and denom > 0 else None

    unaffected = deal.get("unaffected_price")
    unaffected = (float(unaffected) if unaffected is not None and not pd.isna(unaffected)
                  else _unaffected(market, ticker, announced))
    return {
        **out, "equity_value": equity, "net_debt": net_debt, "enterprise_value": ev,
        "revenue_ttm": get("revenue_ttm"), "ebitda_ttm": ebitda,
        "ev_to_revenue": multiple(ev, get("revenue_ttm")),
        "ev_to_ebitda": multiple(ev, ebitda), "ev_to_ebit": multiple(ev, ebit),
        "price_to_earnings": multiple(equity, get("net_income_ttm")),
        "price_to_book": multiple(equity, get("book_value")),
        "offer_price": offer, "unaffected_price": unaffected,
        "premium": offer / unaffected - 1.0 if offer and unaffected else None,
        "figures_as_of": announced,
    }


MULTIPLES = ("ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings",
             "price_to_book", "premium")


def table(market, con) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every deal valued, and median/mean/high/low of each multiple across them."""
    rows = [value(market, d) for d in listing(con).to_dict("records")]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, pd.DataFrame()
    cols = [c for c in MULTIPLES if c in frame.columns]
    numeric = frame[cols].apply(pd.to_numeric, errors="coerce")
    stats = pd.DataFrame({"median": numeric.median(), "mean": numeric.mean(),
                          "high": numeric.max(), "low": numeric.min(),
                          "n": numeric.notna().sum()}).T
    return frame, stats


def apply_to(stats: pd.DataFrame, subject: pd.Series) -> pd.DataFrame:
    """What the precedent medians would pay for `subject` (a derive_metrics row):
    EV multiples bridged to equity through net debt, per listed share, against price."""
    pairs = (("ev_to_revenue", "revenue_ttm", "ev"), ("ev_to_ebitda", "ebitda_ttm", "ev"),
             ("ev_to_ebit", "operating_income_ttm", "ev"),
             ("price_to_earnings", "net_income_ttm", "equity"),
             ("price_to_book", "book_value", "equity"))
    shares, price = subject.get("shares_outstanding"), subject.get("price")
    net_debt = subject.get("net_debt")
    rows = []
    for col, base, kind in pairs:
        if stats.empty or col not in stats.columns:
            continue
        m = pd.to_numeric(stats.at["median", col], errors="coerce")
        figure = pd.to_numeric(subject.get(base), errors="coerce")
        if pd.isna(m) or pd.isna(figure) or figure <= 0 or not shares:
            continue
        equity = m * figure - (net_debt if kind == "ev" else 0.0)
        if kind == "ev" and (net_debt is None or pd.isna(net_debt)):
            continue
        rows.append({"multiple": col, "precedent median": float(m),
                     "implied equity value": float(equity),
                     "implied price": float(equity / shares),
                     "vs price": float(equity / shares / price - 1.0) if price else np.nan})
    return pd.DataFrame(rows)
