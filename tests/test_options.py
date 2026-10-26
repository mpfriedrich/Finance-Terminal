"""Option mathematics. These are analytic checks — the right answers are known."""
import math

import numpy as np
import pandas as pd
import pytest

from portfolio_lab import options as op
from portfolio_lab.options import (
    black_scholes,
    expected_move,
    implied_volatility,
    implied_vs_realised,
)


def test_price_and_volatility_are_inverses():
    """Price an option at a known volatility, then recover it. If this drifts, every
    number in the options view is wrong."""
    price = black_scholes(100, 100, 1.0, 0.04, 0.25, 0.0, "call")
    assert implied_volatility(price, 100, 100, 1.0, 0.04, 0.0, "call") == pytest.approx(0.25, abs=1e-6)


def test_put_call_parity_holds():
    """C - P = S - K·e^(-rT). A pricing bug almost always breaks this first."""
    c = black_scholes(100, 90, 0.5, 0.04, 0.3, 0.0, "call")
    p = black_scholes(100, 90, 0.5, 0.04, 0.3, 0.0, "put")
    assert c - p == pytest.approx(100 - 90 * math.exp(-0.04 * 0.5), abs=1e-9)


def test_price_rises_with_volatility():
    """The property bisection depends on. Without it the solver could converge anywhere."""
    prices = [black_scholes(100, 100, 1.0, 0.04, s, 0.0, "call") for s in (0.1, 0.2, 0.4, 0.8)]
    assert prices == sorted(prices)


def test_a_price_below_intrinsic_value_is_refused():
    """A stale quote on a moved market. No volatility produces it, so none is invented."""
    assert implied_volatility(1.0, 150, 100, 1.0, 0.04, 0.0, "call") is None


def test_zero_and_negative_prices_are_refused():
    assert implied_volatility(0.0, 100, 100, 1.0) is None
    assert implied_volatility(-5.0, 100, 100, 1.0) is None


def test_deep_in_the_money_call_is_worth_about_its_intrinsic_value():
    v = black_scholes(200, 100, 0.01, 0.0, 0.2, 0.0, "call")
    assert v == pytest.approx(100.0, abs=0.5)


def test_expected_move_scales_with_the_square_root_of_time():
    """Four times the days should widen the band by two, not by four."""
    a = expected_move(100, 0.25, 30)
    b = expected_move(100, 0.25, 120)
    assert b["sigma"] / a["sigma"] == pytest.approx(2.0, rel=1e-6)


def test_expected_move_bands_sit_around_the_spot():
    m = expected_move(100, 0.20, 365)
    assert m["low_1"] < 100 < m["high_1"]
    assert m["low_2"] < m["low_1"] and m["high_2"] > m["high_1"]
    assert m["sigma"] == pytest.approx(20.0, rel=1e-6)


def test_implied_against_realised_reports_the_gap():
    r = implied_vs_realised(0.30, 0.20)
    assert r["premium"] == pytest.approx(0.10)
    assert r["ratio"] == pytest.approx(1.5)
    assert implied_vs_realised(None, 0.2) == {}


# ------------------------------------------------------------------ greeks & positions
from portfolio_lab.options import (
    PRESETS,
    Leg,
    greeks,
    payoff_at_expiry,
    position_metrics,
    probability_above,
    value_now,
)


def test_call_and_put_delta_differ_by_exactly_one():
    """With no dividend, delta_call - delta_put = 1. A sign error breaks this first."""
    c = greeks(100, 100, 1.0, 0.04, 0.25, 0.0, "call")
    p = greeks(100, 100, 1.0, 0.04, 0.25, 0.0, "put")
    assert c["delta"] - p["delta"] == pytest.approx(1.0, abs=1e-9)


def test_gamma_and_vega_are_identical_for_call_and_put():
    c = greeks(100, 95, 0.5, 0.04, 0.3, 0.0, "call")
    p = greeks(100, 95, 0.5, 0.04, 0.3, 0.0, "put")
    assert c["gamma"] == pytest.approx(p["gamma"], rel=1e-12)
    assert c["vega"] == pytest.approx(p["vega"], rel=1e-12)


def test_delta_stays_inside_its_bounds():
    for s in (50, 100, 200):
        assert 0.0 <= greeks(s, 100, 1.0, 0.04, 0.25, 0.0, "call")["delta"] <= 1.0
        assert -1.0 <= greeks(s, 100, 1.0, 0.04, 0.25, 0.0, "put")["delta"] <= 0.0


