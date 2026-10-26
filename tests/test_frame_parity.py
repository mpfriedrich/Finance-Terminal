"""The fast frame (FreeSource.screening_frame, SQL) and the reference frame
(base.build_screening_frame) on one small hand-made store: every column and every
exclusion reason must agree (rule 5).

The older parity test in test_screener_contract.py needs the real store and compares four
columns; it never saw that the reference frame had no share volume, so the share-count
sanity check ran in one builder only (audit 24.10.2026 F7). This one runs everywhere.
"""
import datetime as dt

import duckdb
import numpy as np
import pandas as pd
import pytest

from portfolio_lab.data.base import build_screening_frame
from portfolio_lab.data.free import FreeSource
from portfolio_lab.data.schema import MARKET_MIGRATIONS, _apply
from portfolio_lab.screen.engine import run_screen
from portfolio_lab.screen.spec import Filter, PortfolioSpec

ASOF = dt.date(2026, 6, 30)
DAYS = pd.bdate_range("2026-01-02", ASOF).date
FRESH = dt.date(2026, 5, 10)


def store() -> FreeSource:
    """Three US filers at $20, all with a share count filed in May 2026:

      GOOD  50m shares, 2m shares a day, FY2025 earnings filed Feb 2026.
      TINY  100,000 shares but 1m shares traded every day: ten times its whole share
            count a day, so the count is wrong (in practice tagged in thousands).
      OLDE  50m shares, but its newest annual earnings are FY2019, filed Feb 2020.
    """
    c = duckdb.connect(":memory:")
    _apply(c, MARKET_MIGRATIONS)
    c.executemany(
        "INSERT INTO securities (cik, ticker, name, sector, sic, exchange) "
        "VALUES (?,?,?,?,?,?)",
        [(601, "GOOD", "Good Co", "Machinery", "3550", "NYSE"),
         (602, "TINY", "Tiny Co", "Machinery", "3550", "NYSE"),
         (603, "OLDE", "Olde Co", "Machinery", "3550", "NYSE")])
    facts = []
    for cik, shares in ((601, 50e6), (602, 100_000.0), (603, 50e6)):
        facts += [
            (cik, "shares_outstanding", shares, None, dt.date(2026, 3, 31), FRESH,
             f"s{cik}", "EntityCommonStockSharesOutstanding", 0, None, "10-Q"),
            (cik, "book_value", 500e6, None, dt.date(2026, 3, 31), FRESH,
             f"s{cik}", "StockholdersEquity", 0, "USD", "10-Q"),
        ]
    earnings = {601: (dt.date(2025, 1, 1), dt.date(2025, 12, 31), dt.date(2026, 2, 20)),
                602: (dt.date(2025, 1, 1), dt.date(2025, 12, 31), dt.date(2026, 2, 20)),
                603: (dt.date(2019, 1, 1), dt.date(2019, 12, 31), dt.date(2020, 2, 20))}
    for cik, (start, end, filed) in earnings.items():
        facts += [(cik, "net_income", 100e6, start, end, filed, f"k{cik}", "NetIncomeLoss",
                   0, "USD", "10-K"),
                  (cik, "revenue", 1e9, start, end, filed, f"k{cik}", "Revenues", 0, "USD",
                   "10-K")]
    c.executemany(
        "INSERT INTO fundamentals (cik, metric, value, period_start, period_end, filed, "
        "accession, xbrl_tag, tag_rank, currency, form) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        facts)
    rows = [(t, d, 20.0, 20.0, 20.0, 20.0, vol) for d in DAYS
            for t, vol in (("GOOD", 2_000_000), ("TINY", 1_000_000), ("OLDE", 2_000_000))]
    c.executemany("INSERT INTO prices (ticker, date, open, close, close_raw, close_adj, "
                  "volume) VALUES (?,?,?,?,?,?,?)", rows)
    return FreeSource(c)


def _frames():
    src = store()
    fast = src.screening_frame(ASOF).sort_values("ticker").reset_index(drop=True)
    ref = build_screening_frame(src, ASOF).sort_values("ticker").reset_index(drop=True)
    return fast, ref


def _same(a: pd.Series, b: pd.Series) -> bool:
    na, nb = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
    if (na.notna() == a.notna()).all() and (nb.notna() == b.notna()).all():
        return bool(np.allclose(na.astype(float), nb.astype(float), equal_nan=True,
                                rtol=1e-12, atol=0))
    return a.astype(object).where(a.notna(), None).tolist() == \
        b.astype(object).where(b.notna(), None).tolist()


SPECS = [
    PortfolioSpec(size=10, min_dollar_volume=0, min_trading_days_60=0),
    PortfolioSpec(size=10, min_dollar_volume=0, min_trading_days_60=0,
                  filters=(Filter("price_to_earnings", "<", 50),)),
]


def test_both_builders_give_every_column_with_the_same_values():
    fast, ref = _frames()
    assert set(fast.columns) == set(ref.columns), set(fast.columns) ^ set(ref.columns)
    assert list(fast.ticker) == list(ref.ticker)
    for column in fast.columns:
        assert _same(fast[column], ref[column]), column


@pytest.mark.parametrize("spec", SPECS, ids=["floors only", "with a P/E filter"])
def test_both_builders_exclude_the_same_names_for_the_same_reasons(spec):
    fast, ref = _frames()
    a, b = run_screen(fast, spec), run_screen(ref, spec)
    assert a.exclusions == b.exclusions
    assert sorted(a.ranked.ticker) == sorted(b.ranked.ticker)


def test_an_implausible_share_count_is_excluded_by_both_builders():
    """100,000 shares and 1m traded a day: the reference frame had no share volume, so
    only the fast frame excluded TINY (audit 24.10.2026 F7)."""
    spec = SPECS[0]
    for frame in _frames():
        r = run_screen(frame, spec)
        assert r.exclusions.get("share count fails the volume sanity check") == 1
        assert "TINY" not in set(r.ranked.ticker)


# --- F6: a TTM figure is aged on its own ----------------------------------------------

STALE_REASON = "earnings or other twelve-month figure older than 400 days"


def test_old_earnings_beside_a_fresh_share_count_give_no_pe_in_either_builder():
    """OLDE's share count is seven weeks old, so the filer looks current; its newest
    annual profit is from 2019. That profit used to become its P/E (audit 24.10.2026 F6,
    R14). Now it is NaN in both builders and the screen names the reason."""
    from portfolio_lab.screen.engine import derive_metrics

    for frame in _frames():
        rows = derive_metrics(frame).set_index("ticker")
        assert rows.at["OLDE", "data_age_days"] == (ASOF - FRESH).days
        assert pd.isna(rows.at["OLDE", "net_income_ttm"])
        assert pd.isna(rows.at["OLDE", "price_to_earnings"])
        assert rows.at["OLDE", "stale_ttm"] == "net_income_ttm,revenue_ttm"
        assert rows.at["GOOD", "price_to_earnings"] == pytest.approx(1e9 / 100e6)
        r = run_screen(frame, SPECS[1])
        assert r.exclusions.get(STALE_REASON) == 1
        assert "OLDE" not in set(r.ranked.ticker)


def test_a_stale_ttm_figure_only_excludes_where_the_screen_reads_it():
    """Ranked by market cap with no filters, OLDE's old profit is not used: it stays."""
    for frame in _frames():
        r = run_screen(frame, SPECS[0])
        assert STALE_REASON not in r.exclusions
        assert "OLDE" in set(r.ranked.ticker)


def test_the_long_facts_path_ages_ttm_figures_the_same_way():
    """The fixture-style sources hand over long facts with `*_ttm` metrics already;
    wide_from_long applies the same age limit (rule 5)."""
    from portfolio_lab.data.base import wide_from_long

    facts = pd.DataFrame([
        (1, "net_income_ttm", 5.0, dt.date(2019, 12, 31), dt.date(2020, 2, 20)),
        (1, "shares_outstanding", 10.0, dt.date(2026, 3, 31), FRESH),
        (2, "net_income_ttm", 7.0, dt.date(2025, 12, 31), dt.date(2026, 2, 20)),
        (2, "shares_outstanding", 10.0, dt.date(2026, 3, 31), FRESH),
    ], columns=["cik", "metric", "value", "period_end", "filed"])
    wide = wide_from_long(facts, ASOF).set_index("cik")
    assert pd.isna(wide.at[1, "net_income_ttm"]) and wide.at[1, "stale_ttm"] == \
        "net_income_ttm"
    assert wide.at[2, "net_income_ttm"] == 7.0 and pd.isna(wide.at[2, "stale_ttm"])
    assert wide.at[1, "data_age_days"] == (ASOF - FRESH).days
