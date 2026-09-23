"""A daily briefing, written from evidence that is gathered first.

The model here summarises and connects. It does not research, recall or add. Everything
it is allowed to mention is assembled in code before the call — headlines actually
fetched, price moves actually measured, rates actually read from the store — and handed
over as the complete world it may write about.

That ordering is the whole design. Ask a model "what happened in markets today" and it
produces a confident, plausible, unsourced report. Hand it today's measured facts and ask
it to organise them, and it produces a briefing whose every claim traces back to a row
you can check. The difference is not the prompt, it is which of the two jobs it is given.

Unusual moves are detected here rather than asked for: a share three standard deviations
from its own normal day is having news, whether or not a headline reached the wire yet.
That is a measurement, and it often finds the story before the coverage does.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import numpy as np
import pandas as pd

from . import macro as mc
from . import research as rs

MODEL = "claude-opus-5"
UNUSUAL_SIGMA = 2.5

# A z-score alone is not enough. A share whose price has barely moved for months has a
# standard deviation near zero, so a rounding-sized wobble scores as many sigma and would
# top the briefing as the day's biggest story. Seen in testing: a 0.0099% move scoring
# z = 292 because its typical day was 0.00003%. Real data produces this with halted
# tickers and stale feeds that repeat a close.
#
# So "unusual" has to mean statistically odd AND large enough to matter.
MIN_ABS_MOVE = 0.01      # 1% — below this, nothing is news whatever the z-score
MIN_TYPICAL_DAY = 0.001  # a share with no normal has no abnormal either


def unusual_moves(
    con: duckdb.DuckDBPyConnection, tickers: list[str], lookback: int = 120
) -> pd.DataFrame:
    """Shares whose last move was far outside their own normal range.

    Measured per company against its own history, not against a fixed percentage: a 4%
    day is routine for a small biotech and extraordinary for a utility. The z-score makes
    them comparable, which is what lets one list cover a whole portfolio.
    """
    rows = []
    for ticker in tickers:
        hist = con.execute(
            "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) "
            "ORDER BY date DESC LIMIT ?",
            [ticker, lookback + 1],
        ).fetch_df()
        if len(hist) < 40:
            continue
        hist = hist.sort_values("date")
        rets = hist.close_adj.astype(float).pct_change().dropna()
        if rets.empty or rets.std() == 0:
            continue
        last = float(rets.iloc[-1])
        sigma = float(rets.iloc[:-1].std())
        if sigma == 0:
            continue
        if sigma < MIN_TYPICAL_DAY:
            # No meaningful normal to compare against — usually a halted or stale series.
            continue
        z = last / sigma
        rows.append({
            "ticker": ticker.upper(), "move": last, "z": z,
            "typical_day": sigma, "date": hist.date.iloc[-1],
        })
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.reindex(out.z.abs().sort_values(ascending=False).index)


def gather(
    con: duckdb.DuckDBPyConnection, tickers: list[str], headlines: int = 5
) -> dict:
    """Assemble everything the briefing is allowed to mention."""
    moves = unusual_moves(con, tickers)
    notable = (
        moves[(moves.z.abs() >= UNUSUAL_SIGMA) & (moves.move.abs() >= MIN_ABS_MOVE)]
        if not moves.empty else pd.DataFrame()
    )

    feed = rs.market_news(tickers[:20], per_symbol=headlines) if tickers else pd.DataFrame()

    snap = mc.rate_snapshot(con)
    shape = mc.curve_shape(snap) if not snap.empty else {}
    fed = mc.fed_funds_expectation(con)

    commodities = []
    for symbol, label in rs.COMMODITIES.items():
        h = con.execute(
            "SELECT close_adj FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT 22",
            [symbol],
        ).fetch_df()
        if len(h) < 5:
            continue
        s = h.close_adj.astype(float)[::-1].reset_index(drop=True)
        commodities.append({
            "name": label,
            "day": float(s.iloc[-1] / s.iloc[-2] - 1) if len(s) > 1 else np.nan,
            "month": float(s.iloc[-1] / s.iloc[0] - 1),
        })

    return {
        "as_of": dt.date.today(),
        "followed": tickers,
        "moves": moves,
        "notable": notable,
        "news": feed,
        "rates": snap,
        "curve": shape,
        "fed": fed,
        "commodities": pd.DataFrame(commodities),
    }


def _evidence(bundle: dict) -> str:
    """Render the gathered facts as the only material the model may use."""
    parts: list[str] = [f"DATE: {bundle['as_of']}"]

    if not bundle["rates"].empty:
        r = bundle["rates"]
        parts.append("\nYIELD CURVE (rate %, change in basis points):")
        for row in r.itertuples():
            parts.append(
                f"- {row.tenor}: {row.rate:.3f}%  1w {row.change_1w:+.0f}bp  "
                f"1m {row.change_1m:+.0f}bp  1y {row.change_1y:+.0f}bp"
            )
    if bundle["curve"]:
        c = bundle["curve"]
        parts.append(
            f"- slope 10y minus 3m: {c['slope_bp']:+.0f}bp "
            f"({'inverted' if c['inverted'] else 'upward sloping'})"
        )
    if bundle["fed"].get("direction"):
        f = bundle["fed"]
        parts.append(
            f"- front fed funds contract implies {f['implied_rate']:.3f}% vs "
            f"3-month bill {f.get('bill_rate', 0):.3f}% ({f['direction']})"
        )

    if not bundle["commodities"].empty:
        parts.append("\nCOMMODITIES AND MACRO (1 day, 1 month):")
        for row in bundle["commodities"].itertuples():
            day = "n/a" if pd.isna(row.day) else f"{row.day:+.2%}"
            parts.append(f"- {row.name}: {day} today, {row.month:+.2%} over a month")

    if not bundle["notable"].empty:
        parts.append("\nUNUSUAL MOVES AMONG FOLLOWED COMPANIES (z = move / its own typical day):")
        for row in bundle["notable"].itertuples():
            parts.append(
                f"- {row.ticker}: {row.move:+.2%} on {row.date}, z = {row.z:+.1f} "
                f"(a typical day for it is {row.typical_day:.2%})"
            )
    elif not bundle["moves"].empty:
        parts.append("\nNo followed company moved unusually. Largest moves:")
        for row in bundle["moves"].head(5).itertuples():
            parts.append(f"- {row.ticker}: {row.move:+.2%}, z = {row.z:+.1f}")

    if not bundle["news"].empty:
        parts.append("\nHEADLINES (these are the only stories that exist for this report):")
        for i, row in enumerate(bundle["news"].head(30).itertuples(), 1):
            parts.append(f"[{i}] ({row.tickers}) {row.when} · {row.source}: {row.title}")
            if row.summary:
                parts.append(f"    {row.summary}")

    return "\n".join(parts)


SYSTEM = """You write a short daily market briefing for one investor, from evidence that \
has already been gathered for you.

