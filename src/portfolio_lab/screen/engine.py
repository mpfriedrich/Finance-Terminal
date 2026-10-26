"""Apply a PortfolioSpec to data. Deterministic, no model involved.

The design rule throughout: nothing disappears quietly. Every name that drops out is
counted and attributed to the reason that removed it. A screen that returns 3 names when
you asked for 10 must say why, or the user will read "these are the 3 best" when the truth
is "your data is missing".
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..data import industry
from ..data.pit import DURATION_METRICS, INSTANT_METRICS, MAX_FACT_AGE_DAYS
from .spec import FILTERABLE, Filter, PortfolioSpec
from .valuation import Assumptions, add_valuation


@dataclass(slots=True)
class ScreenResult:
    """Result of applying a spec. `unknown_tickers` is only used by hand-picked lists.

    Two frames, and the difference matters:

      * `ranked` — every name that passed every filter, best first. Nothing is cut. This
        is the evidence: what the screen actually found.
      * `picks`  — the ones chosen to be held, and therefore weighted.

    Separating them is what makes the ranking a proposal rather than a verdict. The screen
    orders the candidates; a person decides which of them become a portfolio.
    """

    picks: pd.DataFrame
    n_universe: int
    n_eligible: int
    n_passing: int
    exclusions: dict[str, int] = field(default_factory=dict)
    per_filter_survivors: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    unknown_tickers: list[str] = field(default_factory=list)
    hand_picked: bool = False
    ranked: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def found_nothing(self) -> bool:
        return self.n_passing == 0

    def select(self, tickers, weighting: str = "equal") -> pd.DataFrame:
        """The chosen subset of `ranked`, reweighted, still in ranking order.

        Used by the UI on every interaction, so the weights a person sees always belong
        to the names actually ticked — never to a set the screener chose earlier.
        """
        if self.ranked.empty:
            return self.ranked.copy()
        subset = self.ranked[
            self.ranked.ticker.astype(str).str.upper().isin(_keys(tickers))
        ].copy()
        return weighted(subset, weighting)


def _key(ticker) -> str:
    return str(ticker).strip().upper()


def _keys(tickers) -> set[str]:
    """Tickers as comparison keys: upper case, trimmed, empties gone.

    One function so the selection, the "no longer passing" check and `select()` can never
    disagree about whether " aapl " is AAPL. They did disagree once, and the symptom was a
    silently empty portfolio."""
    return {_key(t) for t in tickers if str(t).strip()}


def merge_selection(stored, visible, ticked) -> tuple[str, ...]:
    """Fold the ticks of a possibly filtered table back into the stored holding.

    The table on screen is not the portfolio. A sidebar filter — industry, size band —
    narrows it, and the names it hides are **absent, not unticked**. Writing the visible
    ticks straight into the spec therefore deletes a hand-curated holding down to whatever
    the current filter happens to show, with one click and no warning.

    So a row can only speak for itself:

      * a stored name that is on screen is kept when its box is ticked, dropped when it
        is not — that is the person unticking it, and it is the only way to remove one;
      * a stored name that is NOT on screen is kept untouched, because nothing was said
        about it;
      * a ticked name that was not stored is appended.

    Stored order is preserved so the holding does not reshuffle on every interaction.
    """
    vis, tick = _keys(visible), _keys(ticked)
    kept = [t for t in stored if _key(t) not in vis or _key(t) in tick]
    seen = _keys(kept)
    added = [t for t in ticked if _key(t) not in seen and str(t).strip()]
    return tuple(kept) + tuple(added)


def weighted(frame: pd.DataFrame, weighting: str) -> pd.DataFrame:
    """Attach portfolio weights. Equal weighting is the honest default: market-cap
    weighting inside a small-cap value screen quietly turns it into a bet on the two
    largest survivors."""
    out = frame.copy()
    total = out["market_cap"].sum() if "market_cap" in out else 0
    if weighting == "market_cap" and total and total > 0:
        out["weight"] = out["market_cap"] / total
    else:
        out["weight"] = 1.0 / max(len(out), 1)
    return out


# Used for after-tax returns when a filer reports no usable effective rate.
STATUTORY_TAX_RATE = 0.21


def derive_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Build valuation ratios from raw inputs.

    Market cap uses the UNADJUSTED price: an adjusted price times a historical share count
    is a number that never existed. Ratios on a negative denominator are undefined, not
    negative — a company with negative equity is not 'cheap', and letting a negative
    price-to-book through would rank the worst names first.
    """
    out = frame.copy()

    def col(name: str) -> pd.Series:
        """A numeric column, or an all-NaN column of the right length if it is absent.

        Without this, a missing column collapses to a scalar NaN and every later
        Series operation on it fails — which is how an absent metric would turn into a
        crash instead of an honest gap.
        """
        if name not in out:
            return pd.Series(np.nan, index=out.index, dtype="float64")
        return pd.to_numeric(out[name], errors="coerce")

    # `col`, not `out.get`: a missing column comes back from .get() as None, and
    # `Series * None` raises. The helper right above exists precisely to avoid that, and
    # this one line was walking past it — which turned a screening date with no visible
    # filings into a crash rather than an empty screen.
    out["market_cap"] = col("price") * col("shares_outstanding")

    def ratio(numer: pd.Series, denom_col: str) -> pd.Series:
        denom = col(denom_col)
        return pd.Series(
            np.where(denom > 0, numer / denom, np.nan), index=out.index, dtype="float64"
        )

    out["price_to_book"] = ratio(out["market_cap"], "book_value")
    out["price_to_earnings"] = ratio(out["market_cap"], "net_income_ttm")
    out["price_to_sales"] = ratio(out["market_cap"], "revenue_ttm")
    out["price_to_cash_flow"] = ratio(out["market_cap"], "operating_cash_flow_ttm")
    out["price_to_cash"] = ratio(out["market_cap"], "cash")
    # price_to_free_cash_flow lives in add_valuation: free cash flow does not exist
    # until capex has been subtracted there, and a ratio against a column that is
    # not there yet would silently be NaN for every company.

    # Leverage. Two measures on purpose, because "debt" means different things:
    #   debt_to_equity       interest-bearing borrowings only — the conventional one
    #   liabilities_to_equity everything owed, including payables and leases
    # A filer that tags neither debt side gets NaN, not a flattering zero.
    lt = col("long_term_debt")
    st_ = col("current_debt")
    total_debt = lt.fillna(0) + st_.fillna(0)
    total_debt = total_debt.where(lt.notna() | st_.notna())
    out["total_debt"] = total_debt
    equity = col("book_value")
    out["debt_to_equity"] = np.where(equity > 0, total_debt / equity, np.nan)
    out["liabilities_to_equity"] = ratio(col("total_liabilities"), "book_value")
    out["current_ratio"] = ratio(col("current_assets"), "current_liabilities")

    # Profitability. Margins are signed: a loss-making company has a negative margin,
    # which is a real and useful fact, unlike a negative price-to-earnings.
    revenue = col("revenue_ttm")
    net_income = col("net_income_ttm")
    ocf = col("operating_cash_flow_ttm")
    out["net_margin"] = np.where(revenue > 0, net_income / revenue, np.nan)
    out["operating_margin_proxy"] = np.where(revenue > 0, ocf / revenue, np.nan)
    out["return_on_equity"] = np.where(equity > 0, net_income / equity, np.nan)

    # --- enterprise value and what it is compared with (added 2026-09-24) -------------
    # EV = market cap + debt - cash. Unknown debt stays unknown rather than zero: a
    # filer that tags no borrowings may have none, or may tag them under a name we do not
    # read, and a flattering EV is worse than a missing one.
    cash = col("cash")
    ebit = col("operating_income_ttm")
    ebitda = ebit + col("depreciation_amortization_ttm")
    out["ebitda_ttm"] = ebitda
    out["net_debt"] = total_debt - cash
    ev = out["market_cap"] + total_debt - cash
    out["enterprise_value"] = ev
    positive_ev = ev.where(ev > 0)       # a negative EV makes every EV multiple meaningless

    def ev_ratio(denom: pd.Series) -> np.ndarray:
        return np.where(denom > 0, positive_ev / denom, np.nan)

    out["ev_to_ebitda"] = ev_ratio(ebitda)
    out["ev_to_ebit"] = ev_ratio(ebit)
    out["ev_to_sales"] = ev_ratio(revenue)
    # Signed on purpose: net cash gives a negative figure, which is a real and good fact.
    out["net_debt_to_ebitda"] = np.where(ebitda > 0, out["net_debt"] / ebitda, np.nan)
    out["interest_coverage"] = np.where(col("interest_expense_ttm") > 0,
                                        ebit / col("interest_expense_ttm"), np.nan)

    out["gross_margin"] = np.where(revenue > 0, col("gross_profit_ttm") / revenue, np.nan)
    out["operating_margin"] = np.where(revenue > 0, ebit / revenue, np.nan)
    out["ebitda_margin"] = np.where(revenue > 0, ebitda / revenue, np.nan)
    out["return_on_assets"] = ratio(net_income, "total_assets")

    # ROIC = EBIT after tax / (equity + debt - cash). The effective rate where the filer
    # reports one between 0 and 50%; otherwise the US federal rate, 21% — a stated
    # assumption, recorded per row in `tax_rate_source`.
    pretax, tax = col("pretax_income_ttm"), col("income_tax_ttm")
    effective = (tax / pretax).where((pretax > 0) & (tax >= 0))
    effective = effective.where(effective <= 0.5)
    out["tax_rate"] = effective.fillna(STATUTORY_TAX_RATE)
    out["tax_rate_source"] = np.where(effective.notna(), "effective", "statutory 21%")
    invested = equity + total_debt - cash
    out["return_on_invested_capital"] = np.where(
        invested > 0, ebit * (1 - out["tax_rate"]) / invested, np.nan)

    prior = col("revenue_ttm_prior")
    out["revenue_growth"] = np.where(prior > 0, revenue / prior - 1.0, np.nan)
    return out


