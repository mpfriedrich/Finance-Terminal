"""Paper books: simulated purchases, frozen when made and followed live.

Every tab that can put money somewhere can open a book here. What the book holds is
fixed at the moment it is opened — the positions, the rule, or the savings plan — and
from then on it is only VALUED, never re-decided. That is what makes it the honest
counterpart to a backtest: the choice exists before the outcome does.

Three kinds, one per way the app invests:

  * ``hold`` — names and weights bought at the latest close, held (SCREEN, RESEARCH).
  * ``rule`` — a LAB rule on one stock or a basket, followed live: signals read at each
    close, filled at the next open, costs charged on what changes hands.
  * ``plan`` — a STRATEGY savings plan: contributions on its schedule from the start
    date, deployed by its rule.

Every book reports the same few numbers — money in, value now, gain — and a daily value
curve, so the comparison tab can put them side by side.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict

import duckdb
import pandas as pd

KINDS = ("hold", "rule", "plan")
DOUBLE_CLICK_SECONDS = 30
BENCHMARK = "^SP500TR"

# -- opening ------------------------------------------------------------------------------

def _new_book(con, name: str, origin: str, kind: str, started_on: dt.date, amount: float,
              config: dict, thesis: str = "") -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown book kind: {kind}")
    if amount <= 0:
        raise ValueError("a paper book needs an amount above zero")
    name = name.strip() or f"{origin} {started_on}"
    # One click, one book. Streamlit can run a button's handler twice when the click
    # lands while the page is still re-running (seen in the app: one click on RESEARCH
    # opened two identical books). The same book opened again within seconds is that
    # double run, not a second purchase.
    same = con.execute(
        "SELECT id FROM paper_books WHERE name = ? AND origin = ? AND kind = ? "
        "AND started_on = ? AND amount = ? AND created_at >= ? LIMIT 1",
        [name, origin, kind, started_on, float(amount),
         dt.datetime.now() - dt.timedelta(seconds=DOUBLE_CLICK_SECONDS)],
    ).fetchone()
    if same:
        return same[0]
    book_id = uuid.uuid4().hex[:12]
    con.execute(
        "INSERT INTO paper_books (id, name, origin, kind, started_on, amount, config_json, "
        "thesis, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [book_id, name, origin, kind, started_on,
         float(amount), json.dumps(config, sort_keys=True, default=str), thesis,
         dt.datetime.now()],
    )
    return book_id


def open_hold(con, name: str, origin: str, positions: pd.DataFrame, started_on: dt.date,
              amount: float, thesis: str = "") -> str:
    """Buy `positions` (ticker, weight, price, optional cost) at their prices now.

    Weights are normalised to one. `cost` is the one-way cost fraction of the purchase,
    charged once at entry — the book starts below the amount by exactly that.
    """
    if positions.empty:
        raise ValueError("nothing to buy")
    weights = positions["weight"].astype(float)
    weights = weights / weights.sum()
    book_id = _new_book(con, name, origin, "hold", started_on, amount, {}, thesis)
    if con.execute("SELECT COUNT(*) FROM paper_positions WHERE book_id = ?",
                   [book_id]).fetchone()[0]:
        return book_id                      # the double run of a click already did this
    for (_, row), weight in zip(positions.iterrows(), weights):
        con.execute(
            "INSERT INTO paper_positions (book_id, ticker, weight, entry_price, entry_cost) "
            "VALUES (?,?,?,?,?)",
            [book_id, str(row["ticker"]).upper(), float(weight), float(row["price"]),
             float(row.get("cost", 0.0) or 0.0)],
        )
    return book_id


def open_rule(con, name: str, tickers: list[str], rule, started_on: dt.date,
              amount: float, cost_tier_market_cap: float = 0.0, thesis: str = "") -> str:
    """Follow a LAB rule live on `tickers` from `started_on` on."""
    if not tickers:
        raise ValueError("a rule needs at least one ticker")
    config = {"tickers": [t.upper() for t in tickers], "rule": asdict(rule),
              "cost_tier_market_cap": float(cost_tier_market_cap)}
    return _new_book(con, name, "LAB", "rule", started_on, amount, config, thesis)


def open_plan(con, name: str, ticker: str, plan, started_on: dt.date,
              thesis: str = "") -> str:
    """Run a STRATEGY savings plan live on `ticker` from `started_on` on. The amount is
    the contribution per period; money in grows with the schedule."""
    if plan.rule == "lump_sum":
        # "Everything the plan would ever contribute, on day one" has no fixed size
        # when the plan runs into the future. A single purchase is a hold book.
        raise ValueError("a lump sum is a one-off purchase: open a hold book instead")
    config = {"ticker": ticker.upper(), "plan": asdict(plan)}
    return _new_book(con, name, "STRATEGY", "plan", started_on, plan.amount, config, thesis)


def close_book(con, book_id: str) -> None:
    con.execute("DELETE FROM paper_positions WHERE book_id = ?", [book_id])
    con.execute("DELETE FROM paper_books WHERE id = ?", [book_id])


def books(con) -> pd.DataFrame:
    return con.execute(
        "SELECT id, name, origin, kind, started_on, amount, config_json, thesis, created_at "
        "FROM paper_books ORDER BY created_at"
    ).fetch_df()


def positions(con, book_id: str) -> pd.DataFrame:
    return con.execute(
        "SELECT ticker, weight, entry_price, entry_cost FROM paper_positions "
        "WHERE book_id = ? ORDER BY weight DESC", [book_id],
    ).fetch_df()


# -- valuing ------------------------------------------------------------------------------

def _closes(market: duckdb.DuckDBPyConnection, tickers: list[str], start: dt.date,
            end: dt.date | None = None) -> pd.DataFrame:
    end = end or dt.date(9999, 12, 31)
    frame = market.execute(
        "SELECT ticker, date, close_adj FROM prices WHERE ticker IN (SELECT UNNEST(?)) "
        "AND date BETWEEN ? AND ? AND close_adj > 0 ORDER BY date",
        [tickers, start, end],
    ).fetch_df()
    if frame.empty:
        return pd.DataFrame()
    wide = frame.pivot_table(index="date", columns="ticker", values="close_adj")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


def _benchmark(market, index: pd.DatetimeIndex, start: dt.date) -> pd.Series | None:
    """The index over the book's own dates, rebased to the book's first day. Forward
    fill only: nothing may be taken from a later date."""
    b = _closes(market, [BENCHMARK], start)
    if b.empty or index.empty:
        return None
    series = b[BENCHMARK]
    series = series.reindex(series.index.union(index)).ffill().reindex(index)
    if series.isna().any():
        return None
    return series / series.iloc[0]


def value_hold(market, book: dict, pos: pd.DataFrame) -> dict:
    """Buy and hold: each name grows with its adjusted close from its entry day."""
    start = book["started_on"]
    wide = _closes(market, pos.ticker.tolist(), start)
    if wide.empty:
        return {"empty": True}
    wide = wide.ffill()
    growth = wide / wide.bfill().iloc[0]           # each name from its first stored close
    w = pos.set_index("ticker")
    net = (w["weight"] * (1.0 - w["entry_cost"])).reindex(growth.columns).fillna(0.0)
    missing = sorted(set(pos.ticker) - set(wide.columns))
    value = book["amount"] * (growth.fillna(1.0) * net).sum(axis=1)
    # A name with no prices at all keeps its money at cost rather than vanishing.
    if missing:
        value = value + book["amount"] * float(
            (w.loc[missing, "weight"] * (1.0 - w.loc[missing, "entry_cost"])).sum())
    invested = pd.Series(book["amount"], index=value.index)
    notes = ([f"no prices yet for {', '.join(missing)} — held at cost"] if missing else [])
    return {"empty": False, "value": value, "invested": invested, "notes": notes}


def value_rule(market, book: dict, config: dict) -> dict:
    """The rule, followed from the start date: indicators warmed up on the history
    before it, positions only from the first open after it."""
    from .lab import engine as lab_engine
    from .lab.spec import StrategySpec

    start = book["started_on"]
    tickers = config["tickers"]
    warm_up = start - dt.timedelta(days=500)
    bars = market.execute(
        """
        SELECT ticker, date, open * (close_adj / NULLIF(close, 0)) AS open_adj, close_adj
        FROM prices WHERE ticker IN (SELECT UNNEST(?)) AND date >= ?
          AND open IS NOT NULL AND close_adj IS NOT NULL ORDER BY date
        """,
        [tickers, warm_up],
    ).fetch_df()
    if bars.empty:
        return {"empty": True}
    from .config import CostModel

    known = set(StrategySpec.__dataclass_fields__)
    rule = StrategySpec(**{k: (tuple(v) if k == "unsupported" else v)
                           for k, v in config["rule"].items() if k in known})
    result = lab_engine.run(bars, rule, costs=CostModel(),
                            cost_tier_market_cap=config.get("cost_tier_market_cap", 0.0),
                            trade_from=start)
    if result is None:
        return {"empty": True}
    curve = pd.Series(result.portfolio, index=pd.to_datetime(result.dates))
    curve = curve[curve.index >= pd.Timestamp(start)]
    if curve.empty:
        return {"empty": True}
    value = book["amount"] * curve / curve.iloc[0]
    fills = result.fills
    if not fills.empty:
        fills = fills[pd.to_datetime(fills.date) >= pd.Timestamp(start)]
    return {"empty": False, "value": value,
            "invested": pd.Series(book["amount"], index=value.index),
            "fills": fills, "notes": list(result.notes)}


def value_plan(market, book: dict, config: dict) -> dict:
    """The savings plan from its start date. Money in grows with each contribution."""
    from .strategy import Plan, simulate

    start = book["started_on"]
    prices = _closes(market, [config["ticker"]], start - dt.timedelta(days=500))
    if prices.empty:
        return {"empty": True}
    plan = Plan(**config["plan"])
    series = prices[config["ticker"].upper()] if config["ticker"].upper() in prices \
        else prices.iloc[:, 0]
    out = simulate(series, plan, start=start)
    if not out:
        return {"empty": True}
    value = out["curve"]
    dates = value.index
    from .strategy import _contribution_dates

    contributed = pd.Series(0.0, index=dates)
    for d in _contribution_dates(dates, plan.frequency):
        contributed.loc[d] += plan.amount
    contributed = contributed.cumsum()
    return {"empty": False, "value": value, "invested": contributed,
            "fills": out["purchases"], "notes": []}


def value_book(market, con, book: dict, held: pd.DataFrame | None = None) -> dict:
    """Value any book. Adds the headline numbers and the index over the same days,
    scaled to the same money so the two lines can share an axis.

    `held` stands in for the stored positions of a hold book — used for the SCREEN
    tab's older one-per-portfolio tracking, which keeps its positions elsewhere.
    """
    config = json.loads(book["config_json"])
    if book["kind"] == "hold":
        out = value_hold(market, book,
                         held if held is not None else positions(con, book["id"]))
    elif book["kind"] == "rule":
        out = value_rule(market, book, config)
    else:
        out = value_plan(market, book, config)
    if out.get("empty"):
        return {"empty": True, "book": book}
    value, invested = out["value"], out["invested"]
    out["book"] = book
    out["value_now"] = float(value.iloc[-1])
    out["invested_now"] = float(invested.iloc[-1])
    out["gain"] = out["value_now"] - out["invested_now"]
    out["return"] = out["gain"] / out["invested_now"] if out["invested_now"] else float("nan")
    out["as_of"] = value.index[-1].date()
    bench = _benchmark(market, value.index, book["started_on"])
    if bench is not None and book["kind"] != "plan":
        out["benchmark_value"] = book["amount"] * bench
        out["benchmark_return"] = float(bench.iloc[-1] - 1.0)
    return out
