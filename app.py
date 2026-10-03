"""Portfolio Lab — chat-driven stock screener with live paper tracking.

Run from VS Code (Run and Debug → "Portfolio Lab (app)") or:
    uv run streamlit run app.py

No orders are placed, no broker is connected, and nothing here is investment advice.
The app applies the criteria you give it to public filings and market data, and shows
what came out — including what it had to leave out.
"""

from __future__ import annotations

import datetime as dt
import html
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

# A Streamlit app is served, not executed. Pressing VS Code's plain "Run Python File"
# button runs `python app.py`, which either fails on a missing dependency (wrong
# interpreter) or starts, does nothing visible and exits. Say so instead.
try:
    import numpy as np
    import pandas as pd
    import streamlit as st
except ModuleNotFoundError as exc:
    sys.exit(
        f"\n{exc}\n\n"
        "This looks like the wrong Python. The project keeps its own environment.\n"
        "Start the app with:\n"
        "    cd ~/portfolio-lab && uv run streamlit run app.py\n"
        "or in VS Code: open the FOLDER ~/portfolio-lab, then\n"
        "Run and Debug -> 'Portfolio Lab (app)'. The plain Run button will not work.\n"
    )

if not st.runtime.exists():
    sys.exit(
        "\nPortfolio Lab is a web app; it has to be served, not executed.\n"
        "    cd ~/portfolio-lab && uv run streamlit run app.py\n"
        "In VS Code: Run and Debug -> 'Portfolio Lab (app)', or press Shift-Cmd-B.\n"
    )

import plotly.graph_objects as go

from portfolio_lab import charts, ui
from portfolio_lab import appcache as ac
from portfolio_lab import command, layout
from portfolio_lab import config as cfg
from portfolio_lab.config import CostModel, Settings, secret
from portfolio_lab.data import history as data_history, industry
from portfolio_lab.data.listing import COUNTRIES as MARKET_NAMES
from portfolio_lab.data.paid import MissingKey
from portfolio_lab.data.schema import open_user
from portfolio_lab.data.sources import SourceNotReady, open_selected, per_thread
from portfolio_lab.data.wrds import WorldSource, is_compustat
from portfolio_lab.screen import translate as T
from portfolio_lab.screen.engine import derive_metrics, merge_selection, run_screen
from portfolio_lab.screen.spec import Filter, spec_to_json
from portfolio_lab.screen.valuation import Assumptions, add_valuation

from portfolio_lab import backtest as bktest
from portfolio_lab import holdout
from portfolio_lab import sensitivity
from portfolio_lab import stage_b as bstage
from portfolio_lab.pit_cache import FrameCache
from portfolio_lab import coverage as cov
from portfolio_lab import briefing as bfg
from portfolio_lab import business as bz
from portfolio_lab import macro as mc
from portfolio_lab import metrics as mx
from portfolio_lab import numeraire as nm
from portfolio_lab.lab import engine as lab_engine
from portfolio_lab.lab import signals as lab_signals
from portfolio_lab.lab import translate as LT
from portfolio_lab.lab.spec import StrategySpec
from portfolio_lab import options as op
from portfolio_lab import strategy as stg
from portfolio_lab import research as rs
from portfolio_lab.store import Store
from portfolio_lab.track import performance
from portfolio_lab import alerts, comps, deals, export, fred, fts, insiders, paper, risk, world
from portfolio_lab import ais, aviation, shipping
from portfolio_lab import wacc as W
from portfolio_lab import summary as ai_summary
from portfolio_lab import volforecast as vfc

