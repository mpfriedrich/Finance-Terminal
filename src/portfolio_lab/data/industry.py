"""Investor-facing industry groups, derived from the filer's own SIC code.

`securities.sector` holds the SEC's own wording: 389 distinct phrases like
"Services-Prepackaged Software", "Pharmaceutical Preparations", "State Commercial Banks"
and "National Commercial Banks". That vocabulary is precise and useless to type. Nobody
asks for "Services-Prepackaged Software"; they ask for tech.

This module maps the numeric SIC code to ~13 groups of the kind an investor actually
reasons in. Three things are deliberate:

  * The mapping is DATA, not code — a table of ranges, first match wins. Regrouping is
    editing a list, which someone can argue with, not rewriting logic.
  * The SEC wording survives next to the group everywhere it is shown. The group is a
    convenience; the filer's own classification stays visible, because that is the thing
    that can be checked against the filing.
  * Nothing lands in a group by accident. A code that matches no range becomes "Other",
    and a company with no SIC at all becomes "Unclassified" — both are counted and shown,
    never quietly folded into a neighbour.

Judgement calls worth knowing about, because they move real companies:

  8731  commercial physical & biological research  -> Health Care, not Business Services.
        Pre-revenue biotech routinely registers here; leaving it in services would hide
        a large part of the sector from a health-care screen.
  3841-3851  surgical, medical and ophthalmic instruments -> Health Care, while the rest
        of major group 38 (measuring and control instruments) stays Industrials.
  2840-2844  soap, detergents, cosmetics -> Consumer Staples, not Materials, although
        SIC files them under chemicals with the industrial ones.
  3710-3716  motor vehicles and bodies -> Consumer Discretionary; the rest of major
        group 37 (aircraft, rail, ships, parts) stays Industrials.
  6798  REITs -> Real Estate. They are kept in the universe on purpose (see sic.py);
        grouping them with banks would make a real-estate screen meaningless.

Funds, commodity trusts and blank-cheque shells never reach this module — `sic.py`
removes them from the universe before a group is ever assigned.
"""

from __future__ import annotations

import pandas as pd

UNCLASSIFIED = "Unclassified"
OTHER = "Other"