def test_a_long_option_loses_value_as_time_passes():
    assert greeks(100, 100, 0.5, 0.04, 0.25, 0.0, "call")["theta"] < 0


def test_delta_matches_a_numerical_derivative():
    """The definition, checked directly: delta is dPrice/dSpot."""
    up = black_scholes(100.01, 100, 1.0, 0.04, 0.25, 0.0, "call")
    down = black_scholes(99.99, 100, 1.0, 0.04, 0.25, 0.0, "call")
    numeric = (up - down) / 0.02
    assert greeks(100, 100, 1.0, 0.04, 0.25, 0.0, "call")["delta"] == pytest.approx(numeric, abs=1e-4)


def test_a_long_call_payoff_is_the_hockey_stick():
    leg = Leg("call", 100, "2027-01-15", 1, entry_price=5.0)
    out = payoff_at_expiry([leg], np.array([80.0, 100.0, 105.0, 120.0]))
    assert out.tolist() == [-500.0, -500.0, 0.0, 1500.0]   # x100 contract size


def test_contract_size_is_applied_and_not_assumed_to_be_one():
    """The hundred-fold error that ruins home-made option sheets."""
    leg = Leg("call", 100, "2027-01-15", 1, entry_price=5.0, contract_size=100)
    assert payoff_at_expiry([leg], np.array([110.0]))[0] == pytest.approx(500.0)


def test_a_short_position_mirrors_the_long_one():
    long_ = Leg("put", 100, "2027-01-15", 1, entry_price=4.0)
    short = Leg("put", 100, "2027-01-15", -1, entry_price=4.0)
    grid = np.array([80.0, 100.0, 120.0])
    assert payoff_at_expiry([long_], grid).tolist() == [-x for x in payoff_at_expiry([short], grid).tolist()]


def test_a_spread_has_a_capped_profit_and_loss():
    legs = [Leg("call", 100, "2027-01-15", 1, 6.0, 0.25),
            Leg("call", 110, "2027-01-15", -1, 2.0, 0.25)]
    m = position_metrics(legs, 100, 180)
    assert m["max_profit"] == pytest.approx(600.0, abs=1.0)   # (10 - 4) x 100
    assert m["max_loss"] == pytest.approx(-400.0, abs=1.0)
    assert not m["unbounded_loss"]
    assert len(m["breakevens"]) == 1


def test_a_naked_short_call_is_flagged_as_unbounded():
    """Reporting a tidy finite maximum loss for this would be the dangerous answer."""
    m = position_metrics([Leg("call", 100, "2027-01-15", -1, 5.0, 0.25)], 100, 180)
    assert m["unbounded_loss"]


def test_a_straddle_at_the_spot_is_not_quite_delta_neutral():
    """A straddle is neutral at the FORWARD price, not the spot. With rates at 4% over a
    year the forward sits near 104, so a 100-strike straddle leans slightly long — small,
    positive, and the right answer. Expecting exactly zero here is the common mistake."""
    legs = [Leg("call", 100, "2027-01-15", 1, 5.0, 0.25),
            Leg("put", 100, "2027-01-15", 1, 5.0, 0.25)]
    m = position_metrics(legs, 100, 365)
    assert 0 < m["greeks"]["delta"] < 40           # on 100-share contracts
    assert m["greeks"]["gamma"] > 0                # long gamma: gains from movement
    assert m["greeks"]["theta"] < 0                # and pays for it in time decay


def test_the_delta_neutral_straddle_strike_includes_the_volatility_term():
    """A straddle's delta is 2*N(d1) - 1, so it vanishes where d1 = 0 — at
    K = S*exp((r + sigma^2/2)*T), NOT at the forward S*exp(r*T).

    At the forward, d1 = sigma*sqrt(T)/2, which leaves a small positive delta. Both the
    spot strike and the forward strike are commonly but wrongly called delta-neutral."""
    S, r, sigma, T = 100.0, 0.04, 0.25, 1.0
    forward = S * math.exp(r * T)
    neutral = S * math.exp((r + 0.5 * sigma * sigma) * T)
    assert neutral > forward > S

    def straddle_delta(strike):
        legs = [Leg("call", strike, "2027-01-15", 1, 5.0, sigma),
                Leg("put", strike, "2027-01-15", 1, 5.0, sigma)]
        return position_metrics(legs, S, 365)["greeks"]["delta"]

    assert abs(straddle_delta(neutral)) < 0.5
    assert straddle_delta(forward) > 5          # the forward is not neutral
    assert straddle_delta(S) > straddle_delta(forward)


