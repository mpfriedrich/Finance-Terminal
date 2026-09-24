"""Turn a chat message into a PortfolioSpec change. Claude is the translator here — not
the stock picker.

The tool schema below is the entire vocabulary the model may answer in. It has:
  * no field that can hold a ticker, company name or CIK — so the model cannot name a
    stock even if asked to. Stock selection stays deterministic and reproducible.
  * an enum of allowed metrics — a criterion outside it cannot be encoded.
  * a required `unsupported` array — anything the model could not map must be reported
    there, which is what makes silent dropping impossible (PLAN.md R3).

`strict: true` makes the API enforce the schema rather than the prompt asking nicely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import require_secret
from ..data import industry
from .spec import FILTERABLE, Filter, PortfolioSpec

MODEL = "claude-opus-5"

SYSTEM = """You translate a person's plain-language portfolio criteria into a structured \
stock screen. You are a translator, not an analyst.

Rules:
- Never name, suggest or think about specific companies or tickers. You cannot: the \
schema has no field for them. Selection happens afterwards from real market data.
- Carry forward the current specification. A new message usually REFINES it. Only drop a \
filter if the person clearly contradicts or removes it.
- Map only what genuinely fits an available metric. Anything else goes in `unsupported`, \
worded as the person said it. Never approximate a criterion you cannot express — an \
approximation the person did not ask for is worse than an honest gap.
- Common shorthand: "cap" = market_cap; "p/b"/"book" = price_to_book; \
"price to profit"/"p/e"/"earnings" = price_to_earnings; "p/s"/"sales" = price_to_sales; \
"debt to equity"/"d/e"/"gearing"/"leverage" = debt_to_equity; \
"net margin"/"profit margin" = net_margin (a FRACTION: 10% is 0.10, not 10); \
"roe" = return_on_equity; "intrinsic value"/"fair value"/"dcf" = price_to_intrinsic \
(below 1 means the market pays less than the model's value). \
"2bn"/"2b" = 2000000000. "max 2/1" or "max 2:1" means a ratio of at most 2.
- "price to cash" is ambiguous and must NOT be guessed. price_to_cash compares the \
market value to cash on the balance sheet; price_to_cash_flow compares it to operating \
cash flow; price_to_free_cash_flow subtracts capital spending first. If the person's \
wording does not make clear which one they mean, put it in `unsupported` naming the \
three options so they can pick.
- Margins, returns and ratios are fractions, never percentages: "margin over 15%" \
becomes net_margin >= 0.15.
- The universe is ALREADY restricted to US-listed common stock (NYSE, Nasdaq, CBOE; \
no funds, ETFs or second share classes). "US market", "US stocks" and the like are \
already satisfied — do not report them as unsupported and do not add a filter for them. \
A request for a specific exchange IS a filter.
- If the person gives a criterion whose meaning is genuinely ambiguous, put it in \
`unsupported` with a short note on what is unclear rather than guessing."""

TOOL = {
    "name": "update_portfolio_spec",
    "description": (
        "Record the portfolio screen implied by the person's message, starting from the "
        "current specification. Return the COMPLETE resulting specification, not a diff."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["size", "filters", "rank_by", "weighting", "rebalance", "unsupported"],
        "properties": {
            "size": {
                "type": "integer",
                "description": "How many positions the portfolio should hold.",
            },
            "filters": {
                "type": "array",
                "description": "All filters that should be active after this message.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["metric", "op", "value"],
                    "properties": {
                        "metric": {"type": "string", "enum": list(FILTERABLE)},
                        "op": {
                            "type": "string",
                            "enum": ["<=", ">=", "<", ">", "==", "!=", "in",
                                     "contains", "not_contains"],
                        },
                        "value": {
                            "type": ["number", "string", "array"],
                            "items": {"type": "string"},
                            "description": (
                                "Plain number for numeric metrics (2000000000, not '2bn'). "
                                "String for industry_group, sector or exchange. A list of "
                                "strings only with op \"in\" — that is how several "
                                "industries are allowed at once."
                            ),
                        },
                    },
                },
            },
            # "auto" rather than null: a JSON-Schema enum cannot mix a string list
            # with null under a union type — the API rejects it outright.
            "rank_by": {
                "type": "string",
                "enum": list(FILTERABLE) + ["auto"],
                "description": (
                    "Which metric decides who makes the cut when more names qualify than "
                    "requested. Use \"auto\" to let the app choose and show its choice."
                ),
            },
            "weighting": {"type": "string", "enum": ["equal", "market_cap"]},
            "rebalance": {
                "type": "string",
                "enum": ["buy_and_hold", "quarterly", "annual"],
            },
            "unsupported": {
                "type": "array",
                "description": (
                    "Every part of the message that could not be expressed as a filter, "
                    "in the person's own words. Must not be omitted when something did "
                    "not fit."
                ),
                "items": {"type": "string"},
            },
        },
    },
}


@dataclass(slots=True)
class Translation:
    spec: PortfolioSpec
    raw: dict
    changed: list[str]


def spec_to_payload(spec: PortfolioSpec) -> dict:
    return {
        "size": spec.size,
        "filters": [{"metric": f.metric, "op": f.op, "value": f.value} for f in spec.filters],
        "rank_by": spec.rank_by or "auto",
        "weighting": spec.weighting,
        "rebalance": spec.rebalance,
    }


def payload_to_spec(payload: dict, previous: PortfolioSpec) -> PortfolioSpec:
    """Build a spec from the model's answer, keeping the user's eligibility settings.

    Floors (min price, turnover, filing age) are deliberately NOT model-controlled: they
    are safety rails, and a chat message should not be able to quietly remove them.
    """
    filters = tuple(
        Filter(metric=f["metric"], op=f["op"], value=f["value"])
        for f in payload.get("filters", [])
        if f.get("metric") in FILTERABLE
    )
    rank_by = payload.get("rank_by")
    if rank_by == "auto":
        rank_by = None
    return PortfolioSpec(
        size=int(payload.get("size") or previous.size),
        filters=filters,
        rank_by=rank_by if rank_by in FILTERABLE else None,
        rank_ascending=previous.rank_ascending,
        weighting=payload.get("weighting") or previous.weighting,
        rebalance=payload.get("rebalance") or previous.rebalance,
        min_price=previous.min_price,
        min_dollar_volume=previous.min_dollar_volume,
        min_trading_days_60=previous.min_trading_days_60,
        max_filing_age_months=previous.max_filing_age_months,
        unsupported=tuple(payload.get("unsupported") or ()),
        # Carried over from the previous spec, never read from the model's answer. The
        # schema has no ticker field, and if a future change ever added one, this line
        # still refuses it.
        tickers=previous.tickers,
    )


def diff_summary(before: PortfolioSpec, after: PortfolioSpec) -> list[str]:
    old = {(f.metric, f.op): f.value for f in before.filters}
    new = {(f.metric, f.op): f.value for f in after.filters}
    changed: list[str] = []
    for key, value in new.items():
        if key not in old:
            changed.append(f"added: {key[0]} {key[1]} {value}")
        elif old[key] != value:
            changed.append(f"changed: {key[0]} {key[1]} {old[key]} → {value}")
    for key in old:
        if key not in new:
            changed.append(f"removed: {key[0]} {key[1]}")
    if before.size != after.size:
        changed.append(f"size: {before.size} → {after.size}")
    return changed


def translate(
    message: str,
    spec: PortfolioSpec,
    client=None,
    sectors: list[str] | None = None,
) -> Translation:
    """Ask Claude to fold `message` into `spec`. Network call; see tests for the offline path."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=require_secret("ANTHROPIC_API_KEY"))

    system = SYSTEM
    # Two industry vocabularies, and the model has to be told which is which.
    # `industry_group` is the short one people speak in; `sector` is the SEC's own
    # wording and the only way to say something as narrow as "silver mining".
    system += (
        "\n\nFor industry, prefer the metric `industry_group`, whose complete set of "
        "values is:\n"
        + "\n".join(f"- {g}" for g in industry.GROUPS)
        + "\nUse op \"in\" with a list, or \"==\" / \"!=\" for a single one. These are "
        "exact values, not fragments."
    )
    if sectors:
        # The full list, not a sample. A truncated vocabulary is worse than none: the
        # model cannot tell the difference between "this industry is absent from the
        # data" and "it was cut off", so it invents a plausible-looking phrase that
        # matches nothing.
        system += (
            "\n\nFor a narrower industry than any group above, filter `sector` with op "
            "\"contains\". These are the SEC's own descriptions, all of them, exactly as "
            "written:\n"
            + "\n".join(f"- {name}" for name in sectors)
            + "\nUse a fragment of one of these. Anything else matches nothing."
        )

    response = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        tools=[TOOL],
        tool_choice={"type": "tool", "name": TOOL["name"]},
        messages=[
            {
                "role": "user",
                "content": (
                    "Current specification:\n"
                    f"{json.dumps(spec_to_payload(spec), indent=2)}\n\n"
                    f"New message from the person:\n{message}"
                ),
            }
        ],
    )

    payload = next(
        (b.input for b in response.content if b.type == "tool_use"),
        None,
    )
    if payload is None:
        raise RuntimeError(
            "The model did not return a specification. The screen is unchanged; "
            "try rephrasing, or set the filters by hand."
        )

    new_spec = payload_to_spec(payload, spec)
    return Translation(spec=new_spec, raw=payload, changed=diff_summary(spec, new_spec))
