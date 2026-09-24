"""The closed vocabulary a strategy may be written in.

Same principle as `PortfolioSpec`, for the same reason: the language model fills this
object and nothing else. It cannot invent an indicator, cannot name a stock, and cannot
emit code that gets executed. What it cannot express lands in `unsupported` and the
screen says so, rather than being quietly approximated by something that looks similar.

The alternative — letting a model write Python that the app runs — fails three ways at
once. It is a code-execution hole, the same sentence produces different code tomorrow so
nothing is reproducible, and there is no way to test a rule that does not exist until
runtime. A rule expressed as DATA can be checked against a series whose answer is known
by hand, which is the only reason any number here is worth looking at.

Every field below is one the engine actually implements. Adding an indicator means adding
an entry here and a function there, not rewriting anything.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Literal

# What decides whether a name is held on a given day.
SIGNALS = ("always_in", "ma_cross", "momentum", "mean_reversion")

# What forces an exit regardless of the signal.
STOPS = ("none", "stop_loss", "trailing_stop")


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """One rule set, complete enough to be run and to be argued with."""

    # --- the signal ------------------------------------------------------------------
    signal: Literal["always_in", "ma_cross", "momentum", "mean_reversion"] = "always_in"
    # ma_cross: hold while the fast average is above the slow one. The classic trend
    # rule, and the classic overfitting trap — the pair of lengths is exactly the kind of
    # knob that finds a pretty curve on any data if you turn it often enough.
    fast_days: int = 50
    slow_days: int = 200
    # momentum: hold while the return over `lookback_days` exceeds `threshold`.
    lookback_days: int = 126
    threshold: float = 0.0
    # mean_reversion: hold while the price sits `z_entry` standard deviations BELOW its
    # own `lookback_days` average, and let go once it is back above `z_exit`.
    z_entry: float = -1.0
    z_exit: float = 0.0

    # --- risk ------------------------------------------------------------------------
    stop: Literal["none", "stop_loss", "trailing_stop"] = "none"
    stop_pct: float = 0.20          # 20% below entry, or below the high since entry
    # Days out of the market after a stop fires, so a falling name cannot be re-bought
    # the next morning on the same signal that just cost money.
    cooldown_days: int = 0

    # --- sizing ----------------------------------------------------------------------
    weighting: Literal["equal", "volatility"] = "equal"
    max_weight: float = 1.0         # cap on any single position
    cash_when_flat: bool = True     # the alternative is to spread over whatever is left

    # --- bookkeeping -----------------------------------------------------------------
    # Anything the translator could not express. Never empty silently.
    unsupported: tuple[str, ...] = ()

    def describe(self) -> list[str]:
        """Plain language, shown after every message — the rule as the app understood it."""
        lines: list[str] = []
        if self.signal == "always_in":
            lines.append("hold every name in the basket, all the time (the baseline)")
        elif self.signal == "ma_cross":
            lines.append(
                f"hold while the {self.fast_days}-day average is above the "
                f"{self.slow_days}-day average")
        elif self.signal == "momentum":
            lines.append(
                f"hold while the return over the last {self.lookback_days} days is "
                f"above {self.threshold:.0%}")
        elif self.signal == "mean_reversion":
            lines.append(
                f"buy when the price is {abs(self.z_entry):.1f} standard deviations below "
                f"its {self.lookback_days}-day average, sell again at {self.z_exit:+.1f}")
        if self.stop == "stop_loss":
            lines.append(f"sell if it falls {self.stop_pct:.0%} below the entry price")
        elif self.stop == "trailing_stop":
            lines.append(f"sell if it falls {self.stop_pct:.0%} below its high since entry")
        if self.cooldown_days:
            lines.append(f"stay out for {self.cooldown_days} days after a stop fires")
        lines.append(
            f"weighting: {self.weighting}"
            + (f", no position above {self.max_weight:.0%}" if self.max_weight < 1 else ""))
        lines.append(
            "out of the market means cash" if self.cash_when_flat
            else "out of the market means the weight goes to the others")
        lines.append("signals are read at the close and filled at the next open")
        return lines

    def fingerprint(self) -> str:
        """Identity of this rule set, for the counter that records how often it was tried.

        A strategy lab is a better machine for finding a pretty curve than a screener is:
        more knobs, more attempts, same data. The count belongs next to the result.
        """
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def spec_to_json(spec: StrategySpec) -> str:
    return json.dumps(asdict(spec), sort_keys=True)


def spec_from_json(blob: str) -> StrategySpec:
    raw = json.loads(blob)
    unsupported = tuple(raw.pop("unsupported", ()))
    return StrategySpec(unsupported=unsupported, **raw)