The evidence below is the entire world of this report. You may organise it, weigh it, \
connect two facts that clearly belong together, and say plainly when something is quiet. \
You may not add a fact that is not in it — no company you were not given, no event not in \
the headlines, no number you were not handed. If you know something relevant from \
elsewhere, leave it out; the reader can check every line of this against the data and \
that is the point.

Structure:
1. **Macro** — rates, the curve, commodities. What changed and what it means for someone \
holding shares. Skip it in a sentence if nothing moved.
2. **What moved** — the unusual moves, each with its z-score. Where a headline explains \
one, say so and cite it as [n]. Where none does, say the move is unexplained by the \
stories available; do not invent a cause.
3. **Worth reading** — at most four headlines that matter to these holdings, each one \
sentence on why, cited as [n].

Rules of tone:
- Plain sentences. No "amid", no "as investors weigh", no market-report filler.
- Numbers as given. Do not round a 2.5-sigma move into "a sharp fall" and lose the size.
- A quiet day is a finding. Say "nothing much happened" rather than inflating noise.
- Never recommend buying or selling anything, and never predict a price."""


def write(bundle: dict, client=None) -> dict:
    """Ask for the briefing. Returns the text and the evidence it was given."""
    evidence = _evidence(bundle)
    if client is None:
        import anthropic

        from .config import require_secret

        client = anthropic.Anthropic(api_key=require_secret("ANTHROPIC_API_KEY"))

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": (
                f"Companies followed: {', '.join(bundle['followed']) or 'none'}\n\n"
                f"EVIDENCE\n{evidence}\n\nWrite the briefing."
            ),
        }],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return {"text": text, "evidence": evidence, "as_of": bundle["as_of"]}