def test_time_value_sits_above_the_expiry_payoff_for_a_long_option():
    leg = Leg("call", 100, "2027-01-15", 1, 5.0, 0.25)
    grid = np.array([90.0, 100.0])
    assert (value_now([leg], grid, 180) > payoff_at_expiry([leg], grid)).all()


def test_probability_above_behaves_at_the_edges():
    # Just above a half: the drift is r - 0.5*sigma^2 = 0.04 - 0.03125 > 0. Forgetting
    # the interest term and expecting a number below 0.5 is the easy slip here.
    assert probability_above(100, 100, 0.25, 365) == pytest.approx(0.51, abs=0.02)
    assert probability_above(100, 1000, 0.25, 30) < 0.01
    assert probability_above(100, 10, 0.25, 30) > 0.99


def test_every_preset_is_made_of_known_leg_kinds():
    for name, legs in PRESETS.items():
        for kind, ratio, qty in legs:
            assert kind in ("call", "put", "share")
            assert qty != 0


# --- the cone: the market's range for every day, not just for one expiry -------------

def _term(days=(7, 30, 90), ivs=(0.35, 0.30, 0.28)):
    return pd.DataFrame({"expiry": [f"e{d}" for d in days], "days": list(days),
                         "atm_iv": list(ivs)})


def test_the_cone_opens_with_the_square_root_of_time():
    """Four times the days is twice the width — the property that makes it a cone and
    not a wedge. A band drawn linearly in time would overstate the near term badly."""
    flat = _term(days=(10, 40), ivs=(0.30, 0.30))
    cone = op.expected_move_cone(100.0, flat).set_index("day")
    width_10 = cone.loc[10, "high_1"] - 100.0
    width_40 = cone.loc[40, "high_1"] - 100.0
    assert width_40 == pytest.approx(2 * width_10, rel=1e-9)


def test_the_cone_is_centred_on_todays_price():
    cone = op.expected_move_cone(100.0, _term())
    assert (cone["mid"] == 100.0).all()
    # numpy arrays, not Series: comparing a Series with approx() collapses to a single
    # bool and hides which rows disagreed.
    assert (cone.high_1 - 100.0).to_numpy() == pytest.approx((100.0 - cone.low_1).to_numpy())


def test_the_outer_band_is_twice_the_inner_one():
    cone = op.expected_move_cone(100.0, _term())
    assert (cone.high_2 - 100.0).to_numpy() == pytest.approx(
        (2 * (cone.high_1 - 100.0)).to_numpy())


def test_the_width_interpolates_between_the_expiries_that_exist():
    """Options for day 15 do not exist. Holding the front-month volatility flat across
    three months would draw a band the market is not quoting."""
    cone = op.expected_move_cone(100.0, _term()).set_index("day")
    assert 0.30 < cone.loc[15, "atm_iv"] < 0.35
    assert cone.loc[7, "atm_iv"] == pytest.approx(0.35)
    assert cone.loc[90, "atm_iv"] == pytest.approx(0.28)


def test_the_cone_agrees_with_the_single_expiry_calculation():
    """Same maths, two presentations — they must not drift apart."""
    cone = op.expected_move_cone(100.0, _term()).set_index("day")
    single = op.expected_move(100.0, 0.30, 30)
    assert cone.loc[30, "high_1"] == pytest.approx(single["high_1"], rel=1e-9)


def test_no_chain_means_no_cone_rather_than_a_flat_line():
    assert op.expected_move_cone(100.0, pd.DataFrame()).empty
    assert op.expected_move_cone(0.0, _term()).empty


def _closed_chain():
    """Yahoo's layout before the US open on 2026-10-02: no bids, open interest 0 everywhere."""
    return pd.DataFrame({
        "strike": [300.0, 330.0, 340.0, 350.0],
        "lastPrice": [33.0, 6.0, 2.5, 0.9],
        "bid": 0.0, "ask": 0.0,
        "openInterest": [0, 0, 0, 0],
        "volume": [40.0, 900.0, 2.0, np.nan],
        "impliedVolatility": 0.00001,
        "lastTradeDate": pd.to_datetime(["2026-09-24 18:21", "2026-10-01 19:59",
                                         "2026-10-01 19:40", "2026-10-01 15:00"], utc=True),
    })


