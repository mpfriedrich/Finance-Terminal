"""What the options market expects.

A share price tells you what people paid. An option price tells you what they expect —
specifically, how far they think the price could move before the contract expires. Back
out that expectation and you have implied volatility, and from it the market's own
estimate of a price range.

Everything here is derived from live quotes, so it has no place in the screener: a screen
has to value every company on one common date, and option data is only meaningful at the
moment it is quoted.

**The data quality problem comes first.** A chain contains many contracts nobody trades.
Deep in-the-money calls routinely quote a bid and ask of zero and an implied volatility
of 0.00001 — not a calm market, a dead contract whose number is an artefact of solving an
equation with no real price in it. Left in, they drag a volatility surface into nonsense.
Everything below is filtered before it is used, and the discards are counted.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

# ------------------------------------------------------------------ Black-Scholes
# Implied volatility is computed here rather than taken from the data provider. Yahoo's
# own impliedVolatility field was measured empty on 2026-09-21 — a median of 0.000 across
# near-the-money contracts, including one with 13,339 open contracts and a $22.60 last
# price. That is not a calm market, it is an unpopulated column, and a surface built on it
# would be a picture of nothing.
#
# The prices themselves are sound, so the volatility is backed out of them. Black-Scholes
# cannot be rearranged for sigma, but the price rises monotonically with it, so bisection
# converges reliably and needs no extra dependency. Its assumptions (lognormal returns,
# constant volatility, European exercise) are wrong in the usual ways; what comes out is
# "the volatility that would justify this price under those assumptions", which is the
# standard meaning of the number and worth remembering when reading it.


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes(S: float, K: float, T: float, r: float, sigma: float,
                  q: float = 0.0, kind: str = "call") -> float:
    """Price of a European option. T in years, r and q as annual decimals."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, (S - K) if kind == "call" else (K - S))
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "call":
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def implied_volatility(price: float, S: float, K: float, T: float, r: float = 0.04,
                       q: float = 0.0, kind: str = "call",
                       lo: float = 1e-4, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """Back the volatility out of an option price by bisection.

    Returns None when the price cannot be produced by any volatility — which happens for
    a price below intrinsic value (a stale quote on a moved market) or above the
    theoretical ceiling. Refusing is the right answer there; a forced number would be an
    artefact.
    """
    if not (price and S and K) or T <= 0 or price <= 0:
        return None
    intrinsic = max(0.0, (S - K) if kind == "call" else (K - S))
    if price < intrinsic - 1e-6:
        return None
    if black_scholes(S, K, T, r, hi, q, kind) < price:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        diff = black_scholes(S, K, T, r, mid, q, kind) - price
        if abs(diff) < tol:
            return mid
        if diff > 0:
            hi = mid
        else:
            lo = mid
    out = 0.5 * (lo + hi)
    return out if lo > 1e-4 * 1.5 else None


# Quality gates. A contract has to clear all of them to say anything about expectations.
MIN_IV, MAX_IV = 0.02, 5.0
MIN_OPEN_INTEREST = 1
MAX_SPREAD_FRACTION = 0.75   # bid/ask wider than this means nobody agrees on a price
MONEYNESS_BAND = (0.70, 1.30)  # strikes far outside this are thin and add only noise


def expiries(symbol: str, strict: bool = False) -> list[str]:
    """Listed expiry dates. `strict=True` raises when Yahoo cannot be reached, so a
    cache in front of it (appcache.py) never stores "unreachable" as "no options"."""
    try:
        import yfinance as yf

        return list(yf.Ticker(symbol).options or [])
    except Exception:
        if strict:
            raise
        return []


def fetch_chain(symbol: str, expiry: str):
    """The raw chain from Yahoo: an object with `.calls` and `.puts`. Raises on failure."""
    import yfinance as yf

    return yf.Ticker(symbol).option_chain(expiry)


# Evidence required when the market is shut and there are no live quotes to lean on:
# open interest OR last session's volume. On 2026-09-21 open interest was filled before
# the open; on 2026-10-02 at 10:25 UTC Yahoo reported 0 open interest for every AAPL,
# MSFT and SPY contract while volume was filled, and the OPTIONS section went empty.
MIN_OPEN_INTEREST_CLOSED = 10
MIN_VOLUME_CLOSED = 10
# A last price older than this (behind the newest trade in the chain) belongs to another
# spot price. AAPL's 290 call on 2026-10-02 still showed a trade from 24.09.
MAX_STALE_DAYS_CLOSED = 4
# Live mode needs most of the chain quoted. A handful of leftover bids before the open
# (SPY, 7 of 219, 2026-10-02) would otherwise switch the whole expiry to live mode and
# throw away every contract with only a last price.
MIN_QUOTED_SHARE = 0.5


def _clean(frame: pd.DataFrame, spot: float, kind: str, t_years: float,
           risk_free: float = 0.04, dividend_yield: float = 0.0) -> pd.DataFrame:
    """Filter a chain down to contracts that mean something.

    Two modes, because the data changes shape with the clock. While the market is open,
    a live bid and ask is the strongest evidence that a price is real. Outside trading
    hours Yahoo returns a bid and ask of zero for EVERY contract, so that gate would
    reject the whole chain — verified on 2026-09-21 before the US open: 0 of 33 contracts
    had a two-sided quote, while 33 of 33 had open interest.

    So when no live quotes exist anywhere in the chain, fall back to the last traded
    price plus a higher open-interest bar. Weaker evidence, and the caller is told which
    mode produced the numbers rather than being left to assume.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()
    df = frame.copy()
    for col in ("bid", "ask", "impliedVolatility", "strike", "openInterest", "lastPrice",
                "volume"):
        if col not in df:
            df[col] = np.nan
    df["moneyness"] = df.strike / spot if spot else np.nan
    df["kind"] = kind

    quoted = ((df.bid > 0) & (df.ask > 0)).mean() >= MIN_QUOTED_SHARE
    if quoted:
        df["mid"] = (df.bid + df.ask) / 2.0
        df["spread"] = np.where(df.mid > 0, (df.ask - df.bid) / df.mid, np.nan)
        keep = (
            (df.bid > 0) & (df.ask > 0)
            & (df.spread <= MAX_SPREAD_FRACTION)
            & (df.openInterest.fillna(0) >= MIN_OPEN_INTEREST)
        )
        mode = "live quotes"
    else:
        df["mid"] = df.lastPrice
        df["spread"] = np.nan
        traded = ((df.openInterest.fillna(0) >= MIN_OPEN_INTEREST_CLOSED)
                  | (df.volume.fillna(0) >= MIN_VOLUME_CLOSED))
        keep = (df.lastPrice > 0) & traded
        when = pd.to_datetime(df.get("lastTradeDate"), errors="coerce", utc=True)
        if isinstance(when, pd.Series) and when.notna().any():
            fresh = when >= when.max() - pd.Timedelta(days=MAX_STALE_DAYS_CLOSED)
            keep = keep & fresh.fillna(False)
        mode = "last traded prices (market closed)"

    keep = keep & df.moneyness.between(*MONEYNESS_BAND)
    out = df[keep].copy()
    # Recompute volatility from the price. See the note at the top of this module.
    if not out.empty:
        out["iv"] = [
            implied_volatility(price=row.mid, S=spot, K=row.strike, T=max(t_years, 1 / 365),
                               r=risk_free, q=dividend_yield, kind=kind)
            for row in out.itertuples()
        ]
        out["provider_iv"] = out["impliedVolatility"]
        before = len(out)
        out = out[out.iv.notna() & out.iv.between(MIN_IV, MAX_IV)]
        out.attrs["unsolvable"] = before - len(out)
    out.attrs["discarded"] = int((~keep).sum())
    out.attrs["mode"] = mode
    return out


def chain(symbol: str, expiry: str, spot: float, risk_free: float = 0.04,
          dividend_yield: float = 0.0, fetch=None, strict: bool = False) -> pd.DataFrame:
    """One expiry, cleaned, calls and puts together.

    `fetch(symbol, expiry)` replaces the download (the app passes a cached one: the raw
    quotes do not depend on the spot, the cleaning does). `strict=True` raises on a
    failed download instead of returning an empty frame."""
    try:
        raw = (fetch or fetch_chain)(symbol, expiry)
    except Exception:
        if strict:
            raise
        return pd.DataFrame()
    days = max((pd.Timestamp(expiry) - pd.Timestamp(dt.date.today())).days, 0)
    t_years = max(days / 365.0, 1 / 365)
    calls = _clean(raw.calls, spot, "call", t_years, risk_free, dividend_yield)
    puts = _clean(raw.puts, spot, "put", t_years, risk_free, dividend_yield)
    discarded = calls.attrs.get("discarded", 0) + puts.attrs.get("discarded", 0)
    mode = calls.attrs.get("mode") or puts.attrs.get("mode") or "unknown"
    out = pd.concat([calls, puts], ignore_index=True)
    out.attrs["discarded"] = discarded
    out.attrs["mode"] = mode
    if not out.empty:
        out["expiry"] = expiry
        out["days"] = (pd.Timestamp(expiry) - pd.Timestamp(dt.date.today())).days
    return out


def surface(symbol: str, spot: float, max_expiries: int = 8,
            risk_free: float = 0.04, dividend_yield: float = 0.0, fetch=None,
            listed=None, strict: bool = False) -> pd.DataFrame:
    """Implied volatility across strikes and expiries — the surface, as a long table.

    Only out-of-the-money contracts are kept per side (calls above spot, puts below).
    That is the market convention and it matters: in-the-money options carry most of
    their value as intrinsic value, so their implied volatility is a small residual on a
    big number and is correspondingly unstable.
    """
    dates = (listed(symbol) if listed else expiries(symbol, strict=strict))[:max_expiries]
    frames, discarded, mode = [], 0, "unknown"
    for e in dates:
        c = chain(symbol, e, spot, risk_free, dividend_yield, fetch=fetch, strict=strict)
        if c.empty:
            continue
        discarded += c.attrs.get("discarded", 0)
        mode = c.attrs.get("mode", mode)
        otm = c[((c.kind == "call") & (c.moneyness >= 1.0))
                | ((c.kind == "put") & (c.moneyness < 1.0))]
        frames.append(otm)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out.attrs["discarded"] = discarded
    out.attrs["mode"] = mode
    return out


def atm_volatility(chain_df: pd.DataFrame) -> float | None:
    """Implied volatility at the money — the single number the rest hangs on."""
    if chain_df.empty:
        return None
    near = chain_df.iloc[(chain_df.moneyness - 1.0).abs().argsort()[:6]]
    return float(near.iv.mean()) if len(near) else None


def expected_move(spot: float, iv: float, days: int) -> dict:
    """The market's own range, from its own volatility.

    One standard deviation over the period is spot × iv × sqrt(days/365). Under the
    usual assumption that returns are roughly normal, about two thirds of outcomes fall
    inside that band and about 95% inside two of them.

    The assumption is the weak part and it is worth saying plainly: real returns have
    fatter tails than the normal curve, so the extreme bands understate how often a big
    move happens. The band is the market's expectation, not a promise.
    """
    if not spot or not iv or days <= 0:
        return {}
    sigma = spot * iv * np.sqrt(days / 365.0)
    return {
        "sigma": float(sigma),
        "days": days,
        "low_1": float(spot - sigma), "high_1": float(spot + sigma),
        "low_2": float(spot - 2 * sigma), "high_2": float(spot + 2 * sigma),
        "pct": float(sigma / spot),
    }


def expected_move_cone(
    spot: float, term: pd.DataFrame, horizon_days: int | None = None
) -> pd.DataFrame:
    """The market's own range for every day between now and the last expiry.

    Where `expected_move` answers "how far by this one date", this answers "how far by
    each date", which is the shape people actually picture: a band that opens up with
    the square root of time, because uncertainty accumulates with time and not with its
    square.

    The width at day d uses the at-the-money volatility of the nearest expiries on either
    side, interpolated — options for day d itself usually do not exist, and holding the
    front-month volatility flat across three months would draw a band the market is not
    quoting.

    What the band is NOT: a forecast, or a promise. It is the volatility the market is
    charging for, converted into a distance. Roughly two thirds of outcomes historically
    land inside the inner band and 95% inside the outer one — except that real returns
    have fatter tails than the bell curve, so the outer band is the optimistic reading.
    """
    if not spot or term is None or term.empty:
        return pd.DataFrame()
    term = term.sort_values("days")
    last = int(horizon_days or term.days.max())
    if last <= 0:
        return pd.DataFrame()

    days = np.arange(1, last + 1)
    iv = np.interp(days, term.days.to_numpy(float), term.atm_iv.to_numpy(float))
    sigma = spot * iv * np.sqrt(days / 365.0)
    return pd.DataFrame({
        "day": days,
        "atm_iv": iv,
        "low_2": spot - 2 * sigma,
        "low_1": spot - sigma,
        "mid": np.full_like(sigma, spot),
        "high_1": spot + sigma,
        "high_2": spot + 2 * sigma,
    })


def term_structure(surface_df: pd.DataFrame) -> pd.DataFrame:
    """At-the-money implied volatility by expiry.

    Rising with time usually means the market sees no immediate trouble but wants
    compensation for the unknown further out. A near-dated spike is the shape that says
    something specific is coming — earnings, a ruling, a vote.
    """
    if surface_df.empty:
        return pd.DataFrame()
    rows = []
    for expiry, grp in surface_df.groupby("expiry"):
        near = grp.iloc[(grp.moneyness - 1.0).abs().argsort()[:8]]
        if near.empty:
            continue
        rows.append({
            "expiry": expiry,
            "days": int(grp.days.iloc[0]),
            "atm_iv": float(near.iv.mean()),
            "contracts": len(grp),
        })
    return pd.DataFrame(rows).sort_values("days")


def skew(surface_df: pd.DataFrame, expiry: str) -> pd.DataFrame:
    """Implied volatility against strike for one expiry — the smile, or the smirk.

    In equities it is usually a smirk rather than a smile: downside puts cost more
    volatility than upside calls, because crashes happen faster than rallies and people
    pay for protection.
    """
    if surface_df.empty:
        return pd.DataFrame()
    one = surface_df[surface_df.expiry == expiry].sort_values("strike")
    return one[["strike", "moneyness", "iv", "kind", "openInterest", "mid"]]


def implied_vs_realised(iv: float | None, realised: float | None) -> dict:
    """Is the market paying more for volatility than the share has actually delivered?

    Implied above realised is the normal state — sellers of options want to be paid for
    taking the risk. A large gap either way is the interesting case, and neither
    direction is a recommendation.
    """
    if not iv or not realised:
        return {}
    return {
        "implied": iv,
        "realised": realised,
        "premium": iv - realised,
        "ratio": iv / realised if realised else None,
    }


# ------------------------------------------------------------------------- greeks
def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def greeks(S: float, K: float, T: float, r: float, sigma: float,
           q: float = 0.0, kind: str = "call") -> dict:
    """Sensitivities of the option price, in the units people actually quote.

    delta  change in price per 1.00 move in the share
    gamma  change in delta per 1.00 move — how fast the position's direction shifts
    vega   change in price per 1 PERCENTAGE POINT of volatility
    theta  change in price per DAY of time passing
    rho    change in price per 1 percentage point of interest rate

    Vega and theta are scaled here because their raw forms (per 100% vol, per year) are
    numbers nobody reasons with. A theta of -0.04 means this position loses four cents a
    day, which is a sentence you can act on.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        intrinsic_delta = (1.0 if S > K else 0.0) if kind == "call" else (-1.0 if S < K else 0.0)
        return {"delta": intrinsic_delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}

    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc_q, disc_r = math.exp(-q * T), math.exp(-r * T)
    pdf = _norm_pdf(d1)

    if kind == "call":
        delta = disc_q * _norm_cdf(d1)
        theta = (-S * pdf * sigma * disc_q / (2 * sqrt_t)
                 - r * K * disc_r * _norm_cdf(d2)
                 + q * S * disc_q * _norm_cdf(d1))
        rho = K * T * disc_r * _norm_cdf(d2)
    else:
        delta = -disc_q * _norm_cdf(-d1)
        theta = (-S * pdf * sigma * disc_q / (2 * sqrt_t)
                 + r * K * disc_r * _norm_cdf(-d2)
                 - q * S * disc_q * _norm_cdf(-d1))
        rho = -K * T * disc_r * _norm_cdf(-d2)

    return {
        "delta": delta,
        "gamma": disc_q * pdf / (S * sigma * sqrt_t),
        "vega": S * disc_q * pdf * sqrt_t / 100.0,
        "theta": theta / 365.0,
        "rho": rho / 100.0,
    }


# --------------------------------------------------------------------- positions
@dataclass(frozen=True, slots=True)
class Leg:
    """One option or share position.

    `quantity` is signed: positive is long, negative is short. `contract_size` is 100 for
    listed US options — the single most common source of a hundred-fold error in a
    home-made options sheet, so it is explicit rather than assumed.
    """

    kind: str          # "call", "put" or "share"
    strike: float
    expiry: str
    quantity: float
    entry_price: float
    iv: float = 0.25
    contract_size: int = 100

    @property
    def multiplier(self) -> float:
        return self.quantity * (1 if self.kind == "share" else self.contract_size)

    def describe(self) -> str:
        side = "long" if self.quantity > 0 else "short"
        n = abs(self.quantity)
        if self.kind == "share":
            return f"{side} {n:g} shares at {self.entry_price:,.2f}"
        return (f"{side} {n:g} {self.expiry} {self.strike:g} {self.kind} "
                f"at {self.entry_price:,.2f}")


def payoff_at_expiry(legs: list[Leg], prices: np.ndarray) -> np.ndarray:
    """Profit and loss at expiry across a range of share prices.

    Ignores time value entirely, which is correct at expiry and wrong before it. The
    companion curve from `value_now` shows the difference, and that gap is where most of
    the surprise in an options position lives.
    """
    total = np.zeros_like(prices, dtype=float)
    for leg in legs:
        if leg.kind == "share":
            value = prices - leg.entry_price
        elif leg.kind == "call":
            value = np.maximum(prices - leg.strike, 0.0) - leg.entry_price
        else:
            value = np.maximum(leg.strike - prices, 0.0) - leg.entry_price
        total += value * leg.multiplier
    return total


def value_now(legs: list[Leg], prices: np.ndarray, days_left: float,
              r: float = 0.04, q: float = 0.0) -> np.ndarray:
    """Profit and loss today, with time value still in the options."""
    total = np.zeros_like(prices, dtype=float)
    T = max(days_left / 365.0, 1e-6)
    for leg in legs:
        if leg.kind == "share":
            total += (prices - leg.entry_price) * leg.multiplier
            continue
        priced = np.array([
            black_scholes(float(s), leg.strike, T, r, leg.iv, q, leg.kind) for s in prices
        ])
        total += (priced - leg.entry_price) * leg.multiplier
    return total


def position_metrics(legs: list[Leg], spot: float, days_left: float,
                     r: float = 0.04, q: float = 0.0,
                     span: float = 0.6, steps: int = 401) -> dict:
    """Cost, exposure and the shape of the outcome.

    Max profit and max loss are reported over the price range examined, not over all
    possible prices — a naked short call has unbounded loss, and printing a finite number
    for it would be the most dangerous kind of tidy. The range is stated so the figure
    can be read for what it is.
    """
    lo, hi = spot * (1 - span), spot * (1 + span)
    grid = np.linspace(max(lo, 0.01), hi, steps)
    at_expiry = payoff_at_expiry(legs, grid)
    today = value_now(legs, grid, days_left, r, q)

    net_debit = sum(leg.entry_price * leg.multiplier for leg in legs)

    totals = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
    for leg in legs:
        if leg.kind == "share":
            totals["delta"] += leg.multiplier
            continue
        g = greeks(spot, leg.strike, max(days_left / 365.0, 1e-6), r, leg.iv, q, leg.kind)
        for key in totals:
            totals[key] += g[key] * leg.multiplier

    # Break-evens: where the expiry curve crosses zero.
    signs = np.sign(at_expiry)
    crossings = np.where(np.diff(signs) != 0)[0]
    breakevens = [float((grid[i] + grid[i + 1]) / 2) for i in crossings]

    unbounded_up = any(leg.kind == "call" and leg.quantity < 0 for leg in legs) and \
        sum(leg.multiplier for leg in legs if leg.kind == "call") < 0
    unbounded_down = any(leg.kind == "put" and leg.quantity < 0 for leg in legs) and \
        sum(leg.multiplier for leg in legs if leg.kind == "put") < 0

    return {
        "grid": grid,
        "at_expiry": at_expiry,
        "today": today,
        "net_debit": net_debit,
        "greeks": totals,
        "max_profit": float(at_expiry.max()),
        "max_loss": float(at_expiry.min()),
        "breakevens": breakevens,
        "range": (float(lo), float(hi)),
        "unbounded_loss": unbounded_up or unbounded_down,
        "days_left": days_left,
    }


def probability_above(spot: float, level: float, iv: float, days: float,
                      r: float = 0.04) -> float:
    """Chance of finishing above a price, under the same lognormal assumption as the
    pricing. Shares the model's weakness in the tails: real markets make large moves more
    often than this allows, so probabilities near the edges read too comfortable."""
    if days <= 0 or iv <= 0 or spot <= 0 or level <= 0:
        return float(spot > level)
    T = days / 365.0
    d2 = (math.log(spot / level) + (r - 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
    return _norm_cdf(d2)


# Textbook structures, named as the teaching material names them.
PRESETS = {
    "Long call": [("call", 1.00, +1)],
    "Long put": [("put", 1.00, +1)],
    "Covered call": [("share", 0.0, +100), ("call", 1.05, -1)],
    "Protective put": [("share", 0.0, +100), ("put", 0.95, +1)],
    "Bull call spread": [("call", 1.00, +1), ("call", 1.10, -1)],
    "Bear put spread": [("put", 1.00, +1), ("put", 0.90, -1)],
    "Straddle": [("call", 1.00, +1), ("put", 1.00, +1)],
    "Strangle": [("call", 1.10, +1), ("put", 0.90, +1)],
    "Iron condor": [("put", 0.90, +1), ("put", 0.95, -1),
                    ("call", 1.05, -1), ("call", 1.10, +1)],
}


# ------------------------------------------------------ what the prices imply (02.10.2026)
# Two additions, kept apart from everything above so that work on the chain cleaning in
# a parallel branch merges without touching them.
#
# 1. The distribution the chain prices in (Breeden-Litzenberger). `expected_move` and the
#    cone assume a bell curve with one volatility; the chain itself quotes a different
#    volatility at every strike, and that smirk is exactly the fat left tail the bell
#    curve leaves out.
# 2. The put-call volatility spread at the money ("SmileSlope", Yan 2011). Of the option
#    signals in Chen & Zimmermann's open-source replication it is the one that kept
#    working after publication: value-weighted deciles, high-minus-low, -0.86 % a month
#    in 2015-2024 (t 2.8) and -1.79 % in 2020-2024 (t 2.7), measured on their data on
#    02.10.2026. The classic smirk (Xing, Zhang & Zhao 2010) earned nothing over the same
#    years (t 0.1). It ranks shares against each other; it is not a price forecast for
#    one of them, and it has not been tested on this app's data (PLAN.md).

_ERF = np.vectorize(math.erf, otypes=[float])


def _ndtr(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + _ERF(np.asarray(x, float) / math.sqrt(2.0)))


def _bs_call_vec(S: float, K: np.ndarray, T: float, r: float, sigma: np.ndarray,
                 q: float = 0.0) -> np.ndarray:
    ndtr = _ndtr
    sq = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / sq
    return S * np.exp(-q * T) * ndtr(d1) - K * np.exp(-r * T) * ndtr(d1 - sq)


def implied_distribution(otm: pd.DataFrame, spot: float, days: int, r: float = 0.04,
                         q: float = 0.0, points: int = 1601) -> dict:
    """Probability of each price at expiry, as the chain prices it.

    Breeden-Litzenberger: the second derivative of the call price with respect to the
    strike, grown at the risk-free rate, is the density the prices imply. Taken directly
    from quotes it is noise, so the volatility smile is smoothed first (a quadratic in
    log-moneyness, the shape equity smiles have) and held flat beyond the last quoted
    strike on each side. How much probability sits out there is reported, because that
    part is extrapolation and not quotes.

    These are RISK-NEUTRAL probabilities: they include what buyers pay to be insured
    against falls, so they put more weight on the downside than the real-world odds.
    They say what the market charges for, not what will happen.
    """
    need = {"strike", "iv", "kind"}
    if otm is None or otm.empty or not need <= set(otm.columns) or spot <= 0 or days <= 0:
        return {}
    pts = otm.dropna(subset=["strike", "iv"])
    pts = pts[(pts.iv > 0) & (pts.strike > 0)]
    if len(pts) < 5 or (pts.kind == "put").sum() < 2 or (pts.kind == "call").sum() < 2:
        return {}
    T = days / 365.0
    fwd = spot * math.exp((r - q) * T)
    pts = pts.groupby("strike", as_index=False).iv.mean()
    k = np.log(pts.strike.to_numpy(float) / fwd)
    ivs = pts.iv.to_numpy(float)
    k_lo, k_hi = float(k.min()), float(k.max())
    atm = float(np.interp(0.0, k, ivs))                 # observed, not fitted
    reach = max(7.0 * atm * math.sqrt(T), 1.2 * max(abs(k_lo), abs(k_hi)))
    grid_k = np.linspace(-reach, reach, points)
    # Straight lines between the quoted strikes, flat beyond them, then a Gaussian
    # smoothing about one strike step wide: close to the quotes, and smooth enough that
    # the second derivative is a density rather than spikes at every quoted strike.
    # (A quadratic fit, tried first, missed the at-the-money level of a kinked smirk.)
    raw = np.interp(np.clip(grid_k, k_lo, k_hi), k, ivs)
    step = grid_k[1] - grid_k[0]
    width = max(float(np.median(np.diff(k))) if len(k) > 1 else 0.02, 0.01)
    half = int(4 * width / step)
    kern = np.exp(-0.5 * (np.arange(-half, half + 1) * step / width) ** 2)
    kern /= kern.sum()
    smooth = np.convolve(np.pad(raw, half, mode="edge"), kern, mode="valid")
    K = fwd * np.exp(grid_k)
    C = _bs_call_vec(spot, K, T, r, smooth, q)
    dens = math.exp(r * T) * np.gradient(np.gradient(C, K), K)
    negative = float(-np.trapezoid(np.minimum(dens, 0.0), K))
    dens = np.maximum(dens, 0.0)
    total = float(np.trapezoid(dens, K))
    if not total > 0:
        return {}
    dens = dens / total
    cdf = np.concatenate([[0.0], np.cumsum(0.5 * (dens[1:] + dens[:-1]) * np.diff(K))])
    cdf = cdf / cdf[-1]

    def quantile(p: float) -> float:
        return float(np.interp(p, cdf, K))

    def below(level: float) -> float:
        return float(np.interp(level, K, cdf))

    # the bell-curve reading of the same expiry, for comparison: one volatility, the ATM
    # one, lognormal around the forward (stdlib only; the project has no scipy)
    from statistics import NormalDist

    sd = atm * math.sqrt(T)
    mu = math.log(fwd) - 0.5 * sd * sd
    unit = NormalDist()

    class _Ref:
        @staticmethod
        def cdf(x):
            return unit.cdf((math.log(x) - mu) / sd)

        @staticmethod
        def ppf(p):
            return math.exp(mu + sd * unit.inv_cdf(p))

        @staticmethod
        def pdf(x):
            x = np.asarray(x, float)
            return np.exp(-0.5 * ((np.log(x) - mu) / sd) ** 2) / (x * sd * math.sqrt(2 * math.pi))

    ref = _Ref()
    inside = (K >= float(pts.strike.min())) & (K <= float(pts.strike.max()))
    return {
        "days": int(days),
        "forward": fwd,
        "atm_iv": atm,
        "quantiles": {p: quantile(p / 100) for p in (5, 10, 25, 50, 75, 90, 95)},
        "mean": float(np.trapezoid(K * dens, K)),
        "p_below_spot": below(spot),
        "p_down_10": below(0.9 * spot),
        "p_up_10": 1.0 - below(1.1 * spot),
        "ref_p_down_10": float(ref.cdf(0.9 * spot)),
        "ref_p_up_10": float(1.0 - ref.cdf(1.1 * spot)),
        "ref_quantiles": {p: float(ref.ppf(p / 100)) for p in (5, 10, 25, 50, 75, 90, 95)},
        "beyond_quotes": float(1.0 - np.trapezoid(dens[inside], K[inside])),
        "negative_mass": negative / (negative + total),
        "contracts": len(pts),
        # mostly quotes, not extrapolation: otherwise the shape is our assumption
        "usable": bool(len(pts) >= 6 and float(1.0 - np.trapezoid(dens[inside], K[inside])) < 0.25),
        "curve": pd.DataFrame({"price": K, "density": dens, "bell_curve": ref.pdf(K)}),
    }


ATM_BAND = (0.95, 1.05)
SIGNAL_DAYS = 30


# A stale share price and a genuine parity deviation look the same from the chain, so
# this only catches the impossible: 3 % of the forward is ~25 volatility points at 30
# days, beyond any spread the literature reports. 1 %, tried first, would have thrown away
# a genuine 10-point spread (a 1.1 % gap at 30 days), the very tail the signal ranks on.
# The real defence is a share price taken at the same moment as the chain.
MAX_PARITY_GAP = 0.03


def put_call_spread(chain_df: pd.DataFrame, band: tuple[float, float] = ATM_BAND,
                    r: float = 0.04, q: float = 0.0) -> dict:
    """Put minus call implied volatility at the same strikes, near the money.

    Weighted towards the strike closest to spot. Needs calls AND puts at one strike,
    which `surface()` does not keep (it drops in-the-money contracts), so this takes a
    full `chain()`.

    The spread measures how far put and call prices stray from put-call parity, so it is
    only as good as the share price they are measured against. Each pair implies a
    forward, K + e^{rT}(C - P); if that differs from spot x e^{(r-q)T} by more than
    MAX_PARITY_GAP the price and the chain are out of step and there is no number. Found
    on 02.10.2026: SXI's pairs implied ~305 while the price on hand was 273, and the
    "spread" came out at -83 volatility points.
    """
    if chain_df is None or chain_df.empty or "kind" not in chain_df:
        return {}
    # Last traded prices are from different moments for each contract. Measured on
    # 02.10.2026 before the US open: spreads of -37 to +25 volatility points across 8
    # shares, where the literature reports a few. Only live two-sided quotes are used.
    if str(chain_df.attrs.get("mode", "")).startswith("last traded"):
        return {"rejected": "market closed: last traded prices are from different "
                            "moments, so puts and calls cannot be compared"}
    spot = float((chain_df.strike / chain_df.moneyness).median())
    days = float(chain_df["days"].iloc[0]) if "days" in chain_df else 30.0
    T = max(days, 1.0) / 365.0
    near = chain_df[chain_df.moneyness.between(*band)]
    side = {kind: near[near.kind == kind].groupby("strike")[["iv", "mid"]].mean()
            for kind in ("call", "put")}
    both = side["put"].join(side["call"], lsuffix="_put", rsuffix="_call", how="inner").dropna()
    if both.empty:
        return {}
    strikes = both.index.to_numpy(float)
    implied_fwd = strikes + math.exp(r * T) * (both.mid_call - both.mid_put).to_numpy(float)
    gap = float(np.median(implied_fwd) / (spot * math.exp((r - q) * T)) - 1.0)
    if abs(gap) > MAX_PARITY_GAP:
        return {"rejected": f"price and chain out of step: the pairs imply a forward "
                            f"{gap:+.1%} from the price on hand", "parity_gap": gap}
    w = 1.0 / (np.abs(strikes / spot - 1.0) + 0.01)
    spread = both.iv_put - both.iv_call
    return {"spread": float(np.average(spread, weights=w)), "pairs": len(both),
            "put_iv": float(np.average(both.iv_put, weights=w)),
            "call_iv": float(np.average(both.iv_call, weights=w)), "parity_gap": gap}


def smile_slope(by_expiry: list[tuple[int, pd.DataFrame]],
                target: int = SIGNAL_DAYS, r: float = 0.04, q: float = 0.0) -> dict:
    """The put-call spread at `target` days: interpolated between the expiries either
    side, or the nearest expiry within 10-60 days (the window the literature uses).

    Positive = puts dearer than calls at the same strike. Historically the shares with
    the highest values earned less the following month than those with the lowest.
    """
    rows, rejected = [], []
    for days, ch in by_expiry:
        if days < 7:
            continue                              # the last week before expiry is noise
        pc = put_call_spread(ch, r=r, q=q)
        if "rejected" in pc:
            rejected.append(pc["rejected"])
        elif pc:
            rows.append({"days": int(days), **{k: pc[k] for k in ("spread", "pairs")}})
    if not rows:
        return {"rejected": rejected[0]} if rejected else {}
    t = pd.DataFrame(rows).sort_values("days")
    before, after = t[t.days <= target], t[t.days >= target]
    if len(before) and len(after) and before.days.iloc[-1] != after.days.iloc[0]:
        a, b = before.iloc[-1], after.iloc[0]
        w = (target - a.days) / (b.days - a.days)
        return {"value": float(a.spread + w * (b.spread - a.spread)), "days": target,
                "method": f"interpolated between {int(a.days)} and {int(b.days)} days",
                "pairs": int(a.pairs + b.pairs)}
    window = t[t.days.between(10, 60)]
    if window.empty:
        return {}
    one = window.iloc[(window.days - target).abs().argsort()[:1]].iloc[0]
    return {"value": float(one.spread), "days": int(one.days),
            "method": f"nearest expiry, {int(one.days)} days", "pairs": int(one.pairs)}


def trailing_dividend_yield(con, ticker: str, sessions: int = 252) -> float:
    """Dividends of the last year as a fraction of the price, from the store alone.

    close_adj is the close with dividends folded back in, so its ratio to the plain close
    drifts by each payout. The drift over a year is the trailing yield. Without it, every
    implied volatility here is solved with no dividend, and a high payer's puts look
    dearer than its calls for no other reason, which is exactly the spread above.
    """
    rows = con.execute(
        "SELECT close_adj / NULLIF(close, 0) FROM prices WHERE ticker = ? "
        "AND close > 0 AND close_adj > 0 ORDER BY date DESC LIMIT ?",
        [ticker, sessions + 1]).fetchall()
    if len(rows) < sessions // 2:
        return 0.0
    now, then = float(rows[0][0]), float(rows[-1][0])
    return max(min(now / then - 1.0, 0.25), 0.0) if then > 0 else 0.0


def percentile(value: float, cross_section: pd.Series) -> float | None:
    """Share of the cross-section at or below `value`; None without a cross-section."""
    s = pd.Series(cross_section).dropna()
    if len(s) < 20 or value is None:
        return None
    return float((s <= value).mean())


def snapshot_stamp(folder) -> tuple[str, int] | None:
    """(name, modification time in ns) of the newest daily snapshot in `folder`, or None.

    The app's cache of the snapshot is keyed by this. Keyed by the day alone it served
    the morning's answer for an hour after scripts/snapshot_options.py had written a new
    file, or the first snapshot of a fresh install (review 03.10.2026, F12g)."""
    from pathlib import Path

    folder = Path(folder)
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    if not files:
        return None
    return files[-1].name, files[-1].stat().st_mtime_ns
