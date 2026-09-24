"""An AI summary of one company, written from the app's own numbers.

The model gets a data sheet assembled here — valuation, the annual and quarterly
history as first filed, restatements, price behaviour, peers — and is told to argue from
those figures only. Two reasons. A summary that quotes numbers the app cannot show is a
summary nobody can check. And the model's own memory of a company is a snapshot of an
unknown date, which is exactly the look-ahead problem this app exists to avoid.

What it will not do: recommend buying or selling, or set a price target. The person
makes that call; the summary organises what the data says for and against.

Each summary is stored with the data build it was written from, so a second click on
the same day costs nothing and an old summary is visibly old.
"""

from __future__ import annotations

import datetime as dt
import json

import duckdb
import pandas as pd

from . import research as rs
from .config import require_secret

MODEL = "claude-opus-5"
# Server-side fallback: if a safety classifier declines the request, the API re-runs it
# on the model Anthropic recommends for that case instead of returning a refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You write a short research summary of one listed company for a private \
investor, from a data sheet the app provides.

Rules:
- Use ONLY the figures in the data sheet. Quote them with their units and periods. If \
something the investor would want is not in the sheet, say it is missing — do not fill \
it in from memory, and do not mention events or numbers from outside the sheet.
- No recommendation to buy, sell or hold, and no price target. Lay out what the data \
says for and against; the investor decides.
- Flag data quality where the sheet shows it: stale filings, restatements, fields \
marked missing, a very short price history.
- Write in English, in Markdown, with exactly these sections:
  **In one line** — what the company is and the single most important fact in the data.
  **Growth and profitability** — revenue, earnings and cash-flow trend, margins.
  **Balance sheet** — equity, debt, cash, anything that stands out.
  **Valuation** — the multiples, against the peers in the sheet.
  **For** — up to four points, each tied to a figure.
  **Against** — up to four points, each tied to a figure.
  **Check before acting** — what the data cannot answer.
- Be concise: about 300 words."""


def _records(frame: pd.DataFrame, limit: int | None = None) -> list[dict]:
    if frame is None or frame.empty:
        return []
    frame = frame.tail(limit) if limit else frame
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.date.astype(str)
    return json.loads(out.to_json(orient="records", date_format="iso", double_precision=4))


VALUATION_FIELDS = (
    "price", "market_cap", "price_to_book", "price_to_earnings", "price_to_sales",
    "price_to_cash_flow", "price_to_free_cash_flow", "book_value", "revenue_ttm",
    "net_income_ttm", "operating_cash_flow_ttm", "free_cash_flow_ttm",
    "shares_outstanding", "data_age_days", "median_dollar_volume_60d",
)


def data_sheet(con: duckdb.DuckDBPyConnection, ticker: str, cik: int,
               row: pd.Series | None, asof: dt.date,
               frame: pd.DataFrame | None = None) -> dict:
    """Everything the summary may use, as plain JSON-able data."""
    sheet: dict = {"ticker": ticker, "as_of": str(asof)}
    prof = rs.profile(con, ticker) or {}
    sheet["profile"] = {k: prof.get(k) for k in ("name", "sector", "exchange", "sic")
                        if prof.get(k) is not None}
    if row is not None:
        sheet["valuation_now"] = {
            k: (None if pd.isna(row[k]) else float(row[k]))
            for k in VALUATION_FIELDS if k in row.index and not isinstance(row[k], str)
        }
    else:
        sheet["valuation_now"] = "missing: the company is not in today's screening frame"
    # History tables are in the filer's own currency; valuation_now is in USD. Said in
    # the sheet, so the summary cannot set euros against a dollar price.
    sheet["reporting_currency"] = rs.reporting_currency(con, cik)
    sheet["units"] = ("valuation_now in USD; annual_as_first_filed, quarterly_trend and "
                      "restatements in reporting_currency")
    sheet["annual_as_first_filed"] = _records(rs.annual_history(con, cik), limit=6)
    sheet["quarterly_trend"] = _records(rs.quarterly_trend(con, cik, quarters=8))
    sheet["restatements"] = _records(rs.restatements(con, cik), limit=10)
    stats = rs.price_stats(con, ticker) or {}
    sheet["price_behaviour"] = {k: (None if isinstance(v, float) and pd.isna(v) else v)
                                for k, v in stats.items()
                                if isinstance(v, (int, float, str)) or v is None}
    if frame is not None and not frame.empty:
        near = rs.peers(frame, ticker)
        cols = [c for c in ("ticker", "name", "market_cap", "price_to_book",
                            "price_to_earnings", "price_to_sales") if c in near.columns]
        sheet["peers"] = _records(near[cols]) if len(near) else []
    return sheet


def summarize(sheet: dict, client=None) -> dict:
    """Ask the model for the summary. Returns text, the model that answered, and why
    it stopped. A refusal comes back as an explanation, never as an empty summary."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=require_secret("ANTHROPIC_API_KEY"))
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        betas=[FALLBACK_BETA],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": "Data sheet:\n" + json.dumps(sheet, indent=1, default=str),
        }],
    )
    if response.stop_reason == "refusal":
        return {"text": None, "model": response.model, "stop_reason": "refusal",
                "error": "The model declined to write this summary."}
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        return {"text": None, "model": response.model, "stop_reason": response.stop_reason,
                "error": "The model returned no text."}
    return {"text": text, "model": response.model, "stop_reason": response.stop_reason}


# -- storage ------------------------------------------------------------------------------

def stored(con, ticker: str, data_fingerprint: str) -> dict | None:
    row = con.execute(
        "SELECT text, model, created_at FROM ai_summaries WHERE ticker = ? "
        "AND data_fingerprint = ? ORDER BY created_at DESC LIMIT 1",
        [ticker.upper(), data_fingerprint],
    ).fetchone()
    return {"text": row[0], "model": row[1], "created_at": row[2]} if row else None


def latest(con, ticker: str) -> dict | None:
    """The newest summary for a ticker from any data build, to show when today's has
    not been written yet — marked with its date."""
    row = con.execute(
        "SELECT text, model, created_at, data_fingerprint FROM ai_summaries "
        "WHERE ticker = ? ORDER BY created_at DESC LIMIT 1", [ticker.upper()],
    ).fetchone()
    return ({"text": row[0], "model": row[1], "created_at": row[2],
             "data_fingerprint": row[3]} if row else None)


def save(con, ticker: str, data_fingerprint: str, result: dict) -> None:
    con.execute(
        "INSERT INTO ai_summaries (ticker, data_fingerprint, text, model, created_at) "
        "VALUES (?,?,?,?,?)",
        [ticker.upper(), data_fingerprint, result["text"], result["model"],
         dt.datetime.now()],
    )
