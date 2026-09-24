"""Point-in-time assembly of fundamentals.

Answers one question: what did a filer's numbers look like to someone standing on date X,
knowing only what had been filed by then?

Three rules, each of which is a way backtests normally cheat:

1. `filed <= asof`. No fact is visible before its filing date.
2. Earliest filing of a period wins. A 2024 restatement of 2023 was not knowable in 2023.
3. Trailing-twelve-months is RECONSTRUCTED from spans, never summed naively. XBRL mixes
   quarterly, year-to-date and annual spans in the same filing, so adding the rows up
   double-counts. Every TTM figure records which method produced it.
"""

from __future__ import annotations

import datetime as dt

import duckdb
import pandas as pd

DURATION_METRICS = ("revenue", "net_income", "operating_cash_flow", "capex")
# Balance-sheet items: a value at one moment, not over a span. Share counts are handled
# separately by share_counts(), which has to cope with both shapes and with filers who
# tag counts in thousands.
INSTANT_METRICS = (
    "book_value",
    "total_assets",
    "total_liabilities",
    "long_term_debt",
    "current_debt",
    "cash",
    "current_assets",
    "current_liabilities",
)

ANNUAL_MIN, ANNUAL_MAX = 330, 400
QUARTER_MIN, QUARTER_MAX = 80, 100


# A period is identified by its END and its LENGTH CLASS, not by its exact start. Filers
# restate a period with a start moved by a day or two; partitioning on the exact start put
# each version in its own partition, and the restatement survived as "the earliest filing
# of its own period" (32,463 groups in the real data, PLAN.md Gegenprüfung 25.09.2026).
# Partitioning on the end alone would merge a quarter with the nine-month or annual span
# ending the same day, so the length is bucketed: the two classes TTM reads get their
# tolerance windows, anything else rounds to whole months.
_SPAN_BUCKET = f"""
    CASE
        WHEN period_start IS NULL THEN NULL
        WHEN date_diff('day', period_start, period_end) BETWEEN {ANNUAL_MIN} AND {ANNUAL_MAX} THEN 12
        WHEN date_diff('day', period_start, period_end) BETWEEN {QUARTER_MIN} AND {QUARTER_MAX} THEN 3
        ELSE CAST(round(date_diff('day', period_start, period_end) / 30.44) AS INTEGER)
    END
"""


def _visible(con: duckdb.DuckDBPyConnection, asof: dt.date) -> pd.DataFrame:
    """All facts filed on or before `asof`, earliest filing per period kept."""
    return con.execute(
        f"""
        WITH visible AS (
            SELECT cik, metric, value, period_start, period_end, filed, tag_rank,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik, metric, period_end, {_SPAN_BUCKET}
                       ORDER BY filed ASC, tag_rank ASC, period_start ASC
                   ) AS rn
            FROM fundamentals
            WHERE filed <= ?
        )
        SELECT cik, metric, value, period_start, period_end, filed
        FROM visible WHERE rn = 1
        """,
        [asof],
    ).fetch_df()


# A listed company does not have 1 share. Filers do tag literal 0 or 1 into
# CommonStockSharesOutstanding (seen in the real data on 2026-09-21: Boost Run tags 1,
# Ethos Technologies tags 0 while its true diluted count is ~65.6m). Taken at face value
# these produce a market cap of a few dollars and a price-to-book near zero — which sorts
# the most broken filings straight to the top of a cheapness screen. Reject, then fall
# through to the next candidate tag.
MIN_PLAUSIBLE_SHARES = 1_000

# How far back a higher-priority tag may reach before recency wins instead.
TAG_PREFERENCE_WINDOW_DAYS = 400

# A fact this old cannot describe the company today. Enforced PER METRIC, not just per
# filer: on 2026-09-21 Warner Music's only instant share counts were from 2019/2020 —
# pre-IPO figures that were still "the newest available" for that one metric, and were
# being multiplied by a 2026 price. A company can file quarterly and still have one
# metric frozen years in the past.
MAX_FACT_AGE_DAYS = 400