def _apply_filter(frame: pd.DataFrame, f: Filter) -> pd.Series:
    col = frame[f.metric]
    # Substring matching for text fields. Industry names come from the SEC as phrases
    # like "Biological Products, (No Diagnostic Substances)" — nobody types that, and
    # exact matching would make sector filtering useless in practice.
    if f.op in ("contains", "not_contains"):
        hit = col.astype(str).str.contains(str(f.value), case=False, na=False)
        return ~hit if f.op == "not_contains" else hit
    if f.op == "in":
        values = f.value if isinstance(f.value, list) else [f.value]
        return col.isin(values)
    if f.op == "==":
        return col == f.value
    if f.op == "!=":
        return col != f.value
    value = float(f.value)  # type: ignore[arg-type]
    return {"<=": col <= value, "<": col < value,
            ">=": col >= value, ">": col > value}[f.op]


def run_screen(
    frame: pd.DataFrame,
    spec: PortfolioSpec,
    assumptions: Assumptions | None = None,
) -> ScreenResult:
    """frame: one row per security with raw inputs already joined.

    Required columns: ticker, cik, name, price, shares_outstanding, data_age_days,
    median_dollar_volume_60d. Optional: book_value, net_income_ttm, revenue_ttm,
    operating_cash_flow_ttm, sector, exchange.
    """
    n_universe = len(frame)
    data = add_valuation(derive_metrics(frame), assumptions or Assumptions())

    # A hand-picked list is the portfolio. Filters and eligibility floors do not apply:
    # they exist to stop a screen wandering into untradeable names, and someone naming a
    # company on purpose has already made that judgement. The metrics are still shown so
    # the holding can be examined, and anything not found is named rather than dropped.
    if spec.tickers:
        wanted = [t.strip().upper() for t in spec.tickers if t.strip()]
        found = data[data.ticker.astype(str).str.upper().isin(wanted)].copy()
        present = set(found.ticker.astype(str).str.upper())
        unknown = [t for t in wanted if t not in present]
        order = {t: i for i, t in enumerate(wanted)}
        found = found.sort_values(
            by="ticker", key=lambda col: col.astype(str).str.upper().map(order)
        )
        found = weighted(found, spec.weighting)
        notes = []
        if unknown:
            notes.append(
                f"not found in the universe: {', '.join(unknown)}. They may be delisted, "
                "a fund, a second share class, or simply mistyped."
            )
        return ScreenResult(
            picks=found, ranked=found, n_universe=n_universe, n_eligible=len(found),
            n_passing=len(found), notes=notes, unknown_tickers=unknown,
            hand_picked=True,
        )

    exclusions: dict[str, int] = {}

    def drop(mask: pd.Series, label: str) -> pd.DataFrame:
        nonlocal data
        removed = int((~mask).sum())
        if removed:
            exclusions[label] = removed
        data = data[mask]
        return data

    max_age = spec.max_filing_age_months * 31
    drop(data["price"].notna(), "no current price")
    # Foreign filers (added 2026-09-24): an amount in a currency with no exchange rate on
    # the screen date cannot be compared with a dollar price, and a US listing whose
    # ADR ratio is unknown has no honest market cap. Each is its own reason, so the
    # screen says why a name is missing instead of pricing it wrong.
    if "fx_missing" in data.columns:
        drop(~data["fx_missing"].fillna(False).astype(bool),
             "no exchange rate for the reporting currency")
    if "listing_ratio_unknown" in data.columns:
        drop(~data["listing_ratio_unknown"].fillna(False).astype(bool),
             "foreign listing: ADR ratio not measured")
    # The column can be absent entirely on a date before anything was filed, which is a
    # universe where nothing is eligible — not an error, and not a crash.
    if "shares_outstanding" not in data.columns:
        data = data.assign(shares_outstanding=np.nan)
    drop(data["shares_outstanding"].notna(), "no share count")
    drop(data["data_age_days"].fillna(10**6) <= max_age,
         f"filing older than {spec.max_filing_age_months} months")
    drop(~share_count_is_implausible(data), "share count fails the volume sanity check")
    # A trailing-twelve-month figure too old to describe the company (audit 24.10.2026
    # F6, R14) leaves its ratios NaN. Such a name would fail the filter anyway; the
    # point of this line is that the reason is counted, not left as "did not pass".
    stale = needs_stale_ttm(data, spec)
    if stale is not None:
        drop(~stale, f"earnings or other twelve-month figure older than "
                     f"{MAX_FACT_AGE_DAYS} days")
    drop(data["price"] >= spec.min_price, f"price below {spec.min_price:g}")
    drop(data["median_dollar_volume_60d"].fillna(0) >= spec.min_dollar_volume,
         "below minimum turnover")
    # Counted separately from turnover on purpose: "too small to trade" and "barely
    # trades at all" are different defects, and a screen that merges them cannot tell you
    # which one it just protected you from (R15).
    if "trading_days_60" in data.columns and spec.min_trading_days_60:
        drop(data["trading_days_60"].fillna(0) >= spec.min_trading_days_60,
             f"traded on fewer than {spec.min_trading_days_60} of the last 60 sessions")

    n_eligible = len(data)

    # Per-filter survivor counts computed on the eligible set INDEPENDENTLY, so a zero-hit
    # screen can tell the user which single rule is the binding one (PLAN.md edge cases).
    eligible = data.copy()
    per_filter: dict[str, int] = {}
    passing = eligible
    notes: list[str] = []
    for f in spec.filters:
        if f.metric not in FILTERABLE or f.metric not in eligible.columns:
            notes.append(f"filter on '{f.metric}' ignored — not available in the data")
            continue
        # A text fragment that matches no value in the data is an invention, and the
        # dangerous case is `not_contains`: it quietly keeps everything, so the person
        # believes they excluded something they did not. Refuse it out loud.
        if f.op in ("contains", "not_contains"):
            present = eligible[f.metric].dropna().astype(str)
            if not present.str.contains(str(f.value), case=False, na=False).any():
                sample = sorted(present.unique())[:6]
                notes.append(
                    f"'{f.value}' matches no {f.metric} in the data, so that filter was "
                    f"not applied. Real values look like: {', '.join(sample)}"
                )
                continue
        # A name whose industry is unknown fails every industry filter — correctly, but
        # invisibly. Say how many, because "no tech companies passed" and "412 companies
        # have no industry on file" are different discoveries (PLAN.md R5).
        if f.metric in ("industry_group", "sector"):
            blank = eligible[f.metric].isna() | eligible[f.metric].astype(str).isin(
                ["", "nan", "None", industry.UNCLASSIFIED]
            )
            if int(blank.sum()):
                notes.append(
                    f"{int(blank.sum())} eligible companies carry no industry on file "
                    f"and therefore fail every {f.metric.replace('_', ' ')} filter."
                )
        per_filter[f.describe()] = int(_apply_filter(eligible, f).fillna(False).sum())
        passing = passing[_apply_filter(passing, f).fillna(False)]

    # A name missing the metric a filter needs fails that filter. It is not a pass.
    n_passing = len(passing)

    rank_by, ascending = spec.effective_rank()
    if rank_by in passing.columns:
        passing = passing.sort_values(rank_by, ascending=ascending, na_position="last")
    else:
        notes.append(f"ranking metric '{rank_by}' unavailable; order is arbitrary")

    # Every passing name, in ranking order. NOT truncated: the ranking is the screener's
    # opinion about order, and cutting the list at an arbitrary count would present that
    # opinion as a decision. The 11th-cheapest company is not disqualified, it is 11th.
    ranked = passing.copy()
    ranked.insert(0, "rank", range(1, len(ranked) + 1))

    # `selected` empty means the screen has not been curated yet, so the top `size` are
    # offered as a starting point. That is a proposal the UI lets you change, not a cap.
    chosen = _keys(spec.selected) or _keys(ranked.ticker.astype(str).head(spec.size))
    picks = weighted(
        ranked[ranked.ticker.astype(str).str.upper().isin(chosen)], spec.weighting
    )

    if n_passing == 0:
        notes.append("no security passed every filter — see the per-filter counts below")
    elif not spec.selected and n_passing < spec.size:
        notes.append(
            f"only {n_passing} securities passed, fewer than the {spec.size} proposed"
        )
    missing = sorted(_keys(spec.selected)
                     - set(ranked.ticker.astype(str).str.upper()))
    if missing:
        notes.append(
            f"no longer passing, so dropped from the holding: {', '.join(missing)}. "
            "The filters changed under a selection made earlier."
        )
    if spec.unsupported:
        notes.append("not applied (no such field): " + "; ".join(spec.unsupported))

    return ScreenResult(
        picks=picks,
        ranked=ranked,
        n_universe=n_universe,
        n_eligible=n_eligible,
        n_passing=n_passing,
        exclusions=exclusions,
        per_filter_survivors=per_filter,
        notes=notes,
    )


