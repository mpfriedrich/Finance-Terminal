"""Cost of capital and a DCF on it — the corporate-finance version of intrinsic value.

screen/valuation.py discounts free cash flow to equity at a rate the person picks. This
module builds the rate instead, the way a banker's model does, and values the whole firm:

  cost of equity   CAPM: risk-free + beta × equity risk premium
  cost of debt     interest expense / debt, held inside plausible bounds
  weights          market value of equity, book value of debt (the usual proxy — the
                   market value of a company's loans is rarely observable)
  WACC             E/V × Ke + D/V × Kd × (1 − tax)
  DCF              free cash flow to the firm, grown and discounted at the WACC, less
                   net debt, per listed share

Every input carries where it came from, because a DCF is only as honest as the list of
what was assumed. The sensitivity grid is the other half of that honesty: the two inputs
nobody can know — the discount rate and growth forever — moved against each other.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Long-run excess return of equities over government bonds. Estimates run from about 4
# to 6%; 5% is the usual middle, and it is a setting because it is an opinion.
DEFAULT_ERP = 0.05
# Where a filer's interest expense cannot give a believable rate: risk-free plus this.
DEFAULT_CREDIT_SPREAD = 0.02
# A computed cost of debt outside [risk-free, risk-free + 10%] is almost always a
# mismatch between the interest line and the debt lines (debt repaid mid-year, interest
# on leases, a mis-tagged figure), not a real rate.
MAX_CREDIT_SPREAD = 0.10


@dataclass(frozen=True, slots=True)
class Inputs:
    risk_free: float
    beta: float
    equity_risk_premium: float = DEFAULT_ERP
    adjust_beta: bool = True          # Blume: 0.67 × raw + 0.33, pulls towards 1
    growth: float = 0.03              # FCFF growth over the explicit years
    years: int = 5
    terminal_growth: float = 0.02


def adjusted_beta(raw: float) -> float:
    """Blume's adjustment. Measured betas drift towards 1 over time; this is the
    standard correction data vendors apply."""
    return 0.67 * raw + 0.33


def cost_of_debt(interest_expense: float, debt: float, risk_free: float) -> tuple[float, str]:
    """Pre-tax cost of debt and how it was found."""
    fallback = risk_free + DEFAULT_CREDIT_SPREAD
    if not (interest_expense and debt) or pd.isna(interest_expense) or pd.isna(debt) \
            or debt <= 0 or interest_expense <= 0:
        return fallback, f"risk-free + {DEFAULT_CREDIT_SPREAD:.0%} (no usable interest line)"
    rate = interest_expense / debt
    if rate < risk_free or rate > risk_free + MAX_CREDIT_SPREAD:
        return fallback, (f"risk-free + {DEFAULT_CREDIT_SPREAD:.0%} (interest/debt gave "
                          f"{rate:.1%}, outside plausible bounds)")
    return rate, "interest expense / debt"


def build(row: pd.Series, inputs: Inputs) -> dict:
    """The WACC build-up for one company from its screening row (derive_metrics)."""
    beta = adjusted_beta(inputs.beta) if inputs.adjust_beta else inputs.beta
    ke = inputs.risk_free + beta * inputs.equity_risk_premium
    equity = float(row.get("market_cap") or np.nan)
    debt = row.get("total_debt")
    debt = float(debt) if debt is not None and not pd.isna(debt) else 0.0
    kd, kd_source = cost_of_debt(row.get("interest_expense_ttm"), debt, inputs.risk_free)
    tax = row.get("tax_rate")
    tax = float(tax) if tax is not None and not pd.isna(tax) else 0.21
    value = equity + debt
    we, wd = (equity / value, debt / value) if value > 0 else (np.nan, np.nan)
    wacc = we * ke + wd * kd * (1 - tax)
    return {
        "risk_free": inputs.risk_free, "beta_raw": inputs.beta, "beta": beta,
        "equity_risk_premium": inputs.equity_risk_premium, "cost_of_equity": ke,
        "cost_of_debt_pre_tax": kd, "cost_of_debt_source": kd_source,
        "tax_rate": tax, "tax_rate_source": row.get("tax_rate_source", "—"),
        "equity_value": equity, "debt_value": debt,
        "weight_equity": we, "weight_debt": wd, "wacc": wacc,
    }


def fcff(row: pd.Series) -> float | None:
    """Free cash flow to the firm: operating cash flow − capex + interest × (1 − tax).

    Built from operating cash flow rather than EBIT because working-capital changes are
    already in it; interest is added back after tax because the WACC prices the lenders'
    claim, so the flow has to include what they are paid.
    """
    ocf, capex = row.get("operating_cash_flow_ttm"), row.get("capex_ttm")
    if ocf is None or pd.isna(ocf):
        return None
    capex = abs(capex) if capex is not None and not pd.isna(capex) else 0.0
    interest = row.get("interest_expense_ttm")
    interest = interest if interest is not None and not pd.isna(interest) else 0.0
    tax = row.get("tax_rate")
    tax = tax if tax is not None and not pd.isna(tax) else 0.21
    return float(ocf - capex + interest * (1 - tax))


def dcf(row: pd.Series, wacc: float, inputs: Inputs) -> dict:
    """Enterprise value from FCFF, then equity and a price per listed share."""
    flow = fcff(row)
    if flow is None or flow <= 0:
        return {"error": "free cash flow to the firm is not positive — a DCF on it says "
                         "nothing"}
    if wacc <= inputs.terminal_growth:
        return {"error": "growth forever must stay below the WACC"}
    pv, f = 0.0, flow
    for t in range(1, inputs.years + 1):
        f *= 1 + inputs.growth
        pv += f / (1 + wacc) ** t
    terminal = f * (1 + inputs.terminal_growth) / (wacc - inputs.terminal_growth)
    pv_terminal = terminal / (1 + wacc) ** inputs.years
    ev = pv + pv_terminal
    net_debt = row.get("net_debt")
    if net_debt is None or pd.isna(net_debt):
        return {"error": "net debt unknown — no bridge from enterprise to equity value"}
    equity = ev - float(net_debt)
    shares = row.get("shares_outstanding")
    price = row.get("price")
    per_share = equity / shares if shares else np.nan
    return {
        "fcff": flow, "pv_explicit": pv, "pv_terminal": pv_terminal,
        "terminal_share": pv_terminal / ev if ev else np.nan,
        "enterprise_value": ev, "equity_value": equity, "per_share": per_share,
        "vs_price": per_share / price - 1.0 if price else np.nan,
    }


def sensitivity(row: pd.Series, wacc: float, inputs: Inputs,
                wacc_steps=(-0.01, -0.005, 0.0, 0.005, 0.01),
                growth_steps=(-0.005, 0.0, 0.005)) -> pd.DataFrame:
    """Price per share for WACC ± 1 point against terminal growth ± 0.5 point."""
    grid = {}
    for g in growth_steps:
        col = {}
        for w in wacc_steps:
            shifted = Inputs(**{**_as_dict(inputs),
                                "terminal_growth": inputs.terminal_growth + g})
            out = dcf(row, wacc + w, shifted)
            col[f"{wacc + w:.1%}"] = out.get("per_share", np.nan)
        grid[f"g {inputs.terminal_growth + g:.1%}"] = col
    return pd.DataFrame(grid)


def _as_dict(inputs: Inputs) -> dict:
    return {k: getattr(inputs, k) for k in Inputs.__slots__}