def test_a_closed_market_chain_without_open_interest_still_yields_contracts():
    # 2026-10-02: every contract had open interest 0, so the old open-interest-only gate
    # kept nothing and RESEARCH -> OPTIONS showed "no usable option contracts".
    out = op._clean(_closed_chain(), spot=330.0, kind="call", t_years=30 / 365)
    assert out.attrs["mode"].startswith("last traded")
    # 300: traded, but last on 24.09 -> stale. 340: volume 2. 350: no volume at all.
    assert out.strike.tolist() == [330.0]


def test_a_few_leftover_bids_do_not_switch_the_chain_to_live_mode():
    chain_ = _closed_chain()
    chain_.loc[0, ["bid", "ask"]] = [32.0, 34.0]
    out = op._clean(chain_, spot=330.0, kind="call", t_years=30 / 365)
    assert out.attrs["mode"].startswith("last traded") and 330.0 in out.strike.tolist()


# --- what the prices imply: the distribution and the put-call spread (02.10.2026) -----

def _otm(spot=100.0, days=30, r=0.04, smile=lambda m: 0.30, strikes=range(70, 131, 5)):
    """An out-of-the-money chain priced from a known smile, the layout surface() keeps."""
    rows = []
    for k in strikes:
        kind = "put" if k < spot else "call"
        rows.append({"strike": float(k), "moneyness": k / spot, "kind": kind,
                     "iv": smile(k / spot)})
    return pd.DataFrame(rows)


def test_a_flat_smile_gives_back_the_bell_curve():
    # one volatility at every strike IS the lognormal; the density must reproduce it
    d = op.implied_distribution(_otm(), 100.0, 30, r=0.04)
    for p in (10, 25, 50, 75, 90):
        assert d["quantiles"][p] == pytest.approx(d["ref_quantiles"][p], rel=0.004)
    assert d["mean"] == pytest.approx(d["forward"], rel=0.002)
    assert d["p_down_10"] == pytest.approx(d["ref_p_down_10"], abs=0.003)
    assert d["negative_mass"] < 1e-3


def test_a_smirk_puts_more_weight_on_a_fall_than_the_bell_curve():
    smile = lambda m: 0.30 + 0.6 * max(1.0 - m, 0.0)
    d = op.implied_distribution(_otm(smile=smile), 100.0, 30, r=0.04)
    assert d["p_down_10"] > d["ref_p_down_10"]
    assert d["quantiles"][5] < d["ref_quantiles"][5]
    # Independent check: P(S_T < K) = e^{rT} dPut/dK, straight from Black-Scholes put
    # prices with the smile's own volatility at each strike (no density involved).
    T, h, K = 30 / 365, 0.5, 90.0
    put = lambda k: op.black_scholes(100.0, k, T, 0.04, smile(k / 100.0), 0.0, "put")
    digital = math.exp(0.04 * T) * (put(K + h) - put(K - h)) / (2 * h)
    assert d["p_down_10"] == pytest.approx(digital, abs=0.006)


def test_a_thin_chain_gives_no_distribution():
    assert op.implied_distribution(_otm(strikes=(90, 95, 105)), 100.0, 30) == {}
    assert op.implied_distribution(pd.DataFrame(), 100.0, 30) == {}


def _full(put_iv, call_iv, spot=100.0, strikes=(95, 100, 105), days=30, priced_at=None):
    """A full chain whose mids are Black-Scholes prices; `priced_at` is the share price
    the options were really quoted against, when it differs from the `spot` on hand."""
    T, true = days / 365, priced_at or spot
    rows = [{"strike": float(k), "moneyness": k / spot, "kind": kind, "days": days,
             "iv": put_iv if kind == "put" else call_iv,
             "mid": op.black_scholes(true, k, T, 0.04,
                                     put_iv if kind == "put" else call_iv, 0.0, kind)}
            for k in strikes for kind in ("call", "put")]
    return pd.DataFrame(rows)


def test_put_call_spread_by_hand():
    pc = op.put_call_spread(_full(0.33, 0.30))
    assert pc["spread"] == pytest.approx(0.03) and pc["pairs"] == 3
    # strikes outside the at-the-money band do not count
    assert op.put_call_spread(_full(0.33, 0.30, strikes=(80, 120))) == {}