def share_counts(con: duckdb.DuckDBPyConnection, asof: dt.date) -> pd.DataFrame:
    """Shares outstanding, from instant OR duration facts.

    Both are admitted because neither alone covers the market: Simon Property Group tags
    no instant share count at all, only a weighted-average diluted figure (a duration
    fact). Preferring the point-in-time tag where it exists and falling back to the
    average is better than having no market cap for a $60bn REIT.

    Scale caveat: some filers tag share counts in THOUSANDS. Suja Life tagged both scales
    in one filing (23,789 and 23,788,700). Where a filer gives both, the larger is the
    real count, so ties within a period resolve to the larger value. Where a filer gives
    only the scaled figure (Vishay Precision, Tempus AI), nothing here can tell — that is
    caught downstream by the volume plausibility check, which EXCLUDES and counts the name
    rather than multiplying by a guessed factor.
    """
    df = con.execute(
        """
        WITH candidates AS (
            -- The age limit is applied AFTER the first filing is found, never before:
            -- filtering first let an amendment become the "first" filing of its
            -- period once the original aged out (R13, third review).
            SELECT * FROM (
                SELECT cik, value, period_end, filed, tag_rank,
                       MIN(filed) OVER (PARTITION BY cik, period_end) AS first_filed
                FROM fundamentals
                WHERE filed <= ? AND metric = 'shares_outstanding'
                  AND value IS NOT NULL AND value >= ?
            ) WHERE date_diff('day', filed, ?) <= ?
        ),
        visible AS (
            -- Only the first filing of a period may speak for it, WHATEVER tag a later
            -- amendment uses (R13). Earliest-per-tag let a 10-K/A under a better tag
            -- replace the original count. The floor above is applied first on purpose:
            -- an original that tagged an impossible count (0, 1) leaves the period to
            -- the next filing rather than leaving the company without a share count.
            SELECT cik, value, period_end, filed, tag_rank,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik, period_end, tag_rank
                       ORDER BY value DESC
                   ) AS rn
            FROM candidates WHERE filed = first_filed
        ),
        best AS (
            SELECT cik, value, period_end, filed,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik
                       ORDER BY filed DESC, tag_rank ASC, period_end DESC
                   ) AS rn2
            FROM visible WHERE rn = 1
        )
        SELECT cik, 'shares_outstanding' AS metric, value, period_end, filed
        FROM best WHERE rn2 = 1
        """,
        [asof, MIN_PLAUSIBLE_SHARES, asof, MAX_FACT_AGE_DAYS],
    ).fetch_df()
    if df.empty:
        return df
    df["age_days"] = (pd.Timestamp(asof) - pd.to_datetime(df["filed"])).dt.days
    return df


def instant_values(con: duckdb.DuckDBPyConnection, asof: dt.date) -> pd.DataFrame:
    """Latest balance-sheet style value per filer, with its age.

    Returns: cik, metric, value, period_end, filed, age_days.

    Selection order matters and was corrected after real data proved it wrong: within a
    recency window, the higher-priority TAG wins first, and only then the later period.
    Picking the latest period first lets a zero from a lower-priority tag beat a correct
    figure from a better one.
    """
    df = con.execute(
        """
        WITH candidates AS (
            -- First filing over EVERYTHING visible, then the age limit; see share_counts.
            SELECT * FROM (
                SELECT cik, metric, value, period_end, filed, tag_rank,
                       MIN(filed) OVER (PARTITION BY cik, metric, period_end) AS first_date
                FROM fundamentals
                WHERE filed <= ? AND metric IN (SELECT UNNEST(?))
                  AND period_start IS NULL
                  AND value IS NOT NULL
            ) WHERE date_diff('day', filed, ?) <= ?
        ),
        visible AS (
            -- The first filing of a period speaks for it; the tag preference below only
            -- chooses among the tags THAT filing used. Ranking tags across filings let
            -- an amendment under a better tag displace the original (R13).
            SELECT cik, metric, value, period_end, filed, tag_rank,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik, metric, period_end, tag_rank
                       ORDER BY filed ASC
                   ) AS rn
            FROM candidates WHERE filed = first_date
        ),
        first_filed AS (SELECT * FROM visible WHERE rn = 1),
        newest AS (
            SELECT *, MAX(period_end) OVER (PARTITION BY cik, metric) AS newest_period
            FROM first_filed
        ),
        recent AS (
            SELECT * FROM newest
            WHERE date_diff('day', period_end, newest_period) <= ?
        ),
        best AS (
            SELECT cik, metric, value, period_end, filed,
                   ROW_NUMBER() OVER (
                       PARTITION BY cik, metric
                       ORDER BY tag_rank ASC, period_end DESC, filed ASC
                   ) AS rn2
            FROM recent
        )
        SELECT cik, metric, value, period_end, filed FROM best WHERE rn2 = 1
        """,
        [asof, list(INSTANT_METRICS), asof, MAX_FACT_AGE_DAYS, TAG_PREFERENCE_WINDOW_DAYS],
    ).fetch_df()
    if df.empty:
        return df
    df["age_days"] = (pd.Timestamp(asof) - pd.to_datetime(df["filed"])).dt.days
    return df


