"""How many home-market shares one US-listed share stands for (the ADR ratio).

A foreign private issuer reports its share count in ORDINARY shares — Toyota about 13
billion — while its US line trades as an ADR that may stand for ten of them. Multiplying
the ADR price by the ordinary count overstates the market cap tenfold and makes every
ratio built on it wrong. The ratio is not in the SEC data, so it is measured
(scripts/build_listing_ratios.py) and stored here; a foreign listing without a measured
ratio is excluded with its own reason rather than priced as if it were 1:1.

Domestic filers (10-K) are always 1:1: their listed share IS the share they count.
"""

from __future__ import annotations

import duckdb
import pandas as pd

FOREIGN_FORMS = ("20-F", "40-F")

# Ratios that depositary programmes actually use, as ordinary shares per listed share.
# A measurement is accepted only within SNAP_TOLERANCE of one of them: an odd number
# like 3.7 means the two share counts were taken at different dates or in different
# units, not that the ratio is 3.7.
COMMON_RATIOS = (1 / 100, 1 / 50, 1 / 20, 1 / 10, 1 / 5, 1 / 4, 1 / 3, 1 / 2, 1.0, 2.0,
                 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0,
                 50.0, 100.0)
SNAP_TOLERANCE = 0.06


def snap_ratio(raw: float | None) -> float | None:
    """The common ratio `raw` is a measurement of, or None if it is none of them."""
    if raw is None or not raw > 0:
        return None
    best = min(COMMON_RATIOS, key=lambda r: abs(raw / r - 1.0))
    return best if abs(raw / best - 1.0) <= SNAP_TOLERANCE else None


def stored_ratios(con: duckdb.DuckDBPyConnection | None) -> dict[str, float]:
    if con is None:
        return {}
    try:
        rows = con.execute(
            "SELECT ticker, ratio FROM listing_ratios WHERE ratio > 0").fetchall()
    except duckdb.Error:
        return {}
    return {str(t).upper(): float(r) for t, r in rows}


def apply(frame: pd.DataFrame, con: duckdb.DuckDBPyConnection | None) -> pd.DataFrame:
    """Put share counts on the basis of the listed share.

    Adds `listing_ratio` (1 for domestic filers) and `listing_ratio_unknown` (a foreign
    filer's listing with no measured ratio); divides `shares_outstanding` by the ratio.
    """
    if frame.empty or "foreign_filer" not in frame.columns:
        return frame
    out = frame.copy()
    foreign = out["foreign_filer"].fillna(False).astype(bool)
    measured = out["ticker"].astype(str).str.upper().map(stored_ratios(con))
    out["listing_ratio"] = measured.where(foreign, 1.0)
    out["listing_ratio_unknown"] = foreign & measured.isna()
    if "shares_outstanding" in out.columns:
        out["shares_outstanding"] = out["shares_outstanding"] / out["listing_ratio"]
    return out


# -- the market a line trades in ------------------------------------------------------------

# ISO 3166 alpha-3 codes Compustat Global uses for the listing country, with the names a
# person types. The US store's lines (SEC and CRSP) all trade in the United States.
COUNTRIES = {
    "USA": "United States", "AUS": "Australia", "AUT": "Austria", "BEL": "Belgium",
    "CHE": "Switzerland", "DEU": "Germany", "DNK": "Denmark", "ESP": "Spain",
    "FIN": "Finland", "FRA": "France", "GBR": "United Kingdom", "HKG": "Hong Kong",
    "IRL": "Ireland", "ISR": "Israel", "ITA": "Italy", "JPN": "Japan",
    "NLD": "Netherlands", "NOR": "Norway", "NZL": "New Zealand", "PRT": "Portugal",
    "SGP": "Singapore", "SWE": "Sweden", "CAN": "Canada", "CHN": "China",
    "KOR": "South Korea", "IND": "India", "BRA": "Brazil", "ZAF": "South Africa",
    "TWN": "Taiwan", "LUX": "Luxembourg", "GRC": "Greece", "POL": "Poland",
}
# Compustat Global's key range (data/wrds.py): its lines carry the country in `exchange`.
_GLOBAL_FIRST, _GLOBAL_LAST = 1_000_000_000, 1_999_999_999


def add_country(frame: pd.DataFrame) -> pd.DataFrame:
    """Add `country`: the ISO-3 code of the market the line trades in.

    The market, not the headquarters: a Japanese company's ADR in New York trades in the
    United States and is priced, costed and settled there, while its Tokyo line is JPN.
    Screening by country therefore picks the market the money would go to.
    """
    out = frame.copy()
    if out.empty or "cik" not in out.columns:
        out["country"] = pd.Series(dtype=object)
        return out
    cik = pd.to_numeric(out["cik"], errors="coerce")
    is_global = cik.between(_GLOBAL_FIRST, _GLOBAL_LAST)
    exchange = (out["exchange"].astype(str).str.upper() if "exchange" in out.columns
                else pd.Series("", index=out.index))
    out["country"] = exchange.where(is_global, "USA")
    return out
