"""Industry groups: the typeable version of the SEC's own classification."""
import numpy as np
import pandas as pd
import pytest

from portfolio_lab.data import industry
from portfolio_lab.data.industry import OTHER, UNCLASSIFIED, group_for


@pytest.mark.parametrize("sic,expected", [
    ("7372", "Technology"),               # prepackaged software
    ("3674", "Technology"),               # semiconductors
    ("2836", "Health Care"),              # biological products
    ("8731", "Health Care"),              # contract research — where biotech registers
    ("3841", "Health Care"),              # surgical instruments
    ("3826", "Industrials"),              # lab instruments: NOT health care
    ("6022", "Financials"),               # state commercial banks
    ("6798", "Real Estate"),              # REITs, ahead of the 67xx holding range
    ("6770", "Financials"),               # blank cheques, if one ever survives sic.py
    ("1311", "Energy"),                   # crude petroleum and natural gas
    ("1221", "Energy"),                   # coal
    ("2911", "Energy"),                   # refining, although SIC files it under chemicals
    ("2834", "Health Care"),              # pharma, although SIC files it under chemicals
    ("2844", "Consumer Staples"),         # cosmetics, although SIC files it under chemicals
    ("2810", "Materials"),                # industrial chemicals
    ("3711", "Consumer Discretionary"),   # motor vehicles
    ("3721", "Industrials"),              # aircraft: the rest of major group 37
    ("4911", "Utilities"),
    ("4512", "Transport & Logistics"),
    ("4813", "Communication & Media"),
    ("0100", "Consumer Staples"),         # crop production
])
def test_known_codes_land_where_an_investor_would_look(sic, expected):
    assert group_for(sic) == expected


@pytest.mark.parametrize("value", [None, "", "  ", np.nan, "n/a", "nan"])
def test_a_missing_code_is_unclassified_not_other(value):
    """'We do not know' and 'it fits no group' are different facts, and both are shown."""
    assert group_for(value) == UNCLASSIFIED


def test_an_unmapped_code_is_other():
    assert group_for("8880") == OTHER  # American depositary receipts


def test_numeric_codes_survive_a_dataframe_round_trip():
    """DuckDB hands back strings, pandas can hand back floats. Both have to work."""
    assert group_for(7372) == group_for("7372") == group_for(7372.0)


def test_ranges_do_not_overlap_except_where_a_narrow_one_comes_first():
    """First match wins, so any earlier range that contains a later one is a silent
    override. Every such pair has to be narrower than the range it precedes."""
    for i, (lo_a, hi_a, _) in enumerate(industry.RANGES):
        for lo_b, hi_b, _ in industry.RANGES[i + 1:]:
            overlaps = lo_a <= hi_b and lo_b <= hi_a
            if overlaps:
                assert (hi_a - lo_a) <= (hi_b - lo_b), (
                    f"{lo_a}-{hi_a} shadows the wider {lo_b}-{hi_b} but is not narrower"
                )


def test_every_range_maps_to_a_group_the_ui_offers():
    known = set(industry.GROUPS)
    assert {g for _, _, g in industry.RANGES} <= known


def test_add_industry_group_without_a_sic_column_still_screens():
    """An older source without SIC must not crash the screen; it filters nothing."""
    frame = pd.DataFrame({"ticker": ["AAA", "BBB"]})
    out = industry.add_industry_group(frame)
    assert list(out.industry_group) == [UNCLASSIFIED, UNCLASSIFIED]
    assert list(out.ticker) == ["AAA", "BBB"]