@functools.cache
def ttm_inputs() -> dict[str, frozenset[str]]:
    """metric -> the `*_ttm` fields its value depends on.

    Measured, not written down: one plausible company is run through derive_metrics and
    add_valuation once whole and once per field with that field blanked, and every
    column that changes depends on the field. A hand-kept table would drift the first
    time someone adds a ratio.
    """
    ttm = [f"{m}_ttm" for m in DURATION_METRICS]
    base = {"price": 20.0, "shares_outstanding": 1e8, "revenue_ttm_prior": 9e8,
            "book_value": 5e8, "total_assets": 2e9, "total_liabilities": 1.5e9,
            "long_term_debt": 4e8, "current_debt": 1e8, "cash": 1e8,
            "current_assets": 6e8, "current_liabilities": 3e8,
            "revenue_ttm": 1e9, "net_income_ttm": 1e8, "operating_cash_flow_ttm": 1.5e8,
            "capex_ttm": 5e7, "operating_income_ttm": 1.4e8,
            "depreciation_amortization_ttm": 3e7, "interest_expense_ttm": 1e7,
            "income_tax_ttm": 2.5e7, "pretax_income_ttm": 1.2e8, "gross_profit_ttm": 4e8}
    if not (set(INSTANT_METRICS) | set(ttm)) <= set(base):
        raise RuntimeError("ttm_inputs: the probe company lacks a field pit.py defines")

    def run(row: dict) -> pd.Series:
        return add_valuation(derive_metrics(pd.DataFrame([row])), Assumptions()).iloc[0]

    whole = run(base)
    deps: dict[str, set[str]] = {}
    for blank in ttm:
        probe = run({**base, blank: np.nan})
        for column in whole.index:
            a, b = whole[column], probe.get(column)
            if (pd.isna(a) != pd.isna(b)) or (not pd.isna(a) and a != b):
                deps.setdefault(column, set()).add(blank)
    return {k: frozenset(v) for k, v in deps.items()}


