"""Turn a plain-language rule into a StrategySpec. The model translates; it does not invent.

The same discipline as the screener's translator, for the same reason. The schema below
is the entire vocabulary the model may answer in: an enum of signals it can actually
compute, numbers with stated units, and a required `unsupported` array. A rule the
vocabulary cannot express is reported as unsupported, in the person's own words, rather
than approximated by the nearest thing that happens to exist.

That last rule is the one that matters here. "Buy when the stochastic oscillator crosses
20" has no home in this schema, and quietly turning it into an RSI rule would produce a
result the person would read as their idea when it is not.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..config import require_secret
from .spec import SIGNALS, STOPS, StrategySpec

MODEL = "claude-opus-5"

SYSTEM = """You translate a person's plain-language trading rule into a structured \
strategy specification. You are a translator, not a strategist.

Rules:
- Never name a company or a ticker. The schema has no field for one; the stock or \
basket is chosen elsewhere in the app.
- Carry the current specification forward. A new message usually REFINES it.
- Map only what genuinely fits a field below. Everything else goes into `unsupported`, \
in the person's own words. Never approximate: an indicator the person did not ask for, \
chosen because it was the closest one available, is worse than an honest gap.
- Common shorthand: "golden cross" = ma_cross with fast 50 and slow 200; "moving \
average" / "MA" / "SMA" = ma_cross; "trend following" = ma_cross; "momentum" / \
"relative strength" = momentum; "buy the dip" / "oversold" / "reversion" = \
mean_reversion; "buy and hold" = always_in; "Bollinger" / "bands": buying the lower \
band = bollinger_reversion, buying a break above the upper band = bollinger_breakout; \
"RSI" / "relative strength index" = rsi (NOT momentum); "breakout" / "new high" / \
"Donchian" / "turtle" = channel_breakout; "MACD" = macd.
- "Only in an uptrend" / "only above the 200-day line" on top of another rule = \
trend_filter_days (200 unless a length is given). Two entry signals combined in any other \
way (e.g. "RSI below 30 AND a MACD cross") cannot be expressed: keep the first and put \
the rest in `unsupported`.
- Percentages are FRACTIONS: 20% is 0.20, not 20.
- Lengths are TRADING days unless the person clearly means calendar time; "6 months" is \
about 126 trading days, "a year" about 252.
- A stop is only set if the person asks for one. Silence means no stop.
- If the person asks for something intraday, high-frequency, order-book based, or \
anything needing data finer than daily bars, put it in `unsupported` and say so. The app \
holds daily bars only.
"""

TOOL = {
    "name": "set_strategy",
    "description": (
        "Return the COMPLETE strategy specification that should be active after this "
        "message, not a diff."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["signal", "stop", "weighting", "unsupported"],
        "properties": {
            "signal": {"type": "string", "enum": list(SIGNALS)},
            "fast_days": {"type": "integer",
                          "description": "ma_cross: the shorter average, in trading days."},
            "slow_days": {"type": "integer",
                          "description": "ma_cross: the longer average, in trading days."},
            "lookback_days": {
                "type": "integer",
                "description": "momentum and mean_reversion: the window, in trading days.",
            },
            "threshold": {
                "type": "number",
                "description": "momentum: the return to beat, as a FRACTION (0.05 = 5%).",
            },
            "z_entry": {
                "type": "number",
                "description": (
                    "mean_reversion: buy at this many standard deviations from the "
                    "average. Negative means below it, which is the usual case."
                ),
            },
            "z_exit": {"type": "number",
                       "description": "mean_reversion: sell once the z-score is back here."},
            "band_days": {"type": "integer",
                          "description": "bollinger_*: the average's length, trading days."},
            "band_k": {"type": "number",
                       "description": "bollinger_*: band width in standard deviations."},
            "rsi_days": {"type": "integer", "description": "rsi: window, trading days."},
            "rsi_entry": {"type": "number",
                          "description": "rsi: buy at or below this level (0-100)."},
            "rsi_exit": {"type": "number",
                         "description": "rsi: sell at or above this level (0-100)."},
            "breakout_days": {
                "type": "integer",
                "description": "channel_breakout: buy above the highest close of this "
                               "many previous days."},
            "exit_days": {
                "type": "integer",
                "description": "channel_breakout: sell below the lowest close of this "
                               "many previous days."},
            "macd_fast": {"type": "integer", "description": "macd: fast EMA, days."},
            "macd_slow": {"type": "integer", "description": "macd: slow EMA, days."},
            "macd_signal": {"type": "integer", "description": "macd: signal EMA, days."},
            "trend_filter_days": {
                "type": "integer",
                "description": "Only hold while the price is above its average of this "
                               "many days. 0 = no filter."},
            "stop": {"type": "string", "enum": list(STOPS)},
            "stop_pct": {
                "type": "number",
                "description": "How far below entry (stop_loss) or below the high since "
                               "entry (trailing_stop), as a FRACTION. 0.15 = 15%.",
            },
            "cooldown_days": {
                "type": "integer",
                "description": "Days barred after a stop fires. 0 means the signal has "
                               "to turn off and on again before re-entry.",
            },
            "weighting": {"type": "string", "enum": ["equal", "volatility"]},
            "max_weight": {
                "type": "number",
                "description": "Cap on one position, as a FRACTION. 1.0 means no cap.",
            },
            "unsupported": {
                "type": "array",
                "description": (
                    "Every part of the message that could not be expressed with the "
                    "fields above, in the person's own words. Must not be omitted when "
                    "something did not fit — especially anything intraday or "
                    "high-frequency, which this app cannot test."
                ),
                "items": {"type": "string"},
            },
        },
    },
}


@dataclass(slots=True)
class Translation:
    spec: StrategySpec
    raw: dict
    changed: list[str]


def payload_to_spec(payload: dict, previous: StrategySpec) -> StrategySpec:
    """Build a spec from the model's answer, keeping anything it did not address."""
    fields = asdict(previous)
    for key, value in payload.items():
        if key == "unsupported":
            continue
        if key in fields and value is not None:
            fields[key] = value
    fields["unsupported"] = tuple(payload.get("unsupported") or ())
    # A fast average at least as long as the slow one cannot be computed, and the model
    # occasionally swaps them. Refusing is better than silently reordering someone's rule.
    if fields.get("signal") == "ma_cross" and fields["fast_days"] >= fields["slow_days"]:
        fields["unsupported"] = fields["unsupported"] + (
            f"fast average ({fields['fast_days']}d) must be shorter than the slow one "
            f"({fields['slow_days']}d) — the previous lengths were kept",
        )
        fields["fast_days"], fields["slow_days"] = previous.fast_days, previous.slow_days
    if fields.get("signal") == "macd" and fields["macd_fast"] >= fields["macd_slow"]:
        fields["unsupported"] = fields["unsupported"] + (
            f"MACD fast EMA ({fields['macd_fast']}d) must be shorter than the slow one "
            f"({fields['macd_slow']}d) — the previous lengths were kept",
        )
        fields["macd_fast"], fields["macd_slow"] = previous.macd_fast, previous.macd_slow
    return StrategySpec(**fields)


def diff_summary(before: StrategySpec, after: StrategySpec) -> list[str]:
    a, b = asdict(before), asdict(after)
    return [f"{k}: {a[k]} → {b[k]}" for k in a
            if k != "unsupported" and a[k] != b[k]]


def translate(message: str, spec: StrategySpec, client=None) -> Translation:
    """Fold `message` into `spec`. Network call; the tests use recorded answers."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=require_secret("ANTHROPIC_API_KEY"))

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        tools=[TOOL],
        tool_choice={"type": "tool", "name": TOOL["name"]},
        messages=[{
            "role": "user",
            "content": (
                "Current specification:\n"
                f"{json.dumps(asdict(spec), indent=2, default=str)}\n\n"
                f"New message from the person:\n{message}"
            ),
        }],
    )
    payload = next((b.input for b in response.content if b.type == "tool_use"), None)
    if payload is None:
        raise RuntimeError(
            "The model did not return a strategy. The rule is unchanged; try rephrasing."
        )
    new_spec = payload_to_spec(payload, spec)
    return Translation(spec=new_spec, raw=payload, changed=diff_summary(spec, new_spec))