# (first SIC, last SIC, group). Checked in order, first match wins — so a narrow range
# must come before the wider one it sits inside.
RANGES: tuple[tuple[int, int, str], ...] = (
    # -- Health Care ---------------------------------------------------------------
    (2833, 2836, "Health Care"),            # medicinal chemicals, pharma, biologicals
    (3841, 3851, "Health Care"),            # surgical, medical, dental, ophthalmic
    (8000, 8099, "Health Care"),            # hospitals, clinics, care providers
    (8731, 8731, "Health Care"),            # contract research — where biotech registers
    # -- Technology ----------------------------------------------------------------
    (3570, 3579, "Technology"),             # computers and peripherals
    (3600, 3699, "Technology"),             # electronics, semiconductors, components
    (7370, 7379, "Technology"),             # software, data processing, IT services
    # -- Communication & Media ------------------------------------------------------
    (2700, 2799, "Communication & Media"),  # publishing and printing
    (4800, 4899, "Communication & Media"),  # telecom, broadcasting, cable
    (7310, 7319, "Communication & Media"),  # advertising
    (7812, 7841, "Communication & Media"),  # motion pictures, video
    # -- Financials -----------------------------------------------------------------
    (6000, 6299, "Financials"),             # banks, savings institutions, brokers
    (6300, 6499, "Financials"),             # insurance carriers and agents
    (6798, 6798, "Real Estate"),            # REITs — before the 67xx holding range
    (6500, 6599, "Real Estate"),            # operators, agents, developers
    (6700, 6799, "Financials"),             # holding and investment offices
    # -- Energy ---------------------------------------------------------------------
    (1200, 1299, "Energy"),                 # coal mining
    (1300, 1399, "Energy"),                 # oil and gas extraction and services
    (2911, 2911, "Energy"),                 # petroleum refining
    (2990, 2999, "Energy"),                 # other petroleum and coal products
    (5171, 5172, "Energy"),                 # petroleum wholesale
    # -- Utilities ------------------------------------------------------------------
    (4900, 4999, "Utilities"),
    # -- Transport & Logistics ------------------------------------------------------
    (4000, 4789, "Transport & Logistics"),
    # -- Consumer Staples -----------------------------------------------------------
    (100, 999, "Consumer Staples"),         # farming, fishing, forestry — food producers
    (2000, 2199, "Consumer Staples"),       # food, beverages, tobacco
    (2840, 2844, "Consumer Staples"),       # soap, cosmetics, household products
    (5400, 5499, "Consumer Staples"),       # grocery stores
    (5912, 5912, "Consumer Staples"),       # drug stores
    # -- Materials ------------------------------------------------------------------
    (1000, 1099, "Materials"),              # metal mining
    (1400, 1499, "Materials"),              # nonmetallic minerals
    (2600, 2699, "Materials"),              # paper and pulp
    (2800, 2829, "Materials"),              # industrial chemicals
    (2850, 2899, "Materials"),              # paints, agricultural and other chemicals
    (3200, 3299, "Materials"),              # stone, clay, glass, concrete
    (3300, 3399, "Materials"),              # primary metals
    # -- Consumer Discretionary -----------------------------------------------------
    (2200, 2399, "Consumer Discretionary"), # textiles and apparel
    (2500, 2599, "Consumer Discretionary"), # furniture
    (3710, 3716, "Consumer Discretionary"), # motor vehicles and bodies
    (3900, 3999, "Consumer Discretionary"), # jewellery, toys, sporting goods
    (5200, 5399, "Consumer Discretionary"), # building supply, general merchandise
    (5500, 5799, "Consumer Discretionary"), # auto dealers, apparel, furniture retail
    (5800, 5899, "Consumer Discretionary"), # eating and drinking places
    (5900, 5999, "Consumer Discretionary"), # other retail
    (7000, 7099, "Consumer Discretionary"), # hotels and lodging
    (7900, 7999, "Consumer Discretionary"), # amusement and recreation
    # -- Industrials ----------------------------------------------------------------
    (1500, 1799, "Industrials"),            # construction and contractors
    (2400, 2499, "Industrials"),            # lumber and wood
    (3000, 3199, "Industrials"),            # rubber, plastics, leather
    (3400, 3569, "Industrials"),            # fabricated metal, machinery
    (3580, 3599, "Industrials"),            # service and general industrial machinery
    (3700, 3799, "Industrials"),            # aircraft, rail, ships, vehicle parts
    (3800, 3840, "Industrials"),            # measuring and control instruments
    (3860, 3899, "Industrials"),            # photographic, watches, other manufacturing
    (5000, 5099, "Industrials"),            # durable goods wholesale
    (8711, 8711, "Industrials"),            # engineering services
    # -- Business Services ----------------------------------------------------------
    (7200, 7299, "Business Services"),      # personal services
    (7300, 7389, "Business Services"),      # the rest of services, after tech and ads
    (7500, 7699, "Business Services"),      # repair services
    (8100, 8299, "Business Services"),      # legal, education
    (8300, 8399, "Business Services"),      # social services
    (8700, 8799, "Business Services"),      # accounting, management, research
    (5100, 5199, "Business Services"),      # nondurable goods wholesale
)

# Shown in this order in the UI: the groups people reach for first, then the residuals.
GROUPS: tuple[str, ...] = (
    "Technology",
    "Health Care",
    "Financials",
    "Real Estate",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials",
    "Materials",
    "Energy",
    "Utilities",
    "Communication & Media",
    "Transport & Logistics",
    "Business Services",
    OTHER,
    UNCLASSIFIED,
)


def group_for(sic: str | int | float | None) -> str:
    """Map one SIC code to its group.

    A missing or unparseable code is `Unclassified` — a fact about the data, not a
    company that happens to belong nowhere.
    """
    if sic is None or (isinstance(sic, float) and pd.isna(sic)):
        return UNCLASSIFIED
    text = str(sic).strip()
    if not text or text.lower() in {"nan", "none"}:
        return UNCLASSIFIED
    try:
        code = int(float(text))
    except ValueError:
        return UNCLASSIFIED
    for lo, hi, group in RANGES:
        if lo <= code <= hi:
            return group
    return OTHER


def add_industry_group(frame: pd.DataFrame, sic_column: str = "sic") -> pd.DataFrame:
    """Add an `industry_group` column derived from `sic_column`.

    Returns the frame unchanged apart from the new column. If the SIC column is absent
    every row becomes `Unclassified`, so a screen on a frame built by an older source
    still runs — it just cannot filter by industry, which the screener then says.
    """
    out = frame.copy()
    if sic_column not in out.columns:
        out["industry_group"] = UNCLASSIFIED
        return out
    out["industry_group"] = out[sic_column].map(group_for)
    return out
