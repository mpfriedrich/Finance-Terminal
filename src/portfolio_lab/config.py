"""Settings and secrets.

Secrets are read from .env.local and never printed, logged or surfaced in an error
message. The file is gitignored. You write it yourself; this code only reads it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
MARKET_DB = DATA_DIR / "market.duckdb"
# The paid source's store, built by scripts/build_paid.py. Separate from the free one so
# switching back is a setting, not a rebuild (Phase 10).
PAID_DB = DATA_DIR / "paid.duckdb"
# World stage 3: the US store plus Compustat Global from WRDS, built by
# scripts/build_wrds.py. Its own file for the same reason.
WORLD_DB = DATA_DIR / "world.duckdb"
USER_DB = DATA_DIR / "user.duckdb"
CACHE_DIR = DATA_DIR / "cache"
# What alerts have fired, kept by the checker (alerts.py) outside the portfolio store.
ALERTS_STATE = DATA_DIR / "alerts_state.json"


def load_env(path: Path | None = None) -> None:
    """Minimal .env.local reader. No dependency, no surprises."""
    p = path or (PROJECT_ROOT / ".env.local")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def secret(name: str) -> str | None:
    """Fetch a secret. Callers must never include the return value in output."""
    load_env()
    return os.environ.get(name)


def _setting(name: str, default: str) -> str:
    """A plain (non-secret) setting from the environment or .env.local."""
    load_env()
    return (os.environ.get(name) or default).strip().lower()


def require_secret(name: str) -> str:
    value = secret(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to {PROJECT_ROOT / '.env.local'} "
            f"as a line '{name}=...' and restart. The value is never shown here."
        )
    return value


# SEC requires a descriptive User-Agent with a contact address on every request.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "portfolio-lab personal research (5qvmsc96v4@privaterelay.appleid.com)",
)


@dataclass(frozen=True, slots=True)
class CostModel:
    """Trading costs. Spread is tiered by market cap because the whole point of this
    tool's typical screen is small, cheap companies — where a flat large-cap spread
    would understate real cost by an order of magnitude (PLAN.md R8, A10).

    Tiers: (upper bound of market cap in USD, one-way spread cost as a fraction).
    Values are order-of-magnitude estimates, NOT verified. Override them with your
    broker's actual terms.
    """

    commission: float = 0.0005
    spread_tiers: tuple[tuple[float, float], ...] = (
        (300e6, 0.0100),
        (2e9, 0.0040),
        (10e9, 0.0015),
        (float("inf"), 0.0005),
    )

    def spread_for(self, market_cap: float | None) -> float:
        # Unknown size pays the most expensive tier (R8). NaN fails every `<` below, so
        # without this line a missing market cap fell through to the CHEAPEST tier —
        # which is what Store.start_tracking passed on (audit 02.10.2026).
        if market_cap is None or market_cap != market_cap:
            return self.spread_tiers[0][1]
        for upper, cost in self.spread_tiers:
            if market_cap < upper:
                return cost
        return self.spread_tiers[-1][1]

    def round_trip_cost(self, market_cap: float) -> float:
        return self.commission + self.spread_for(market_cap)


@dataclass(frozen=True, slots=True)
class Settings:
    cost: CostModel = field(default_factory=CostModel)
    # Eligibility floors — keep untradeable micro-caps out of the screen (R15).
    min_price: float = 5.0
    min_median_dollar_volume_60d: float = 1_000_000.0
    # A filing older than this cannot make a company eligible (R14). Stops dead filers
    # from scoring "cheapest" forever on a stale book value against a collapsed price.
    max_filing_age_months: int = 6
    # Assumed return on a delisting, applied instead of the last observed price (R18).
    # Literature convention, unverified.
    delisting_return: float = -0.30
    # Trailing years withheld from the default backtest until explicitly unlocked (R21).
    holdout_years: int = 2
    rebalance: str = "quarterly"
    risk_free_fallback: float = 0.02
    # "free" (SEC + yfinance), "paid" (Sharadar) or "world" (free US + Compustat Global
    # via WRDS). Set PORTFOLIO_LAB_SOURCE in
    # .env.local; see data/sources.py for what happens when the paid one is not ready.
    data_source: str = field(default_factory=lambda: _setting("PORTFOLIO_LAB_SOURCE", "free"))
