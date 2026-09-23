"""The briefing. What matters is what the model is ALLOWED to see."""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab import briefing as bf
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply


@pytest.fixture()
def con():
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    dates = pd.bdate_range("2026-01-05", periods=120).date
    rows = []
    rng = np.random.default_rng(7)
    # CALM has ordinary daily noise; JUMPY ends with a violent day. FROZEN barely moves
    # at all, which is the case that used to break the detector.
    calm = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(dates)))
    for i, d in enumerate(dates):
        rows.append(("CALM", d, 100.0, calm[i], calm[i], 1000))
        frozen = 10.0 if i < len(dates) - 1 else 10.001
        rows.append(("FROZEN", d, 10.0, frozen, frozen, 1000))
        price = 50 + 0.5 * np.sin(i / 5)
        if i == len(dates) - 1:
            price = 40.0
        rows.append(("JUMPY", d, 50.0, price, price, 1000))
    c.executemany(
        "INSERT INTO prices (ticker, date, open, close, close_adj, volume) VALUES (?,?,?,?,?,?)",
        rows,
    )
    return c


def test_an_unusual_move_is_found_and_scored(con):
    out = bf.unusual_moves(con, ["CALM", "JUMPY"])
    top = out.iloc[0]
    assert top.ticker == "JUMPY"
    assert top.z < -bf.UNUSUAL_SIGMA
    assert top.move < -0.15


def test_a_quiet_share_does_not_register_as_news(con):
    out = bf.unusual_moves(con, ["CALM"])
    assert out.empty or abs(out.iloc[0].z) < bf.UNUSUAL_SIGMA


def test_a_frozen_price_cannot_produce_a_giant_z_score(con):
    """Regression. A share that has not moved for months has a standard deviation near
    zero, so a rounding-sized wobble scored hundreds of sigma and topped the briefing.
    A series with no normal has no abnormal."""
    out = bf.unusual_moves(con, ["FROZEN"])
    assert out.empty


def test_a_statistically_odd_but_tiny_move_is_not_notable(con):
    """Both thresholds have to hold: strange for that share AND big enough to matter."""
    bundle = bf.gather(con, ["FROZEN", "CALM", "JUMPY"])
    assert "FROZEN" not in bundle["notable"].get("ticker", pd.Series(dtype=object)).tolist()


def test_the_same_move_scores_differently_for_two_different_shares(con):
    """The property the whole detector rests on: a 4% day is routine for a small biotech
    and extraordinary for a utility, so the score has to be relative to each company's
    own history rather than to a fixed percentage."""
    out = bf.unusual_moves(con, ["CALM", "JUMPY"]).set_index("ticker")
    quiet, lively = sorted(out.typical_day.tolist())
    assert quiet < lively
    # Give both the identical 5% day and the scores must differ.
    scores = [0.05 / t for t in (quiet, lively)]
    assert scores[0] > scores[1] * 1.5


def test_the_evidence_block_contains_only_gathered_facts(con):
    bundle = bf.gather(con, ["CALM", "JUMPY"])
    bundle["news"] = pd.DataFrame([{
        "when": "2026-06-18", "title": "JUMPY cuts guidance", "summary": "",
        "source": "Reuters", "url": "u", "tickers": "JUMPY", "n_tickers": 1,
    }])
    text = bf._evidence(bundle)
    assert "JUMPY" in text and "JUMPY cuts guidance" in text
    assert "[1]" in text                      # headlines are numbered for citation
    assert "z = " in text                     # moves carry their score


def test_a_company_not_followed_never_reaches_the_evidence(con):
    """The guarantee: the model can only write about what was gathered, and nothing
    gathers a name the user does not follow."""
    text = bf._evidence(bf.gather(con, ["CALM"]))
    assert "JUMPY" not in text


def test_the_system_prompt_forbids_adding_facts_and_recommendations():
    lowered = bf.SYSTEM.lower()
    assert "may not add a fact" in lowered
    assert "never recommend" in lowered
    assert "never predict a price" in lowered