def test_a_stale_share_price_gives_no_spread():
    # SXI, 02.10.2026: the chain was quoted against ~305, the price on hand was 273, and
    # the "spread" came out at -83 volatility points. Parity exposes the mismatch.
    stale = op.put_call_spread(_full(0.30, 0.30, spot=100.0, priced_at=112.0))
    assert "rejected" in stale and stale["parity_gap"] > 0.05
    assert op.smile_slope([(30, _full(0.30, 0.30, priced_at=112.0))]).get("rejected")
    # a genuine spread of a few volatility points stays well inside the tolerance
    assert abs(op.put_call_spread(_full(0.33, 0.30))["parity_gap"]) < 0.01


def test_no_spread_from_last_traded_prices():
    closed = _full(0.33, 0.30)
    closed.attrs["mode"] = "last traded prices (market closed)"
    assert "rejected" in op.put_call_spread(closed)


def test_smile_slope_interpolates_to_thirty_days():
    s = op.smile_slope([(20, _full(0.32, 0.30, days=20)), (40, _full(0.34, 0.30, days=40))])
    assert s["value"] == pytest.approx(0.03) and s["days"] == 30
    # the last week is skipped, and nothing within 10-60 days means no number
    assert op.smile_slope([(5, _full(0.40, 0.30, days=5))]) == {}
    assert op.smile_slope([(90, _full(0.40, 0.30, days=90))]) == {}
    one = op.smile_slope([(45, _full(0.31, 0.30, days=45))])
    assert one["value"] == pytest.approx(0.01) and one["days"] == 45


def test_a_dividend_solved_as_zero_makes_puts_look_dear():
    # Why the yield goes into the solver: prices of a 3 % payer, solved with q = 0,
    # show a put-call spread that is nothing but the dividend.
    S, K, T, r, sigma, q = 100.0, 100.0, 30 / 365, 0.04, 0.30, 0.03
    call = op.black_scholes(S, K, T, r, sigma, q, "call")
    put = op.black_scholes(S, K, T, r, sigma, q, "put")
    wrong = (op.implied_volatility(put, S, K, T, r, 0.0, "put")
             - op.implied_volatility(call, S, K, T, r, 0.0, "call"))
    right = (op.implied_volatility(put, S, K, T, r, q, "put")
             - op.implied_volatility(call, S, K, T, r, q, "call"))
    assert wrong > 0.005 and abs(right) < 1e-4


def test_trailing_dividend_yield_from_the_adjusted_close():
    import duckdb

    from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply

    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    days = pd.bdate_range("2025-06-02", periods=300)
    # one 3 % dividend half a year ago: adjusted closes before it sit 3 % lower
    c.executemany("INSERT INTO prices (ticker, date, close, close_adj) VALUES ('D', ?, ?, ?)",
                  [(d.date(), 100.0, 100.0 * (0.97 if i < 170 else 1.0))
                   for i, d in enumerate(days)])
    assert op.trailing_dividend_yield(c, "D") == pytest.approx(1 / 0.97 - 1, rel=1e-6)
    assert op.trailing_dividend_yield(c, "NONE") == 0.0


def test_percentile_needs_a_cross_section():
    assert op.percentile(0.02, pd.Series([0.01] * 10)) is None
    assert op.percentile(0.02, pd.Series(np.linspace(0, 0.04, 41))) == pytest.approx(21 / 41)


def test_a_new_snapshot_changes_the_cache_key(tmp_path):
    """F12g: the app cached the snapshot by day for an hour, so a file written by the
    snapshot script after the first look that day stayed unseen."""
    import os
    from pathlib import Path

    folder = tmp_path / "options"
    assert op.snapshot_stamp(folder) is None
    folder.mkdir()
    first = folder / "2026-10-02.parquet"
    first.write_bytes(b"x")
    before = op.snapshot_stamp(folder)
    assert before[0] == "2026-10-02.parquet"
    os.utime(first, ns=(first.stat().st_atime_ns, first.stat().st_mtime_ns + 10**9))
    rewritten = op.snapshot_stamp(folder)
    assert rewritten != before                         # the same day written again
    (folder / "2026-10-03.parquet").write_bytes(b"y")
    assert op.snapshot_stamp(folder)[0] == "2026-10-03.parquet"
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "_option_snapshot(dt.date.today(),\n" in source
    assert "op.snapshot_stamp(cfg.CACHE_DIR" in source