# The sidebar starts closed: it holds the portfolio list, the watchlist and the unit,
# and the screen's filters moved into SCREEN (02.10.2026). » at the top left opens it.
st.set_page_config(page_title="Portfolio Lab", page_icon="◫", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(ui.CSS, unsafe_allow_html=True)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# Streamlit re-runs this file on every interaction; cached resources survive that, so the
# database connections are opened once per session instead of once per click. The paths
# are read from `config` at call time (not bound at import), so a test can point the app
# at a store built from fixtures.
@st.cache_resource
def _market():
    # Read-only: the app never writes market data, and holding the write lock would stop
    # every build script from running while the app is open.
    # Which store is opened is a setting (Phase 10): the free one by default, the paid
    # Sharadar store with PORTFOLIO_LAB_SOURCE=paid.
    source, path = open_selected(Settings(), read_only=True, market_db=cfg.MARKET_DB,
                                 paid_db=cfg.PAID_DB, world_db=cfg.WORLD_DB)
    return source, path, path.stat().st_ino


def _market_source():
    """The market connection, reopened if the file underneath it was replaced.

    A price rebuild that ran while this app was open swaps a freshly built file into
    place. The open connection keeps serving the OLD file — on Unix an open handle
    follows the inode, not the name — so without this check the app would show yesterday
    prices indefinitely and give no sign that anything had changed. Comparing the inode
    is how you notice a file was replaced rather than edited.

    Every cached answer was computed from the old file, so all of them go, not just the
    screening frame: until 02.10.2026 the market list, the industry vocabulary, the
    survivorship verdict and the options snapshot stayed stale for up to an hour.
    """
    source, path, opened_inode = _market()
    try:
        if path.stat().st_ino != opened_inode:
            # Close the old connection first. DuckDB hands a new connect() to the same
            # path the database that is still open in this process, i.e. the REPLACED
            # file: measured 02.10.2026, the reopen saw the old rows until the old
            # connection was closed. So the swap check never showed the new build.
            source.con.close()
            _market.clear()
            st.cache_data.clear()
            st.rerun()
    except OSError:
        pass  # Mid-swap. The next rerun will see the new file.
    return per_thread(source)


@st.cache_resource
def _shared_store():
    return Store(open_user(cfg.USER_DB))


def _store():
    """The portfolio store with this thread's own cursor (see sources.per_thread)."""
    return per_thread(_shared_store())


def _guarded(label: str, view, *args, **kwargs) -> None:
    """Draw one view; if it fails, say so in its own place and carry on.

    Before 02.10.2026 an exception (or the `st.stop()` for a missing portfolio) ended the
    whole script, so a broken option chain or an empty store took every tab after it
    down too. The message names the error type and text without addresses (R24)."""
    try:
        view(*args, **kwargs)
    except Exception as exc:                                        # noqa: BLE001
        st.error(f"{label} could not be drawn — {ui.error_text(exc)}. "
                 "The other views are unaffected.")


def _keep(name: str, default):
    """The persisted value of a setting whose widget may not be drawn on this run.

    Tabs render lazily, and Streamlit forgets a widget's state when it is not drawn. So
    a widget writes its value to its own session key through `_kept` and is seeded from
    it again, and every reader asks `_keep` rather than the widget."""
    return st.session_state.get(name, default)


def _persist(name: str) -> None:
    st.session_state[name] = st.session_state[f"w_{name}"]


def _kept(name: str, default, field: str = "value") -> dict:
    """Keyword arguments that tie a widget to the persisted setting `name`.

    The seed value is passed only when the widget has no state of its own yet; passing
    it as well makes Streamlit warn that the value was set twice."""
    kwargs = {"key": f"w_{name}", "on_change": _persist, "args": (name,)}
    if f"w_{name}" not in st.session_state:
        kwargs[field] = _keep(name, default)
    return kwargs


@st.cache_data(ttl=600)
def _frame(asof: dt.date, key: str) -> pd.DataFrame:
    del key
    return _market_source().screening_frame(asof)


@st.cache_data(ttl=3600, show_spinner=False)
def _option_snapshot(day: dt.date) -> pd.DataFrame | None:
    """The newest daily options snapshot (scripts/snapshot_options.py), if it is at most
    four days old; the cross-section the put-call spread is ranked against."""
    folder = cfg.CACHE_DIR / "options"
    files = sorted(folder.glob("*.parquet")) if folder.exists() else []
    if not files:
        return None
    taken = dt.date.fromisoformat(files[-1].stem)
    if (day - taken).days > 4:
        return None
    frame = pd.read_parquet(files[-1])
    frame.attrs["day"] = taken
    return frame if frame.smile_slope.notna().sum() >= 20 else None


@st.cache_data(ttl=600, show_spinner=False)
def _price_stats(ticker: str, asof: dt.date, key: str) -> dict:
    """research.price_stats once per ticker and data build. RESEARCH, the WACC beta and
    OPTIONS each asked for it on every rerun, each time reading the full price history
    and the benchmark again (audit 02.10.2026). The build key is part of the cache key,
    so a price update is never served from yesterday's entry."""
    del asof, key
    return rs.price_stats(_market_source().con, ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _vol_model(asof: dt.date, key: str) -> "vfc.Model | None":
    """The pooled Log-HAR fit (volforecast.py) on data up to `asof`: ~1 s, once a day."""
    del key
    return vfc.fit_from_store(_market_source().con, asof)


@st.cache_data(ttl=3600)
def _universe_verdict(key: str) -> str:
    """What the historical universe actually is, from the last measurement (Phase 7) —
    or, on a source that holds ended listings (Sharadar, CRSP), that it holds them."""
    del key
    source = _market_source()
    if getattr(source, "survivorship_free", False):
        ended = source.con.execute(
            "SELECT COUNT(*) FROM securities WHERE last_seen < "
            "(SELECT MAX(date) FROM prices) - INTERVAL 30 DAY").fetchone()[0]
        return (f"The historical universe includes {ended:,} listings that stopped "
                "trading, with their prices to the last day, so backtests are not run on "
                "survivors only; where the data records what holders received at the end "
                "(CRSP), that return is used instead of the assumed loss.")
    return data_history.universe_verdict(data_history.stored_recovery(source.con))


@st.cache_data(ttl=3600)
def _markets(key: str) -> list[str]:
    """The markets the store holds lines in, USA first."""
    del key
    rows = _market_source().con.execute(
        "SELECT DISTINCT CASE WHEN cik BETWEEN 1000000000 AND 1999999999 "
        "THEN upper(exchange) ELSE 'USA' END FROM securities").fetchall()
    found = sorted({r[0] for r in rows if r[0]})
    return (["USA"] if "USA" in found else []) + [c for c in found if c != "USA"]


@st.cache_data(ttl=3600)
def _sectors(key: str) -> list[str]:
    """The real industry vocabulary, handed to the translator so it cannot invent one."""
    del key
    return [r[0] for r in _market_source().con.execute(
        "SELECT sector FROM securities WHERE sector IS NOT NULL "
        "GROUP BY 1 ORDER BY COUNT(*) DESC"
    ).fetchall()]


# Pure views of the store, cached per data build: the boards are read on every rerun of
# their tab otherwise, each a scan over a year of prices (audit 02.10.2026).
@st.cache_data(ttl=3600, show_spinner=False)
def _monitor(unit_key: str, key: str) -> dict:
    del key
    return world.monitor(_market_source().con, unit=unit_key)


@st.cache_data(ttl=3600, show_spinner=False)
def _fred_board(key: str) -> pd.DataFrame:
    del key
    return fred.dashboard(_market_source().con)


@st.cache_data(ttl=3600, show_spinner=False)
def _rates(key: str) -> tuple[pd.DataFrame, dict]:
    del key
    con = _market_source().con
    return mc.rate_snapshot(con), mc.fed_funds_expectation(con)


@st.cache_data(ttl=3600, show_spinner=False)
def _ship_boards(key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    del key
    con = _market_source().con
    return shipping.dashboard(con), shipping.port_board(con)


@st.cache_data(ttl=3600, show_spinner=False)
def _valued(book: dict, key: str) -> dict:
    """paper.value_book per book and data build; a book is frozen once opened."""
    del key
    return paper.value_book(_market_source().con, _store().con, book)


@st.cache_data(ttl=3600, show_spinner=False)
def _risk_measures(positions: pd.DataFrame, asof: dt.date, key: str) -> dict:
    del key
    return risk.measures(_market_source().con, positions, asof)


@st.cache_data(ttl=3600, show_spinner=False)
def _commodity_links(ticker: str, key: str) -> pd.DataFrame:
    """research.commodity_sensitivity, the one helper behind every 'moves with' table."""
    del key
    return rs.commodity_sensitivity(_market_source().con, ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def _risk_free_now(key: str) -> dict:
    """Both rates the app uses, read once per build and labelled where they differ.

    Options and the Sharpe ratios use the 13-week bill (the horizon of an option); the
    cost of equity uses the 10-year yield (the horizon of a company). Before 02.10.2026
    each section read its own and nothing said why they differed."""
    del key
    con = _market_source().con
    short, short_src = mx.current_risk_free(con, Settings().risk_free_fallback)
    tnx = con.execute(
        "SELECT close, date FROM prices WHERE ticker = '^TNX' "
        "AND close IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
    long_, long_src = ((tnx[0] / 100.0, f"US 10-year yield (^TNX) of {tnx[1]}") if tnx
                       else (short, short_src + "; no 10-year yield stored"))
    return {"short": short, "short_source": short_src,
            "long": long_, "long_source": long_src}


def _last_close(ticker: str) -> float | None:
    """The newest stored close of `ticker` (exact match first, then any case)."""
    con = _market_source().con
    for sql in ("SELECT close FROM prices WHERE ticker = ? AND close IS NOT NULL "
                "ORDER BY date DESC LIMIT 1",
                "SELECT close FROM prices WHERE upper(ticker) = upper(?) "
                "AND close IS NOT NULL ORDER BY date DESC LIMIT 1"):
        row = con.execute(sql, [ticker]).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    return None


def _spot(ticker: str) -> tuple[float | None, str]:
    """The price an option view uses: the live quote, else the last stored close."""
    quote, _ = ac.attempt(ac.live_quote, ticker)
    if quote and quote.get("price"):
        return float(quote["price"]), "live quote"
    close = _last_close(ticker)
    return (close, "last stored close") if close else (None, "")


# Size bands in billions. "Any" leaves the screen alone; the rest write a filter that
# sits on top of whatever the chat produced, so the slider and the conversation compose
# instead of fighting.
CAP_BANDS = {
    "Any": (0.0, 0.0),
    "Micro (< $0.3bn)": (0.0, 0.3),
    "Small ($0.3-2bn)": (0.3, 2.0),
    "Mid ($2-10bn)": (2.0, 10.0),
    "Large ($10-200bn)": (10.0, 200.0),
    "Mega (> $200bn)": (200.0, 0.0),
}


def _band_changed() -> None:
    """Choosing a size band writes its bounds into the two number fields; typing a bound
    afterwards refines it. Before, the fields had no key and the band reset them on
    every rerun without saying so."""
    _persist("band")
    lo_b, hi_b = CAP_BANDS[st.session_state["band"]]
    st.session_state["cap_min"] = st.session_state["w_cap_min"] = float(lo_b)
    st.session_state["cap_max"] = st.session_state["w_cap_max"] = float(hi_b)


def _assumptions() -> Assumptions:
    """The ONE set of intrinsic-value assumptions: the P/IV column of the screen, the
    price/intrinsic line of a company's overview, and the starting values of its DCF
    all read these (audit 02.10.2026: the sidebar and the DCF had separate defaults)."""
    return Assumptions(discount_rate=_keep("iv_disc", 10) / 100,
                       growth_rate=_keep("iv_grow", 0) / 100,
                       terminal_growth=_keep("iv_term", 2.0) / 100)


def _filters() -> tuple[float, float, list[str], list[str]]:
    """(min $bn, max $bn, industry groups, markets) from the screen filters."""
    return (float(_keep("cap_min", 0.0)), float(_keep("cap_max", 0.0)),
            list(_keep("groups", [])), list(_keep("markets", [])))


try:
    src = _market_source()
except (MissingKey, SourceNotReady) as exc:
    # A chosen source that cannot open yet is a setup step, not a crash: say which one.
    st.error(str(exc))
    st.stop()
store, settings = _store(), Settings()
asof = src.latest_equity_date()
age = (dt.date.today() - asof).days if asof else None
# Read once per run. It was computed three to five times (the bar, stage B, the research
# summary, LAB twice), each a count over every table.
snapshot = src.snapshot()
data_fp = snapshot.fingerprint()
counts = snapshot.row_counts
# The key every cached view of the store carries: a new build is a new key.
DATA_KEY = f"{data_fp}|{asof}"

st.markdown(
    ui.bar("PORTFOLIO LAB", {
        "PX": asof or "none",
        "AGE": f"{age}d" if age is not None else "—",
        "SEC": f"{counts['securities']:,}",
        "FACTS": f"{counts['fundamentals']:,}",
    }),
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("#### Portfolios")
    listing = store.list_portfolios()
    if listing.empty:
        st.caption("None yet.")
        chosen = None
    else:
        names = dict(zip(listing.id, listing.name))
        chosen = st.radio(
            "Open", options=list(names), format_func=lambda i: names[i],
            label_visibility="collapsed", key="chosen",
        )

    with st.form("new", clear_on_submit=True):
        new_name = st.text_input("New portfolio", placeholder="Deep value")
        if st.form_submit_button("Create", width="stretch") and new_name:
            try:
                store.create(new_name)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if chosen:
        # A popover: deleting takes the chat, the screen, the ticks and the tracking
        # with it, and a single click next to "Create" did that without asking.
        with st.popover("Delete portfolio", width="stretch"):
            st.caption(f"Deletes '{names[chosen]}' with its screen, conversation and "
                       "tracking. Paper books stay.")
            if st.button("Delete for good", type="primary", key="delete_portfolio"):
                store.delete(chosen)
                st.rerun()

    st.markdown("#### Watchlist")
    wl = store.watchlist()
    if wl.empty:
        st.caption("Empty. Names here appear in the daily briefing.")
    else:
        for row in wl.itertuples():
            wc1, wc2 = st.columns([4, 1])
            wc1.markdown(
                f"<span style='color:{ui.AMBER};font-weight:600'>"
                f"{html.escape(row.ticker)}</span>"
                + (f" <span style='color:{ui.MUTED};font-size:0.68rem'>"
                   f"{html.escape(row.note)}</span>" if row.note else ""),
                unsafe_allow_html=True,
            )
            if wc2.button("×", key=f"unwatch_{row.ticker}"):
                store.unwatch(row.ticker)
                st.rerun()
    with st.form("watch_add", clear_on_submit=True):
        wt = st.text_input("Add ticker", placeholder="NVDA")
        wn = st.text_input("Note", placeholder="why you are watching")
        if st.form_submit_button("Watch", width="stretch") and wt.strip():
            store.watch(wt, wn)
            st.rerun()

    st.markdown("#### Data")
    if age is None:
        st.error("No prices. Run the build scripts.")
    elif age > 7:
        st.warning(f"Prices {age} days old. Refresh before trading on them.")
    world_note = (
        "Plus every company Compustat Global lists in the chosen countries (tickers end "
        "in the country, e.g. 7203.JPN), priced in dollars at each day's rate; their "
        "report dates are estimated from the legal deadline. "
        if isinstance(src, WorldSource) else "")
    st.caption(
        "US common stock on NYSE, Nasdaq and CBOE. Funds, commodity trusts and blank-"
        "cheque shells are excluded by the filer's own SEC industry code; REITs are kept. "
        + world_note + _universe_verdict(DATA_KEY)
    )
    if not secret("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY missing from .env.local. Chat is off.")

    st.markdown("#### Unit")
    unit = st.radio(
        "Price in", options=[nm.USD, nm.GOLD],
        format_func=lambda u: "US dollars" if u == nm.USD else "Ounces of gold",
        horizontal=True, label_visibility="collapsed", key="unit",
    )
    if unit == nm.GOLD:
        st.caption(
            "Charts, boards and backtest curves are divided by the gold price of the same "
            "day. Ratios and the eligibility floors stay in dollars — see the note under "
            "any converted chart."
        )

# ---------------------------------------------------------------- main
spec = store.get_spec(chosen) if chosen else None


def _need_portfolio(what: str) -> bool:
    """Views that act on the open portfolio say so instead of stopping the whole app:
    until 02.10.2026 a missing portfolio ended the script before any tab was drawn,
    MONITOR and MAP included."""
    if chosen:
        return True
    st.info("No portfolio open. Create one in the sidebar (the » arrow at the top left "
            f"opens it) — {what}")
    return False


def _yahoo_bars(symbol: str) -> pd.DataFrame:
    """Bars for a symbol outside the store; empty when Yahoo has none or was not
    reached (the cache keeps only answers, see appcache.py)."""
    bars, _ = ac.attempt(ac.yahoo_bars, symbol)
    return bars if bars is not None else pd.DataFrame()


# ------------------------------------------------------------- shared view helpers
# Each of these replaced two to five hand-copied blocks (audit 02.10.2026), which had
# drifted apart: "MAX DRAWDOWN" here, "MAX DD" there, a total-return row in one result
# table and not the next.
PALETTE = [ui.AMBER, ui.CYAN, ui.UP, "#C792EA", "#F78C6C", "#FFCB6B", "#89DDFF", ui.DOWN]
CHART_HEIGHT = 250


def _layout(fig, height: int = CHART_HEIGHT, **extra):
    """The terminal look for a plotly figure: black, monospace, thin grid."""
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=6, b=0), paper_bgcolor=ui.BG,
        plot_bgcolor=ui.BG, font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
        **extra)
    return fig


def _risk_cells(stats: dict, drawdown_label: str = "MAX DD") -> list:
    """The return/risk strip. Takes research.price_stats keys (return_1m …) or
    world.stats keys (1M …), so a share, an index and a Yahoo symbol read alike."""
    cells = []
    for label in ("1M", "3M", "6M", "1Y"):
        v = stats.get(f"return_{label.lower()}", stats.get(label))
        if v is not None and not pd.isna(v):
            cells.append((label, ui.pct(v, 1), "up" if v >= 0 else "down"))
    vol = stats.get("volatility")
    if vol is not None and not pd.isna(vol):
        cells.append(("VOL 1Y", f"{vol:.0%}", ""))
    if stats.get("max_drawdown") is not None:
        cells.append((drawdown_label, ui.pct(stats["max_drawdown"], 1), "down"))
    if stats.get("beta") is not None:
        cells.append(("BETA", ui.num(stats["beta"]), ""))
    return cells


def _corr_heatmap(corr: pd.DataFrame, key: str, annotate: bool = False) -> None:
    if corr.empty or corr.shape[0] < 2:
        return
    heat = go.Figure(go.Heatmap(
        z=corr.values, x=list(corr.columns), y=list(corr.index), zmin=-1, zmax=1,
        colorscale=[[0, ui.CYAN], [0.5, ui.BG], [1, ui.AMBER]],
        **({"text": corr.round(2).values, "texttemplate": "%{text}"} if annotate else {})))
    st.plotly_chart(_layout(heat, min(120 + 30 * len(corr), 520)), width="stretch",
                    key=f"corr_{key}")


def _links_table(links: pd.DataFrame, first: str = "series", limit: int = 10) -> None:
    """A 'moves with' table from research.commodity_sensitivity."""
    st.markdown(ui.grid(pd.DataFrame({
        first: links.commodity,
        "correlation": links.correlation.map(lambda v: f"{v:+.2f}"),
        "beta": links.beta.map(lambda v: f"{v:+.2f}"),
        "days": links.days.astype(str),
    }).head(limit), numeric={"days"}, signed={"correlation", "beta"}, ticker_col=first),
        unsafe_allow_html=True)


def _result_block(out, *, name: str, cells: list, turnover: float,
                  total_return: bool = False, lines: dict | None = None,
                  height: int = 280) -> None:
    """Headline strip, curve against the index, and the R9 table for any backtest:
    point-in-time (stage B), buy and hold (stage A) and a rule (LAB). Portfolio and
    index side by side, both from metrics.summary, so the yardstick cannot differ from
    the thing it measures."""
    m = out.metrics
    st.markdown(ui.stats([
        ("FROM", str(pd.Timestamp(out.dates[0]).date()), ""),
        ("YEARS", f"{out.years:.1f}", ""),
        (f"{name.upper()} P.A.", ui.pct(m["annual_return"]),
         "up" if m["annual_return"] >= 0 else "down"),
        ("S&P 500 TR P.A.", ui.pct(m.get("benchmark_annual_return")), ""),
        ("EXCESS P.A.", ui.pct(m.get("excess_annual")),
         "up" if (m.get("excess_annual") or 0) >= 0 else "down"),
    ] + cells), unsafe_allow_html=True)
    curve = pd.DataFrame({name: out.portfolio}, index=out.dates)
    colours = [ui.AMBER]
    for label, (series, colour) in (lines or {}).items():
        curve[label] = series
        colours.append(colour)
    if out.benchmark:
        curve[out.benchmark_symbol] = out.benchmark
        colours.append(ui.CYAN)
    st.line_chart(curve, height=height, color=colours)
    rows = [("Return p.a.", m["annual_return"], m.get("benchmark_annual_return"))]
    if total_return:
        rows.append(("Total return", m["total_return"], m.get("benchmark_total_return")))
    rows += [
        ("Volatility p.a.", m["volatility"], m.get("benchmark_volatility")),
        ("Max drawdown", m["max_drawdown"], m.get("benchmark_max_drawdown")),
        ("Deviation from index", m.get("tracking_error"), None),
    ]
    st.markdown(ui.grid(pd.DataFrame({
        "metric": [r[0] for r in rows] + ["Sharpe", "Turnover p.a."],
        name.lower(): [ui.pct(r[1]) for r in rows] + [ui.num(m["sharpe"]), ui.pct(turnover)],
        out.benchmark_symbol or "index":
            [ui.pct(r[2]) if r[2] is not None else "—" for r in rows]
            + [ui.num(m.get("benchmark_sharpe")), "—"],
    }), numeric=set(), ticker_col="metric"), unsafe_allow_html=True)


def _workbook_button(label: str, sheets, title: str, source: str, stem: str,
                     key: str) -> None:
    """An Excel download built only when it is clicked. `sheets` is a callable that
    returns frames already in memory: Streamlit runs it in another thread, where the
    database must not be touched (sources.per_thread). Building the workbook on every
    rerun cost ~0.5 s per button (audit 02.10.2026)."""
    st.download_button(
        label, data=lambda: export.workbook(sheets(), title=title, source=source,
                                            as_of=asof),
        file_name=export.filename(stem, asof), mime=XLSX, key=key)


def _lab_price_chart(con, ticker: str, result, rule) -> None:
    """One stock under a rule: price on top with every buy and sell marked where it
    filled, candles underneath. Drawn on the adjusted basis the rule traded on, so the
    markers sit on the line rather than a split away from it."""
    px_rule = con.execute(
        """
        SELECT date,
               open * (close_adj / NULLIF(close, 0)) AS open,
               high * (close_adj / NULLIF(close, 0)) AS high,
               low  * (close_adj / NULLIF(close, 0)) AS low,
               close_adj AS close, volume
        FROM prices WHERE ticker = ? AND date BETWEEN ? AND ? ORDER BY date
        """,
        [ticker, result.dates[0], result.dates[-1]],
    ).fetch_df()
    if px_rule.empty:
        # A Yahoo ticker outside the store: the same bars the rule ran on, on the same
        # adjusted basis, so the markers sit on the line.
        yb = _yahoo_bars(ticker)
        if not yb.empty:
            factor = yb["close_adj"] / yb["close"].where(yb["close"] != 0)
            px_rule = pd.DataFrame({
                "date": yb["date"], "open": yb["open"] * factor,
                "high": yb["high"] * factor, "low": yb["low"] * factor,
                "close": yb["close_adj"], "volume": yb["volume"]})
            px_rule = px_rule[(pd.to_datetime(px_rule.date) >= pd.Timestamp(result.dates[0]))
                              & (pd.to_datetime(px_rule.date)
                                 <= pd.Timestamp(result.dates[-1]))]
    if px_rule.empty:
        return
    bands = rule.signal.startswith("bollinger")
    if bands:
        px_rule = px_rule.join(lab_signals.bollinger_bands(
            px_rule["close"], rule.band_days, rule.band_k))
    trades = result.fills[result.fills.ticker == ticker] if not result.fills.empty \
        else result.fills
    st.plotly_chart(
        charts.price_figure(px_rule, ticker, bands=bands, trades=trades),
        width="stretch", key=f"lab_px_{ticker}")
    n_buy = int((trades.side == "buy").sum()) if not trades.empty else 0
    st.caption(
        f"▲ {n_buy} buys, ▼ {len(trades) - n_buy} sells, each at the open after the "
        "close that triggered it. Prices adjusted for splits and dividends; volume "
        "below, with its 15-day average."
    )


# ---------------------------------------------------------------- paper books
def _paper_rows(book_frame: pd.DataFrame) -> list[dict]:
    """Value every book in the frame. A book that cannot be valued yet (no price since
    it was opened) is listed with its money at cost rather than hidden."""
    out = []
    for _, row in book_frame.iterrows():
        try:
            v = _valued(row.to_dict(), DATA_KEY)
        except Exception as exc:                                  # noqa: BLE001
            v = {"empty": True, "book": row.to_dict(), "error": str(exc)}
        out.append(v)
    return out


def _paper_table(valued: list[dict]) -> pd.DataFrame:
    rows = []
    for v in valued:
        b = v["book"]
        rows.append({
            "book": b["name"], "from": b["origin"], "kind": b["kind"],
            "since": str(pd.Timestamp(b["started_on"]).date()),
            "money in": ui.money(v.get("invested_now", b["amount"])),
            "value": ui.money(v["value_now"]) if not v.get("empty") else "—",
            "gain": ui.money(v["gain"]) if not v.get("empty") else "—",
            "return": v.get("return") if not v.get("empty") else None,
            "S&P 500 TR": v.get("benchmark_return"),
        })
    frame = pd.DataFrame(rows)
    for col in ("return", "S&P 500 TR"):
        frame[col] = frame[col].map(lambda x: ui.pct(x) if x is not None and not pd.isna(x)
                                    else "—")
    return frame


def _risk_block(positions: pd.DataFrame, key: str) -> None:
    """Risk of a set of positions at their weights: historical, three years (risk.py)."""
    st.markdown("**Risk at these weights**")
    m = _risk_measures(positions, asof, DATA_KEY)
    if not m or m.get("error"):
        st.caption(m.get("error", "No price history for these names.") if m else
                   "No price history for these names.")
        return
    st.markdown(ui.stats([
        ("VOLATILITY", f"{m['volatility']:.1%}", ""),
        ("BETA", ui.num(m.get("beta")), ""),
        ("VAR 95% (1 DAY)", f"{m['var_95']:.2%}", "down"),
        ("CVAR 95%", f"{m['cvar_95']:.2%}", "down"),
        ("MAX DD", f"{m['max_drawdown']:.1%}", "down"),
        ("LARGEST", f"{m['largest_weight']:.0%}", ""),
        ("EFFECTIVE NAMES", f"{m['effective_names']:.1f}", ""),
    ]), unsafe_allow_html=True)
    st.caption(
        f"{m['days']} trading days, {m['from']} to {m['to']}, at today's weights. VaR: "
        "the daily loss exceeded on one day in twenty; CVaR: the average loss on those "
        "days. Effective names: how many equal positions the concentration equals.")
    r1, r2 = st.columns(2)
    with r1:
        ex = risk.exposure(src.con, positions)
        st.markdown(ui.grid(pd.DataFrame({
            "industry": ex["sector"].astype(str).str.slice(0, 34),
            "weight": ex["weight"].map(lambda w: f"{w:.0%}")}),
            numeric={"weight"}, ticker_col="industry"), unsafe_allow_html=True)
    with r2:
        sx = risk.stress(src.con, positions)
        st.markdown(ui.grid(pd.DataFrame({
            "crisis": sx["scenario"],
            "portfolio": sx["portfolio"].map(lambda v: ui.pct(v, 1)),
            "S&P 500 TR": sx["index"].map(lambda v: ui.pct(v, 1)),
            "covered": sx["coverage"].map(lambda v: f"{v:.0%}"),
        }), signed={"portfolio", "S&P 500 TR"}, numeric={"covered"},
            ticker_col="crisis"), unsafe_allow_html=True)
        st.caption("Return over each crisis, peak to trough of the S&P 500, had these "
                   "weights been held; 'covered' is the weight that had prices then.")
    _corr_heatmap(m["correlation"], key)


def _paper_section(origin: str, key: str, opener, default_name: str,
                   what: str, amount_label: str | None = "Amount ($)") -> None:
    """Open a paper book from here; every book is listed and compared in PORTFOLIOS.

    `opener(name, amount)` opens the book and returns its id. The purchase is frozen at
    the latest stored close; from then on the book is only valued, never re-decided.
    Until 02.10.2026 each tab also listed and valued its own books again here.
    """
    with st.expander(f"Paper money · open a book from {origin}"):
        st.caption(what)
        c1, c2, c3 = st.columns([1.1, 2, 1])
        amount = (c1.number_input(amount_label, min_value=100.0, max_value=1e9,
                                  value=10_000.0, step=500.0, key=f"paper_amt_{key}")
                  if amount_label else None)
        name = c2.text_input("Name", value=default_name, key=f"paper_name_{key}")
        c3.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if c3.button("Buy on paper", type="primary", width="stretch",
                     key=f"paper_buy_{key}"):
            try:
                opener(name, amount)
                st.success(f"Opened '{name}'. It is valued on every visit; compare all "
                           "books in PORTFOLIOS → BOOKS.")
            except ValueError as exc:
                st.error(str(exc))


def _screen_filters() -> None:
    """Size, industry, market and the intrinsic-value assumptions — on top of the chat.

    In the sidebar until 02.10.2026, where they sat apart from the screen they change.
    Every value is persisted (`_kept`), so it holds while another tab is open."""
    lo, hi, groups, markets = _filters()
    shown = []
    if lo > 0 or hi > 0:
        shown.append("size")
    if groups:
        shown.append("industry")
    if markets:
        shown.append("market")
    with st.expander("Filters on top of the chat"
                     + (f" · active: {', '.join(shown)}" if shown else "")):
        c1, c2, c3 = st.columns([2, 1, 1])
        c1.select_slider("Market cap band", options=list(CAP_BANDS),
                         **{**_kept("band", "Any"), "on_change": _band_changed,
                            "args": ()})
        c2.number_input("Min $bn", min_value=0.0, step=0.1, **_kept("cap_min", 0.0))
        c3.number_input("Max $bn", min_value=0.0, step=0.1,
                        help="0 means no upper limit", **_kept("cap_max", 0.0))
        st.multiselect(
            "Industry groups", options=list(industry.GROUPS), placeholder="Any industry",
            help="Grouped from the filer's own SEC code — the SEC's own wording stays "
                 "visible in the table.", **_kept("groups", [], "default"))
        # Markets appear only when the store holds more than one (the world store).
        available = _markets(DATA_KEY)
        if len(available) > 1:
            st.multiselect(
                "Markets", options=available, placeholder="Every market",
                format_func=lambda c: f"{c} · {MARKET_NAMES.get(c, c)}",
                help="Where the stock trades: a Japanese company's New York ADR is USA, "
                     "its Tokyo line JPN.", **_kept("markets", [], "default"))
        st.markdown("**Intrinsic value** — a model, not a measurement. These assumptions "
                    "decide the P/IV column, a company's price/intrinsic line and the "
                    "starting values of its DCF.")
        # Sliders work in whole percent. Streamlit formats the raw value, so a 0.10 shown
        # with "%.0f%%" renders as "0%" — the number has to carry the percent.
        i1, i2, i3 = st.columns(3)
        i1.slider("Required return %", 4, 20, step=1, **_kept("iv_disc", 10))
        i2.slider("Growth assumed % a year", -5, 15, step=1, **_kept("iv_grow", 0))
        i3.slider("Growth forever after %", 0.0, 4.0, step=0.5, **_kept("iv_term", 2.0))
        a = _assumptions()
        if not a.valid():
            st.error("Perpetual growth must stay below the required return, or value is "
                     "infinite.")
        else:
            st.caption(a.describe() + ". All filters apply on top of the chat.")


def _effective_spec(spec):
    """The chat's spec with the filters above layered on (hand-picked names ignore the
    size and industry filters: they were chosen on purpose)."""
    lo, hi, groups, markets = _filters()
    effective = spec
    if spec.is_hand_picked:
        lo = hi = 0.0
    elif groups:
        effective = effective.with_filter(Filter("industry_group", "in", groups))
    if markets and not spec.is_hand_picked:
        effective = effective.with_filter(Filter("country", "in", markets))
    if lo > 0:
        effective = effective.with_filter(Filter("market_cap", ">=", lo * 1e9))
    if hi > 0:
        effective = effective.with_filter(Filter("market_cap", "<=", hi * 1e9))
    return effective


def _picks(spec):
    """What the open portfolio holds: the ticks of the last screen drawn in this session,
    else the stored selection run through the screen again. Tracking and the backtests
    read this, so they no longer depend on the screen table being on the same page."""
    held = st.session_state.get(f"held_picks_{chosen}")
    if held is not None and st.session_state.get(f"held_spec_{chosen}") == spec:
        return held
    return run_screen(_frame(asof, DATA_KEY), _effective_spec(spec), _assumptions()).picks


def _view_screen() -> None:
    if not _need_portfolio("a screen is saved with its portfolio."):
        return
    spec = store.get_spec(chosen)
    history = store.messages(chosen)
    if history:
        st.markdown(
            ui.log([(m["role"], m["text"]) for m in history]), unsafe_allow_html=True
        )

    prompt = st.chat_input("Describe or refine this portfolio")
    if prompt:
        store.add_message(chosen, "user", prompt)
        st.markdown(ui.log([("user", prompt)]), unsafe_allow_html=True)
        try:
            with st.spinner("Translating"):
                result = T.translate(prompt, spec, sectors=_sectors(DATA_KEY))
            spec = result.spec
            store.save_spec(chosen, spec)
            reply = "\n".join(f"- {c}" for c in result.changed) or "- filters unchanged"
            if spec.unsupported:
                reply += "\n\n**Not applied** — no field matches these:\n"
                reply += "\n".join(f"- {u}" for u in spec.unsupported)
            store.add_message(chosen, "assistant", reply, spec)
            st.markdown(ui.log([("assistant", reply)]), unsafe_allow_html=True)
        except Exception as exc:                                    # noqa: BLE001
            st.error(f"Could not translate that: {ui.error_text(exc)}")

    _screen_filters()
    lo, hi, groups, markets = _filters()
    left, right = st.columns([1, 2.3], gap="medium")

    with left:
        st.markdown("#### Hand-picked")
        # A form with its own button, not a bare text area: Streamlit only commits a text
        # area on Ctrl+Enter or blur, which is an invisible rule to anyone who has not met it.
        with st.form(f"picks_{chosen}"):
            typed = st.text_area(
                "Tickers",
                value=", ".join(spec.tickers),
                placeholder="AAPL, MSFT, NVDA",
                height=80,
                label_visibility="collapsed",
            )
            if st.form_submit_button("Use these companies", width="stretch"):
                picked = tuple(
                    t.strip().upper()
                    for t in typed.replace("\n", ",").replace(" ", ",").split(",")
                    if t.strip()
                )
                spec = replace(spec, tickers=picked)
                store.save_spec(chosen, spec)
                st.rerun()
        if spec.tickers:
            st.caption(
                f"{len(spec.tickers)} named companies. Filters and eligibility floors do not "
                "apply — you chose these on purpose. Clear the box to go back to screening."
            )
        else:
            st.caption("Name companies directly, or leave empty and describe a screen in chat.")

        st.markdown("#### Screen")
        for line in spec.describe():
            st.markdown(f"- {line}")
        if lo > 0 or hi > 0:
            bounds = []
            if lo > 0:
                bounds.append(f"≥ ${lo:g}bn")
            if hi > 0:
                bounds.append(f"≤ ${hi:g}bn")
            st.markdown(f"- market cap {' and '.join(bounds)} *(from the filters)*")
        if groups:
            st.markdown(f"- industry one of {', '.join(groups)} *(from the filters)*")
        if markets:
            st.markdown(f"- market one of {', '.join(markets)} *(from the filters)*")
        if spec.unsupported:
            st.warning("Not applied: " + "; ".join(spec.unsupported))

    with right:
        if asof is None:
            st.info("No prices stored yet, so nothing can be screened. Run the build "
                    "scripts (README).")
            return
        effective = _effective_spec(spec)
        res = run_screen(_frame(asof, DATA_KEY), effective, _assumptions())

        # Criterion 1(e): coverage was measured in phase 1 and written to a table. A
        # measurement nobody reads protects nobody, so the filters that depend on a
        # thinly reported field say so here.
        thin = cov.thin_for([f.metric for f in effective.filters], src.con)
        if not thin.empty and not spec.is_hand_picked:
            st.warning(cov.sentence(thin))

        st.markdown("#### Holdings" if spec.is_hand_picked else "#### Candidates")
        # Filled after the table, because the HELD count comes from what is ticked there.
        headline = st.empty()
        for note in res.notes:
            st.warning(note)

        with st.expander("Why names dropped out", expanded=not res.n_passing):
            if res.per_filter_survivors:
                st.caption("Each filter alone, against the eligible universe")
                st.markdown(
                    ui.grid(
                        pd.DataFrame([{"filter": k, "survivors": f"{v:,}"}
                                      for k, v in res.per_filter_survivors.items()]),
                        numeric={"survivors"}, ticker_col="",
                    ),
                    unsafe_allow_html=True,
                )
            if res.exclusions:
                st.caption("Removed before filtering")
                st.markdown(
                    ui.grid(
                        pd.DataFrame([{"reason": k, "count": f"{v:,}"}
                                      for k, v in res.exclusions.items()]),
                        numeric={"count"}, ticker_col="",
                    ),
                    unsafe_allow_html=True,
                )

    # The candidate table needs the whole page: fifteen metric columns do not
    # fit beside the chat panel, and a screen that passes 300 names is meant to be
    # read, not squinted at.
    picks = res.picks
    if res.n_passing:
        r = res.ranked

        # The editor keeps its own tick state across reruns. That is what we want
        # while clicking, but a quick-select button has to be able to overrule it —
        # so those bump a version that becomes part of the widget key, remounting it
        # with the new defaults. Ordinary ticking leaves the key alone and therefore
        # does not throw away the scroll position.
        ver_key = f"selver_{chosen}"
        ver = st.session_state.setdefault(ver_key, 0)

        def _choose(tickers) -> None:
            store.save_spec(chosen, replace(spec, selected=tuple(tickers)))
            st.session_state[ver_key] = ver + 1
            st.rerun()

        if not spec.is_hand_picked:
            # Stable keys: the ALL button's label carries the count, and a key taken
            # from the label remounted the button whenever the count changed.
            quick = st.columns(5)
            for col, n in zip(quick, (10, 25, 50)):
                if col.button(f"TOP {n}", width="stretch", key=f"top_{n}",
                              disabled=res.n_passing <= n and n != 10):
                    _choose(r.ticker.astype(str).head(n))
            if quick[3].button(f"ALL {res.n_passing}", width="stretch", key="pick_all"):
                _choose(r.ticker.astype(str))
            if quick[4].button("NONE", width="stretch", key="pick_none"):
                _choose(())

        def _col(name: str) -> pd.Series:
            return pd.to_numeric(r[name], errors="coerce") if name in r \
                else pd.Series(float("nan"), index=r.index)

        held = set(picks.ticker.astype(str))
        editor = pd.DataFrame({
            "hold": r.ticker.astype(str).isin(held),
            "#": r["rank"],
            "ticker": r.ticker.astype(str),
            "name": r.name.astype(str).str.slice(0, 28),
            # Both classifications, on purpose: the group is what you filtered on, the
            # SEC wording is what can be checked against the filing.
            "industry": r.get("industry_group", pd.Series(dtype=object)).astype(str),
            "sector": r.get("sector", pd.Series(dtype=object)).astype(str).str.slice(0, 22),
            "px": _col("price"),
            "mkt cap": _col("market_cap"),
            "p/b": _col("price_to_book"),
            "p/e": _col("price_to_earnings"),
            "p/s": _col("price_to_sales"),
            "p/fcf": _col("price_to_free_cash_flow"),
            "p/iv": _col("price_to_intrinsic"),
            "d/e": _col("debt_to_equity"),
            "margin": _col("net_margin"),
            "age": _col("data_age_days"),
        })
        two = st.column_config.NumberColumn(format="%.2f")
        edited = st.data_editor(
            editor,
            hide_index=True,
            width="stretch",
            row_height=28,
            height=min(660, 80 + 28 * len(editor)),
            disabled=[c for c in editor.columns if c != "hold"],
            column_config={
                "hold": st.column_config.CheckboxColumn(
                    "✓", help="In the portfolio", width="small", pinned=True),
                "#": st.column_config.NumberColumn(
                    "#", help="Rank by the screen's ordering metric",
                    format="%d", width="small", pinned=True),
                "ticker": st.column_config.TextColumn(width="small", pinned=True),
                "px": two,
                "mkt cap": st.column_config.NumberColumn(format="compact"),
                "p/b": two, "p/e": two, "p/s": two, "p/fcf": two,
                "p/iv": st.column_config.NumberColumn(
                    "p/iv", format="%.2f",
                    help="Price over intrinsic value under the assumptions in the "
                         "filters above."),
                "d/e": two,
                "margin": st.column_config.NumberColumn(format="percent"),
                "age": st.column_config.NumberColumn(
                    "age", help="Days since the filing this is built from",
                    format="%d"),
            },
            key=f"screen_sel_{chosen}_{ver}",
        )

        # The ticks are the truth from here on: weights, the tracking button and the
        # stored spec all follow this one list rather than what the screen proposed.
        # They speak only for the rows on screen, though — see merge_selection: a
        # filter hides names, and hidden is not the same as unticked.
        ticked = tuple(edited.loc[edited["hold"], "ticker"].astype(str))
        live = merge_selection(spec.selected, edited["ticker"].astype(str), ticked)
        picks = res.select(live, spec.weighting)
        if set(ticked) != held and not spec.is_hand_picked:
            spec = replace(spec, selected=live)
            store.save_spec(chosen, spec)

        if len(picks):
            st.caption(
                f"{len(picks)} held · {spec.weighting.replace('_', ' ')} weighting · "
                f"{picks.weight.max():.1%} largest position. "
                "Sort by any column header; ticking is what decides the portfolio."
            )
        else:
            st.caption("Nothing ticked. Tick a row, or use the buttons above.")
        ranked, held_now, excluded = res.ranked, picks, res.exclusions
        filters_now = list(spec.filters)
        _workbook_button(
            "Export screen to Excel",
            lambda: {"Passed": ranked, "Held": held_now,
                     "Excluded": pd.DataFrame(
                         [{"reason": k, "names": v} for k, v in excluded.items()]),
                     "Filters": pd.DataFrame([{"metric": f.metric, "op": f.op,
                                               "value": str(f.value)}
                                              for f in filters_now])},
            title="Screen: " + "; ".join(spec.describe()),
            source="SEC XBRL as first filed, daily closes; USD", stem="screen",
            key="export_screen")
    else:
        st.info("Nothing passed. The breakdown above names the binding rule.")
    st.session_state[f"held_picks_{chosen}"] = picks
    st.session_state[f"held_spec_{chosen}"] = spec

    headline.markdown(
        ui.stats([
            ("UNIVERSE", f"{res.n_universe:,}", ""),
            ("FOUND" if res.hand_picked else "ELIGIBLE", f"{res.n_eligible:,}", ""),
            ("NOT FOUND" if res.hand_picked else "PASSED",
             f"{len(res.unknown_tickers):,}" if res.hand_picked else f"{res.n_passing:,}",
             ("down" if res.unknown_tickers else "") if res.hand_picked
             else ("amber" if res.n_passing else "down")),
            ("HELD", f"{len(picks):,}", "amber" if len(picks) else "down"),
        ]),
        unsafe_allow_html=True,
    )

    def _open_screen_book(name, amount):
        if picks.empty:
            raise ValueError("Nothing ticked to buy.")
        caps = (picks["market_cap"] if "market_cap" in picks.columns
                else pd.Series(0.0, index=picks.index)).fillna(0.0)
        pos = picks[["ticker", "weight", "price"]].copy()
        pos["cost"] = [settings.cost.round_trip_cost(c) for c in caps]
        return paper.open_hold(store.con, name, "SCREEN", pos, asof, amount)

    _paper_section(
        "SCREEN", f"screen_{chosen}", _open_screen_book, f"Screen {asof}",
        f"Buy the {len(picks)} ticked names with their weights at the {asof} closes, "
        "entry cost charged once, then hold. Open as many books as you like — each is "
        "its own frozen purchase.")


def _view_tracking() -> None:
    """Live tracking of the open portfolio's ticked names against the S&P 500 TR."""
    if not _need_portfolio("tracking follows the names ticked in its screen."):
        return
    spec = store.get_spec(chosen)
    st.markdown("#### Live tracking")
    info = store.tracking_info(chosen)

    if info is None:
        picks = _picks(spec) if asof else pd.DataFrame()
        st.write(
            "Freeze this selection at today's prices and follow it forward against the "
            "S&P 500 total return. The picks are fixed before the outcome exists, so no "
            "survivorship bias, no look-ahead and no amount of filter-tweaking can flatter "
            "the result. It only needs time to pass."
        )
        thesis = st.text_input(
            "What are you testing?", key=f"thesis_{chosen}",
            placeholder="cheap small caps beat the index over 12 months",
        )
        if age and age > 1:
            st.warning(
                f"Entry would use the closes of {asof}, {age} days ago — not what you would "
                "pay this morning. Refresh prices first if the entry needs to match a real trade."
            )
        can = len(picks) > 0
        # A stable key; the label carries the count and changed with every tick.
        if st.button(f"Start tracking {len(picks)} positions", key=f"track_go_{chosen}",
                     type="primary", disabled=not can, width="stretch"):
            n = store.start_tracking(chosen, picks, asof, spec_to_json(spec), thesis,
                                     cost_of=settings.cost.round_trip_cost)
            st.success(f"Tracking {n} positions from {asof}.")
            st.rerun()
        if not can:
            st.caption("Nothing to track — tick names in SCREEN first.")
        return

    holdings = store.holdings(chosen)
    perf = performance(src.con, holdings, info["started_on"])

    if perf.get("empty"):
        st.info("No price history since the start date yet.")
    else:
        tone = "up" if perf["portfolio_return"] >= 0 else "down"
        cells = [
            ("SINCE", str(info["started_on"]), ""),
            ("DAYS", str(perf["days"]), ""),
            ("POSITIONS", str(len(holdings)), ""),
            ("PORTFOLIO", ui.pct(perf["portfolio_return"]), tone),
        ]
        if perf["benchmark_return"] is not None:
            cells.append(("S&P 500 TR", ui.pct(perf["benchmark_return"]), ""))
            cells.append(("EXCESS", ui.pct(perf["excess"]),
                          "up" if perf["excess"] >= 0 else "down"))
        st.markdown(ui.stats(cells), unsafe_allow_html=True)
        if info["thesis"]:
            st.caption(f"Thesis — {info['thesis']}")

        curve = pd.DataFrame({"Portfolio": perf["portfolio"]}, index=perf["dates"])
        if perf["benchmark"]:
            curve[perf["benchmark_symbol"]] = perf["benchmark"]
        st.line_chart(curve, height=CHART_HEIGHT, color=[ui.AMBER, ui.CYAN][:len(curve.columns)])
        st.caption(
            "Both lines are total return, rebased to 100 at entry. Entry costs are "
            "charged to the portfolio and not to the index, so the comparison runs "
            "slightly against you. Buy and hold — no rebalancing."
        )

        d = perf["detail"]
        if d["stopped_trading"].any():
            st.warning(
                f"{int(d['stopped_trading'].sum())} position(s) stopped trading, held at "
                "last observed price. Delisting proceeds are not modelled."
            )
        table = pd.DataFrame({
            "ticker": d.ticker,
            "name": d.name.astype(str).str.slice(0, 28),
            "wt": d.weight.map(lambda v: f"{v:.1%}"),
            "entry": d.entry_price.map(ui.num),
            "last": d.last_price.map(ui.num),
            "return": d["return"].map(ui.pct),
            "contrib": d.contribution.map(ui.pct),
        })
        st.markdown(
            ui.grid(table, numeric={"wt", "entry", "last"},
                    signed={"return", "contrib"}),
            unsafe_allow_html=True,
        )

    # A confirmation: this throws away the frozen entry, which cannot be re-created.
    with st.popover("Stop tracking and discard", width="stretch"):
        st.caption("The frozen entry prices and the record so far are deleted. A new "
                   "tracking starts at the prices of its own day.")
        if st.button("Discard for good", type="primary", key=f"track_stop_{chosen}"):
            store.stop_tracking(chosen)
            st.rerun()


def _stage_b_section(spec) -> None:
    """Point-in-time: re-screen at every date, buy at the next open (Phase 8)."""
    st.markdown("#### Backtest — stage B (point-in-time)")
    st.caption(
        "Stage A runs today's basket backwards, which is hindsight. This re-runs the "
        "screen at every rebalance date on only the filings published by then, and "
        "fills at the next session's open. It answers a different question: would "
        "the RULE have worked, not would these names have worked."
    )
    cache = FrameCache(src, cfg.CACHE_DIR / "pit", data_fp)

    c1, c2, _ = st.columns([1.1, 1, 1.1])
    pit_start = c1.date_input(
        "From", value=dt.date(2011, 1, 3), min_value=dt.date(2011, 1, 3),
        max_value=asof, key=f"pit_start_{chosen}", format="YYYY-MM-DD",
        help="2011 is where XBRL coverage becomes usable — measured in phase 1.")
    frequency = c2.selectbox(
        "Rebalance", ["quarterly", "annual", "buy_and_hold"], index=0,
        key=f"pit_freq_{chosen}")
    # R21: the last two years are out of the picture until someone says otherwise.
    unlock = st.checkbox(
        f"Unlock the withheld period (the last {settings.holdout_years} years)",
        value=False, key=f"pit_unlock_{chosen}",
        help="Tune on what you can see; open this once at the end. Opening it is "
             "recorded on the run.")
    try:
        hw = holdout.window(asof, pit_start, settings.holdout_years, unlock)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.caption(hw.describe())
    burned = holdout.burn_count(store, spec.fingerprint(), data_fp)
    if burned:
        st.warning(holdout.burn_warning(burned))

    dates = bstage.rebalance_dates(hw.start, hw.end, frequency)
    ready = sum(1 for d in dates if cache.path_for(d).exists())
    c4, c5 = st.columns([1, 1])
    go_pit = c4.button("Run point-in-time", width="stretch",
                       key=f"pit_go_{chosen}")
    go_sens = c5.button("Run with sensitivity checks", width="stretch",
                        key=f"pit_sens_{chosen}",
                        help="The same run started six months earlier and later, and "
                             "again without the holding it leaned on most.")

    if ready < len(dates):
        st.warning(
            f"{len(dates) - ready} of {len(dates)} dates are not cached yet. Each one "
            "costs 6-10 seconds to assemble, almost all of it the point-in-time "
            "fundamentals — so a cold run takes minutes, not seconds. Warm it once "
            "with `uv run python scripts/warm_pit_cache.py`; every later run, with "
            "any screen, is then fast. The cache changes speed and nothing else."
        )

    if go_pit or go_sens:
        shared = dict(
            costs=settings.cost, frequency=frequency,
            risk_free=mx.risk_free_rate(
                src.con, hw.start, hw.end, settings.risk_free_fallback)[0],
            label=bstage.stage_b_label(_universe_verdict(DATA_KEY)),
            data_fingerprint=data_fp, frame_for=cache.frame_for,
        )
        with st.spinner(f"Re-screening {len(dates)} dates"):
            started = time.time()
            out = bstage.run(src, spec, hw.start, hw.end, **shared)
            report = (sensitivity.run(src, spec, hw.start, hw.end, **shared)
                      if go_sens else None)
            st.session_state[f"pit_result_{chosen}"] = out
            st.session_state[f"pit_sens_{chosen}_result"] = report
            st.session_state[f"pit_seconds_{chosen}"] = time.time() - started
        if out is not None:
            store.record_run(
                chosen, "stage_b", spec.fingerprint(), data_fp,
                out.dates[0], out.dates[-1],
                {"frequency": frequency, "dates": len(dates),
                 "sensitivity": bool(go_sens)}, out.metrics,
                holdout_unlocked=unlock)

    out = st.session_state.get(f"pit_result_{chosen}")
    if out is None and go_pit:
        st.info("Nothing passed the screen at any rebalance date in that window.")
    elif out is not None:
        _result_block(out, name="Portfolio", turnover=out.turnover, height=300, cells=[
            ("TURNOVER P.A.", ui.pct(out.turnover), ""),
            ("REBALANCES", f"{len(out.rebalances):,}", ""),
        ])
        if not out.rebalances.empty:
            with st.expander(f"What it held, {len(out.rebalances)} rebalances"):
                reb = out.rebalances
                st.markdown(ui.grid(pd.DataFrame({
                    "screened": reb.screened_on.astype(str),
                    "filled": reb.filled_on.astype(str),
                    "names": reb.names.astype(int),
                    "turnover": reb.turnover.map(ui.pct),
                    "cost": reb.cost.map(lambda v: ui.pct(v, 2)),
                }), numeric={"names"}, ticker_col="screened"),
                    unsafe_allow_html=True)

        report = st.session_state.get(f"pit_sens_{chosen}_result")
        if report is not None:
            st.markdown("#### Does it survive being poked?")
            table = sensitivity.as_table(report)
            st.markdown(ui.grid(pd.DataFrame({
                "variation": table["variation"],
                "return p.a.": table["return p.a."].map(ui.pct),
                "vs index": table["vs index"].map(ui.pct),
                "max drawdown": table["max drawdown"].map(ui.pct),
            }), numeric=set(), ticker_col="variation"), unsafe_allow_html=True)
            st.caption(report.verdict())

        for note in out.notes[:5]:
            st.warning(note)
        st.warning(out.label)
        if hw.unlocked:
            st.warning(hw.describe())
        seconds = st.session_state.get(f"pit_seconds_{chosen}", 0.0)
        tried = store.backtest_count(spec.fingerprint(), out.data_fingerprint)
        st.caption(
            f"Ran in {seconds:.0f} seconds over {len(dates)} rebalance dates. This "
            f"exact screen has been tested {tried}× against this data build."
        )


def _stage_a_section(spec, picks) -> None:
    st.markdown("#### Backtest — stage A (today's ticks, held backwards)")
    if picks.empty:
        st.caption(
            "Nothing held yet. The backtest runs on the names ticked in SCREEN, not on "
            "everything that passed."
        )
        return
    bt_tickers = [str(x) for x in picks.ticker]
    span = src.con.execute(
        "SELECT MIN(date), MAX(date) FROM prices WHERE ticker IN (SELECT UNNEST(?))",
        [bt_tickers],
    ).fetchone()
    first_bar, last_bar = (span or (None, None))
    if not first_bar:
        st.info("No stored price history for these names.")
        return
    c1, c2, c3 = st.columns([1.1, 1.1, 1.2])
    start_on = c1.date_input(
        "Start", value=first_bar, min_value=first_bar, max_value=last_bar,
        key=f"bt_start_{chosen}", format="YYYY-MM-DD",
    )
    charge = c2.toggle("Trading costs", value=True, key=f"bt_costs_{chosen}",
                       help="Tiered by market cap; switch off to see what they cost you.")
    run_bt = c3.button("Run backtest", width="stretch", key=f"bt_go_{chosen}")

    if run_bt:
        model = settings.cost if charge else CostModel(
            commission=0.0, spread_tiers=((float("inf"), 0.0),))
        rate, rate_source = mx.risk_free_rate(
            src.con, start_on, last_bar, settings.risk_free_fallback)
        gold = (nm.gold_series(src.con, start_on, last_bar)
                if unit == nm.GOLD else pd.Series(dtype=float))
        if unit == nm.GOLD and not nm.covers(gold, start_on, last_bar):
            st.warning(
                "No gold price stored for the whole window, so this run stays in "
                "dollars. `uv run python scripts/build_commodities.py` fills it."
            )
            gold = pd.Series(dtype=float)
        result = bktest.buy_and_hold(
            src.con, picks, start_on, last_bar, costs=model,
            risk_free=rate, delisting_return=settings.delisting_return,
            data_fingerprint=data_fp,
            universe_verdict=_universe_verdict(DATA_KEY),
            numeraire=gold if len(gold) else None,
        )
        st.session_state[f"bt_unit_{chosen}"] = (
            nm.GOLD if len(gold) else nm.USD)
        st.session_state[f"bt_rate_{chosen}"] = (rate, rate_source)
        if result is None:
            st.session_state[f"bt_result_{chosen}"] = None
        else:
            store.record_run(
                chosen, "stage_a", spec.fingerprint(), data_fp,
                result.dates[0], result.dates[-1],
                {"costs_charged": charge, "positions": len(picks)},
                result.metrics,
            )
            st.session_state[f"bt_result_{chosen}"] = result

    result = st.session_state.get(f"bt_result_{chosen}")
    if result is None and run_bt:
        st.info("Not enough overlapping price history to run this basket.")
    elif result is not None:
        _result_block(result, name="Portfolio", turnover=0.0, total_return=True, cells=[
            ("ENTRY COST", ui.pct(result.entry_cost_total), "")])
        if st.session_state.get(f"bt_unit_{chosen}", nm.USD) == nm.GOLD:
            st.caption(nm.note(nm.GOLD))
        rate, rate_source = st.session_state.get(
            f"bt_rate_{chosen}", (settings.risk_free_fallback, "settings"))
        st.caption(
            "Turnover is zero by construction: stage A buys once and holds, so "
            "costs are charged at entry only (R16b). Deviation from the index is "
            "the annualised standard deviation of the daily difference — how "
            "differently the portfolio travelled, not how far behind it ended. "
            f"Sharpe uses a risk-free rate of {rate:.2%} ({rate_source})."
        )

        for note in result.notes:
            st.warning(note)
        st.warning(result.label)

        tried = store.backtest_count(spec.fingerprint(), result.data_fingerprint)
        other = store.other_data_builds(spec.fingerprint(), result.data_fingerprint)
        st.caption(
            f"This exact screen has been tested {tried}× against this data build "
            f"(`{bktest.short_build_id(result.data_fingerprint)}`). The more "
            "attempts, the more the "
            "best-looking curve is a product of the search rather than of the rule."
        )
        if other:
            st.warning(
                f"{other} earlier run(s) of this screen read a DIFFERENT data "
                "build. Free price sources restate adjusted closes, so those "
                "curves and this one are not directly comparable (R22)."
            )


def _view_backtest() -> None:
    """Stage B first (the honest one: point in time, holdout, sensitivity), then A."""
    if not _need_portfolio("a backtest runs the portfolio's screen."):
        return
    if asof is None:
        st.info("No prices stored yet.")
        return
    spec = store.get_spec(chosen)
    _stage_b_section(spec)
    _stage_a_section(spec, _picks(spec))


SUBMISSIONS = "submissions.zip"


def _sec_overview(c) -> None:
    """DES and GP: who the company is, its chart, return and risk, the 20-day range."""
    ticker, cik, prof, me = c.ticker, c.cik, c.prof, c.me
    details = rs.company_details(cfg.CACHE_DIR / SUBMISSIONS, cik)
    left_c, right_c = st.columns([1, 2], gap="medium")
    with left_c:
        pairs = [
            (prof["name"][:40], "", False),
            ("Sector", prof.get("sector") or "—", False),
            ("Exchange", prof.get("exchange") or "—", False),
        ]
        if details.get("incorporated_in"):
            pairs.append(("Incorporated", details["incorporated_in"], False))
        if details.get("fiscal_year_end"):
            pairs.append(("Fiscal year ends", details["fiscal_year_end"], False))
        if details.get("former_names"):
            pairs.append(("Formerly", details["former_names"][0][:28], False))
        if me is not None:
            pairs += [
                ("Market cap", ui.money(me.market_cap), True),
                ("P/E", ui.num(me.price_to_earnings), False),
                ("P/B", ui.num(me.price_to_book), False),
                ("P/S", ui.num(me.price_to_sales), False),
                ("Net margin", ui.pct(me.net_margin, 1), False),
                ("Debt/equity", ui.num(me.debt_to_equity), False),
                ("Price/intrinsic", ui.num(me.price_to_intrinsic), False),
            ]
        st.markdown(ui.kv(pairs), unsafe_allow_html=True)
        if me is not None:
            st.caption("Price/intrinsic under the screen's assumptions: "
                       + _assumptions().describe() + ".")
    with right_c:
        px_hist = rs.price_history(src.con, ticker, years=5)
        if px_hist.empty:
            st.info("No stored price history.")
        else:
            c_a, c_b, c_c = st.columns([1.2, 1, 1.4])
            span = c_a.segmented_control(
                "Range", ["6M", "1Y", "5Y"], default="1Y",
                label_visibility="collapsed", key=f"span_{ticker}",
            ) or "1Y"
            # Bands are computed on the FULL history and only then cropped to
            # the visible span. Computing them on the crop would leave the
            # first 20 sessions of every view without an envelope.
            chart_px = px_hist
            chart_unit = nm.USD
            if unit == nm.GOLD:
                gold_px = nm.gold_series(src.con)
                if not gold_px.empty:
                    priced = chart_px.set_index("date")[
                        ["open", "high", "low", "close"]]
                    converted = nm.convert(priced, gold_px).dropna(how="all")
                    if not converted.empty:
                        chart_px = (chart_px.set_index("date")
                                    .drop(columns=["open", "high", "low", "close"])
                                    .join(converted, how="right")
                                    .reset_index())
                        chart_unit = nm.GOLD
            # Bands are computed on the SAME series the candles are drawn
            # from, so they follow the unit rather than sitting in dollars
            # around a chart in ounces.
            view = chart_px.join(rs.bollinger(chart_px))
            view = view.tail({"6M": 126, "1Y": 252, "5Y": 1260}[span])
            # Judged on what is actually on screen: older bars predate the
            # high/low columns, so a long view can fall back to a line while
            # a short one draws candles.
            can_candle = rs.has_intraday_range(view)
            candles = c_b.toggle(
                "Candles", value=False, disabled=not can_candle,
                key=f"candles_{ticker}_{span}",
                help="Candles instead of the line. Volume stays underneath "
                     "either way." if can_candle else
                "Stored bars carry no high/low for this range.",
            )
            bands = c_c.toggle(
                "Bollinger 20d", value=False, key=f"bands_{ticker}",
                help="20-day average with a 2-standard-deviation envelope.",
            )

            fig = charts.price_figure(view, ticker, bands=bands,
                                      candles=candles)
            st.plotly_chart(fig, width="stretch",
                            key=f"pxchart_{ticker}")
            if chart_unit == nm.GOLD:
                st.caption(nm.note(nm.GOLD))
            if not can_candle:
                st.caption(
                    "Line, not candles: the bars in this range carry no high "
                    "or low. `uv run python scripts/build_prices.py --full` "
                    "backfills the intraday range for the stored window."
                )
            elif bands:
                st.caption(
                    "Bands are 20-day average ± 2 population standard "
                    "deviations of the unadjusted close — the same series the "
                    "candles are drawn from. About one session in twenty closes "
                    "outside them even when nothing is happening; that is what "
                    "two standard deviations means, not a signal."
                )
        stats_now = _price_stats(ticker, asof, DATA_KEY)
        if stats_now:
            st.markdown(ui.stats(_risk_cells(stats_now)), unsafe_allow_html=True)
            bits = [f"{lab} {stats_now[k]:,.2f}" for lab, k in
                    (("50d avg", "ma50"), ("200d avg", "ma200"))
                    if stats_now.get(k) and not pd.isna(stats_now[k])]
            st.caption(
                (" · ".join(bits) + " · " if bits else "")
                + f"{stats_now['days']} stored days to {stats_now['to']}; max drawdown "
                "over all of them, volatility over the last year. Beta describes the "
                "past and is noisy on a short window."
            )
        # Width of the next 20 sessions, never the direction (volforecast.py).
        # Measured on US shares only, so foreign lines (TIC.CTY) get no number.
        if "." not in ticker and not ticker.startswith("^"):
            vf_model = _vol_model(asof, DATA_KEY)
            vf_now = (vfc.for_ticker(src.con, vf_model, ticker)
                      if vf_model else None)
            if vf_now:
                st.markdown(ui.stats([
                    ("VOL MODEL 20D", f"{vf_now['volatility']:.0%}", "amber"),
                    ("VOL 60D", f"{vf_now['trailing_60d']:.0%}", ""),
                    ("80% RANGE", f"{vf_now['low']:,.2f} – {vf_now['high']:,.2f}", ""),
                ]), unsafe_allow_html=True)
                st.caption(
                    f"Range for the close 20 sessions after {vf_now['date']}, "
                    "centred on today's close: it describes how far the price "
                    "may move, not which way. Pooled Log-HAR on "
                    f"{vf_model.series} US shares, fitted on data to "
                    f"{vf_model.asof}. "
                    "Measured 2014–2026 on 200 US shares, refitted yearly: "
                    "21 % lower forecast error than the 60-day figure, and the "
                    "range held 78 % of the time (75–83 % by year). A frozen "
                    "price or under a year of history gives no number."
                )


def _sec_financials(c) -> None:
    """FA and ERN: the history as first filed, quarters, surprises, restatements."""
    ticker, cik = c.ticker, c.cik
    upcoming = rs.next_report_estimate(cfg.CACHE_DIR / SUBMISSIONS, cik)
    if upcoming:
        st.markdown(ui.stats([
            ("NEXT REPORT (EST.)", str(upcoming["date"]), "amber"),
            ("IN DAYS", str(upcoming["days_away"]), ""),
            ("LAST FILED", str(upcoming["last_filed"]), ""),
        ]), unsafe_allow_html=True)
        st.caption("Estimated from last year's filing for the same quarter, moved forward "
                   "364 days; an announced date, where there is one, is in the calendar.")
    annual = rs.annual_history(src.con, cik)
    filed_in = rs.reporting_currency(src.con, cik)
    if filed_in != "USD":
        st.caption(
            f"Figures below are in {filed_in}, as filed. The screen and the "
            "valuation convert them to USD at the exchange rate of their date."
        )
    if annual.empty:
        st.info("No annual XBRL figures for this filer.")
    else:
        show = annual.sort_values("year", ascending=False).head(12)
        st.markdown(ui.grid(pd.DataFrame({
            "year": show.year.astype(int).astype(str),
            "revenue": show.revenue.map(ui.money),
            "net income": show.net_income.map(ui.money),
            "op cash flow": show.operating_cash_flow.map(ui.money),
            "capex": show.capex.map(ui.money),
            "free cash flow": show.free_cash_flow.map(ui.money),
            "equity": show.book_value.map(ui.money),
            "assets": show.total_assets.map(ui.money),
            "margin": show.net_margin.map(lambda v: ui.pct(v, 1)),
            "roe": show.return_on_equity.map(lambda v: ui.pct(v, 1)),
        }), numeric={"revenue", "net income", "op cash flow", "capex",
                     "free cash flow", "equity", "assets"},
            signed={"margin", "roe"}, ticker_col="year"),
            unsafe_allow_html=True)
        st.caption("As first reported. Later revisions appear below, not here.")

    qt = rs.quarterly_trend(src.con, cik)
    if not qt.empty and "revenue" in qt:
        st.markdown("#### By quarter")
        st.bar_chart(qt.set_index("period_end")[["revenue"]],
                     height=180, color=[ui.AMBER])
        st.caption(
            "A company can grow every year while shrinking for three straight "
            "quarters. Annual rows hide that.")

    eh, eh_err = ac.attempt(ac.earnings_history, ticker)
    if eh_err:
        st.caption(f"Earnings against expectations: Yahoo unreachable ({eh_err}).")
    elif not eh.empty:
        st.markdown("#### Earnings against expectations")
        dcol = eh.columns[0]
        st.markdown(ui.grid(pd.DataFrame({
            "date": eh[dcol].dt.date.astype(str),
            "estimate": eh.get("EPS Estimate", pd.Series(dtype=float)).map(ui.num),
            "reported": eh.get("Reported EPS", pd.Series(dtype=float)).map(ui.num),
            "surprise": eh.get("Surprise(%)", pd.Series(dtype=float)).map(
                lambda v: "—" if pd.isna(v) else f"{v:+.1f}%"),
        }), numeric={"estimate", "reported"}, signed={"surprise"},
            ticker_col="date"), unsafe_allow_html=True)
        st.caption("Consensus estimates, so this section is opinion meeting fact.")

    rest = rs.restatements(src.con, cik)
    if not rest.empty:
        st.markdown("#### Restated after the fact")
        top = rest.head(10)
        st.markdown(ui.grid(pd.DataFrame({
            "metric": top.metric,
            "period": top.period_end.astype(str),
            "first said": top.first_reported.map(ui.money),
            "later said": top.later_value.map(ui.money),
            "change": top.change.map(lambda v: ui.pct(v, 1)),
            "revised on": top.later_filed.astype(str),
        }), numeric={"first said", "later said"}, signed={"change"},
            ticker_col="metric"), unsafe_allow_html=True)
        st.caption(
            "Revisions are routine and usually small. A large one, or a "
            "pattern of them, is something a screen cannot see.")


def _sec_relationships(c) -> None:
    """SPLC: counterparties named in the 10-K, each with a checked quote."""
    ticker, cik, prof = c.ticker, c.cik, c.prof
    st.write(
        "Who this company says it depends on — read out of its own annual "
        "report. Every line carries a passage copied from the filing, and "
        "each passage is checked against the document in code before it is "
        "shown. A relationship the filing does not state does not appear "
        "here, however well known it is."
    )
    tenk = rs.filings(cfg.CACHE_DIR / SUBMISSIONS, cik, forms=["10-K"], limit=1)
    if tenk.empty and is_compustat(cik):
        st.info("A Compustat Global company has no SEC annual report to read.")
    elif tenk.empty:
        st.info(
            "No 10-K in the local archive for this filer. Foreign issuers "
            "file a 20-F instead, which this does not read yet."
        )
    elif not secret("ANTHROPIC_API_KEY"):
        st.warning("Needs ANTHROPIC_API_KEY in .env.local.")
    else:
        row = tenk.iloc[0]
        st.caption(f"From the 10-K filed {row.filed} for the period {row.period}.")
        if st.button("Read the filing", key=f"read_10k_{ticker}", width="stretch"):
            with st.spinner("Downloading and reading Item 1"):
                try:
                    text = bz.fetch_filing_text(row.url)
                    found = bz.extract(text, prof["name"])
                except Exception as exc:                            # noqa: BLE001
                    found = None
                    st.error(f"Could not read that filing: {ui.error_text(exc)}")
            if found is not None:
                st.session_state[f"rel_{ticker}"] = found

        found = st.session_state.get(f"rel_{ticker}")
        if found:
            rel = found["relationships"]
            st.markdown(ui.stats([
                ("NAMED", str(len(rel)), "amber" if len(rel) else ""),
                ("UNNAMED NOTES", str(len(found["notes"])), ""),
                ("QUOTES REJECTED", str(found["rejected"]),
                 "down" if found["rejected"] else "up"),
            ]), unsafe_allow_html=True)
            if found["rejected"]:
                st.caption(
                    f"{found['rejected']} claim(s) were dropped because the "
                    "quote could not be found in the filing. That is the "
                    "check working, not a failure."
                )
            if len(rel):
                for r in rel.itertuples():
                    st.markdown(
                        f"**{r.counterparty}** — {r.relationship}",
                    )
                    # Escaped: the passage comes out of a filing via a model.
                    st.markdown(
                        f"<div style='border-left:2px solid {ui.AMBER_DIM};"
                        f"padding-left:0.7rem;color:{ui.MUTED};"
                        f"font-size:0.74rem;margin-bottom:0.5rem'>"
                        f"“{html.escape(str(r.quote))}”</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.info(
                    "The filing names no counterparty. That is common and "
                    "informative: large companies often describe suppliers "
                    "only in the abstract, so the household names you may "
                    "expect are genuinely not in the document."
                )
            if found["notes"]:
                st.markdown("#### Dependencies stated without a name")
                for n in found["notes"]:
                    st.markdown(
                        f"<div style='border-left:2px solid {ui.RULE};"
                        f"padding-left:0.7rem;color:{ui.DATA};"
                        f"font-size:0.74rem;margin-bottom:0.4rem'>"
                        f"“{html.escape(str(n))}”</div>",
                        unsafe_allow_html=True,
                    )
            st.caption(f"[Open the filing at the SEC]({row.url})")


def _sec_valuation(c) -> None:
    """RV, COMP, WACC and DCF: own history, trading comps, cost of capital, DCF."""
    ticker, cik, me, enriched = c.ticker, c.cik, c.me, c.enriched
    sheet, implied_v, b, out_dcf, grid = {}, None, None, None, None
    val = rs.valuation_history(src.con, ticker, cik)
    if val.empty:
        st.info("Not enough history to rebuild past multiples.")
    else:
        st.line_chart(val.set_index("year")[["price_to_earnings", "price_to_book"]],
                      height=210, color=[ui.AMBER, ui.CYAN])
        st.caption(
            "Each year's multiple uses that year's price and the figures public "
            "then. Today's price against old earnings would describe nothing "
            "that ever happened.")
        st.markdown(ui.grid(pd.DataFrame({
            "year": val.year.astype(str),
            "price": val.price.map(ui.num),
            "mkt cap": val.market_cap.map(ui.money),
            "p/e": val.price_to_earnings.map(ui.num),
            "p/b": val.price_to_book.map(ui.num),
            "p/s": val.price_to_sales.map(ui.num),
        }), numeric={"price", "mkt cap", "p/e", "p/b", "p/s"},
            ticker_col="year"), unsafe_allow_html=True)

    # --------------------------------------------------- trading comps
    st.markdown("#### Trading comps")
    suggested = rs.peers(enriched, ticker).ticker.astype(str).tolist()[:8]
    chosen_peers = st.multiselect(
        "Peers", options=sorted(enriched.ticker.astype(str).unique()),
        default=suggested, key=f"peers_{ticker}",
        help="Suggested: same SEC industry, closest in size. Add or remove "
             "names — a comps set is a judgement, not a formula.")
    sheet = comps.sheet(enriched, ticker, chosen_peers) if chosen_peers else {}
    if not sheet:
        st.info("Pick at least one peer.")
    else:
        labels = {c: l for c, l, _ in comps.COLUMNS}
        pct_cols = {"ebitda_margin", "return_on_invested_capital",
                    "revenue_growth"}

        def _fmt(col, v):
            if v is None or pd.isna(v):
                return "—"
            return ui.pct(v, 1) if col in pct_cols else f"{v:,.1f}x"

        def _vs(col, v):
            if v is None or pd.isna(v):
                return "—"
            if col in pct_cols:
                return f"{v * 100:+.1f} pp"
            if col == "net_debt_to_ebitda":
                return f"{v:+.1f}x"
            return ui.pct(v, 0)

        shown = [c for c, _, _ in comps.COLUMNS if c in sheet["rows"].columns]
        body = pd.DataFrame({
            "company": sheet["rows"].ticker.astype(str),
            "mkt cap": sheet["rows"].market_cap.map(ui.money),
            "EV": sheet["rows"].enterprise_value.map(ui.money),
            **{labels[c]: [_fmt(c, v) for v in sheet["rows"][c]] for c in shown},
        })
        summary = pd.DataFrame({
            "company": ["peer median", "peer mean", "peer high", "peer low",
                        f"{ticker} vs median"],
            "mkt cap": [""] * 5, "EV": [""] * 5,
            **{labels[c]: [_fmt(c, sheet["stats"].at[k, c])
                           for k in ("median", "mean", "high", "low")]
               + [_vs(c, sheet["vs_median"].get(c))] for c in shown},
        })
        st.markdown(ui.grid(pd.concat([body, summary], ignore_index=True),
                            numeric=set(labels.values()) | {"mkt cap", "EV"},
                            ticker_col="company"), unsafe_allow_html=True)
        st.caption(
            f"First row is {ticker}; statistics are over the peers only. "
            "Point-in-time figures in USD as of the screen date.")

        implied_v = comps.implied(enriched, ticker, sheet["stats"])
        if not implied_v.empty:
            st.markdown("#### What the peer multiples imply")
            st.markdown(ui.grid(pd.DataFrame({
                "multiple": implied_v["multiple"],
                "peer median": implied_v["peer median"].map(
                    lambda v: f"{v:,.1f}x"),
                "applied to": implied_v["applied to"].str.replace("_ttm", " TTM")
                    .str.replace("_", " "),
                "implied equity": implied_v["implied equity value"].map(ui.money),
                "per share": implied_v["implied price"].map(ui.num),
                "vs price": implied_v["vs price"],
            }).assign(**{"vs price": lambda d: d["vs price"].map(
                lambda v: ui.pct(v, 0))}),
                numeric={"peer median", "implied equity", "per share"},
                signed={"vs price"}, ticker_col="multiple"),
                unsafe_allow_html=True)
            st.caption(
                "Median multiple × the company's own figure. EV multiples are "
                "taken to equity by subtracting net debt; equity multiples are "
                "not. A multiple is skipped where the company's figure is not "
                "positive. The spread between rows is the honest answer: "
                "comps give a range, not a price.")

    if me is not None:
        zs = comps.against_history(val, me)
        if not zs.empty:
            st.markdown("#### Against its own history")
            st.markdown(ui.grid(pd.DataFrame({
                "multiple": zs["multiple"],
                "now": zs["now"].map(lambda v: f"{v:,.1f}x"),
                "own average": zs["own average"].map(lambda v: f"{v:,.1f}x"),
                "years": zs["years"].astype(str),
                "z-score": zs["z-score"].map(lambda v: f"{v:+.1f}"),
            }), numeric={"now", "own average", "years"}, signed={"z-score"},
                ticker_col="multiple"), unsafe_allow_html=True)
            st.caption("How many standard deviations today's multiple sits "
                       "from the company's own yearly average. Past years "
                       "use the price and figures of their own date.")

    # ------------------------------------------ cost of capital and DCF
    if me is not None:
        st.markdown("#### Cost of capital and DCF")
        rates = _risk_free_now(DATA_KEY)
        beta_raw = (_price_stats(ticker, asof, DATA_KEY) or {}).get("beta")
        w1, w2, w3, w4 = st.columns(4)
        rf_in = w1.number_input(
            "Risk-free %", 0.0, 15.0, round(rates["long"] * 100, 2), 0.05,
            key=f"rf_{ticker}",
            help=f"{rates['long_source']}: the long rate, because a company is valued "
                 "over decades. Options use the 13-week bill instead.")
        beta_in = w2.number_input(
            "Beta (raw)", -1.0, 4.0, round(min(max(float(beta_raw or 1.0), -1.0), 4.0), 2), 0.05,
            key=f"beta_{ticker}",
            help="Daily returns against the S&P 500 total return over the "
                 "stored history." if beta_raw else "No beta measured; 1.0.")
        erp_in = w3.number_input("Equity risk premium %", 2.0, 9.0,
                                 W.DEFAULT_ERP * 100, 0.25, key=f"erp_{ticker}")
        blume = w4.toggle("Blume-adjusted beta", value=True, key=f"blume_{ticker}")
        # Growth and growth-forever start from the screen's assumptions — one set, so
        # the P/IV column and this DCF cannot quietly disagree; the discount rate does
        # differ, by design: here it is the WACC, there the required return.
        shared = _assumptions()
        g1, g2, g3 = st.columns(3)
        growth_in = g1.number_input("FCFF growth %/yr", -10.0, 30.0,
                                    float(shared.growth_rate * 100), 0.5,
                                    key=f"g_{ticker}")
        years_in = g2.number_input("Years", 1, 15, 5, 1, key=f"yrs_{ticker}")
        tg_in = g3.number_input("Growth forever %", -2.0, 5.0,
                                float(shared.terminal_growth * 100), 0.25,
                                key=f"tg_{ticker}")
        st.caption("Growth and growth forever start at the screen's intrinsic-value "
                   "assumptions (SCREEN → filters); the discount rate here is the WACC "
                   "below, not the screen's required return.")
        w_inputs = W.Inputs(risk_free=rf_in / 100, beta=beta_in,
                            equity_risk_premium=erp_in / 100, adjust_beta=blume,
                            growth=growth_in / 100, years=int(years_in),
                            terminal_growth=tg_in / 100)
        b = W.build(me, w_inputs)
        st.markdown(ui.grid(pd.DataFrame({
            "component": ["Cost of equity (CAPM)", "Cost of debt, pre-tax",
                          "Tax rate", "Weight of equity", "Weight of debt",
                          "WACC"],
            "value": [f"{b['cost_of_equity']:.2%}",
                      f"{b['cost_of_debt_pre_tax']:.2%}",
                      f"{b['tax_rate']:.1%}", f"{b['weight_equity']:.1%}",
                      f"{b['weight_debt']:.1%}", f"{b['wacc']:.2%}"],
            "from": [f"{rf_in:.2f}% + {b['beta']:.2f} × {erp_in:.2f}%",
                     b["cost_of_debt_source"], str(b["tax_rate_source"]),
                     f"market cap {ui.money(b['equity_value'])}",
                     f"book debt {ui.money(b['debt_value'])}", ""],
        }), numeric={"value"}, ticker_col="component"),
            unsafe_allow_html=True)
        out_dcf = W.dcf(me, b["wacc"], w_inputs)
        if "error" in out_dcf:
            st.info(f"No DCF: {out_dcf['error']}.")
        else:
            tone = "up" if out_dcf["vs_price"] >= 0 else "down"
            st.markdown(ui.stats([
                ("FCFF TTM", ui.money(out_dcf["fcff"]), ""),
                ("ENTERPRISE VALUE", ui.money(out_dcf["enterprise_value"]), ""),
                ("EQUITY VALUE", ui.money(out_dcf["equity_value"]), ""),
                ("PER SHARE", ui.num(out_dcf["per_share"]), tone),
                ("VS PRICE", ui.pct(out_dcf["vs_price"], 0), tone),
                ("TERMINAL SHARE", f"{out_dcf['terminal_share']:.0%}", ""),
            ]), unsafe_allow_html=True)
            grid = W.sensitivity(me, b["wacc"], w_inputs)
            st.markdown("Per share — WACC down the side, growth forever across")
            st.markdown(ui.grid(
                grid.map(ui.num).reset_index().rename(columns={"index": "WACC"}),
                numeric=set(grid.columns), ticker_col="WACC"),
                unsafe_allow_html=True)
            st.caption(
                "FCFF = operating cash flow − capex + interest × (1 − tax), "
                "grown for the stated years, then forever; discounted at the "
                "WACC, less net debt, per listed share. Debt is weighted at "
                "book value. When the terminal share is high, the answer is "
                "mostly the growth-forever guess — which is what the grid shows.")

    # Everything the workbook needs is read here, on the app's thread; the button only
    # assembles it when clicked, on another thread that must not query the store.
    annual, quarterly = rs.annual_history(src.con, cik), rs.quarterly_trend(src.con, cik)
    stats_ = sheet.get("stats") if sheet else None
    _workbook_button(
        f"Export {ticker} to Excel",
        lambda: {"Annual as filed": annual, "Quarterly": quarterly,
                 "Valuation history": val,
                 "Comps": sheet.get("rows", pd.DataFrame()) if sheet else pd.DataFrame(),
                 "Comps stats": stats_.reset_index(names="statistic")
                 if stats_ is not None else pd.DataFrame(),
                 "Implied by peers": implied_v if implied_v is not None else pd.DataFrame(),
                 "WACC": pd.DataFrame([b or {}]),
                 "DCF": pd.DataFrame([dict(out_dcf or {})]),
                 "DCF sensitivity": grid.reset_index(names="wacc")
                 if isinstance(grid, pd.DataFrame) else pd.DataFrame()},
        title=f"{ticker} — research",
        source=f"SEC XBRL as first filed (history in "
               f"{rs.reporting_currency(src.con, cik)}); screen figures in USD; daily "
               "closes", stem=f"{ticker}_research", key=f"export_{ticker}")


def _sec_options(c) -> None:
    """OMON, OVDV and SKEW: what the option chain prices for this company."""
    ticker, prof, quote = c.ticker, c.prof, c.quote
    spot = (quote or {}).get("price") or prof.get("price")
    if not spot:
        st.info("No price available, so nothing can be backed out.")
    else:
        # The rate is not an opinion. It is quoted every day, it is already
        # downloaded, and a slider on it is a knob for tuning a fair value
        # until it says what you hoped.
        rates = _risk_free_now(DATA_KEY)
        rate, rate_src = rates["short"], rates["short_source"]
        # Trailing dividend yield from the store: solved with none, a payer's
        # puts look dearer than its calls for no other reason (options.py).
        div_q = op.trailing_dividend_yield(src.con, ticker)
        st.caption(f"Risk-free rate {rate:.2%} — {rate_src}. Dividend yield {div_q:.1%}, "
                   "trailing, from the stored closes.")
        with st.spinner("Reading the option chain"):
            surf, surf_err = ac.attempt(ac.surface, ticker, spot, 10, rate, div_q)
        if surf_err:
            st.warning(f"The option chain could not be read: {surf_err}. Nothing is "
                       "kept from a failed attempt, so the next visit asks again.")
        elif surf.empty:
            st.info(
                "No usable option contracts. Either this company has no "
                "listed options, or nothing in the chain carried a price "
                "that a volatility could be solved from."
            )
        else:
            st.caption(
                f"{len(surf)} contracts kept · source: {surf.attrs.get('mode')} · "
                "volatility is solved from the option price here, not taken "
                "from the data provider's own field, which was measured empty."
            )
            ts = op.term_structure(surf)

            pick_row = ts[ts.days.between(20, 120)]
            default_expiry = (pick_row.iloc[0] if len(pick_row) else ts.iloc[-1]).expiry
            expiry = st.selectbox(
                "Expiry", options=ts.expiry.tolist(),
                index=ts.expiry.tolist().index(default_expiry),
                format_func=lambda e: f"{e}  ({int(ts.set_index('expiry').loc[e,'days'])}d)",
                key=f"opt_expiry_{ticker}",
            )
            one = surf[surf["expiry"] == expiry]
            atm = op.atm_volatility(one)
            days = int(ts.set_index("expiry").loc[expiry, "days"])

            if atm:
                em = op.expected_move(spot, atm, max(days, 1))
                st.markdown(ui.stats([
                    ("SPOT", ui.num(spot), ""),
                    ("ATM VOL", f"{atm:.1%}", "amber"),
                    ("DAYS", str(days), ""),
                    ("EXPECTED MOVE", f"±{em['pct']:.2%}", ""),
                    ("1 SIGMA LOW", ui.num(em["low_1"]), "down"),
                    ("1 SIGMA HIGH", ui.num(em["high_1"]), "up"),
                ]), unsafe_allow_html=True)
                st.caption(
                    f"The market is pricing roughly a two-in-three chance of "
                    f"{ticker} finishing between {em['low_1']:,.2f} and "
                    f"{em['high_1']:,.2f} on {expiry}, and about 95% between "
                    f"{em['low_2']:,.2f} and {em['high_2']:,.2f}. That rests on "
                    "returns being normally distributed, which they are not — "
                    "real markets produce big moves more often than the curve "
                    "allows, so the outer band is the optimistic reading."
                )
                # --- the cone: the market's range for every day ---------
                cone = op.expected_move_cone(spot, ts)
                if not cone.empty:
                    today = dt.date.today()
                    x = [today + dt.timedelta(days=int(d)) for d in cone.day]
                    fig_c = go.Figure()
                    for lo_col, hi_col, shade, name in (
                        ("low_2", "high_2", "rgba(255, 158, 27, 0.10)", "95%"),
                        ("low_1", "high_1", "rgba(255, 158, 27, 0.22)", "68%"),
                    ):
                        fig_c.add_trace(go.Scatter(
                            x=x, y=cone[hi_col], name=name, mode="lines",
                            line=dict(color=ui.AMBER_DIM, width=0.5),
                            hoverinfo="skip"))
                        fig_c.add_trace(go.Scatter(
                            x=x, y=cone[lo_col], name=name, mode="lines",
                            line=dict(color=ui.AMBER_DIM, width=0.5),
                            fill="tonexty", fillcolor=shade, hoverinfo="skip"))
                    fig_c.add_trace(go.Scatter(
                        x=x, y=cone["mid"], name="spot today", mode="lines",
                        line=dict(color=ui.DATA, width=1, dash="dot")))
                    # Where options actually exist. Between them the width is
                    # interpolated, and saying so is the difference between a
                    # measurement and a drawing.
                    fig_c.add_trace(go.Scatter(
                        x=[today + dt.timedelta(days=int(d)) for d in ts.days],
                        y=[spot] * len(ts),
                        mode="markers", name="expiries",
                        marker=dict(color=ui.CYAN, size=6, symbol="line-ns-open"),
                        hovertext=[f"{e} · ATM {v:.1%}"
                                   for e, v in zip(ts.expiry, ts.atm_iv)],
                        hoverinfo="text"))
                    _layout(fig_c, showlegend=False, hovermode="x unified",
                            xaxis=dict(gridcolor=ui.RULE, color=ui.MUTED),
                            yaxis=dict(gridcolor=ui.RULE, color=ui.MUTED, side="right"))
                    st.markdown("#### Where the market thinks the price will be")
                    st.plotly_chart(fig_c, width="stretch",
                                    key=f"cone_{ticker}")
                    widest = cone.iloc[-1]
                    st.caption(
                        f"The band opens with the square root of time, because "
                        f"uncertainty accumulates with time and not with its "
                        f"square. By {x[-1]} the market is charging for a "
                        f"two-in-three range of {widest.low_1:,.2f} to "
                        f"{widest.high_1:,.2f}. Width comes from the at-the-money "
                        "volatility of the nearest expiries on each side "
                        "(marked), interpolated in between. It is what options "
                        "cost, not a forecast — and the outer band is the "
                        "optimistic reading, because real returns have fatter "
                        "tails than the bell curve it assumes."
                    )

                stats_now = _price_stats(ticker, asof, DATA_KEY)
                # the last 60 sessions: the horizon of the nearest expiries
                cmp_ = op.implied_vs_realised(atm, stats_now.get("volatility_60d"))
                if cmp_:
                    tone = "up" if cmp_["premium"] > 0 else "down"
                    st.markdown(ui.stats([
                        ("IMPLIED", f"{cmp_['implied']:.1%}", "amber"),
                        ("REALISED", f"{cmp_['realised']:.1%}", ""),
                        ("PREMIUM", f"{cmp_['premium']:+.1%}", tone),
                    ]), unsafe_allow_html=True)
                    st.caption(
                        "Realised is the last 60 sessions, annualised. "
                        "Implied above realised is the normal state — option "
                        "sellers want paying for the risk. A large gap either "
                        "way is the interesting case, and neither direction is "
                        "a recommendation."
                    )

            # --- the distribution the prices imply ----------------------
            dist = op.implied_distribution(one, spot, max(days, 1), rate, div_q)
            st.markdown(f"#### What the {expiry} prices imply")
            if dist.get("usable"):
                qd = dist["quantiles"]
                st.markdown(ui.stats([
                    ("MEDIAN", ui.num(qd[50]), "amber"),
                    ("10% BELOW", ui.num(qd[10]), "down"),
                    ("10% ABOVE", ui.num(qd[90]), "up"),
                    ("P(< -10%)", f"{dist['p_down_10']:.0%}", "down"),
                    ("P(> +10%)", f"{dist['p_up_10']:.0%}", "up"),
                ]), unsafe_allow_html=True)
                curve = dist["curve"]
                curve = curve[curve.price.between(qd[5] * 0.85, qd[95] * 1.15)]
                fig_d = go.Figure()
                fig_d.add_trace(go.Scatter(
                    x=curve.price, y=curve.bell_curve, name="bell curve (ATM vol)",
                    mode="lines", line=dict(color=ui.MUTED, width=1, dash="dot")))
                fig_d.add_trace(go.Scatter(
                    x=curve.price, y=curve.density, name="from the chain",
                    mode="lines", fill="tozeroy",
                    fillcolor="rgba(255, 158, 27, 0.18)",
                    line=dict(color=ui.AMBER, width=1.5)))
                fig_d.add_vline(x=spot, line=dict(color=ui.DATA, width=1, dash="dot"))
                _layout(fig_d, hovermode="x unified",
                        legend=dict(orientation="h", y=1.08, x=0),
                        xaxis=dict(gridcolor=ui.RULE, color=ui.MUTED),
                        yaxis=dict(gridcolor=ui.RULE, color=ui.MUTED,
                                   showticklabels=False))
                st.plotly_chart(fig_d, width="stretch", key=f"dist_{ticker}")
                st.caption(
                    f"The probability of each price on {expiry}, read from "
                    f"{dist['contracts']} strikes (Breeden-Litzenberger), against "
                    "the bell curve the expected move assumes. The chain puts "
                    f"{dist['p_down_10']:.0%} on a fall of more than 10 % where "
                    f"the bell curve has {dist['ref_p_down_10']:.0%}. These are "
                    "risk-neutral odds: they include what buyers pay for "
                    "protection, so they lean further to the downside than the "
                    "real-world odds. What the market charges for, not a "
                    f"forecast. {dist['beyond_quotes']:.0%} of the probability "
                    "lies beyond the last quoted strikes and comes from holding "
                    f"the smile flat there. Prices: {surf.attrs.get('mode', 'n/a')}."
                )
            else:
                st.caption(
                    "Too few strikes on both sides of the price, or most of the "
                    "probability would come from beyond the quoted strikes, so "
                    "no distribution is drawn for this expiry."
                )

            # --- the put-call volatility spread (SmileSlope) ------------
            st.markdown("#### Put-call volatility spread")
            near_exp = sorted(
                [(e, int(d)) for e, d in zip(ts.expiry, ts.days) if 7 <= d <= 75],
                key=lambda x: abs(x[1] - op.SIGNAL_DAYS))[:2]
            with st.spinner("Reading full chains near 30 days"):
                full = [(d, ac.attempt(ac.chain, ticker, e, spot, rate, div_q)[0])
                        for e, d in near_exp]
            slope = op.smile_slope([(d, ch) for d, ch in full
                                    if ch is not None and not ch.empty],
                                   r=rate, q=div_q)
            if slope.get("value") is not None:
                snap = _option_snapshot(dt.date.today())
                pct = (op.percentile(slope["value"], snap.smile_slope)
                       if snap is not None else None)
                st.markdown(ui.stats([
                    ("PUT - CALL VOL", f"{slope['value'] * 100:+.1f} pts", "amber"),
                    ("AT", f"{slope['days']}d", ""),
                    ("PAIRS", str(slope["pairs"]), ""),
                    ("RANK", "—" if pct is None else f"{pct:.0%}", ""),
                ]), unsafe_allow_html=True)
                rank_note = (
                    f"RANK places it among {len(snap)} US shares recorded on "
                    f"{snap.attrs['day']}: 100 % = dearest puts. "
                    if pct is not None else
                    "No recent cross-section is recorded yet, so it cannot be "
                    "ranked (scripts/snapshot_options.py collects one daily). ")
                st.caption(
                    "Implied volatility of puts minus calls at the same strikes "
                    f"near the money, {slope['method']}, dividend yield "
                    f"{div_q:.1%} included. {rank_note}In published data "
                    "(Yan 2011; Chen & Zimmermann's open replication, "
                    "value-weighted tenths, measured 02.10.2026) shares with the "
                    "dearest puts earned about 0.9 % a month less over the "
                    "following month than those with the cheapest, 2015-2024 "
                    "(t 2.8). It ranks shares against each other and says "
                    "nothing certain about this one; it has not been tested on "
                    "this app's own data, and the snapshots exist so that it can "
                    "be."
                )
            else:
                st.caption(slope.get("rejected") or
                           "No expiry between 10 and 60 days with puts and calls "
                           "at the same strikes near the money.")

            # --- the surface, in three dimensions -----------------------
            try:
                grid_ = surf.pivot_table(
                    index="days", columns="moneyness", values="iv", aggfunc="mean"
                ).sort_index()
                if grid_.shape[0] >= 2 and grid_.shape[1] >= 3:
                    z = grid_.interpolate(axis=1, limit_direction="both").to_numpy()
                    fig = go.Figure(go.Surface(
                        x=grid_.columns.to_numpy(), y=grid_.index.to_numpy(), z=z,
                        colorscale=[[0, "#3A1D00"], [0.5, ui.AMBER], [1, "#FFE3B0"]],
                        colorbar=dict(title="IV", tickformat=".0%",
                                      outlinewidth=0, tickfont=dict(color=ui.MUTED)),
                    ))
                    _layout(fig, 420,
                        scene=dict(
                            xaxis=dict(title="strike / spot", backgroundcolor=ui.BG,
                                       gridcolor=ui.RULE, color=ui.MUTED),
                            yaxis=dict(title="days to expiry", backgroundcolor=ui.BG,
                                       gridcolor=ui.RULE, color=ui.MUTED),
                            zaxis=dict(title="implied vol", backgroundcolor=ui.BG,
                                       gridcolor=ui.RULE, color=ui.MUTED,
                                       tickformat=".0%"),
                            camera=dict(eye=dict(x=1.6, y=-1.5, z=0.8)),
                        ),
                    )
                    st.markdown("#### Volatility surface")
                    st.plotly_chart(fig, width="stretch", key=f"surface_{ticker}")
                    st.caption(
                        "Drag to rotate. Left of 1.0 are puts, right are calls. "
                        "The lift towards the left edge is the equity smirk: "
                        "downside protection costs more volatility than upside "
                        "speculation, because crashes happen faster than rallies."
                    )
            except Exception as exc:
                st.caption(f"Surface unavailable: {exc}")

            c1, c2 = st.columns(2, gap="medium")
            with c1:
                st.markdown("#### Smile")
                sk = op.skew(surf, expiry)
                if not sk.empty:
                    st.scatter_chart(
                        sk.rename(columns={"iv": "implied vol"}),
                        x="moneyness", y="implied vol", color="kind", height=230,
                    )
            with c2:
                st.markdown("#### Term structure")
                if len(ts) >= 2:
                    st.line_chart(ts.set_index("days")[["atm_iv"]],
                                  height=230, color=[ui.AMBER])
                    st.caption(
                        "A near-dated spike usually means something specific "
                        "is coming — earnings, a ruling, a vote."
                    )


def _sec_exposure(c) -> None:
    """HRA: what this share has moved with — commodities, rates, one event."""
    ticker = c.ticker
    st.markdown("#### Commodities")
    links = _commodity_links(ticker, DATA_KEY)
    if links.empty:
        st.info("Not enough overlapping price history to measure this.")
    else:
        _links_table(links, first="commodity")
        top = links.iloc[0]
        direction = "with" if top.correlation > 0 else "against"
        st.caption(
            f"Strongest link: {top.commodity}, moving {direction} the share "
            f"(beta {top.beta:+.2f} — about {abs(top.beta):.2f}% per 1% move). "
            "This is association, not cause. A miner and its metal move "
            "together because it is the product; an airline and oil move "
            "apart because it is a cost. The arithmetic cannot tell those "
            "apart, and a short window invents relationships that are not there."
        )
    _rate_exposure(ticker)


def _sec_analysts(c) -> None:
    """ANR: the sell side's ratings and targets, reported as data."""
    ticker = c.ticker
    view, err = ac.attempt(ac.analyst_view, ticker)
    if err:
        st.warning(f"Analyst data unreachable ({err}).")
    elif not view:
        st.info("No analyst data available for this ticker.")
    else:
        r = view.get("ratings")
        if r:
            st.markdown(ui.stats(
                [(k.upper(), str(v), "amber" if v else "") for k, v in r.items()]
            ), unsafe_allow_html=True)
            st.caption(
                f"Consensus as of {view.get('ratings_period', '—')}. These are "
                "Wall Street analysts' own ratings, reported as data — this "
                "app does not rate anything."
            )
        t_ = view.get("targets") or {}
        if t_.get("mean"):
            cells = [(k.upper(), ui.num(v), "")
                     for k, v in t_.items() if isinstance(v, (int, float))]
            st.markdown("#### Price targets")
            st.markdown(ui.stats(cells), unsafe_allow_html=True)
        ch = view.get("changes")
        if ch is not None and len(ch):
            st.markdown("#### Recent rating changes")
            dcol = ch.columns[0]
            st.markdown(ui.grid(pd.DataFrame({
                "date": pd.to_datetime(ch[dcol]).dt.date.astype(str),
                "firm": ch.get("Firm", pd.Series(dtype=object)).astype(str).str.slice(0, 22),
                "from": ch.get("FromGrade", pd.Series(dtype=object)).astype(str),
                "to": ch.get("ToGrade", pd.Series(dtype=object)).astype(str),
                "action": ch.get("Action", pd.Series(dtype=object)).astype(str),
            }), ticker_col="firm"), unsafe_allow_html=True)
        st.caption(
            "These are opinions, not measurements — the only section here "
            "that is not derived from a filing or a price.")


def _sec_ownership(c) -> None:
    """INS and HDS: insiders and institutions."""
    ticker, cik = c.ticker, c.cik
    st.markdown("#### Insider transactions (Form 4)")
    if is_compustat(cik):
        st.info("A Compustat Global company files with its home regulator, "
                "not the SEC: there are no Form 4 filings to read.")
    elif st.button("Load insider trades from the SEC", key=f"ins_{ticker}"):
        st.session_state[f"ins_loaded_{ticker}"] = True
    if not is_compustat(cik) and st.session_state.get(f"ins_loaded_{ticker}"):
        with st.spinner("Reading the latest Form 4 filings…"):
            trades, ins_err = ac.attempt(ac.insider_trades, cik)
        if ins_err:
            st.warning(f"The SEC could not be reached ({ins_err}). A failed attempt is "
                       "not kept; press the button again later.")
        elif trades.empty:
            st.info("No Form 4 filings with share transactions.")
        else:
            ins = insiders.summary(trades, asof)
            st.markdown(ui.stats([
                ("BOUGHT (12M)", f"{ins['bought_trades']} by "
                 f"{ins['bought_insiders']}", "up" if ins["bought_trades"] else ""),
                ("BOUGHT VALUE", ui.money(ins["bought_value"]), ""),
                ("SOLD (12M)", f"{ins['sold_trades']} by "
                 f"{ins['sold_insiders']}", "down" if ins["sold_trades"] else ""),
                ("SOLD VALUE", ui.money(ins["sold_value"]), ""),
            ]), unsafe_allow_html=True)
            st.markdown(ui.grid(pd.DataFrame({
                "date": trades["date"].astype(str),
                "insider": trades["insider"].astype(str).str.slice(0, 26),
                "role": trades["role"].astype(str).str.slice(0, 30),
                "what": trades["what"],
                "shares": trades["shares"].map(
                    lambda v: f"{v:,.0f}" if pd.notna(v) else "—"),
                "price": trades["price"].map(ui.num),
                "value": trades["value"].map(ui.money),
            }).head(40), numeric={"shares", "price", "value"},
                ticker_col="date"), unsafe_allow_html=True)
            st.caption(
                "The counts above are open-market purchases and sales only — "
                "an insider spending their own money at the market price. "
                "Grants, option exercises, tax withholding and gifts are "
                "listed but not counted: they are pay, not decisions. From "
                "the last 30 Form 4 filings.")
    st.markdown("#### Institutions")
    hold, hold_err = ac.attempt(ac.holders, ticker)
    if hold_err:
        st.caption(f"Institutional holdings unreachable ({hold_err}).")
    elif hold.empty:
        st.info("No institutional holdings reported for this ticker.")
    else:
        st.markdown(ui.grid(pd.DataFrame({
            "holder": hold.get("Holder", pd.Series(dtype=object)).astype(str).str.slice(0, 30),
            "shares": hold.get("Shares", pd.Series(dtype=float)).map(ui.money),
            "% held": hold.get("pctHeld", pd.Series(dtype=float)).map(
                lambda v: "—" if pd.isna(v) else f"{v:.2%}"),
            "value": hold.get("Value", pd.Series(dtype=float)).map(ui.money),
            "as of": hold.get("Date Reported", pd.Series(dtype=object)).astype(str).str.slice(0, 10),
        }), numeric={"shares", "value", "% held"}, ticker_col="holder"),
            unsafe_allow_html=True)
        st.caption(
            "From quarterly 13F filings, so positions are as of the report "
            "date and up to a quarter stale by the time you read them.")


def _sec_dividends(c) -> None:
    """DVD: what the company paid, from Yahoo."""
    ticker, quote = c.ticker, c.quote
    div, _ = ac.attempt(ac.dividend_history, ticker)
    if div is not None and not div.empty:
        st.markdown("#### Dividends")
        st.bar_chart(div.set_index("date")[["dividend"]], height=170,
                     color=[ui.AMBER])
        last12 = div[div.date >= pd.Timestamp.now() - pd.Timedelta(days=365)]
        if len(last12) and quote and quote.get("price"):
            y = last12.dividend.sum() / quote["price"]
            st.caption(
                f"Paid over the last year: {last12.dividend.sum():,.2f} — "
                f"{y:.2%} on the current price.")


def _sec_news(c) -> None:
    """CN: headlines for this company."""
    items, err = ac.attempt(ac.news, c.ticker)
    if err:
        st.warning(f"Headlines unreachable ({err}).")
    elif items.empty:
        st.info("No headlines returned for this ticker.")
    else:
        for row in items.itertuples():
            st.markdown(
                f"**[{row.title}]({row.url})**  \n"
                f"<span style='color:{ui.MUTED};font-size:0.72rem'>"
                f"{html.escape(str(row.when))} · {html.escape(str(row.source))}</span>",
                unsafe_allow_html=True,
            )
            if row.summary:
                st.caption(row.summary)
        st.caption(
            "Headlines and links only. Follow the link for the article — "
            "the text belongs to its publisher.")


def _sec_filings(c) -> None:
    """CF: the filing list from the local SEC archive."""
    cik = c.cik
    fl = rs.filings(cfg.CACHE_DIR / SUBMISSIONS, cik, limit=300)
    if fl.empty and is_compustat(cik):
        st.info("A Compustat Global company files with its home regulator, "
                "not the SEC.")
    elif fl.empty:
        st.info("No filing history in the local SEC archive for this filer.")
    else:
        kinds = ["everything", "reports", "insider trades", "ownership stakes"]
        pick = st.radio("Filter", kinds, horizontal=True,
                        label_visibility="collapsed", key=f"filing_filter_{cik}")
        view = fl
        if pick == "reports":
            view = fl[fl.form.isin(["10-K", "10-Q", "8-K", "DEF 14A"])]
        elif pick == "insider trades":
            view = fl[fl.form.isin(["3", "4", "5"])]
        elif pick == "ownership stakes":
            view = fl[fl.form.str.startswith("SC 13")]
        view = view.head(20)
        if view.empty:
            st.info("Nothing of that kind in the recent filings.")
        else:
            st.markdown(ui.grid(pd.DataFrame({
                "filed": view.filed,
                "form": view.form,
                "what": view.what,
                "period": view.period,
            }), ticker_col="form"), unsafe_allow_html=True)
            st.caption("Open at the SEC: " + " · ".join(
                f"[{r.form} {r.filed}]({r.url})" for r in view.head(6).itertuples()))


@st.cache_data(ttl=600, show_spinner=False)
def _enriched(asof: dt.date, key: str, disc: float, grow: float, term: float) -> pd.DataFrame:
    """The screening frame with every derived metric and the intrinsic value, once per
    build and assumption set (it was rebuilt on every rerun of a company page)."""
    return add_valuation(derive_metrics(_frame(asof, key)),
                         Assumptions(discount_rate=disc, growth_rate=grow,
                                     terminal_growth=term))


def _enriched_now() -> pd.DataFrame:
    a = _assumptions()
    return _enriched(asof, DATA_KEY, a.discount_rate, a.growth_rate, a.terminal_growth)


@st.cache_data(ttl=3600, show_spinner=False)
def _price_only(key: str) -> pd.DataFrame:
    del key
    return rs.price_only_symbols(_market_source().con)


def _price_only_page(sym) -> None:
    """Crypto, a commodity or an index: price history and nothing behind it."""
    st.markdown(ui.kv([
        (sym["name"], "", False),
        ("Symbol", sym.symbol, False),
        ("Kind", sym["kind"], False),
        ("History", f"{sym['from']} to {sym['to']} ({sym.bars:,} bars)", False),
    ]), unsafe_allow_html=True)
    st.caption(
        f"{sym['name']} has no financial statements — not missing: there is no issuer "
        "filing accounts, so book value, earnings and every ratio built on them do not "
        "exist for it. The chart, the risk figures and what moves with it work, and "
        "SCREEN & BACKTEST → SAVINGS PLAN tests regular or dip buying on its history."
    )
    ph = src.con.execute(
        "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date",
        [sym.symbol],
    ).fetch_df()
    if len(ph) > 10:
        ph["date"] = pd.to_datetime(ph["date"])
        st.line_chart(ph.set_index("date")[["close_adj"]].rename(
            columns={"close_adj": sym.symbol}), height=CHART_HEIGHT, color=[ui.AMBER])
    stats_np = _price_stats(sym.symbol, asof, DATA_KEY)
    if stats_np:
        st.markdown(ui.stats(_risk_cells(stats_np)), unsafe_allow_html=True)
        st.caption(f"Max drawdown over the {stats_np['days']:,} stored days; "
                   "volatility over the last year.")
    links = _commodity_links(sym.symbol, DATA_KEY)
    if not links.empty:
        st.markdown("#### Moves with")
        _links_table(links, limit=6)


def _yahoo_page(ysym: str, ybars: pd.DataFrame) -> None:
    """Any Yahoo ticker outside the store: BHP.AX, SAP.DE, 7203.T. Price and volume
    only — there are no SEC filings behind it (world.py, stage 2)."""
    yquote, quote_err = ac.attempt(ac.live_quote, ysym)
    yquote = yquote or {}
    ycur = yquote.get("currency") or "the listing's currency"
    st.markdown(ui.kv([
        ("Symbol", ysym, False),
        ("Currency", ycur, False),
        ("History", f"{ybars.date.iloc[0]} to {ybars.date.iloc[-1]} "
                    f"({len(ybars):,} days, from Yahoo)", False),
    ]), unsafe_allow_html=True)
    if yquote.get("price"):
        tone = "up" if (yquote.get("change") or 0) >= 0 else "down"
        cells = [("LAST", ui.num(yquote["price"]), tone)]
        if yquote.get("change") is not None:
            cells.append(("CHANGE", ui.pct(yquote["change"]), tone))
        st.markdown(ui.stats(cells), unsafe_allow_html=True)
    elif quote_err:
        st.caption(f"Live quote unreachable ({quote_err}).")
    st.caption(
        f"{ysym} is outside the stored universe, so there are no filings behind it: no "
        f"fundamentals, no screen, no point-in-time backtest. The chart and risk figures "
        f"below are in {ycur}; SCREEN & BACKTEST → RULE tests a buy/sell rule on it."
    )
    c1, c2 = st.columns([1, 3])
    yspan = c1.segmented_control("Range", ["6M", "1Y", "5Y"], default="1Y",
                                 label_visibility="collapsed", key="yspan")
    yview = ybars.join(rs.bollinger(ybars)).tail(
        {"6M": 126, "1Y": 252, "5Y": 1260}[yspan or "1Y"])
    ybands = c2.toggle("Bollinger 20d", value=False, key="ybands")
    st.plotly_chart(charts.price_figure(yview, ysym, bands=ybands),
                    width="stretch", key=f"ychart_{ysym}")
    st.markdown(ui.stats(_risk_cells(world.stats(ybars), "MAX DD 1Y")),
                unsafe_allow_html=True)


def _security_page(symbol: str) -> None:
    """Everything about one security. `symbol` is a store ticker, a price-only series
    (index, commodity, crypto) or any Yahoo symbol."""
    if rs.profile(src.con, symbol):
        _equity_page(symbol)
        return
    non_equity = _price_only(DATA_KEY)
    hit = non_equity[non_equity.symbol.str.upper() == symbol.upper()]
    if len(hit):
        _price_only_page(hit.iloc[0])
        return
    ybars, err = ac.attempt(ac.yahoo_bars, symbol.upper())
    if ybars is not None and not ybars.empty:
        _yahoo_page(symbol.upper(), ybars)
    elif err:
        st.warning(f"'{symbol}' is not in the store, and Yahoo could not be reached to "
                   f"look it up ({err}). Try again in a moment.")
    else:
        st.warning(
            f"Nothing matches '{symbol}'. It is not a listing in this store, and Yahoo "
            "has no prices under that symbol. Other exchanges need Yahoo's suffix, e.g. "
            "BHP.AX (ASX), SAP.DE (Xetra), 7203.T (Tokyo)."
        )
        if not non_equity.empty:
            st.caption("Price-only symbols available: "
                       + ", ".join(non_equity.symbol.tolist()))


def _equity_page(ticker: str) -> None:
    prof = rs.profile(src.con, ticker)
    ticker = prof["ticker"]
    cik = int(prof["cik"])
    quote, quote_err = ac.attempt(ac.live_quote, ticker)
    enriched = _enriched_now()
    me = enriched[enriched.ticker.astype(str).str.upper() == ticker.upper()]
    me = me.iloc[0] if len(me) else None

    # Quote strip — always on top, whichever section is open.
    if quote:
        tone = "up" if (quote.get("change") or 0) >= 0 else "down"
        cells = [("LAST", ui.num(quote["price"]), tone)]
        if quote.get("change") is not None:
            cells.append(("CHANGE", ui.pct(quote["change"]), tone))
        for label, key in (("OPEN", "open"), ("HIGH", "day_high"),
                           ("LOW", "day_low"), ("52W HIGH", "year_high"),
                           ("52W LOW", "year_low")):
            if quote.get(key):
                cells.append((label, ui.num(quote[key]), ""))
        st.markdown(ui.stats(cells), unsafe_allow_html=True)
        st.caption(
            "Live quote for this company only. The screener stays on one common "
            f"date ({asof}); mixing a live price into a ranking would favour it."
        )
    elif quote_err:
        st.caption(f"Live quote unreachable ({quote_err}); figures below use the stored "
                   f"closes to {asof}.")
    else:
        st.caption(f"No live quote for {ticker}; figures below use the stored closes.")

    # ------------------------------------------------------------ AI summary
    have = ai_summary.stored(store.con, ticker, data_fp)
    with st.expander("AI summary", expanded=bool(have)):
        a1, a2 = st.columns([3, 1])
        label = "Write again" if have else "Write AI summary"
        if a2.button(label, key=f"ai_sum_{ticker}", width="stretch"):
            if not secret("ANTHROPIC_API_KEY"):
                st.error("ANTHROPIC_API_KEY missing from .env.local.")
            else:
                with st.spinner("Reading the filings and prices…"):
                    try:
                        sheet = ai_summary.data_sheet(
                            src.con, ticker, cik, me, asof, frame=enriched)
                        result = ai_summary.summarize(sheet)
                    except Exception as exc:                 # noqa: BLE001
                        result = {"text": None, "error": ui.error_text(exc)}
                if result.get("text"):
                    ai_summary.save(store.con, ticker, data_fp, result)
                    have = ai_summary.stored(store.con, ticker, data_fp)
                else:
                    st.error(result.get("error") or "No summary came back.")
        shown = have or ai_summary.latest(store.con, ticker)
        if shown:
            a1.markdown(shown["text"])
            stamp = pd.Timestamp(shown["created_at"]).strftime("%Y-%m-%d %H:%M")
            st.caption(
                f"Written {stamp} by {shown['model']} from this app's own figures "
                "only — no outside news, no recommendation."
                + ("" if have else " Written on an older data build; the numbers "
                   "may have moved since. Press the button for a fresh one.")
            )
        else:
            a1.caption(
                "A short write-up from the figures on this page — valuation, the "
                "history as first filed, restatements, price behaviour and peers. "
                "The model may only argue from those numbers and does not "
                "recommend buying or selling. Uses your Anthropic API key."
            )

    def _open_research_book(name, amount):
        last = src.con.execute(
            "SELECT COALESCE(close_raw, close) FROM prices WHERE ticker = ? "
            "AND date <= ? ORDER BY date DESC LIMIT 1", [ticker, asof],
        ).fetchone()
        if not last or not last[0]:
            raise ValueError(f"No stored price for {ticker}.")
        cap = float(me["market_cap"]) if me is not None and "market_cap" in me \
            and not pd.isna(me["market_cap"]) else 0.0
        return paper.open_hold(
            store.con, name, "RESEARCH",
            pd.DataFrame({"ticker": [ticker], "weight": [1.0],
                          "price": [float(last[0])],
                          "cost": [settings.cost.round_trip_cost(cap)]}),
            asof, amount)

    _paper_section(
        "RESEARCH", f"research_{ticker}", _open_research_book,
        f"{ticker} {asof}",
        f"Buy {ticker} at the {asof} close and hold it. Every paper book lands "
        "next to the others in PORTFOLIOS → BOOKS.")

    c = SimpleNamespace(ticker=ticker, cik=cik, prof=prof, quote=quote, me=me,
                        enriched=enriched)

    def _valuation(c) -> None:
        _sec_valuation(c)
        # MA: precedent transactions (the old DEALS tab), applied to this company.
        with st.expander("Precedent transactions (MA)",
                         expanded=st.session_state.get("cmd_focus") == "MA"):
            _view_deals(c.ticker)

    def _options(c) -> None:
        _sec_options(c)
        # The position builder (the old OPTIONS tab), on the same underlying.
        with st.expander("Build an options position",
                         expanded=st.session_state.get("cmd_focus") == "OMON"):
            _view_options_builder(c.ticker)

    def _financials(c) -> None:
        _sec_financials(c)
        _sec_dividends(c)

    def _exposure(c) -> None:
        _sec_exposure(c)
        # SPLC: who the company says it depends on, read out of its own 10-K.
        with st.expander("Suppliers and customers named in the 10-K",
                         expanded=st.session_state.get("cmd_focus") == "SPLC"):
            _sec_relationships(c)

    def _overview(c) -> None:
        _sec_overview(c)
        with st.expander("Price table (HP)",
                         expanded=st.session_state.get("cmd_focus") == "HP"):
            _sec_prices(c)

    def _news_and_filings(c) -> None:
        _sec_news(c)
        st.markdown("#### Filings")
        _sec_filings(c)


    # Lazy and isolated: only the open section runs, and one that fails (a chain that
    # does not parse, a filing that cannot be read) leaves the others standing.
    _sections(command.SECURITY, {
        "OVERVIEW": _overview, "FINANCIALS": _financials,
        "VALUATION": _valuation, "OPTIONS": _options, "EXPOSURE": _exposure,
        "ANALYSTS": _sec_analysts, "OWNERSHIP": _sec_ownership,
        "NEWS & FILINGS": _news_and_filings,
    }, c)


def _view_options_builder(default_under: str = "") -> None:
    """The position builder. Legs are saved against the open portfolio; the underlying
    starts at the security in the command line (no hard-coded AAPL any more)."""
    st.markdown("#### Build an options position")
    if _need_portfolio("legs are saved against it."):
        u1, u2 = st.columns([2, 1])
        under = u1.text_input("Underlying", value=default_under,
                              key=f"opt_under_{default_under}",
                              placeholder="a ticker, e.g. the one in the command line"
                              ).strip().upper()
        rates = _risk_free_now(DATA_KEY)
        rate_b, rate_b_src = rates["short"], rates["short_source"]
        u2.markdown(ui.stats([("RISK-FREE", f"{rate_b:.2%}", "")]), unsafe_allow_html=True)
        u2.caption(rate_b_src)

        spot_b, _ = _spot(under) if under else (None, "")
        # The same trailing yield the analysis uses (it was left out here until
        # 02.10.2026, so the builder and OPTIONS solved the same chain differently).
        div_b = op.trailing_dividend_yield(src.con, under) if under else 0.0

        if not under:
            st.caption("Type an underlying, or set one in the command line.")
        elif not spot_b:
            st.warning(f"No price for {under}.")
        else:
            exps, exp_err = ac.attempt(ac.expiries, under)
            if exp_err:
                st.warning(f"The option chain could not be reached ({exp_err}).")
            elif not exps:
                st.warning(f"{under} has no listed options.")
            else:
                st.markdown(ui.stats([
                    ("SPOT", ui.num(spot_b), "amber"),
                    ("EXPIRIES", str(len(exps)), ""),
                ]), unsafe_allow_html=True)

                b1, b2, b3, b4 = st.columns(4)
                exp_b = b1.selectbox("Expiry", exps, index=min(6, len(exps) - 1),
                                     key=f"opt_b_exp_{under}")
                kind_b = b2.selectbox("Type", ["call", "put", "share"], key="opt_b_kind")
                side_b = b3.selectbox("Side", ["buy", "sell"], key="opt_b_side")
                qty_b = b4.number_input("Contracts", 1, 100, 1, key="opt_b_qty")


                if kind_b == "share":
                    strike_b, price_b, iv_b = 0.0, spot_b, 0.0
                    st.caption(f"{qty_b * 100:g} shares at {spot_b:,.2f}.")
                else:
                    ch, _ = ac.attempt(ac.chain, under, exp_b, spot_b, rate_b, div_b)
                    ch = ch if ch is not None else pd.DataFrame()
                    side_ch = ch[ch.kind == kind_b] if not ch.empty else ch
                    if side_ch.empty:
                        st.warning("No usable contracts at that expiry.")
                        strike_b = price_b = iv_b = None
                    else:
                        side_ch = side_ch.sort_values("strike")
                        labels = {
                            float(r.strike): (
                                f"{r.strike:g}   mid {r.mid:,.2f}   iv {r.iv:.0%}"
                                f"   {'ITM' if (r.moneyness < 1) == (kind_b == 'call') else 'OTM'}"
                            )
                            for r in side_ch.itertuples()
                        }
                        nearest = min(labels, key=lambda k: abs(k - spot_b))
                        strike_b = st.selectbox(
                            "Strike", options=list(labels),
                            index=list(labels).index(nearest),
                            format_func=lambda k: labels[k], key=f"opt_b_strike_{under}_{exp_b}_{kind_b}",
                        )
                        picked = side_ch[side_ch.strike == strike_b].iloc[0]
                        price_b, iv_b = float(picked.mid), float(picked.iv)

                if strike_b is not None and st.button(
                    "Add leg", type="primary", width="stretch", key="opt_b_add"
                ):
                    store.add_leg(
                        chosen, under, kind_b, strike_b, exp_b,
                        qty_b if side_b == "buy" else -qty_b,
                        price_b, iv_b, contract_size=1 if kind_b == "share" else 100,
                    )
                    st.rerun()

                st.markdown("#### Presets")
                pc = st.columns(3)
                for i, (name, spec_legs) in enumerate(op.PRESETS.items()):
                    if pc[i % 3].button(name, key=f"preset_{i}", width="stretch"):
                        store.clear_legs(chosen)
                        for lk, ratio, qty in spec_legs:
                            if lk == "share":
                                store.add_leg(chosen, under, "share", 0.0, exp_b,
                                              qty / 100, spot_b, 0.0, contract_size=100)
                                continue
                            target = spot_b * ratio
                            ch2, _ = ac.attempt(ac.chain, under, exp_b, spot_b, rate_b,
                                                div_b)
                            ch2 = ch2 if ch2 is not None else pd.DataFrame()
                            side2 = ch2[ch2.kind == lk] if not ch2.empty else ch2
                            if side2.empty:
                                continue
                            near = side2.iloc[(side2.strike - target).abs().argsort()[:1]].iloc[0]
                            store.add_leg(chosen, under, lk, float(near.strike), exp_b,
                                          qty, float(near.mid), float(near.iv))
                        st.rerun()
                st.caption(
                    "A preset clears the position and rebuilds it at the nearest listed "
                    "strikes to the textbook ratios. Adjust the legs afterwards."
                )

        # ------------------------------------------------------------ the position
        saved = store.legs(chosen)
        if saved.empty:
            st.info("No legs yet. Add one above, or start from a preset.")
        else:
            st.markdown("#### Position")
            legs_obj = [
                op.Leg(kind=r.kind, strike=float(r.strike), expiry=r.expiry,
                       quantity=float(r.quantity), entry_price=float(r.entry_price),
                       iv=float(r.iv) if pd.notna(r.iv) else 0.25,
                       contract_size=int(r.contract_size))
                for r in saved.itertuples()
            ]
            st.markdown(ui.grid(pd.DataFrame({
                "leg": [l.describe() for l in legs_obj],
                "iv": [f"{l.iv:.0%}" if l.kind != "share" else "—" for l in legs_obj],
                "cost": [ui.money(l.entry_price * l.multiplier) for l in legs_obj],
            }), numeric={"iv", "cost"}, ticker_col="leg"), unsafe_allow_html=True)

            if st.button("Clear position", key="opt_b_clear", width="stretch"):
                store.clear_legs(chosen)
                st.rerun()

            # The SAVED legs' underlying, not whatever is typed above: until 02.10.2026
            # a position in one share was valued at the price of the next one typed in,
            # and an underlying without a stored close raised a TypeError.
            under_b = saved.underlying.iloc[0]
            spot_use, spot_src = _spot(under_b)
            if not spot_use:
                st.warning(f"No price for {under_b}, so the position cannot be valued.")
                return
            st.caption(f"{under_b} at {spot_use:,.2f} ({spot_src}).")
            soonest = min(saved.expiry)
            days_left = max((pd.Timestamp(soonest) - pd.Timestamp(dt.date.today())).days, 1)
            m = op.position_metrics(legs_obj, spot_use, days_left, r=rate_b)

            cells = [
                ("NET " + ("DEBIT" if m["net_debit"] > 0 else "CREDIT"),
                 ui.money(abs(m["net_debit"])), "down" if m["net_debit"] > 0 else "up"),
                ("MAX PROFIT", ui.money(m["max_profit"]), "up"),
                ("MAX LOSS", "unbounded" if m["unbounded_loss"] else ui.money(m["max_loss"]),
                 "down"),
                ("DAYS LEFT", str(int(days_left)), ""),
            ]
            st.markdown(ui.stats(cells), unsafe_allow_html=True)
            if m["unbounded_loss"]:
                st.error(
                    "This position contains a short option with no long option above or "
                    "below it. The loss has no ceiling — the figure over the range "
                    "examined would be a tidy number for something that is not bounded, "
                    "so it is not shown."
                )
            if m["breakevens"]:
                st.caption(
                    "Break-even at expiry: "
                    + ", ".join(f"{b:,.2f}" for b in m["breakevens"])
                    + f"  ·  examined between {m['range'][0]:,.0f} and {m['range'][1]:,.0f}"
                )

            g = m["greeks"]
            st.markdown(ui.stats([
                ("DELTA", ui.num(g["delta"], 1), "up" if g["delta"] >= 0 else "down"),
                ("GAMMA", ui.num(g["gamma"], 2), ""),
                ("VEGA / 1%", ui.num(g["vega"], 1), ""),
                ("THETA / DAY", ui.num(g["theta"], 1),
                 "down" if g["theta"] < 0 else "up"),
            ]), unsafe_allow_html=True)
            st.caption(
                f"Delta {g['delta']:,.0f} means the position behaves like holding "
                f"{g['delta']:,.0f} shares right now. Theta {g['theta']:,.1f} is what a "
                "day of nothing happening costs (or earns). Vega is the move per "
                "percentage point of implied volatility — the number that decides "
                "whether you are betting on direction or on movement."
            )

            payoff = pd.DataFrame({
                "at expiry": m["at_expiry"], "today": m["today"],
            }, index=m["grid"])
            st.line_chart(payoff, height=320, color=[ui.AMBER, ui.CYAN])
            st.caption(
                "Amber is the outcome at expiry, blue is the position's value today. The "
                "gap between them is time value — it shrinks to nothing by expiry, which "
                "is the part that surprises people who look only at the hockey stick."
            )

            if any(l.kind != "share" for l in legs_obj):
                iv_avg = np.mean([l.iv for l in legs_obj if l.kind != "share"])
                cols_p = st.columns(3)
                for i, b in enumerate(m["breakevens"][:3]):
                    prob = op.probability_above(spot_use, b, iv_avg, days_left, rate_b)
                    cols_p[i].metric(f"P(above {b:,.0f})", f"{prob:.0%}")
                if m["breakevens"]:
                    st.caption(
                        "Probabilities under the same lognormal assumption the pricing "
                        "uses. Real markets make large moves more often than that curve "
                        "allows, so readings near the extremes are the optimistic ones."
                    )


def _view_strategy(default: str = "") -> None:
    st.markdown("#### When to invest")
    st.write(
        "Not which company — when. Money arrives on a schedule; a rule decides when it "
        "goes into the market. Every strategy below gets the same contributions on the "
        "same dates, and whatever is waiting sits in a cash account. Without that, a "
        "dip-buying rule wins automatically by spending money the other strategies had "
        "already committed."
    )

    what = st.text_input(
        "What to buy", value=default or "^SP500TR",
        help="A ticker in the local store. ^SP500TR is the S&P 500 including dividends.",
        key=f"stg_symbol_{default}",
    )
    c1, c2, c3 = st.columns(3)
    # Stable keys: without them every one of these reset whenever a label changed.
    amount = c1.number_input("Amount each time", 50.0, 100000.0, 500.0, 50.0,
                             key="stg_amount")
    freq = c2.selectbox("How often", ["weekly", "fortnightly", "monthly", "quarterly"],
                        index=2, key="stg_freq")
    cash_rate = c3.slider("Idle cash earns % a year", 0.0, 6.0, 2.0, 0.25,
                          key="stg_cash") / 100

    c4, c5 = st.columns(2)
    trigger = c4.slider("Dip trigger: % below the running high", 1.0, 30.0, 2.0, 0.5,
                        key="stg_trigger") / 100
    ma_days = c5.slider("Moving average, days", 20, 300, 200, 10, key="stg_ma")

    series = src.con.execute(
        "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date",
        [what.strip()],
    ).fetch_df()
    if series.empty:
        series = src.con.execute(
            "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) "
            "ORDER BY date", [what.strip()]).fetch_df()

    if len(series) < 60:
        st.warning(
            f"Not enough stored history for '{what}'. The benchmark ^SP500TR goes back "
            "to 2010; individual shares hold about 15 months unless you rebuilt them."
        )
    else:
        series["date"] = pd.to_datetime(series["date"])
        prices = series.set_index("date")["close_adj"]

        plans = [
            stg.Plan(amount=amount, frequency=freq, rule="immediately", cash_rate=cash_rate),
            stg.Plan(amount=amount, frequency=freq, rule="lump_sum", cash_rate=cash_rate),
            stg.Plan(amount=amount, frequency=freq, rule="on_drawdown",
                     drawdown_trigger=trigger, cash_rate=cash_rate),
            stg.Plan(amount=amount, frequency=freq, rule="below_average",
                     average_days=ma_days, cash_rate=cash_rate),
        ]
        results = [stg.simulate(prices, p) for p in plans]
        results = [r for r in results if r]

        if not results:
            st.info("Nothing to simulate over this window.")
        else:
            table = pd.DataFrame([{
                "strategy": r["plan"].describe(),
                "invested": ui.money(r["invested"]),
                "final value": ui.money(r["final_value"]),
                "profit": ui.money(r["profit"]),
                "return p.a.": ui.pct(r["money_weighted_return"], 1),
                "buys": str(r["n_purchases"]),
                "avg cost": ui.num(r["average_cost"]),
                "idle cash": f"{r['idle_cash_share']:.0%}",
            } for r in results])
            st.markdown(
                ui.grid(table, numeric={"invested", "final value", "profit", "buys",
                                        "avg cost", "idle cash"},
                        signed={"return p.a."}, ticker_col="strategy"),
                unsafe_allow_html=True,
            )

            curves = pd.DataFrame({
                r["plan"].describe()[:34]: r["curve"] for r in results
            })
            st.line_chart(curves, height=300,
                          color=[ui.AMBER, ui.CYAN, ui.UP, ui.MUTED][:len(curves.columns)])

            live_choice = st.selectbox(
                "Follow one of these live from today",
                options=list(range(len(plans))),
                format_func=lambda i: plans[i].describe(), key="stg_live_choice")

            def _open_plan_book(name, _amount):
                chosen_plan = plans[live_choice]
                if chosen_plan.rule == "lump_sum":
                    last_px = float(prices.iloc[-1])
                    return paper.open_hold(
                        store.con, name, "STRATEGY",
                        pd.DataFrame({"ticker": [what.strip().upper()], "weight": [1.0],
                                      "price": [last_px], "cost": [chosen_plan.cost]}),
                        asof, amount * 12)
                return paper.open_plan(store.con, name, what.strip(), chosen_plan, asof)

            _paper_section(
                "STRATEGY", "strategy", _open_plan_book,
                f"{what.strip().upper()} plan {asof}",
                f"The chosen plan runs from {asof} on: {amount:,.0f} arrives "
                f"{freq}, and its rule decides when it goes in. The lump sum is bought "
                f"once as {amount * 12:,.0f} (a year of contributions) and held.",
                amount_label=None)
            st.caption(
                f"Portfolio value over {results[0]['years']:.1f} years, including cash "
                "not yet invested. The average price of the asset over the window was "
                f"{prices.mean():,.2f} — a strategy's average cost below that means the "
                "timing rule did buy lower, which is not the same as ending up richer."
            )

            st.caption(
                "One row answers a different question: **everything invested at the "
                "start** assumes you already had the whole sum on day one. Its final "
                "value is not comparable with the others — its return per year is, and "
                "that is usually the lower of the two, because the money simply sat in "
                "the market for longer rather than being deployed better."
            )

            comparable = [r for r in results if r["plan"].rule != "lump_sum"] or results
            best = max(comparable, key=lambda r: r["final_value"])
            worst = min(comparable, key=lambda r: r["final_value"])
            gap = best["final_value"] - worst["final_value"]
            st.markdown(ui.stats([
                ("BEST", ui.money(best["final_value"]), "up"),
                ("WORST", ui.money(worst["final_value"]), "down"),
                ("GAP", ui.money(gap), "amber"),
                ("ON", ui.money(best["invested"]), ""),
            ]), unsafe_allow_html=True)
            st.caption(
                f"Best here: {best['plan'].describe()}. Read that as one outcome on one "
                "history, not a rule. Move the dip trigger a percentage point and the "
                "ranking often flips — which is itself the finding: over a long enough "
                "rising market, when you invest matters far less than whether you do."
            )

            with st.expander("Purchases made by the dip rule"):
                dip = next((r for r in results if r["plan"].rule == "on_drawdown"), None)
                if dip is None or dip["purchases"].empty:
                    st.info(
                        "The trigger never fired over this window, so the whole balance "
                        "sat in cash. That is the real risk of waiting for a fall."
                    )
                else:
                    b = dip["purchases"].tail(20)
                    st.markdown(ui.grid(pd.DataFrame({
                        "date": b.date.dt.date.astype(str),
                        "price": b.price.map(ui.num),
                        "amount": b.amount.map(ui.money),
                    }), numeric={"price", "amount"}, ticker_col="date"),
                        unsafe_allow_html=True)


# ---------------------------------------------------------------------- watchlist
def _watched() -> list[str]:
    """Every company followed anywhere in the app: the watchlist, tracked holdings and
    hand-picked names (store.everything_followed). The calendar and the commodity links
    used their own copy of this list, which left the watchlist out (audit 02.10.2026)."""
    return store.everything_followed()


def _view_monitor() -> None:
    """WEI and GLCO: indices, currencies, rates, commodities, crypto. Priced in the ONE
    unit switch in the sidebar (the board had a second switch of its own until
    02.10.2026, which could disagree with the first)."""
    in_gold = unit == nm.GOLD
    unit_key = "gold" if in_gold else "USD"
    boards = _monitor(unit_key, DATA_KEY)
    stored = sum(int(t["last"].notna().sum()) for t in boards.values() if "last" in t)
    if not stored:
        st.info(
            "The monitor's series are not stored yet. Run once: "
            "`uv run python scripts/build_world.py` — build_prices.py keeps them "
            "current after that."
        )
    else:
        def _move(v, symbol):
            if v is None or pd.isna(v):
                return "—"
            if symbol in world.YIELD_SYMBOLS:
                bp = round(v)
                return "0 bp" if bp == 0 else f"{bp:+.0f} bp"
            return ui.pct(v, 2)

        def _board(group: str, frame: pd.DataFrame) -> None:
            st.markdown(f"#### {group}")
            def _last(v, sym, kind=None):
                if kind == "unit":
                    return "1 oz"
                if kind == "no_rate":
                    return "no rate"
                if v is None or pd.isna(v):
                    return "—"
                if kind == "oz":
                    return f"{v:,.4f} oz" if v < 10 else f"{v:,.2f} oz"
                if kind == "mg":
                    return f"{v:,.3f} mg" if v < 1 else f"{v:,.2f} mg"
                return f"{v:,.4f}" if ("=X" in sym and v < 20) else f"{v:,.2f}"

            kinds = frame["kind"] if "kind" in frame else [None] * len(frame)
            rows = pd.DataFrame({
                "market": frame["label"],
                "last": [_last(v, sym, k) for v, sym, k in zip(
                    frame.get("last", pd.Series([None] * len(frame))), frame["symbol"],
                    kinds)],
                **{h: [_move(v, sym) for v, sym in zip(frame.get(h, [None] * len(frame)),
                                                        frame["symbol"])]
                   for h in ("1D", "1W", "1M", "YTD", "1Y")},
            })
            st.markdown(ui.grid(rows, numeric={"last"},
                                signed={"1D", "1W", "1M", "YTD", "1Y"},
                                ticker_col="market"), unsafe_allow_html=True)

        groups = list(boards.items())
        left, right = st.columns(2)
        for i, (group, frame) in enumerate(groups):
            with (left if i % 2 == 0 else right):
                _board(group, frame)
        newest = max((d for t in boards.values() for d in t.get("as_of", pd.Series())
                      if isinstance(d, dt.date)), default=None)
        if in_gold:
            no_rate = sorted({world.CURRENCY.get(sym, "USD") for t in boards.values()
                              for sym, k in zip(t["symbol"], t.get("kind", []))
                              if k == "no_rate"} - {"USD"})
            st.caption(
                f"Daily closes, newest {newest}, priced in gold: each value divided by the "
                "gold price of the same day (GC=F, front-month futures). Indices and "
                "shares in troy ounces; currencies in milligrams of gold per unit (the "
                "currency named in the row). Foreign indices are converted to dollars "
                "first at that day's rate. Yields, the dollar index and EUR/AUD have no "
                "currency and are unchanged. A fall here can be gold rising, not the "
                "market falling."
                + (f" No dollar rate stored for {', '.join(no_rate)}: run "
                   "`uv run python scripts/build_world.py` once." if no_rate else "")
            )
        else:
            st.caption(
                f"Daily closes, newest {newest}. Each move compares with the last close on "
                "or before the start of its window; yields move in basis points. Indices "
                "are in their own currency and without dividends."
            )

        st.markdown("#### Side by side")
        labels = {s: l for g in world.MONITOR.values() for s, l in g}
        c1, c2 = st.columns([3, 1])
        picked = c1.multiselect(
            "Markets", options=list(labels), format_func=lambda s: labels[s],
            default=["^GSPC", "^AXJO", "^GDAXI", "^N225"], key="monitor_pick",
            label_visibility="collapsed")
        span = c2.selectbox("Since", ["1Y", "3Y", "5Y", "10Y"], index=0,
                            label_visibility="collapsed", key="monitor_span")
        if picked:
            since = dt.date.today() - dt.timedelta(days=365 * int(span[:-1]))
            curves = world.rebased(src.con, picked, since, unit=unit_key)
            if curves.empty:
                st.info("No stored history for these yet.")
            else:
                _rebased_chart(curves, labels, "monitor_cmp")
                st.caption("Each line starts at 100 on its first close in the window, so "
                           "an index, a currency and gold can share one scale. Price "
                           "only, " + ("measured in gold (gold itself is the unit and "
                                       "has no line)." if in_gold
                                       else "in each market's own currency."))




def _rebased_chart(curves: pd.DataFrame, labels: dict, key: str) -> None:
    """Lines rebased to 100, one colour each, a dotted line at the start value."""
    fig = go.Figure()
    for i, sym in enumerate(curves.columns):
        fig.add_trace(go.Scatter(x=curves.index, y=curves[sym], name=labels.get(sym, sym),
                                 line=dict(color=PALETTE[i % len(PALETTE)], width=1.5)))
    fig.add_hline(y=100, line=dict(color=ui.MUTED, width=1, dash="dot"))
    _layout(fig, 300, hovermode="x unified", legend=dict(orientation="h", y=-0.15),
            xaxis=dict(gridcolor=ui.RULE), yaxis=dict(gridcolor=ui.RULE, side="right"))
    st.plotly_chart(fig, width="stretch", key=key)


def _view_compare(default: str = "") -> None:
    """CORR: any stored series side by side, with their correlation."""
    in_gold = unit == nm.GOLD
    unit_key = "gold" if in_gold else "USD"
    st.markdown("#### Compare anything")
    k1, k2 = st.columns([3, 1])
    start = ", ".join(s for s in (default, "^GSPC", "GC=F") if s)
    cmp_syms = k1.text_input(
        "Symbols", value=start, key=f"cmp_syms_{default}",
        help="Any stored series: shares, the monitor's indices, currencies, "
             "commodities, crypto. Comma-separated.")
    cmp_span = k2.selectbox("Since", ["1Y", "3Y", "5Y", "10Y"], index=1, key="cmp_span",
                            label_visibility="visible")
    cmp = world.compare(src.con, cmp_syms.split(","),
                        dt.date.today() - dt.timedelta(days=365 * int(cmp_span[:-1])),
                        unit=unit_key)
    if not cmp:
        st.info("None of these has stored prices in that window.")
    else:
        if cmp["missing"]:
            st.caption(("Not shown (no stored prices, no exchange rate, or gold itself "
                        "in the gold view): " if in_gold else "No stored prices for: ")
                       + ", ".join(cmp["missing"]))
        _rebased_chart(cmp["curves"], {}, "cmp_chart")
        cs = cmp["stats"]
        st.markdown(ui.grid(pd.DataFrame({
            "symbol": cs["symbol"],
            "return": cs["return"].map(lambda v: ui.pct(v, 1)),
            "p.a.": cs["annualised"].map(lambda v: ui.pct(v, 1)),
            "volatility": cs["volatility"].map(
                lambda v: f"{v:.1%}" if v is not None and pd.notna(v) else "—"),
            "max dd": cs["max_drawdown"].map(lambda v: ui.pct(v, 1)),
            "beta": cs.get("beta", pd.Series([None] * len(cs))).map(ui.num),
        }), numeric={"volatility", "beta"}, signed={"return", "p.a.", "max dd"},
            ticker_col="symbol"), unsafe_allow_html=True)
        _corr_heatmap(cmp["correlation"], "cmp", annotate=True)
        st.caption(
            f"Correlation of daily returns over the {cmp['shared_days']} days all of them "
            "traded. Beta against the S&P 500 total return. "
            + ("Everything, the S&P 500 too, measured in gold; gold itself is the unit "
               "and has no line. " if in_gold else "Each series in its own currency. ")
            + "Indices and commodities without dividends.")


def _view_books() -> None:
    st.markdown("#### Paper books — every simulated purchase, side by side")
    st.caption(
        "Each book was frozen when it was opened: the positions, the rule or the savings "
        "plan cannot change afterwards, so nothing here is fitted to the outcome. Open "
        "new ones from the Paper money box of a screen, a security, a rule or a savings "
        "plan."
    )
    all_books = paper.books(store.con)
    valued = _paper_rows(all_books) if not all_books.empty else []
    # The SCREEN tab's older live tracking (one per portfolio) belongs in the same
    # comparison. It has no amount of its own, so it is shown on 10,000.
    for prow in store.list_portfolios().itertuples():
        tinfo = store.tracking_info(prow.id)
        if tinfo is None:
            continue
        legacy = {"id": f"track-{prow.id}", "name": f"{prow.name} (tracking)",
                  "origin": "SCREEN", "kind": "hold", "started_on": tinfo["started_on"],
                  "amount": 10_000.0, "config_json": "{}", "thesis": tinfo["thesis"]}
        try:
            valued.append(paper.value_book(src.con, store.con, legacy,
                                           held=store.holdings(prow.id)))
        except Exception as exc:                                   # noqa: BLE001
            valued.append({"empty": True, "book": legacy, "error": str(exc)})

    if not valued:
        st.info("No paper books yet.")
    else:
        live = [v for v in valued if not v.get("empty")]
        money_in = sum(v["invested_now"] for v in live)
        worth = sum(v["value_now"] for v in live)
        st.markdown(ui.stats([
            ("BOOKS", f"{len(valued)}", ""),
            ("MONEY IN", ui.money(money_in), ""),
            ("VALUE", ui.money(worth), ""),
            ("GAIN", ui.money(worth - money_in), "up" if worth >= money_in else "down"),
            ("RETURN", ui.pct(worth / money_in - 1.0) if money_in else "—",
             "up" if worth >= money_in else "down"),
        ]), unsafe_allow_html=True)

        if live:
            fig = go.Figure()
            for i, v in enumerate(live):
                ret = 100.0 * (v["value"] / v["invested"] - 1.0)
                fig.add_trace(go.Scatter(
                    x=ret.index, y=ret, name=v["book"]["name"],
                    line=dict(color=PALETTE[i % len(PALETTE)], width=1.6)))
            fig.add_hline(y=0, line=dict(color=ui.MUTED, width=1, dash="dot"))
            _layout(fig, 300, hovermode="x unified", legend=dict(orientation="h", y=-0.15),
                    yaxis=dict(title="% on money in", gridcolor=ui.RULE, side="right"),
                    xaxis=dict(gridcolor=ui.RULE))
            st.plotly_chart(fig, width="stretch", key="books_compare")
            st.caption(
                "Each line is the gain on the money that book has put in so far. Books "
                "opened on different days start at different points; a savings plan's "
                "money in grows with each contribution."
            )

        st.markdown(ui.grid(_paper_table(valued), ticker_col="book"),
                    unsafe_allow_html=True)
        daily = pd.DataFrame({
            f"{v['book']['name']} ({v['book']['origin']})": v["value"]
            for v in valued if not v.get("empty")})
        _workbook_button(
            "Export books to Excel",
            lambda: {"Books": pd.DataFrame([{
                "book": v["book"]["name"], "from": v["book"]["origin"],
                "kind": v["book"]["kind"], "since": str(v["book"]["started_on"]),
                "money_in": v.get("invested_now"), "value": v.get("value_now"),
                "gain": v.get("gain"), "return": v.get("return"),
                "sp500tr_return": v.get("benchmark_return")} for v in valued]),
                "Daily value": daily.reset_index(names="date") if not daily.empty
                else pd.DataFrame()},
            title="Paper books", source="Stored daily closes; values in USD",
            stem="paper_books", key="export_books")

        for v in valued:
            b = v["book"]
            with st.expander(f"{b['name']} · {b['origin']} · {b['kind']} · since "
                             f"{pd.Timestamp(b['started_on']).date()}"):
                if b.get("thesis"):
                    st.markdown(f"_{b['thesis']}_")
                if v.get("empty"):
                    st.info(v.get("error") or "No prices since this book was opened yet.")
                else:
                    detail = go.Figure()
                    detail.add_trace(go.Scatter(x=v["value"].index, y=v["value"],
                                                name="value",
                                                line=dict(color=ui.AMBER, width=1.6)))
                    detail.add_trace(go.Scatter(x=v["invested"].index, y=v["invested"],
                                                name="money in", line=dict(
                                                    color=ui.MUTED, width=1, dash="dot")))
                    if v.get("benchmark_value") is not None:
                        detail.add_trace(go.Scatter(
                            x=v["benchmark_value"].index, y=v["benchmark_value"],
                            name="S&P 500 TR, same money",
                            line=dict(color=ui.CYAN, width=1.2)))
                    detail.update_layout(
                        height=260, margin=dict(l=0, r=0, t=6, b=0), paper_bgcolor=ui.BG,
                        plot_bgcolor=ui.BG, hovermode="x unified", showlegend=True,
                        legend=dict(orientation="h", y=-0.2),
                        font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
                        yaxis=dict(gridcolor=ui.RULE, side="right"),
                        xaxis=dict(gridcolor=ui.RULE))
                    st.plotly_chart(detail, width="stretch",
                                    key=f"book_{b['id']}")
                    fills = v.get("fills")
                    if fills is not None and len(fills):
                        st.caption(f"{len(fills)} trades since it was opened; last: "
                                   f"{pd.Timestamp(fills.iloc[-1]['date']).date()}.")
                    for note in v.get("notes", []):
                        st.caption(note)
                if b["kind"] == "hold" and not v.get("empty"):
                    held_pos = (store.holdings(str(b["id"])[6:])
                                if str(b["id"]).startswith("track-")
                                else paper.positions(store.con, b["id"]))
                    if len(held_pos):
                        _risk_block(held_pos, key=str(b["id"]))
                if not str(b["id"]).startswith("track-") and st.button(
                        "Close this book", key=f"close_{b['id']}"):
                    paper.close_book(store.con, b["id"])
                    st.rerun()


def _view_deals(default_subject: str = "") -> None:
    """MA: precedent transactions entered by hand, valued point in time."""
    st.markdown("#### Precedent transactions — what acquirers paid")
    st.caption(
        "Enter the deal terms from the press release; every multiple is computed from "
        "the target's SEC filings as they stood on the announcement date, so no figure "
        "published after the deal enters it. A delisted target can be given by CIK."
    )
    with st.expander("Add a deal", expanded=deals.listing(store.con).empty):
        d1, d2, d3 = st.columns(3)
        d_target = d1.text_input("Target (ticker or CIK)", key="deal_target")
        d_acq = d2.text_input("Acquirer", key="deal_acquirer")
        d_date = d3.date_input("Announced", value=asof or dt.date.today(),
                               max_value=dt.date.today(), key="deal_date")
        d4, d5, d6 = st.columns(3)
        d_offer = d4.number_input("Offer per share ($, 0 = not given)", min_value=0.0,
                                  value=0.0, step=0.5, key="deal_offer")
        d_equity = d5.number_input("Equity value ($m, 0 = offer × shares)", min_value=0.0,
                                   value=0.0, step=100.0, key="deal_equity")
        d_unaff = d6.number_input("Unaffected price ($, 0 = last close before)",
                                  min_value=0.0, value=0.0, step=0.5, key="deal_unaff")
        d_cons = st.selectbox("Consideration", ["cash", "stock", "cash and stock", "other"],
                              key="deal_cons")
        d_note = st.text_input("Note (source, conditions)", key="deal_note")
        if st.button("Add deal", key="deal_add"):
            try:
                deals.add(store.con, d_target, d_date, d_acq,
                          equity_value=d_equity * 1e6 or None,
                          offer_price=d_offer or None,
                          unaffected_price=d_unaff or None,
                          consideration=d_cons, note=d_note)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    deal_rows, deal_stats = deals.table(src.con, store.con)
    if deal_rows.empty:
        st.info("No deals yet.")
    else:
        def _x(v):
            return "—" if v is None or pd.isna(v) else f"{v:,.1f}x"

        mult_labels = {"ev_to_revenue": "EV/Sales", "ev_to_ebitda": "EV/EBITDA",
                       "ev_to_ebit": "EV/EBIT", "price_to_earnings": "P/E",
                       "price_to_book": "P/B", "premium": "premium"}
        for col in list(mult_labels) + ["equity_value", "enterprise_value", "error"]:
            if col not in deal_rows.columns:
                deal_rows[col] = None
        shown_deals = pd.DataFrame({
            "target": deal_rows["target"].astype(str),
            "acquirer": deal_rows["acquirer"].astype(str),
            "announced": deal_rows["announced"].astype(str),
            "equity": deal_rows["equity_value"].map(
                lambda v: "—" if v is None or pd.isna(v) else ui.money(v)),
            "EV": deal_rows["enterprise_value"].map(
                lambda v: "—" if v is None or pd.isna(v) else ui.money(v)),
            **{mult_labels[c]: deal_rows[c].map(_x) for c in mult_labels if c != "premium"},
            "premium": deal_rows["premium"].map(
                lambda v: "—" if v is None or pd.isna(v) else ui.pct(v, 0)),
        })
        if not deal_stats.empty:
            summary_rows = pd.DataFrame({
                "target": ["median", "mean", "high", "low"],
                "acquirer": [""] * 4, "announced": [""] * 4, "equity": [""] * 4,
                "EV": [f"n = {int(deal_stats.at['n', 'ev_to_ebitda'])}"
                       if "ev_to_ebitda" in deal_stats.columns else ""] + [""] * 3,
                **{mult_labels[c]: [
                    (ui.pct(deal_stats.at[k, c], 0) if c == "premium"
                     and not pd.isna(deal_stats.at[k, c]) else
                     "—" if pd.isna(deal_stats.at[k, c]) else _x(deal_stats.at[k, c]))
                    for k in ("median", "mean", "high", "low")]
                   for c in mult_labels if c in deal_stats.columns},
            })
            shown_deals = pd.concat([shown_deals, summary_rows], ignore_index=True)
        st.markdown(ui.grid(shown_deals, numeric={"equity", "EV", *mult_labels.values()},
                            signed={"premium"}, ticker_col="target"),
                    unsafe_allow_html=True)
        for r in deal_rows.itertuples():
            if isinstance(r.error, str) and r.error:
                st.caption(f"{r.target} ({r.announced}): {r.error}")
        st.caption(
            "EV = equity value + the target's net debt (debt − cash) on file at the "
            "announcement. TTM figures are the four quarters filed by then. Premium is "
            "the offer over the unaffected price — the last close before the "
            "announcement unless one was entered; a leak before that date hides part "
            "of it."
        )

        subj_opts = [""] + (sorted(_frame(asof, DATA_KEY).ticker.astype(str).unique())
                            if asof else [])
        subject = st.selectbox("Apply the precedent medians to", subj_opts,
                               index=subj_opts.index(default_subject)
                               if default_subject in subj_opts else 0,
                               key=f"deal_subject_{default_subject}")
        if subject:
            enriched_d = _enriched_now()
            srow = enriched_d[enriched_d.ticker.astype(str) == subject]
            implied_d = (deals.apply_to(deal_stats, srow.iloc[0]) if len(srow)
                         else pd.DataFrame())
            if implied_d.empty:
                st.info("None of the median multiples can be applied — the company's "
                        "figures are missing or not positive.")
            else:
                st.markdown(ui.grid(pd.DataFrame({
                    "multiple": implied_d["multiple"].map(mult_labels),
                    "precedent median": implied_d["precedent median"].map(_x),
                    "implied equity": implied_d["implied equity value"].map(ui.money),
                    "per share": implied_d["implied price"].map(ui.num),
                    "vs price": implied_d["vs price"].map(lambda v: ui.pct(v, 0)),
                }), numeric={"precedent median", "implied equity", "per share"},
                    signed={"vs price"}, ticker_col="multiple"), unsafe_allow_html=True)
                st.caption(
                    "A takeover value: it includes the control premium buyers paid, so "
                    "it sits above what the trading comps imply for the same company.")

        _workbook_button(
            "Export deals to Excel",
            lambda: {"Deals": deal_rows.drop(columns=["id"], errors="ignore"),
                     "Statistics": deal_stats.reset_index(names="statistic")
                     if not deal_stats.empty else pd.DataFrame()},
            title="Precedent transactions",
            source="Deal terms entered by hand; target figures from SEC filings as of "
                   "each announcement", stem="precedent_deals", key="export_deals")

        with st.expander("Remove a deal"):
            gone = st.selectbox(
                "Deal", deal_rows["id"].tolist(), key="deal_remove_pick",
                format_func=lambda i: " · ".join(
                    str(x) for x in deal_rows.loc[deal_rows.id == i,
                                                  ["target", "acquirer", "announced"]]
                    .iloc[0]))
            if st.button("Remove", key="deal_remove"):
                deals.remove(store.con, gone)
                st.rerun()


def _view_economy() -> None:
    """ECST: the US economy from FRED."""
    st.markdown("#### The economy")
    board = _fred_board(DATA_KEY)
    if board["latest"].notna().sum() == 0:
        st.info("No macro series stored yet. Run once: `uv run python scripts/build_fred.py` "
                "— FRED needs no key, and build_prices.py keeps the series current.")
    else:
        def _reading(v, unit):
            if v is None or pd.isna(v):
                return "—"
            if unit == "%":
                return f"{v:.1f}%"
            if unit == "k jobs":
                return f"{v:+,.0f}k"
            return f"{v:,.1f} {unit}" if unit not in ("claims",) else f"{v:,.0f}"

        mcols = st.columns(2)
        for i, (group, g) in enumerate(board.groupby("group", sort=False)):
            with mcols[i % 2]:
                st.markdown(f"**{group}**")
                st.markdown(ui.grid(pd.DataFrame({
                    "series": g["series"],
                    "latest": [_reading(v, u) for v, u in zip(g["latest"], g["unit"])],
                    "previous": [_reading(v, u) for v, u in zip(g["previous"], g["unit"])],
                    "period": [pd.Timestamp(p).strftime("%Y-%m")
                               if p is not None and not pd.isna(p) else "—"
                               for p in g["period"]],
                }), numeric={"latest", "previous"}, ticker_col="series"),
                    unsafe_allow_html=True)
        st.caption(
            "From FRED (St. Louis Fed). Inflation, prices, output and wages as the change "
            "on a year earlier; GDP as the annualised change on the previous quarter; "
            "payrolls as the monthly change. `period` is the month or quarter the figure "
            "describes — it is published weeks later. Consensus forecasts and surprises "
            "are a paid dataset and are not shown.")
        m1, m2 = st.columns([3, 1])
        macro_pick = m1.multiselect(
            "Series", options=list(fred.SERIES),
            format_func=lambda sid: fred.SERIES[sid][0],
            default=["CPIAUCSL", "CPILFESL", "FEDFUNDS"], key="macro_pick",
            label_visibility="collapsed")
        macro_since = m2.selectbox("Since", ["5Y", "10Y", "20Y"], key="macro_since",
                                   label_visibility="collapsed")
        if macro_pick:
            start_m = dt.date.today() - dt.timedelta(days=365 * int(macro_since[:-1]))
            lines = pd.concat([fred.history(src.con, sid, start_m) for sid in macro_pick],
                              axis=1)
            if not lines.empty:
                st.line_chart(lines, height=300)


def _view_rates() -> None:
    """GC and WIRP: the Treasury curve and what the front fed funds contract prices."""
    st.markdown("#### The curve, and what it prices")
    snap, ff = _rates(DATA_KEY)
    if snap.empty:
        st.info("No rate history stored. Run scripts/build_macro.py.")
    else:
        shape = mc.curve_shape(snap)
        cells = [(r.tenor.upper(), f"{r.rate:.2f}%", "") for r in snap.itertuples()]
        if shape:
            cells.append(("10Y - 3M", f"{shape['slope_bp']:+.0f}bp",
                          "down" if shape["inverted"] else "up"))
        st.markdown(ui.stats(cells), unsafe_allow_html=True)

        if ff.get("direction"):
            st.markdown(ui.stats([
                ("FED FUNDS IMPLIED", f"{ff['implied_rate']:.3f}%", "amber"),
                ("3-MONTH BILL", f"{ff.get('bill_rate', 0):.3f}%", ""),
                ("GAP", f"{ff.get('gap_bp', 0):+.0f}bp",
                 "down" if ff.get("gap_bp", 0) < 0 else "up"),
            ]), unsafe_allow_html=True)
            st.caption(
                f"The front fed funds contract implies **{ff['direction']}** for the "
                "current month. One contract describes one month — a full path of "
                "decisions would need the dated futures strip, which this data source "
                "does not carry, so it is not shown rather than guessed at."
            )

        st.markdown(ui.grid(pd.DataFrame({
            "tenor": snap.tenor,
            "rate %": snap.rate.map(lambda v: f"{v:.3f}"),
            "1w bp": snap.change_1w.map(lambda v: "—" if pd.isna(v) else f"{v:+.0f}"),
            "1m bp": snap.change_1m.map(lambda v: "—" if pd.isna(v) else f"{v:+.0f}"),
            "3m bp": snap.change_3m.map(lambda v: "—" if pd.isna(v) else f"{v:+.0f}"),
            "1y bp": snap.change_1y.map(lambda v: "—" if pd.isna(v) else f"{v:+.0f}"),
        }), numeric={"rate %"}, signed={"1w bp", "1m bp", "3m bp", "1y bp"},
            ticker_col="tenor"), unsafe_allow_html=True)
        st.caption(
            "Changes in basis points. A move in the three-month rate IS a change in "
            "what the market expects from policy — nothing has to be modelled to read "
            "it. The 10-year minus 3-month slope has preceded most US recessions of the "
            "last fifty years when negative; a regularity, not a mechanism, and early "
            "by a year or more when it has been right."
        )

        hist_rates = src.con.execute(
            "SELECT date, ticker, close FROM prices WHERE ticker IN "
            "('^IRX','^FVX','^TNX','^TYX') ORDER BY date"
        ).fetch_df()
        if not hist_rates.empty:
            hist_rates["date"] = pd.to_datetime(hist_rates["date"])
            wide = hist_rates.pivot_table(index="date", columns="ticker", values="close")
            wide = wide.rename(columns={k: v[0] for k, v in mc.RATE_SERIES.items()})
            # One colour per series actually stored: Streamlit refuses a colour list
            # longer than the columns, and a store may hold only some of the four.
            st.line_chart(wide, height=250,
                          color=[ui.AMBER, ui.CYAN, ui.UP, ui.MUTED][:len(wide.columns)])


def _rate_exposure(ticker: str) -> None:
    """Rate sensitivity and an event study for one share — in MACRO until 02.10.2026,
    with its own ticker box (default AAPL); now part of the security's EXPOSURE."""
    st.markdown("#### What a company does when rates move")
    rate_links = mc.rate_sensitivity(src.con, ticker)
    if rate_links.empty:
        st.info("Not enough overlapping history with the Treasury yields.")
    else:
        st.markdown(ui.grid(pd.DataFrame({
            "tenor": rate_links.tenor,
            "correlation": rate_links.correlation.map(lambda v: f"{v:+.2f}"),
            "move per 1pp": rate_links.beta_per_1pp.map(lambda v: f"{v:+.1%}"),
            "days": rate_links.days.astype(str),
        }), numeric={"days"}, signed={"correlation", "move per 1pp"},
            ticker_col="tenor"), unsafe_allow_html=True)
        st.caption(
            "Regressed on the CHANGE in each yield, not its level. A share does "
            "not care that the ten-year sits at 5%, it cares that it moved from "
            "4.9 to 5.0 today — using levels would find a relationship between "
            "any two series that happen to trend together."
        )

    st.markdown("#### Event study")
    st.write(
        "What a share did around one date, with the market's own move on that day "
        "stripped out. The leftover is what is attributable to the event."
    )
    e1, e2, e3 = st.columns(3)
    e1.markdown(f"**{ticker}**")
    ev_date = e2.date_input("Event date", value=dt.date.today() - dt.timedelta(days=60),
                            key="ev_date", format="YYYY-MM-DD")
    ev_win = e3.slider("Window, days either side", 1, 20, 5, key="ev_window")
    if st.button("Measure", key=f"ev_run_{ticker}", width="stretch"):
        res_ev = mc.event_study(src.con, ticker, ev_date,
                                window=(-ev_win, ev_win))
        if not res_ev:
            st.warning("No price history for that company.")
        elif "error" in res_ev:
            st.warning(res_ev["error"])
        else:
            st.markdown(ui.stats([
                ("EVENT", str(res_ev["event_date"]), ""),
                ("BETA", ui.num(res_ev["beta"]), ""),
                (f"CAR({-ev_win},{ev_win})", ui.pct(res_ev["car"]),
                 "up" if res_ev["car"] >= 0 else "down"),
                ("t", f"{res_ev['t_stat']:+.2f}",
                 "amber" if res_ev["significant"] else ""),
            ]), unsafe_allow_html=True)
            d_ = res_ev["daily"]
            st.bar_chart(d_.set_index("offset")[["abnormal"]], height=200,
                         color=[ui.AMBER])
            st.caption(
                f"Market model fitted on {res_ev['estimation_days']} trading days "
                "ending ten days before the event, so anticipation does not "
                "contaminate the yardstick. A |t| above 1.96 is the conventional "
                "threshold — note that a one-day reaction spread over a wide window "
                "gets diluted by the quiet days around it, so widening the window "
                "until the number looks good measures your search, not the event."
            )


def _view_commodity_links() -> None:
    """Which followed companies move with which commodity. The rest of the old
    COMMODITIES tab (a board and a rebased chart of the same series) duplicated the
    commodity board and the side-by-side chart of MARKETS & MACRO; wheat and corn
    joined that board on 02.10.2026."""
    st.markdown("#### Which of your holdings move with commodities")
    watch_c = _watched()
    pick_c = st.text_input(
        "Companies", value=", ".join(watch_c[:10]),
        placeholder="tickers, comma-separated — defaults to everything you follow",
        key=f"links_tickers_{len(watch_c)}",
    )
    tickers_c = [t.strip().upper() for t in pick_c.replace("\n", ",").split(",")
                 if t.strip()][:12]
    if not tickers_c:
        st.caption("Follow names (watchlist, tracking, hand-picked) or type some.")
        return
    found_rows = []
    for t in tickers_c:
        links = _commodity_links(t, DATA_KEY)
        if links.empty:
            continue
        top = links.iloc[0]
        found_rows.append({
            "ticker": t, "strongest link": top.commodity,
            "correlation": f"{top.correlation:+.2f}",
            "beta": f"{top.beta:+.2f}",
            "days": str(int(top.days)),
        })
    if found_rows:
        st.markdown(ui.grid(pd.DataFrame(found_rows),
                            numeric={"days"}, signed={"correlation", "beta"}),
                    unsafe_allow_html=True)
        st.caption(
            "The sign says which side of the trade the company is on. A "
            "producer moves with its commodity because it is the product; a "
            "consumer moves against it because it is a cost. The arithmetic "
            "cannot tell those apart — you can."
        )
    else:
        st.info("Not enough overlapping history for those companies.")


def _air(fetch: bool):
    """(time, aircraft) from OpenSky, or the last answer of this session when `fetch` is
    False. A failed call is not cached (appcache) and keeps the previous answer."""
    if fetch:
        got, _ = ac.attempt(ac.aircraft_now)
        if got is not None:
            st.session_state["air_last"] = got
    return st.session_state.get("air_last")


def _view_map() -> None:
    # Laid out like a terminal map function: a strip of the numbers that moved, the map
    # with its layers, the movers beside it, and the detail underneath.

    ship_board, port_now = _ship_boards(DATA_KEY)
    # Anonymous OpenSky allows ~100 worldwide calls a day. Until 02.10.2026 every visit
    # to this tab asked, whichever layer was on; now only the AIR and FREIGHTERS layers
    # and the AIR table do, and the strip shows the last answer of this session.
    layer_now = st.session_state.get("map_layer", "SHIPS")
    live_air = _air(layer_now in ("AIR", "FREIGHTERS"))
    ais_static_path = cfg.CACHE_DIR / "ais_static.json"

    def _tone(v):
        return "" if v is None or pd.isna(v) else ("up" if v > 0 else "down")

    strip = []
    if not ship_board.empty:
        by_name = ship_board.set_index("chokepoint")
        for name, short in (("Strait of Hormuz", "HORMUZ"), ("Suez Canal", "SUEZ"),
                            ("Bab el-Mandeb Strait", "BAB EL-MANDEB"),
                            ("Cape of Good Hope", "CAPE"), ("Panama Canal", "PANAMA")):
            if name in by_name.index:
                chg = by_name.at[name, "vs_year_ago"]
                strip.append((f"{short} SHIPS/D",
                              f"{by_name.at[name, 'ships_per_day']:.0f} {ui.pct(chg, 0)}",
                              _tone(chg)))
    if not port_now.empty:
        known = port_now.dropna(subset=["total_vs_year_ago"])
        then = (known.total_t / (1 + known.total_vs_year_ago)).sum()
        chg = known.total_t.sum() / then - 1 if then else None
        strip.append(("PORTS KT/D", (f"{port_now.total_t.sum() / 1e3:,.0f} "
                                     f"{ui.pct(chg, 0)}"), _tone(chg)))
    air_at, air, freighters = None, pd.DataFrame(), pd.DataFrame()
    if live_air is not None:
        air_at, air = live_air
        freighters = aviation.cargo_flights(air)
        strip.append(("AIRBORNE", f"{len(air):,}", ""))
        strip.append(("FREIGHTERS", (f"{len(freighters):,} "
                                     f"{len(freighters) / max(len(air), 1):.1%}"), "amber"))
    if strip:
        st.markdown(ui.stats(strip), unsafe_allow_html=True)

    layer = st.radio("Layer", ["SHIPS", "TANKERS", "PORT CARGO", "LIVE AIS", "AIR",
                               "FREIGHTERS"],
                     horizontal=True, key="map_layer", label_visibility="collapsed")
    c_map, c_side = st.columns([3, 1])

    with c_map:
        fig = None
        if layer in ("SHIPS", "TANKERS"):
            if ship_board.empty:
                st.info("No ship traffic stored yet. Run once: "
                        "`uv run python scripts/build_shipping.py` — IMF PortWatch needs "
                        "no key, and build_prices.py keeps it current.")
            else:
                fig = charts.chokepoint_map(shipping.on_map(ship_board),
                                            tankers=layer == "TANKERS")
        elif layer == "PORT CARGO":
            if port_now.empty:
                st.info("No port cargo stored yet. Run once: "
                        "`uv run python scripts/build_shipping.py` (the first run takes "
                        "about ten minutes; PortWatch answers port queries slowly).")
            else:
                fig = charts.port_map(port_now)
        elif layer in ("AIR", "FREIGHTERS"):
            if live_air is None:
                st.info("OpenSky did not answer. Its anonymous allowance is about 100 "
                        "worldwide calls a day; try again in a while.")
            elif layer == "FREIGHTERS":
                fig = charts.aircraft_map(aviation.grid(freighters), air_at,
                                          what="freighters", colour=ui.AMBER)
            else:
                fig = charts.aircraft_map(aviation.grid(air), air_at)
        else:                                                        # LIVE AIS
            ais_key = secret(ais.KEY_NAME)
            if not ais_key:
                st.info("Live ships need a free AISStream key: sign in with GitHub at "
                        "aisstream.io/account, create a key, and add the line "
                        f"`{ais.KEY_NAME}=…` to `.env.local` (open it with "
                        "`open -e .env.local`). Restart the app afterwards.")
            else:
                l1, l2, l3 = st.columns([3, 1, 1])
                ais_region = l1.selectbox("Region", list(ais.REGIONS), key="ais_region",
                                          label_visibility="collapsed")
                ais_seconds = l2.selectbox("Listen", [30, 60, 120, 300], index=1,
                                           format_func=lambda s: f"{s} s",
                                           key="ais_seconds", label_visibility="collapsed")
                if l3.button("LISTEN", key="ais_listen"):
                    with st.spinner(f"Listening to {ais_region} for {ais_seconds} s …"):
                        try:
                            heard = ais.listen(ais_key, ais_region, ais_seconds)
                        except ais.ListenFailed as exc:
                            st.warning(f"AISStream: {exc}. Check the key in .env.local, "
                                       "or wait a minute (3 connections at most).")
                            heard = None
                    if heard is not None:
                        prior = st.session_state.get("ais_static")
                        if prior is None:
                            prior = ais.load_static(ais_static_path)
                        ships_now, ais_static = ais.read(heard, prior)
                        ais.save_static(ais_static_path, ais_static)
                        st.session_state["ais_static"] = ais_static
                        st.session_state["ais_last"] = (ais_region, ais.listened_at(),
                                                        ais_seconds, len(heard), ships_now)
                last = st.session_state.get("ais_last")
                if last is None:
                    st.caption("Pick a region and press LISTEN. Nothing is fetched until "
                               "then. Coverage is best near Europe, North America and "
                               "East Asia.")
                elif last[4].empty:
                    # 02.10.2026, Hormuz: 60 s, nothing but the subscription confirmed.
                    st.info(f"No ship heard in {last[0]} during {last[2]} s. Either no "
                            "free coastal receiver covers it — the Persian Gulf and the "
                            "Red Sea are thin — or no ship there is transmitting. That "
                            "is not the same as no ships.")
                else:
                    fig = charts.live_ship_map(last[4], ais.REGIONS[last[0]])
        if fig is not None:
            fig.update_layout(height=520)
            st.plotly_chart(fig, width="stretch", key="map_main_fig")

    with c_side:
        movers = []
        if not ship_board.empty:
            # Busy now OR a year ago: Hormuz at 3 ships a day (from ~85) is the biggest
            # mover of 2026, and a filter on today's level alone dropped it.
            year_ago = ship_board.ships_per_day / (1 + ship_board.vs_year_ago.astype(float))
            busy = ship_board[(ship_board.ships_per_day >= 5) | (year_ago >= 5)]
            movers += [{"where": r.chokepoint, "kind": "STRAIT", "chg": r.vs_year_ago}
                       for r in busy.itertuples() if r.vs_year_ago is not None
                       and not pd.isna(r.vs_year_ago)]
        if not port_now.empty:
            movers += [{"where": r.port, "kind": "PORT", "chg": r.total_vs_year_ago}
                       for r in port_now.itertuples() if r.total_vs_year_ago is not None
                       and not pd.isna(r.total_vs_year_ago) and r.mostly
                       != "too little to tell"]
        st.markdown("**MOVERS vs 1Y**")
        if movers:
            mv = pd.DataFrame(movers)
            mv = mv.reindex(mv.chg.abs().sort_values(ascending=False).index).head(14)
            st.markdown(ui.grid(pd.DataFrame({
                "where": mv["where"], "": mv["kind"],
                "chg": mv["chg"].map(lambda v: ui.pct(v, 0))}),
                signed={"chg"}, ticker_col="where"), unsafe_allow_html=True)
            st.caption("Seven-day average against the same week a year earlier: "
                       "ships per day for straits, tonnes per day for ports. Straits "
                       "under 5 ships a day both now and a year ago, and near-empty "
                       "ports, are left out.")
        else:
            st.caption("Nothing stored yet.")

    d_straits, d_ports, d_air, d_ais = st.tabs(
        ["CHOKEPOINTS", "PORT CARGO", "AIR", "LIVE AIS"], key="map_detail",
        on_change="rerun")
    if d_air.open and live_air is None:
        live_air = _air(True)
        if live_air is not None:
            air_at, air = live_air
            freighters = aviation.cargo_flights(air)

    with d_straits:
        if ship_board.empty:
            st.caption("No ship traffic stored yet.")
        else:
            key_only = st.toggle("Main chokepoints only", value=True, key="ship_key")
            shown = ship_board[ship_board.chokepoint.isin(shipping.KEY)] if key_only \
                else ship_board
            st.markdown(ui.grid(pd.DataFrame({
                "chokepoint": shown.chokepoint,
                "ships/day": shown.ships_per_day.map(lambda v: ui.num(v, 0)),
                "vs year ago": shown.vs_year_ago.map(lambda v: ui.pct(v, 0)),
                "tankers/day": shown.tankers_per_day.map(lambda v: ui.num(v, 0)),
                "tankers vs year ago": shown.tankers_vs_year_ago.map(
                    lambda v: ui.pct(v, 0)),
            }), numeric={"ships/day", "tankers/day"},
                signed={"vs year ago", "tankers vs year ago"}, ticker_col="chokepoint"),
                unsafe_allow_html=True)
            s1, s2, s3 = st.columns([3, 1, 1])
            ship_pick = s1.multiselect(
                "Chokepoints", options=ship_board.chokepoint.tolist(),
                default=shipping.KEY[:3], key="ship_pick", label_visibility="collapsed")
            ship_field = s2.selectbox(
                "Ships", ["n_total", "n_tanker", "n_container", "n_dry_bulk"],
                format_func={"n_total": "all ships", "n_tanker": "tankers",
                             "n_container": "container ships",
                             "n_dry_bulk": "bulk carriers"}.get,
                key="ship_field", label_visibility="collapsed")
            ship_since = s3.selectbox("Since", ["1Y", "3Y", "7Y"], key="ship_since",
                                      label_visibility="collapsed")
            if ship_pick:
                ship_lines = shipping.history(
                    src.con, ship_pick, ship_field,
                    dt.date.today() - dt.timedelta(days=365 * int(ship_since[:-1])))
                if not ship_lines.empty:
                    st.line_chart(ship_lines, height=280,
                                  color=[ui.AMBER, ui.CYAN, ui.UP, ui.DOWN,
                                         ui.MUTED][:len(ship_lines.columns)])
            st.caption(
                f"IMF PortWatch, ships passing each day as a seven-day average, as of "
                f"{ship_board.as_of.max():%Y-%m-%d} (about five days behind). On the map "
                "the circle's area is the count and its colour the change; hover for the "
                "numbers. Compared with the same week a year earlier, so seasons cancel "
                "out. Ships that switch off their transponder are not counted — tankers "
                "carrying sanctioned oil often do, so tanker counts through Hormuz and "
                "the Black Sea read low. Recent days are revised by PortWatch.")

    with d_ports:
        if port_now.empty:
            st.caption("No port cargo stored yet.")
        else:
            st.markdown(ui.grid(pd.DataFrame({
                "port": port_now.port,
                "known for": port_now.known_for,
                "mostly (30d)": port_now.mostly.fillna("—"),
                "out kt/day": port_now.export_t.map(lambda v: ui.num(v / 1e3, 0)),
                "out vs year ago": port_now.export_vs_year_ago.map(
                    lambda v: ui.pct(v, 0)),
                "in kt/day": port_now.import_t.map(lambda v: ui.num(v / 1e3, 0)),
                "in vs year ago": port_now.import_vs_year_ago.map(lambda v: ui.pct(v, 0)),
            }), numeric={"out kt/day", "in kt/day"},
                signed={"out vs year ago", "in vs year ago"}, ticker_col="port"),
                unsafe_allow_html=True)
            p1, p2, p3 = st.columns([3, 1, 1])
            port_names = dict(zip(port_now.id, port_now.port))
            port_pick = p1.multiselect(
                "Ports", options=list(port_names), format_func=port_names.get,
                default=[p for p in ("port955", "port1091", "port570") if p in port_names],
                key="port_pick", label_visibility="collapsed")
            port_field = p2.selectbox(
                "Flow", ["export", "import", "export_tanker", "import_tanker",
                         "export_dry_bulk", "import_dry_bulk", "export_container",
                         "import_container"],
                format_func=lambda f: f.replace("_", " ").replace("export", "out")
                .replace("import", "in"),
                key="port_field", label_visibility="collapsed")
            port_since = p3.selectbox("Since", ["1Y", "3Y", "7Y"], key="port_since",
                                      label_visibility="collapsed")
            if port_pick:
                port_lines = shipping.port_history(
                    src.con, port_pick, port_field,
                    dt.date.today() - dt.timedelta(days=365 * int(port_since[:-1])))
                if not port_lines.empty:
                    st.line_chart(port_lines / 1e3, height=280, y_label="kt a day",
                                  color=[ui.AMBER, ui.CYAN, ui.UP, ui.DOWN,
                                         ui.MUTED][:len(port_lines.columns)])
            st.caption(
                "IMF PortWatch estimates of the tonnes ships loaded (out) and unloaded "
                "(in) each day, from each ship's size and the change in its draught; a "
                "seven-day average in thousand tonnes, against the same week a year "
                "earlier. Quantities and ship types, not commodities: tanker tonnes may "
                "be crude or products, bulk tonnes ore or grain. Ships sailing dark are "
                "missing — Saudi tankers at Yanbu have mostly switched off their "
                "transponders since the Houthi blockade of July 2026, so Yanbu reads "
                "near zero while Lloyd's List reports traffic down about a third.")

    with d_air:
        if live_air is None:
            st.caption("OpenSky did not answer; no aircraft right now.")
        else:
            regions_now = aviation.by_region(air)
            air_hist = aviation.history(src.con)
            a1, a2 = st.columns(2)
            table_air = pd.DataFrame({"region": regions_now.index,
                                      "airborne now": regions_now.map("{:,}".format)})
            if not air_hist.empty:
                last_stored = air_hist.iloc[-1]
                table_air[f"stored {last_stored.name:%Y-%m-%d %H:%M}"] = [
                    f"{int(last_stored[r]):,}" if r in last_stored else "—"
                    for r in regions_now.index]
            a1.markdown(ui.grid(table_air, numeric=set(table_air.columns) - {"region"},
                                ticker_col="region"), unsafe_allow_html=True)
            if not freighters.empty:
                by_airline = freighters.airline.value_counts()
                a2.markdown(ui.grid(pd.DataFrame({
                    "cargo airline": by_airline.index,
                    "in the air now": by_airline.map("{:,}".format)}),
                    numeric={"in the air now"}, ticker_col="cargo airline"),
                    unsafe_allow_html=True)
            if len(air_hist) > 1:
                st.line_chart(air_hist, height=240)
            cargo_hist = aviation.cargo_history(src.con)
            if len(cargo_hist) > 1:
                st.line_chart(cargo_hist[["share"]] * 100, height=200)
                st.caption("Freighters as % of all aircraft in the air, per stored "
                           "snapshot.")
            st.caption(
                f"{len(air):,} aircraft airborne at {air_at:%Y-%m-%d %H:%M} UTC, each "
                "circle the count in a 3° cell. Data: "
                f"{aviation.ATTRIBUTION}. Coverage follows volunteer receivers: dense "
                "over Europe and North America, thin over oceans, Africa, Russia and "
                "China — a low count there can mean no receiver, not no aircraft. "
                "Military and some private aircraft do not broadcast. Freighters are "
                "recognised by the cargo airline in their callsign (FedEx, UPS, DHL, "
                "Cargolux, …); what they carry is never public, and passenger airlines' "
                "belly cargo — about half of all air freight — is not counted. "
                "Owners are not shown: for private aircraft they are people. The daily "
                "price build stores one count per region, at about the same time each "
                "weekday; counts from different times of day do not compare.")

    with d_ais:
        last = st.session_state.get("ais_last")
        if last is None:
            st.caption("Choose the LIVE AIS layer above, a region, and press LISTEN.")
        else:
            region_l, at_l, secs_l, n_msgs, ships_l = last
            st.markdown(ui.grid(ais.summary(ships_l), numeric={
                "ships", "under way", "mean speed kn"}, ticker_col="type"),
                unsafe_allow_html=True)
            shown_l = ships_l.sort_values(["type", "name"], na_position="last")
            st.markdown(ui.grid(pd.DataFrame({
                "ship": shown_l["name"].fillna("—"),
                "type": shown_l["type"],
                "kn": shown_l["speed_kn"].map(lambda v: ui.num(v, 1)),
                "status": shown_l["status"].replace("", "—"),
                "destination": shown_l["destination"].fillna("—"),
                "draught m": shown_l["draught_m"].map(lambda v: ui.num(v, 1)),
                "length m": shown_l["length_m"].map(lambda v: ui.num(v, 0)),
                "IMO": shown_l["imo"].map(
                    lambda v: "—" if v is None or pd.isna(v) else str(int(v))),
            }), numeric={"kn", "draught m", "length m"}, ticker_col="ship"),
                unsafe_allow_html=True)
            with_type = (ships_l["type"] != "unknown").sum()
            st.caption(
                f"{len(ships_l)} ships heard in {region_l} during {secs_l} s at "
                f"{at_l:%Y-%m-%d %H:%M} UTC ({n_msgs:,} messages), via AISStream "
                f"(coastal receivers). {with_type} of them with type, destination and "
                "draught known — ships send those only every six minutes; what was "
                "heard is kept on this Mac, so every listen fills more gaps. Destination "
                "and draught are typed in by the crew and often stale; a deep draught "
                "on a tanker usually means it is loaded. Ships with their transponder "
                "off are not here — in 2026 many tankers around Hormuz and Yanbu. "
                "Owners are not shown; the IMO number identifies the ship.")


def _view_calendar() -> None:
    st.markdown("#### Who reports next")
    st.write(
        "Estimated locally from each company's own filing rhythm, then confirmed against "
        "the announced date for the soonest names. Every row says which of the two it "
        "is — a projected date presented as a schedule would be a quiet fiction."
    )
    weeks = st.slider("Weeks ahead", 1, 12, 6, key="cal_weeks")
    watch = _watched()
    extra = st.text_input(
        "Companies to include", value=", ".join(watch),
        placeholder="tickers, comma-separated",
        help="Defaults to everything you follow: watchlist, tracking, hand-picked.",
        key=f"cal_tickers_{len(watch)}",
    )
    wanted = [t.strip().upper() for t in extra.replace("\n", ",").split(",") if t.strip()]
    if not wanted:
        st.info("Add some tickers, put names on the watchlist, or track a portfolio.")
    else:
        with st.spinner("Reading filing rhythms"):
            cal, cal_err = ac.attempt(ac.earnings_calendar, src.con,
                                      str(cfg.CACHE_DIR / SUBMISSIONS),
                                      tuple(wanted), weeks, asof)
            if cal_err:
                # Estimates are local; only the announced dates need the network.
                st.caption(f"Announced dates unreachable ({cal_err}); estimates only.")
                cal = rs.earnings_calendar(src.con, cfg.CACHE_DIR / SUBMISSIONS,
                                           wanted, weeks=weeks, confirm=False)
        if cal.empty:
            st.info(
                f"Nothing due in the next {weeks} weeks for those companies — or their "
                "filing history is too short to project from."
            )
        else:
            confirmed = int((cal.source.str.startswith("confirmed")).sum())
            st.markdown(ui.stats([
                ("REPORTING", str(len(cal)), "amber"),
                ("CONFIRMED", str(confirmed), "up"),
                ("ESTIMATED", str(len(cal) - confirmed), ""),
                ("NEXT", str(cal.iloc[0].date), ""),
            ]), unsafe_allow_html=True)
            st.markdown(ui.grid(pd.DataFrame({
                "ticker": cal.ticker,
                "name": cal.name.astype(str).str.slice(0, 26),
                "date": cal.date.astype(str),
                "in days": cal.days_away.astype(str),
                "basis": np.where(cal.source.str.startswith("confirmed"),
                                  "announced", "estimated"),
                "eps est": cal.eps_estimate.map(ui.num),
                "last filed": cal.last_filed.astype(str),
            }), numeric={"in days", "eps est"}), unsafe_allow_html=True)
            st.caption(
                "An estimate is last year's filing date for the same fiscal quarter "
                "moved forward 364 days — accurate to within a few days because filing "
                "deadlines do not move much, but still an estimate. Announced dates "
                "come from the company."
            )


def _view_lab(default_ticker: str = "") -> None:
    """A buy/sell rule on the held basket or one stock (the old LAB)."""
    if not _need_portfolio("a rule is saved with its portfolio."):
        return
    lab_mode = st.radio(
        "Test the rule on", ["Basket you hold", "Single stock"], horizontal=True,
        key=f"lab_mode_{chosen}",
    )
    lab_single = lab_mode == "Single stock"
    if lab_single:
        held_default = default_ticker or (store.get_spec(chosen).selected or [""])[0]
        lab_ticker = st.text_input(
            "Ticker", value=held_default, key=f"lab_ticker_{chosen}_{held_default}",
            placeholder="e.g. AAPL",
        ).strip().upper()
        held_now = [lab_ticker] if lab_ticker else []
    else:
        lab_ticker = ""
        held_now = store.get_spec(chosen).selected
    lab_key = f"lab_result_{chosen}_{lab_ticker or 'basket'}"
    if not held_now:
        st.caption(
            "Type a ticker to test a buy/sell rule on one stock." if lab_single else
            "The lab tests a RULE on the names you hold. Tick some in SCREEN first — "
            "the rule decides when to be in them, not which ones to own."
        )
    else:
        lab_spec = store.get_strategy(chosen)
        lab_history = store.messages(chosen, kind="lab")
        if lab_history:
            # ui.log escapes the text; the hand-rolled f-string this replaced did not,
            # which made anything typed into the box — or returned by the model — run as
            # HTML in the person's own browser.
            st.markdown(
                ui.log([(m["role"], m["text"]) for m in lab_history]),
                unsafe_allow_html=True,
            )

        # A plain text input rather than a second chat_input: chat_input pins itself to
        # the bottom of the page, and two of them on one page is confusing rather than
        # convenient. (The empty tab that first sent me looking here was neither — the
        # calendar tab above was still computing, and Streamlit renders the tabs after
        # it only once the script gets that far.)
        prompt = st.text_input(
            "Rule", placeholder="buy below the lower Bollinger band, sell at the average, 10% stop",
            label_visibility="collapsed", key=f"lab_prompt_{chosen}",
        )
        if prompt and prompt == st.session_state.get(f"lab_last_{chosen}"):
            prompt = None                      # already handled on the previous rerun
        elif prompt:
            st.session_state[f"lab_last_{chosen}"] = prompt
        if prompt:
            if not secret("ANTHROPIC_API_KEY"):
                st.error("ANTHROPIC_API_KEY missing from .env.local.")
            else:
                store.add_message(chosen, "user", prompt, kind="lab")
                try:
                    out = LT.translate(prompt, lab_spec)
                    store.save_strategy(chosen, out.spec)
                    store.add_message(
                        chosen, "assistant",
                        "\n".join(f"- {c}" for c in out.changed) or "- no change",
                        kind="lab")
                except Exception as exc:                      # noqa: BLE001
                    store.add_message(chosen, "assistant", f"could not translate: {exc}",
                                      kind="lab")
                st.rerun()

        st.markdown("#### Rule")
        for line in lab_spec.describe():
            st.markdown(f"- {line}")
        if lab_spec.unsupported:
            st.warning("Not applied: " + "; ".join(lab_spec.unsupported))

        c1, c2 = st.columns([1, 1])
        lab_start = c1.date_input(
            "From", value=max(dt.date(2011, 1, 3), asof - dt.timedelta(days=365 * 10)),
            min_value=dt.date(2010, 1, 4), max_value=asof, key=f"lab_start_{chosen}")
        go_lab = c2.button("Run rule", width="stretch", key=f"lab_go_{chosen}")

        if go_lab:
            bars = src.con.execute(
                """
                SELECT ticker, date,
                       open * (close_adj / NULLIF(close, 0)) AS open_adj, close_adj
                FROM prices
                WHERE ticker IN (SELECT UNNEST(?)) AND date BETWEEN ? AND ?
                  AND open IS NOT NULL AND close_adj IS NOT NULL
                ORDER BY date
                """,
                [list(held_now), lab_start, asof],
            ).fetch_df()
            if bars.empty and lab_single:
                # Not in the store: any Yahoo ticker (BHP.AX, SAP.DE), fetched on demand.
                yb = _yahoo_bars(lab_ticker)
                if not yb.empty:
                    yb = yb[pd.to_datetime(yb.date) >= pd.Timestamp(lab_start)]
                    bars = yb[["ticker", "date", "open_adj", "close_adj"]].dropna()
            bench = src.con.execute(
                "SELECT date, close_adj FROM prices WHERE ticker = ? "
                "AND date BETWEEN ? AND ? ORDER BY date",
                [bktest.BENCHMARK, lab_start, asof],
            ).fetch_df()
            rate_l, rate_l_src = mx.risk_free_rate(src.con, lab_start, asof,
                                                   settings.risk_free_fallback)
            caps = _frame(asof, DATA_KEY).set_index("ticker").reindex(list(held_now))
            median_cap = float(pd.to_numeric(
                caps.get("shares_outstanding", pd.Series(dtype=float)), errors="coerce"
            ).mul(pd.to_numeric(caps.get("price", pd.Series(dtype=float)),
                                errors="coerce")).median() or 0.0)
            out = lab_engine.run(
                bars, lab_spec, costs=settings.cost,
                benchmark=(bench.set_index("date")["close_adj"] if len(bench) else None),
                benchmark_symbol=bktest.BENCHMARK, risk_free=rate_l,
                cost_tier_market_cap=median_cap,
            )
            # One stock: the question is always "better than just holding it?", so the
            # same stock, same window, same costs, held throughout, runs alongside.
            base = (lab_engine.run(bars, StrategySpec(signal="always_in"),
                                   costs=settings.cost, cost_tier_market_cap=median_cap)
                    if lab_single and out is not None else None)
            st.session_state[lab_key] = out
            st.session_state[lab_key + "_base"] = base
            if out is not None:
                store.record_run(
                    chosen, "lab", lab_spec.fingerprint(), data_fp,
                    out.dates[0], out.dates[-1],
                    {"signal": lab_spec.signal, "names": len(held_now),
                     "ticker": lab_ticker or None}, out.metrics)

        out = st.session_state.get(lab_key)
        base = st.session_state.get(lab_key + "_base")
        if out is None and go_lab:
            st.info("No overlapping price history for these names in that window.")
        elif out is not None:
            m = out.metrics
            _result_block(
                out, name="Rule", turnover=out.turnover, height=300,
                lines=({f"Buy & hold {lab_ticker}": (base.portfolio, ui.MUTED)}
                       if base is not None else None),
                cells=[("IN THE MARKET", ui.pct(m.get("average_exposure")), ""),
                       ("TRADES", f"{out.trades:,}", "")])
            if lab_single:
                _lab_price_chart(src.con, lab_ticker, out, lab_spec)
            if base is not None:
                bm = base.metrics
                st.caption(
                    f"Buy & hold {lab_ticker}: {ui.pct(bm['annual_return'])} a year, max "
                    f"drawdown {ui.pct(bm['max_drawdown'])}. The rule is only worth its "
                    "trades if it beats this line after costs, or gets close with a "
                    "smaller drawdown."
                )

            st.caption(
                f"Sharpe uses a risk-free rate of {rate_l:.2%} ({rate_l_src}). Turnover "
                "is one-way and annualised: 200% means the whole basket changed hands "
                "twice a year, and every one of those changes paid a spread."
            )
            for note in out.notes:
                st.warning(note)
            st.warning(out.label)
            tried = store.backtest_count(lab_spec.fingerprint(), data_fp)
            st.caption(
                f"This exact rule has been tried {tried}× against this data build. A lab "
                "has more knobs than a screen, so the count matters more here: turn "
                "enough of them and something always looks good."
            )

        def _open_rule_book(name, amount):
            stored = src.con.execute(
                "SELECT COUNT(DISTINCT ticker) FROM prices WHERE ticker IN "
                "(SELECT UNNEST(?))", [list(held_now)]).fetchone()[0]
            if stored < len(held_now):
                raise ValueError(
                    "A paper book is valued from the stored prices, and this ticker is "
                    "not in the store (it came from Yahoo on demand). Only US-listed "
                    "names can be followed as paper books for now.")
            caps_now = _frame(asof, DATA_KEY).set_index("ticker").reindex(list(held_now))
            cap_now = float(pd.to_numeric(
                caps_now.get("shares_outstanding", pd.Series(dtype=float)),
                errors="coerce").mul(pd.to_numeric(
                    caps_now.get("price", pd.Series(dtype=float)),
                    errors="coerce")).median() or 0.0)
            return paper.open_rule(store.con, name, list(held_now), lab_spec, asof,
                                   amount, cost_tier_market_cap=cap_now)

        _paper_section(
            "LAB", f"lab_{chosen}_{lab_ticker or 'basket'}", _open_rule_book,
            f"{lab_ticker or 'Basket'} · {lab_spec.signal} {asof}",
            "Follow this rule live from today with paper money: signals read at each "
            "close, orders filled at the next open, costs charged on every trade. The "
            "rule is frozen as it stands now.")


def _view_alerts() -> None:
    """ALRT: level, move and new-filing alerts on end-of-day closes."""
    st.markdown("#### Alerts")
    rules_now = alerts.rules(store.con)
    with st.form("alert_form", clear_on_submit=True):
        a1, a2, a3, a4 = st.columns([1, 2, 1, 1])
        a_ticker = a1.text_input("Ticker", placeholder="AAPL")
        a_kind = a2.selectbox("When", list(alerts.KINDS),
                              format_func=lambda k: alerts.KINDS[k])
        a_x = a3.number_input("Level / %", min_value=0.0, value=0.0, step=1.0)
        a4.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if a4.form_submit_button("Add", width="stretch") and a_ticker.strip():
            try:
                alerts.add(store.con, a_ticker, a_kind,
                           None if a_kind == "new_filing" else a_x)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    if rules_now.empty:
        st.caption("No alerts yet.")
    else:
        for rule in rules_now.itertuples():
            r1, r2 = st.columns([5, 1])
            level = "" if rule.kind == "new_filing" else f" {rule.threshold:g}"
            r1.markdown(f"`{rule.ticker}` — {alerts.KINDS[rule.kind]}{level}")
            if r2.button("×", key=f"rm_alert_{rule.id}"):
                alerts.remove(store.con, rule.id)
                st.rerun()
        if st.button("Check now against the stored closes", key="alerts_now"):
            fired, state_now = alerts.run(store.con, src.con,
                                          alerts.load_state(cfg.ALERTS_STATE))
            alerts.save_state(cfg.ALERTS_STATE, state_now)
            st.success("; ".join(e["message"] for e in fired) if fired
                       else "Nothing new fired.")
    history_log = alerts.load_state(cfg.ALERTS_STATE).get("log", [])[-10:][::-1]
    if history_log:
        st.markdown(ui.grid(pd.DataFrame({
            "bar": [e["on"] for e in history_log],
            "alert": [e["message"] for e in history_log]}),
            ticker_col="bar"), unsafe_allow_html=True)
    st.caption(
        "Checked on end-of-day closes: every morning run (after the price refresh, "
        "with a Mac notification) and with the button above. A level alert fires "
        "when the close crosses the level, once. New-filing alerts need the morning "
        "run or scripts/check_alerts.py, which ask EDGAR.")


def _view_filing_search(default_ticker: str = "") -> None:
    """FTS: every SEC filing since 2001, full text."""
    st.markdown("#### Search every SEC filing (full text, 2001 onwards)")
    with st.form("fts_form"):
        f1, f2 = st.columns([3, 1])
        fts_q = f1.text_input("Words", placeholder='"pricing pressure" tariff',
                              help='Phrases in double quotes. Every word must appear.')
        fts_ticker = f2.text_input("Only this ticker", value=default_ticker,
                               placeholder="optional")
        f3, f4, f5 = st.columns([2, 1, 1])
        fts_forms = f3.multiselect(
            "Forms", ["10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "DEF 14A", "SC 13D"],
            default=["10-K", "10-Q", "8-K"])
        fts_from = f4.date_input("From", value=dt.date.today() - dt.timedelta(days=730),
                             format="YYYY-MM-DD")
        fts_to = f5.date_input("To", value=dt.date.today(), format="YYYY-MM-DD")
        go_fts = st.form_submit_button("Search", width="stretch")
    if go_fts and fts_q.strip():
        fts_cik = None
        if fts_ticker.strip():
            hit_cik = src.con.execute(
                "SELECT cik FROM securities WHERE ticker = upper(?) LIMIT 1",
                [fts_ticker.strip()]).fetchone()
            fts_cik = hit_cik[0] if hit_cik else None
            if fts_cik is None:
                st.warning(f"{fts_ticker} is not a listing in the store; "
                           "searching all companies.")
        fts_failed = False
        try:
            found, total = fts.search(fts_q, fts_forms, fts_from, fts_to, fts_cik,
                                      pages=2)
        except Exception as exc:                                  # noqa: BLE001
            found, total, fts_failed = pd.DataFrame(), 0, True
            st.error(f"The SEC search could not be reached ({type(exc).__name__}).")
        if not found.empty:
            st.caption(f"{total:,} filings contain it; the latest {len(found)} shown. "
                       "A hit means the words appear, not that they matter — "
                       "risk-factor boilerplate matches too.")
            st.dataframe(found, hide_index=True, width="stretch",
                         column_config={"url": st.column_config.LinkColumn(
                             "filing", display_text="open")})
        elif not fts_failed:
            st.info("No filing contains that.")


def _view_briefing() -> None:
    """TOP: the daily briefing on what you follow."""
    st.markdown("#### Daily briefing")
    followed = store.everything_followed()
    if not followed:
        st.info(
            "Nothing followed yet. Add names to the watchlist on the left, or track a "
            "portfolio on the SCREEN tab — the briefing is written about what you hold "
            "and watch, not about the market in general."
        )
    else:
        st.caption(
            f"Covering {len(followed)} companies: {', '.join(followed[:12])}"
            + (f" and {len(followed) - 12} more" if len(followed) > 12 else "")
        )
        if not secret("ANTHROPIC_API_KEY"):
            st.error("Needs ANTHROPIC_API_KEY in .env.local.")
        elif st.button("Write today's briefing", type="primary",
                       width="stretch", key="brief_go"):
            with st.spinner("Gathering evidence, then writing"):
                bundle = bfg.gather(src.con, followed)
                try:
                    st.session_state["briefing"] = bfg.write(bundle)
                    st.session_state["bundle"] = bundle
                except Exception as exc:
                    st.error(f"Could not write the briefing: {exc}")

        brief = st.session_state.get("briefing")
        bundle = st.session_state.get("bundle")
        if brief:
            # The model's text is rendered as Markdown, never as raw HTML: it is built
            # from fetched headlines, and a headline is text someone else wrote. Until
            # 02.10.2026 it went straight into unsafe_allow_html.
            with st.container(border=True):
                st.markdown(ui.markdown_text(brief["text"]))
            st.caption(
                "Written only from the evidence below — the headlines that were actually "
                "fetched and the moves that were actually measured. Nothing was recalled "
                "or looked up, so every line traces to something you can check."
            )

        if bundle is not None:
            notable = bundle["notable"]
            if not notable.empty:
                st.markdown("#### Moved unusually")
                st.markdown(ui.grid(pd.DataFrame({
                    "ticker": notable.ticker,
                    "move": notable.move.map(lambda v: ui.pct(v, 1)),
                    "z": notable.z.map(lambda v: f"{v:+.1f}"),
                    "typical day": notable.typical_day.map(lambda v: f"{v:.1%}"),
                    "date": notable.date.astype(str),
                }), signed={"move", "z"}, numeric={"typical day"}),
                    unsafe_allow_html=True)
                st.caption(
                    "Scored against each company's own normal day, so a 4% move on a "
                    "quiet utility outranks the same move on a biotech. Two thresholds "
                    "have to hold — statistically odd AND at least 1% — because a share "
                    "that never moves would otherwise top the list on a rounding error."
                )

            with st.expander("The evidence the briefing was given"):
                st.code(brief["evidence"] if brief else bfg._evidence(bundle),
                        language="text")

            feed = bundle["news"]
            if not feed.empty:
                st.markdown("#### Headlines")
                for row in feed.head(25).itertuples():
                    st.markdown(
                        f"<span style='color:{ui.AMBER};font-weight:600'>{html.escape(str(row.tickers))}</span>  "
                        f"<span style='color:{ui.MUTED};font-size:0.70rem'>{html.escape(str(row.when))} · "
                        f"{html.escape(str(row.source))}</span>", unsafe_allow_html=True)
                    st.markdown(f"**[{row.title}]({row.url})**")
                    if row.summary:
                        st.caption(row.summary)


# ------------------------------------------------------------------ the command line
# One security is "loaded" at a time, as on a terminal: every single-ticker field (the
# security tab, the option builder, a rule, a savings plan, compare, precedent deals,
# the filing search) starts at it. Typed into the line at the top: `AAPL US FA`.
# Parsing, resolution and routing are command.py (pure, tested); this is the wiring.
def _ctx() -> str | None:
    """The loaded security's symbol, or None."""
    return command.current(st.session_state)


def _in_store(ticker: str) -> bool:
    return bool(_market_source().con.execute(
        "SELECT 1 FROM securities WHERE ticker = ? LIMIT 1", [ticker]).fetchone())


def _ctx_symbol() -> str:
    return _ctx() or ""


def _open(tab: str, section: str | None) -> None:
    """Select a tab (and its section) before the tabs are drawn on this run."""
    st.session_state[layout.MAIN_KEY] = tab
    if section:
        st.session_state[layout.section_key(tab)] = section


def _execute(text: str) -> None:
    """Run one command: load the security it names, open the tab its function lives in."""
    st.session_state.pop("cmd_choices", None)
    parsed, found, action = command.run(_market_source().con, text, _ctx())
    st.session_state["cmd_msg"] = action.message
    if action.tab is None:
        if found.choices:
            st.session_state["cmd_choices"] = (list(found.choices), parsed.function)
        return
    command.push(st.session_state, action.symbol)
    code = parsed.function or "DES"
    st.session_state["cmd_focus"] = code
    _open(action.tab, action.section or layout.SUB_SECTION.get(code))


def _on_command() -> None:
    _execute(st.session_state.get("cmd_input", ""))
    st.session_state["cmd_input"] = ""


def _pick_choice(ticker: str, function: str | None) -> None:
    _execute(f"{ticker} {function or ''}")


def _go_back() -> None:
    previous = command.back(st.session_state)
    if previous is not None:
        st.session_state["cmd_msg"] = f"Back to {previous}."
        _open(layout.SECURITY, None)


def _command_bar() -> None:
    # A link like http://localhost:8501/?cmd=AAPL%20FA runs once when opened.
    linked = st.query_params.get("cmd")
    if linked and st.session_state.get("cmd_linked") != linked:
        st.session_state["cmd_linked"] = linked
        _execute(linked)
    c1, c2, c3, c4 = st.columns([6, 0.8, 0.9, 2.6], vertical_alignment="center")
    c1.text_input(
        "Command", key="cmd_input", on_change=_on_command, label_visibility="collapsed",
        placeholder="AAPL US FA <GO>  ·  SAP GR DES  ·  SPX INDEX GP  ·  EQS  ·  MA  ·  "
                    "WEI — Enter runs it")
    c2.button("GO", key="cmd_go", on_click=_on_command, width="stretch")
    history = st.session_state.get(command.HISTORY_KEY) or []
    c3.button("◀ BACK", key="cmd_back", on_click=_go_back, width="stretch",
              disabled=len(history) < 2,
              help="The security before this one (the last ten are kept).")
    sec = _ctx()
    c4.markdown(
        f"<span style='color:{ui.MUTED};font-size:0.7rem'>LOADED</span> "
        f"<span style='color:{ui.AMBER};font-weight:600'>"
        f"{html.escape(sec) if sec else '—'}</span>", unsafe_allow_html=True)
    msg = st.session_state.get("cmd_msg")
    if msg:
        st.caption(msg)
    picks = st.session_state.get("cmd_choices")
    if picks:
        options, function = picks
        cols = st.columns(min(len(options), 5))
        for i, ticker in enumerate(options):
            cols[i % len(cols)].button(ticker, key=f"cand_{ticker}", width="stretch",
                                       on_click=_pick_choice, args=(ticker, function))
    with st.expander("Functions"):
        st.markdown(ui.grid(pd.DataFrame(
            [(code, t.tab + (f" → {t.section or layout.SUB_SECTION.get(code, '')}"
                             if (t.section or code in layout.SUB_SECTION) else ""),
              t.label) for code, t in command.FUNCTIONS.items()],
            columns=["code", "opens", "what"]), ticker_col="code"),
            unsafe_allow_html=True)
        st.caption("A security alone opens its description. A market after the ticker "
                   "picks the line: SAP GR = Xetra, 7203 JT = Tokyo; Canada is typed "
                   "RY CAN, because CN is the news function. A yellow key is optional "
                   "(SPX INDEX = the S&P 500, not a share called SPX). A company name "
                   "works too (newmont). Links: ?cmd=AAPL%20FA.")


# ---------------------------------------------------------------- HP: the price table
def _sec_prices(c) -> None:
    """HP: the stored bars as a table — daily, weekly or monthly — with the change."""
    symbol = c.ticker
    bars = src.con.execute(
        "SELECT date, open, high, low, COALESCE(close_raw, close) AS close, close_adj, "
        "volume FROM prices WHERE ticker = ? ORDER BY date", [symbol]).fetch_df()
    source = "stored closes"
    if bars.empty:
        bars = _yahoo_bars(symbol)
        source = "Yahoo, not stored"
    if bars.empty:
        st.info(f"No price history for {symbol}.")
        return
    p1, p2 = st.columns([1, 1])
    every = p1.segmented_control("Every", ["day", "week", "month"], default="day",
                                 key="hp_every") or "day"
    span = p2.segmented_control("Last", ["1M", "3M", "1Y", "5Y"], default="3M",
                                key="hp_span") or "3M"
    frame = bars.assign(date=pd.to_datetime(bars["date"])).set_index("date")
    if every != "day":
        rule = {"week": "W-FRI", "month": "ME"}[every]
        frame = frame.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                          "close": "last", "close_adj": "last",
                                          "volume": "sum"}).dropna(subset=["close"])
    since = frame.index[-1] - pd.Timedelta(days={"1M": 31, "3M": 92, "1Y": 366,
                                                 "5Y": 1827}[span])
    frame["change"] = frame["close_adj"].pct_change()
    view = frame[frame.index > since].sort_index(ascending=False)
    st.markdown(ui.grid(pd.DataFrame({
        "date": view.index.strftime("%Y-%m-%d"),
        "open": view["open"].map(ui.num), "high": view["high"].map(ui.num),
        "low": view["low"].map(ui.num), "close": view["close"].map(ui.num),
        "change": view["change"].map(lambda v: ui.pct(v, 2)),
        "volume": view["volume"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}"),
    }), numeric={"open", "high", "low", "close", "volume"}, signed={"change"},
        ticker_col="date"), unsafe_allow_html=True)
    st.caption(f"{len(view)} rows from the {source}, newest first. Close as traded; "
               "the change is on the close adjusted for splits and dividends, so an ex-"
               "dividend day does not show as a fall.")


# ---------------------------------------------------------------------- the six tabs
def _sections(tab: str, views: dict, *args) -> None:
    """A tab's own row of sections: lazy, each guarded, selectable by the command."""
    labels = layout.SECTIONS[tab]
    for label, part in zip(labels, st.tabs(labels, key=layout.section_key(tab),
                                            on_change="rerun")):
        with part:
            if part.open:
                _guarded(label, views[label], *args)


def _tab_security() -> None:
    """DES … CF on the loaded security."""
    sec = _ctx()
    if not sec:
        st.markdown("#### No security loaded")
        st.caption("Type a ticker or a name in the command line above — AAPL, SAP GR, "
                   "newmont, SPX, GC=F — and Enter. Add a function to jump straight to "
                   "it: AAPL FA, AAPL OMON, AAPL DCF.")
        with st.expander("Precedent transactions (MA)",
                         expanded=st.session_state.get("cmd_focus") == "MA"):
            _view_deals()
        return
    if rs.profile(src.con, sec):
        _equity_page(sec)
    else:
        # A price-only series (index, commodity, crypto) or a Yahoo symbol.
        _security_page(sec)
        with st.expander("Price table (HP)",
                         expanded=st.session_state.get("cmd_focus") == "HP"):
            _sec_prices(SimpleNamespace(ticker=sec))


def _tab_screen() -> None:
    _sections(command.SCREEN, {
        "SCREEN": _view_screen, "BACKTEST": _view_backtest,
        "RULE": lambda: _view_lab(_ctx_symbol()),
        "SAVINGS PLAN": lambda: _view_strategy(_ctx_symbol()),
    })


def _tab_portfolios() -> None:
    _sections(command.PORTFOLIOS, {
        "BOOKS": _view_books, "TRACKING": _view_tracking,
        "WATCHLIST & ALERTS": _view_alerts, "COMMODITY LINKS": _view_commodity_links,
    })


def _tab_markets() -> None:
    _sections(command.MARKETS, {
        "BOARDS": _view_monitor, "COMPARE": lambda: _view_compare(_ctx_symbol()),
        "ECONOMY": _view_economy, "RATES": _view_rates,
    })


def _tab_events() -> None:
    _sections(command.EVENTS, {
        "BRIEFING": _view_briefing, "CALENDAR": _view_calendar,
        "FILING SEARCH": lambda: _view_filing_search(
            _ctx_symbol() if _ctx() and _in_store(_ctx()) else ""),
    })


TABS = {command.SECURITY: _tab_security, command.SCREEN: _tab_screen,
        command.PORTFOLIOS: _tab_portfolios, command.MARKETS: _tab_markets,
        command.WORLD: _view_map, command.EVENTS: _tab_events}
assert tuple(TABS) == layout.TAB_LABELS

_command_bar()
# Lazy: only the open tab runs. Before 02.10.2026 every rerun drew all thirteen tabs,
# with their network calls, whichever one was on screen.
for (label, view), tab in zip(TABS.items(), st.tabs(layout.TAB_LABELS,
                                                       key=layout.MAIN_KEY,
                                                       on_change="rerun")):
    with tab:
        if tab.open:
            _guarded(label, view)

st.caption(
    "Applies your criteria to public filings and market data. Places no orders, "
    "connects to no broker, recommends no security. "
    f"Costs: {settings.cost.commission:.2%} commission plus a spread that scales with size."
)
