"""Trading comparables: one company against its peers, the way a comps sheet reads.

Three outputs, each a standard piece of relative valuation:

  * **The table** — the company and its peers on the same multiples, with the peer
    median, mean, high and low, and the company's premium or discount to the median.
  * **Implied value** — the peer median multiple applied to the company's own figure,
    taken back from enterprise value to equity (less net debt) and to a price per
    share. EV multiples and equity multiples are kept apart on purpose: applying an
    EV/EBITDA to earnings, or a P/E to EBITDA, is the classic comps mistake.
  * **Against its own history** — how far today's multiple sits from the company's
    own average, in standard deviations.

Everything is computed from the screening frame (point-in-time, in USD) and the
valuation history; nothing here fetches data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# (column, label, basis). "ev" multiples imply an enterprise value, "equity" multiples
# a market cap; "metric" rows are compared but imply nothing.
COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("ev_to_ebitda", "EV/EBITDA", "ev"),
    ("ev_to_ebit", "EV/EBIT", "ev"),
    ("ev_to_sales", "EV/Sales", "ev"),
    ("price_to_earnings", "P/E", "equity"),
    ("price_to_book", "P/B", "equity"),
    ("price_to_free_cash_flow", "P/FCF", "equity"),
    ("ebitda_margin", "EBITDA margin", "metric"),
    ("return_on_invested_capital", "ROIC", "metric"),
    ("revenue_growth", "Revenue growth", "metric"),
    ("net_debt_to_ebitda", "Net debt/EBITDA", "metric"),
)

# Which of the company's own figures each multiple is applied to.
BASES = {
    "ev_to_ebitda": "ebitda_ttm",
    "ev_to_ebit": "operating_income_ttm",
    "ev_to_sales": "revenue_ttm",
    "price_to_earnings": "net_income_ttm",
    "price_to_book": "book_value",
    "price_to_free_cash_flow": "free_cash_flow_ttm",
}


def _row(frame: pd.DataFrame, ticker: str) -> pd.Series | None:
    hit = frame[frame.ticker.astype(str).str.upper() == ticker.upper()]
    return hit.iloc[0] if len(hit) else None


def sheet(frame: pd.DataFrame, ticker: str, peers: list[str]) -> dict:
    """The comps table plus the peer statistics and the company's position.

    Returns {"rows": DataFrame (company first, then peers), "stats": DataFrame indexed
    by median/mean/high/low, "vs_median": Series of premium (+) or discount (−)}.
    """
    cols = [c for c, _, _ in COLUMNS if c in frame.columns]
    keep = ["ticker", "name", "market_cap", "enterprise_value", *cols]
    keep = [c for c in keep if c in frame.columns]
    subject = _row(frame, ticker)
    if subject is None:
        return {}
    wanted = [t.upper() for t in peers if t.upper() != ticker.upper()]
    peer_rows = frame[frame.ticker.astype(str).str.upper().isin(wanted)][keep]
    rows = pd.concat([subject[keep].to_frame().T, peer_rows], ignore_index=True)
    numeric = peer_rows[cols].apply(pd.to_numeric, errors="coerce")
    stats = pd.DataFrame({
        "median": numeric.median(), "mean": numeric.mean(),
        "high": numeric.max(), "low": numeric.min(),
        "n": numeric.notna().sum(),
    }).T
    own = pd.to_numeric(subject[cols], errors="coerce")
    median = stats.loc["median", cols].astype(float)
    # A multiple is compared in percent (a 17x against a 10x median is a 70% premium);
    # a margin, a return or a leverage figure is compared as a difference — a 28%
    # margin against a 2.5% median is 25.5 points better, not "+1,020%".
    basis = {c: b for c, _, b in COLUMNS}
    vs = pd.Series({
        c: ((own[c] / median[c] - 1.0) if median[c] > 0 else np.nan)
        if basis.get(c) != "metric" else own[c] - median[c]
        for c in cols
    })
    return {"rows": rows, "stats": stats, "vs_median": vs}


def implied(frame: pd.DataFrame, ticker: str, stats: pd.DataFrame) -> pd.DataFrame:
    """What each peer median multiple says the company's shares are worth.

    EV multiples: median × company figure = implied EV; less net debt = equity value.
    Equity multiples: median × company figure = equity value directly. Per share on
    the listed-share count the frame already uses. A multiple is skipped when the
    company's own figure is not positive — a median P/E times a loss is not a value.
    """
    subject = _row(frame, ticker)
    if subject is None or stats is None or stats.empty:
        return pd.DataFrame()
    shares = pd.to_numeric(subject.get("shares_outstanding"), errors="coerce")
    price = pd.to_numeric(subject.get("price"), errors="coerce")
    net_debt = pd.to_numeric(subject.get("net_debt"), errors="coerce")
    out = []
    for col, label, basis in COLUMNS:
        if basis == "metric" or col not in stats.columns:
            continue
        multiple = pd.to_numeric(stats.at["median", col], errors="coerce")
        figure = pd.to_numeric(subject.get(BASES[col]), errors="coerce")
        if pd.isna(multiple) or pd.isna(figure) or figure <= 0 or not shares:
            continue
        value = multiple * figure
        if basis == "ev":
            if pd.isna(net_debt):
                continue                   # no bridge from EV to equity without it
            value -= net_debt
        per_share = value / shares
        out.append({
            "multiple": label, "peer median": float(multiple),
            "applied to": BASES[col], "company figure": float(figure),
            "implied equity value": float(value), "implied price": float(per_share),
            "vs price": float(per_share / price - 1.0) if price else np.nan,
        })
    return pd.DataFrame(out)


def against_history(history: pd.DataFrame, current: pd.Series) -> pd.DataFrame:
    """Today's multiple against the company's own yearly history, as a z-score.

    Needs three past years; fewer is not a history. `history` is
    research.valuation_history (year, price_to_earnings, price_to_book, price_to_sales).
    """
    rows = []
    for col, label in (("price_to_earnings", "P/E"), ("price_to_book", "P/B"),
                       ("price_to_sales", "P/S")):
        if history is None or history.empty or col not in history:
            continue
        past = pd.to_numeric(history[col], errors="coerce").dropna()
        past = past[past > 0]
        now = pd.to_numeric(current.get(col), errors="coerce")
        if len(past) < 3 or pd.isna(now):
            continue
        sd = past.std(ddof=1)
        rows.append({
            "multiple": label, "now": float(now), "own average": float(past.mean()),
            "years": len(past),
            "z-score": float((now - past.mean()) / sd) if sd > 0 else np.nan,
        })
    return pd.DataFrame(rows)