def ttm_values(con: duckdb.DuckDBPyConnection, asof: dt.date) -> pd.DataFrame:
    """Trailing-twelve-months figures for span metrics.

    Method, in order of preference, recorded per row in `method`:
      "annual"     — a single ~365-day span. Cleanest, used whenever one is recent enough.
      "quarters"   — four contiguous ~90-day spans summed.
    A filer that yields neither is absent from the result and will be excluded from any
    screen that needs the metric — counted, never silently dropped.
    """
    df = _visible(con, asof)
    if df.empty:
        return df
    df = df[df.metric.isin(DURATION_METRICS) & df.period_start.notna()].copy()
    if df.empty:
        return df

    df["period_start"] = pd.to_datetime(df["period_start"])
    df["period_end"] = pd.to_datetime(df["period_end"])
    df["span"] = (df.period_end - df.period_start).dt.days

    annual = df[df.span.between(ANNUAL_MIN, ANNUAL_MAX)].copy()
    annual = annual.sort_values("period_end").groupby(["cik", "metric"], as_index=False).last()
    annual["method"] = "annual"

    quarters = df[df.span.between(QUARTER_MIN, QUARTER_MAX)].copy()
    rows: list[dict] = []
    if not quarters.empty:
        quarters = quarters.sort_values(["cik", "metric", "period_end"])
        for (cik, metric), grp in quarters.groupby(["cik", "metric"], sort=False):
            grp = grp.drop_duplicates(subset=["period_end"], keep="last").tail(4)
            if len(grp) < 4:
                continue
            # Contiguity: each quarter must start within a few days of the previous end.
            gaps = (
                grp.period_start.iloc[1:].to_numpy()
                - grp.period_end.iloc[:-1].to_numpy()
            )
            if any(abs(pd.Timedelta(g).days) > 10 for g in gaps):
                continue
            rows.append(
                {
                    "cik": cik,
                    "metric": metric,
                    "value": float(grp.value.sum()),
                    "period_end": grp.period_end.iloc[-1],
                    "filed": grp.filed.max(),
                    "method": "quarters",
                }
            )
    quarterly = pd.DataFrame(rows)

    cols = ["cik", "metric", "value", "period_end", "filed", "method"]
    annual = annual[cols]
    if quarterly.empty:
        out = annual
    else:
        # Prefer whichever ends later — a fresh four-quarter roll beats a stale annual.
        out = (
            pd.concat([annual, quarterly[cols]], ignore_index=True)
            .sort_values(["cik", "metric", "period_end"])
            .groupby(["cik", "metric"], as_index=False)
            .last()
        )
    out["age_days"] = (pd.Timestamp(asof) - pd.to_datetime(out["filed"])).dt.days
    return out.reset_index(drop=True)


def fundamentals_wide(con: duckdb.DuckDBPyConnection, asof: dt.date) -> pd.DataFrame:
    """One row per filer: book_value, shares_outstanding, net_income_ttm, revenue_ttm,
    operating_cash_flow_ttm, plus the age of the oldest input used."""
    inst = instant_values(con, asof)
    shares = share_counts(con, asof)
    ttm = ttm_values(con, asof)
    frames = []
    if not inst.empty:
        frames.append(inst.assign(field=inst.metric))
    if not shares.empty:
        frames.append(shares.assign(field=shares.metric))
    if not ttm.empty:
        frames.append(ttm.assign(field=ttm.metric + "_ttm"))
    if not frames:
        return pd.DataFrame()
    long = pd.concat(frames, ignore_index=True)
    wide = long.pivot_table(index="cik", columns="field", values="value", aggfunc="last")
    # MIN, not max: the rule is "has this company reported RECENTLY", so the most recent
    # filing decides. Using the oldest metric's age instead marked 3,310 of 6,041 active
    # companies stale on 2026-09-21, because one slow-moving annual figure dragged the
    # whole filer down.
    ages = long.groupby("cik")["age_days"].min().rename("data_age_days")
    methods = (
        ttm.assign(field=ttm.metric + "_method")
        .pivot_table(index="cik", columns="field", values="method", aggfunc="last")
        if not ttm.empty
        else pd.DataFrame()
    )
    out = wide.join(ages)
    if not methods.empty:
        out = out.join(methods)
    return out.reset_index()