def needs_stale_ttm(data: pd.DataFrame, spec: PortfolioSpec) -> pd.Series | None:
    """Rows whose stale TTM figure (`stale_ttm`, from the frame builders) feeds a
    metric this spec filters or ranks on; None when nothing can be affected."""
    if "stale_ttm" not in data.columns or data["stale_ttm"].isna().all():
        return None
    deps = ttm_inputs()
    needed: set[str] = set()
    for metric in [f.metric for f in spec.filters] + [spec.effective_rank()[0]]:
        needed |= deps.get(metric, frozenset())
    if not needed:
        return None
    return data["stale_ttm"].map(
        lambda s: isinstance(s, str) and bool(needed & set(s.split(","))))


# A company cannot trade a large fraction of its entire share count every single day.
# When it appears to, the share count is wrong — in practice because the filer tagged it
# in thousands. Detected names are EXCLUDED and COUNTED, never silently rescaled: a
# guessed factor of 1000 would be exactly the kind of invention this tool refuses.
MAX_PLAUSIBLE_DAILY_TURNOVER = 0.5


def share_count_is_implausible(frame: pd.DataFrame) -> pd.Series:
    vol = frame.get("median_share_volume_60d")
    shares = frame.get("shares_outstanding")
    if vol is None or shares is None:
        return pd.Series(False, index=frame.index)
    return (vol.notna() & shares.notna() & (shares > 0)
            & (vol > shares * MAX_PLAUSIBLE_DAILY_TURNOVER))
