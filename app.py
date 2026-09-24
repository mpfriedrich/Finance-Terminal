"""Portfolio Lab — chat-driven stock screener with live paper tracking.

Run from VS Code (Run and Debug → "Portfolio Lab (app)") or:
    uv run streamlit run app.py

No orders are placed, no broker is connected, and nothing here is investment advice.
The app applies the criteria you give it to public filings and market data, and shows
what came out — including what it had to leave out.
"""

from __future__ import annotations

import datetime as dt
import sys

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

from portfolio_lab import ui
from portfolio_lab.config import CACHE_DIR, MARKET_DB, USER_DB, Settings, secret
from portfolio_lab.data import industry
from portfolio_lab.data.free import FreeSource
from portfolio_lab.data.schema import open_market, open_user
from portfolio_lab.screen import translate as T
from portfolio_lab.screen.engine import derive_metrics, merge_selection, run_screen
from portfolio_lab.screen.valuation import add_valuation
from dataclasses import replace

from portfolio_lab.screen.spec import Filter, spec_to_json
from portfolio_lab.screen.valuation import Assumptions
from portfolio_lab import briefing as bfg
from portfolio_lab import business as bz
from portfolio_lab import macro as mc
from portfolio_lab import options as op
from portfolio_lab import strategy as stg
from portfolio_lab import research as rs
from portfolio_lab.store import Store
from portfolio_lab.track import performance

st.set_page_config(page_title="Portfolio Lab", page_icon="◫", layout="wide")
st.markdown(ui.CSS, unsafe_allow_html=True)


# Streamlit re-runs this file on every interaction; cached resources survive that, so the
# database connections are opened once per session instead of once per click.
@st.cache_resource
def _market():
    # Read-only: the app never writes market data, and holding the write lock would stop
    # every build script from running while the app is open.
    return FreeSource(open_market(MARKET_DB, read_only=True)), MARKET_DB.stat().st_ino


def _market_source():
    """The market connection, reopened if the file underneath it was replaced.

    A price rebuild that ran while this app was open swaps a freshly built file into
    place. The open connection keeps serving the OLD file — on Unix an open handle
    follows the inode, not the name — so without this check the app would show yesterday
    prices indefinitely and give no sign that anything had changed. Comparing the inode
    is how you notice a file was replaced rather than edited.
    """
    source, opened_inode = _market()
    try:
        if MARKET_DB.stat().st_ino != opened_inode:
            _market.clear()
            _frame.clear()
            st.rerun()
    except OSError:
        pass  # Mid-swap. The next rerun will see the new file.
    return source


@st.cache_resource
def _store():
    return Store(open_user(USER_DB))


@st.cache_data(ttl=600)
def _frame(asof: dt.date) -> pd.DataFrame:
    return _market_source().screening_frame(asof)


@st.cache_data(ttl=3600)
def _sectors() -> list[str]:
    """The real industry vocabulary, handed to the translator so it cannot invent one."""
    return [r[0] for r in _market_source().con.execute(
        "SELECT sector FROM securities WHERE sector IS NOT NULL "
        "GROUP BY 1 ORDER BY COUNT(*) DESC"
    ).fetchall()]


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

src, store, settings = _market_source(), _store(), Settings()
asof = src.latest_equity_date()
age = (dt.date.today() - asof).days if asof else None
counts = src.snapshot().row_counts

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
        if st.form_submit_button("Create", use_container_width=True) and new_name:
            try:
                store.create(new_name)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if chosen and st.button("Delete portfolio", use_container_width=True):
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
                f"<span style='color:{ui.AMBER};font-weight:600'>{row.ticker}</span>"
                + (f" <span style='color:{ui.MUTED};font-size:0.68rem'>{row.note}</span>"
                   if row.note else ""),
                unsafe_allow_html=True,
            )
            if wc2.button("×", key=f"unwatch_{row.ticker}"):
                store.unwatch(row.ticker)
                st.rerun()
    with st.form("watch_add", clear_on_submit=True):
        wt = st.text_input("Add ticker", placeholder="NVDA")
        wn = st.text_input("Note", placeholder="why you are watching")
        if st.form_submit_button("Watch", use_container_width=True) and wt.strip():
            store.watch(wt, wn)
            st.rerun()

    st.markdown("#### Data")
    if age is None:
        st.error("No prices. Run the build scripts.")
    elif age > 7:
        st.warning(f"Prices {age} days old. Refresh before trading on them.")
    st.caption(
        "US common stock on NYSE, Nasdaq and CBOE. Funds, commodity trusts and blank-"
        "cheque shells are excluded by the filer's own SEC industry code; REITs are kept. "
        "**Delisted companies are not in the data at all**, so any backward-looking "
        "number here is flattered by survivors."
    )
    if not secret("ANTHROPIC_API_KEY"):
        st.error("ANTHROPIC_API_KEY missing from .env.local. Chat is off.")

    st.markdown("#### Market cap")
    band = st.select_slider(
        "Size band", options=list(CAP_BANDS), value=st.session_state.get("band", "Any"),
        label_visibility="collapsed", key="band",
    )
    lo, hi = CAP_BANDS[band]
    c1, c2 = st.columns(2)
    lo = c1.number_input("Min $bn", value=float(lo or 0.0), min_value=0.0, step=0.1)
    hi = c2.number_input("Max $bn", value=float(hi or 0.0), min_value=0.0, step=0.1,
                         help="0 means no upper limit")
    st.caption("Applies on top of anything you said in chat.")

    st.markdown("#### Industry")
    groups = st.multiselect(
        "Industry groups", options=list(industry.GROUPS),
        label_visibility="collapsed", key="groups",
        placeholder="Any industry",
    )
    st.caption(
        "Grouped from the filer's own SEC code — the SEC's own wording stays visible "
        "in the table. Also applies on top of the chat."
    )

    st.markdown("#### Intrinsic value")
    st.caption("A model, not a measurement. These assumptions decide the answer.")
    # Sliders work in whole percent. Streamlit formats the raw value, so a 0.10 shown
    # with "%.0f%%" renders as "0%" — the number has to carry the percent, not the label.
    disc = st.slider("Required return %", 4, 20, 10, 1)
    grow = st.slider("Growth assumed % a year", -5, 15, 0, 1)
    term = st.slider("Growth forever after %", 0.0, 4.0, 2.0, 0.5)
    assumptions = Assumptions(
        discount_rate=disc / 100, growth_rate=grow / 100, terminal_growth=term / 100
    )
    if not assumptions.valid():
        st.error("Perpetual growth must stay below the required return, or value is infinite.")
    else:
        st.caption(assumptions.describe())

# ---------------------------------------------------------------- main
if not chosen:
    st.markdown("#### Start")
    st.write(
        "Create a portfolio on the left, then describe it in plain language — "
        "*10 US stocks, price to book max 2, market cap under 2bn*. "
        "Each message refines the same screen rather than starting over."
    )
    st.stop()

spec = store.get_spec(chosen)

(tab_screen, tab_research, tab_options, tab_strategy, tab_macro, tab_commodities,
 tab_calendar, tab_news) = st.tabs(
    ["SCREEN", "RESEARCH", "OPTIONS", "STRATEGY", "MACRO", "COMMODITIES",
     "CALENDAR", "NEWS"]
)

with tab_screen:
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
                result = T.translate(prompt, spec, sectors=_sectors())
            spec = result.spec
            store.save_spec(chosen, spec)
            reply = "\n".join(f"- {c}" for c in result.changed) or "- filters unchanged"
            if spec.unsupported:
                reply += "\n\n**Not applied** — no field matches these:\n"
                reply += "\n".join(f"- {u}" for u in spec.unsupported)
            store.add_message(chosen, "assistant", reply, spec)
            st.markdown(ui.log([("assistant", reply)]), unsafe_allow_html=True)
        except Exception as exc:
            st.error(f"Could not translate that: {exc}")

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
            if st.form_submit_button("Use these companies", use_container_width=True):
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
            st.markdown(f"- market cap {' and '.join(bounds)} *(from the sidebar)*")
        if groups:
            st.markdown(f"- industry one of {', '.join(groups)} *(from the sidebar)*")
        if spec.unsupported:
            st.warning("Not applied: " + "; ".join(spec.unsupported))

    with right:
        if asof is None:
            st.stop()
        effective = spec
        if spec.is_hand_picked:
            lo = hi = 0.0
        elif groups:
            effective = effective.with_filter(
                Filter("industry_group", "in", list(groups)))
        if lo > 0:
            effective = effective.with_filter(Filter("market_cap", ">=", lo * 1e9))
        if hi > 0:
            effective = effective.with_filter(Filter("market_cap", "<=", hi * 1e9))
        res = run_screen(_frame(asof), effective, assumptions)

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
            quick = st.columns(5)
            for col, n in zip(quick, (10, 25, 50)):
                if col.button(f"TOP {n}", use_container_width=True,
                              disabled=res.n_passing <= n and n != 10):
                    _choose(r.ticker.astype(str).head(n))
            if quick[3].button(f"ALL {res.n_passing}", use_container_width=True):
                _choose(r.ticker.astype(str))
            if quick[4].button("NONE", use_container_width=True):
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
            use_container_width=True,
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
                "p/b": two, "p/e": two, "p/s": two, "p/fcf": two, "p/iv": two,
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
        # They speak only for the rows on screen, though — see merge_selection: a sidebar
        # filter hides names, and hidden is not the same as unticked.
        ticked = tuple(edited.loc[edited["hold"], "ticker"].astype(str))
        live = merge_selection(spec.selected, edited["ticker"].astype(str), ticked)
        picks = res.select(live, spec.weighting)
        if set(ticked) != held and not spec.is_hand_picked:
            store.save_spec(chosen, replace(spec, selected=live))

        if len(picks):
            st.caption(
                f"{len(picks)} held · {spec.weighting.replace('_', ' ')} weighting · "
                f"{picks.weight.max():.1%} largest position. "
                "Sort by any column header; ticking is what decides the portfolio."
            )
        else:
            st.caption("Nothing ticked. Tick a row, or use the buttons above.")
    else:
        st.info("Nothing passed. The breakdown below names the binding rule.")

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

    # ---------------------------------------------------------------- live tracking
    st.markdown("#### Live tracking")
    info = store.tracking_info(chosen)

    if info is None:
        st.write(
            "Freeze this selection at today's prices and follow it forward against the "
            "S&P 500 total return. The picks are fixed before the outcome exists, so no "
            "survivorship bias, no look-ahead and no amount of filter-tweaking can flatter "
            "the result. It only needs time to pass."
        )
        thesis = st.text_input(
            "What are you testing?",
            placeholder="cheap small caps beat the index over 12 months",
        )
        if age and age > 1:
            st.warning(
                f"Entry would use the closes of {asof}, {age} days ago — not what you would "
                "pay this morning. Refresh prices first if the entry needs to match a real trade."
            )
        can = len(picks) > 0
        if st.button(f"Start tracking {len(picks)} positions",
                     type="primary", disabled=not can, use_container_width=True):
            n = store.start_tracking(chosen, picks, asof, spec_to_json(spec), thesis,
                                     cost_of=settings.cost.round_trip_cost)
            st.success(f"Tracking {n} positions from {asof}.")
            st.rerun()
        if not can:
            st.caption(
                "Nothing to track — no security passed the filters."
                if not res.n_passing else
                f"Nothing ticked. {res.n_passing:,} names passed; choose the ones to hold above."
            )
    else:
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
            st.line_chart(curve, height=260, color=[ui.AMBER, ui.CYAN])
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

        if st.button("Stop tracking and discard", use_container_width=True):
            store.stop_tracking(chosen)
            st.rerun()

with tab_research:
    st.markdown('<div class="tl-cmd"><span class="p">&gt;</span></div>', unsafe_allow_html=True)
    query = st.text_input(
        "Company", placeholder="ticker or name — AAPL, newmont, american airlines",
        label_visibility="collapsed", key="research_query",
    )

    if not query.strip():
        st.caption(
            "Look one company up. Reported figures are as first filed, not as later "
            "restated, so you see what the market saw at the time. The quote is live."
        )
    else:
        matches = rs.find_ticker(src.con, query)
        non_equity = rs.price_only_symbols(src.con)
        hit = non_equity[non_equity.symbol.str.upper() == query.strip().upper()]

        if matches.empty and len(hit):
            # Crypto, a commodity or an index: price history and nothing behind it.
            sym = hit.iloc[0]
            st.markdown(ui.kv([
                (sym["name"], "", False),
                ("Symbol", sym.symbol, False),
                ("Kind", sym["kind"], False),
                ("History", f"{sym['from']} to {sym['to']} ({sym.bars:,} bars)", False),
            ]), unsafe_allow_html=True)
            st.info(
                f"**{sym['name']} has no financial statements.** Not missing — there is "
                "no issuer filing accounts, so book value, earnings and every ratio "
                "built on them do not exist for it. What does work: the price chart and "
                "risk figures below, the commodity and macro links, and the whole "
                "STRATEGY tab, where you can test regular buying or dip-buying against "
                "its actual history."
            )
            ph = src.con.execute(
                "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date",
                [sym.symbol],
            ).fetch_df()
            if len(ph) > 10:
                ph["date"] = pd.to_datetime(ph["date"])
                st.line_chart(ph.set_index("date")[["close_adj"]].rename(
                    columns={"close_adj": sym.symbol}), height=250, color=[ui.AMBER])
            stats_np = rs.price_stats(src.con, sym.symbol)
            if stats_np:
                cells = []
                for label, key in (("1M", "return_1m"), ("3M", "return_3m"),
                                   ("6M", "return_6m"), ("1Y", "return_1y")):
                    v = stats_np.get(key)
                    if v is not None and not pd.isna(v):
                        cells.append((label, ui.pct(v, 1), "up" if v >= 0 else "down"))
                if stats_np.get("volatility") and not pd.isna(stats_np["volatility"]):
                    cells.append(("VOLATILITY", f"{stats_np['volatility']:.0%}", ""))
                if stats_np.get("max_drawdown") is not None:
                    cells.append(("MAX DRAWDOWN", ui.pct(stats_np["max_drawdown"], 1), "down"))
                if stats_np.get("beta") is not None:
                    cells.append(("BETA vs S&P", ui.num(stats_np["beta"]), ""))
                st.markdown(ui.stats(cells), unsafe_allow_html=True)
            sens_np = rs.commodity_sensitivity(src.con, sym.symbol)
            if not sens_np.empty:
                st.markdown("#### Moves with")
                st.markdown(ui.grid(pd.DataFrame({
                    "series": sens_np.commodity,
                    "correlation": sens_np.correlation.map(lambda v: f"{v:+.2f}"),
                    "beta": sens_np.beta.map(lambda v: f"{v:+.2f}"),
                    "days": sens_np.days.astype(str),
                }).head(6), numeric={"days"}, signed={"correlation", "beta"},
                    ticker_col="series"), unsafe_allow_html=True)
        elif matches.empty:
            st.warning(
                f"Nothing matches '{query}'. It may be delisted, a fund, or outside the "
                "US listings this app carries."
            )
            if not non_equity.empty:
                st.caption(
                    "Price-only symbols available: "
                    + ", ".join(non_equity.symbol.tolist())
                )
        else:
            ticker = matches.iloc[0].ticker
            if len(matches) > 1:
                names = matches.set_index("ticker")["name"]
                ticker = st.radio(
                    "Matches", options=matches.ticker.tolist(),
                    format_func=lambda t: f"{t} · {names.loc[t][:26]}",
                    horizontal=True, key="research_pick",
                )
            prof = rs.profile(src.con, ticker)
            cik = int(prof["cik"])
            quote = rs.live_quote(ticker)
            enriched = add_valuation(derive_metrics(_frame(asof)), assumptions)
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

            sections = st.tabs([
                "OVERVIEW", "FINANCIALS", "RELATIONSHIPS", "VALUATION", "OPTIONS",
                "EXPOSURE", "ANALYSTS", "OWNERSHIP", "NEWS", "FILINGS",
            ])

            # ---------------------------------------------------------------- overview
            with sections[0]:
                details = rs.company_details(CACHE_DIR / "submissions.zip", cik)
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
                with right_c:
                    px_hist = rs.price_history(src.con, ticker, years=5)
                    if px_hist.empty:
                        st.info("No stored price history.")
                    else:
                        import plotly.graph_objects as go

                        c_a, c_b, c_c = st.columns([1, 1, 1.4])
                        span = c_a.selectbox(
                            "Range", ["6M", "1Y", "5Y"], index=1,
                            label_visibility="collapsed", key=f"span_{ticker}",
                        )
                        # Bands are computed on the FULL history and only then cropped to
                        # the visible span. Computing them on the crop would leave the
                        # first 20 sessions of every view without an envelope.
                        view = px_hist.join(rs.bollinger(px_hist))
                        view = view.tail({"6M": 126, "1Y": 252, "5Y": 1260}[span])
                        # Judged on what is actually on screen: older bars predate the
                        # high/low columns, so a long view can fall back to a line while
                        # a short one draws candles.
                        can_candle = rs.has_intraday_range(view)
                        candles = c_b.toggle(
                            "Candles", value=can_candle, disabled=not can_candle,
                            key=f"candles_{ticker}_{span}",
                            help=None if can_candle else
                            "Stored bars carry no high/low for this range.",
                        )
                        bands = c_c.toggle(
                            "Bollinger 20d", value=False, key=f"bands_{ticker}",
                            help="20-day average with a 2-standard-deviation envelope.",
                        )

                        fig = go.Figure()
                        if bands:
                            fig.add_trace(go.Scatter(
                                x=view["date"], y=view["upper"], name="upper",
                                line=dict(color=ui.CYAN, width=1), hoverinfo="skip"))
                            fig.add_trace(go.Scatter(
                                x=view["date"], y=view["lower"], name="lower",
                                line=dict(color=ui.CYAN, width=1), fill="tonexty",
                                fillcolor="rgba(77, 184, 255, 0.10)", hoverinfo="skip"))
                            fig.add_trace(go.Scatter(
                                x=view["date"], y=view["middle"], name="20d avg",
                                line=dict(color=ui.MUTED, width=1, dash="dot")))
                        if candles:
                            fig.add_trace(go.Candlestick(
                                x=view["date"], open=view["open"], high=view["high"],
                                low=view["low"], close=view["close"], name=ticker,
                                increasing=dict(line=dict(color=ui.UP), fillcolor=ui.UP),
                                decreasing=dict(line=dict(color=ui.DOWN), fillcolor=ui.DOWN),
                            ))
                        else:
                            fig.add_trace(go.Scatter(
                                x=view["date"], y=view["close"], name=ticker,
                                line=dict(color=ui.AMBER, width=1.4)))
                        fig.update_layout(
                            height=300, margin=dict(l=0, r=0, t=6, b=0),
                            paper_bgcolor=ui.BG, plot_bgcolor=ui.BG, showlegend=False,
                            font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
                            hovermode="x unified",
                            xaxis=dict(gridcolor=ui.RULE, color=ui.MUTED,
                                       rangeslider=dict(visible=False),
                                       # Without this the chart draws empty weekends and
                                       # every candle body shrinks to make room for them.
                                       rangebreaks=[dict(bounds=["sat", "mon"])]),
                            yaxis=dict(gridcolor=ui.RULE, color=ui.MUTED, side="right"),
                        )
                        st.plotly_chart(fig, use_container_width=True,
                                        key=f"pxchart_{ticker}")
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
                    stats_now = rs.price_stats(src.con, ticker)
                    if stats_now:
                        cells = []
                        for label, key in (("1M", "return_1m"), ("3M", "return_3m"),
                                           ("6M", "return_6m"), ("1Y", "return_1y")):
                            v = stats_now.get(key)
                            if v is not None and not pd.isna(v):
                                cells.append((label, ui.pct(v, 1), "up" if v >= 0 else "down"))
                        if stats_now.get("volatility") and not pd.isna(stats_now["volatility"]):
                            cells.append(("VOL", f"{stats_now['volatility']:.0%}", ""))
                        if stats_now.get("max_drawdown") is not None:
                            cells.append(("MAX DD", ui.pct(stats_now["max_drawdown"], 1), "down"))
                        if stats_now.get("beta") is not None:
                            cells.append(("BETA", ui.num(stats_now["beta"]), ""))
                        st.markdown(ui.stats(cells), unsafe_allow_html=True)
                        bits = [f"{lab} {stats_now[k]:,.2f}" for lab, k in
                                (("50d avg", "ma50"), ("200d avg", "ma200"))
                                if stats_now.get(k) and not pd.isna(stats_now[k])]
                        st.caption(
                            (" · ".join(bits) + " · " if bits else "")
                            + f"{stats_now['days']} stored days to {stats_now['to']}. "
                            "Beta describes the past and is noisy on a short window."
                        )

            # -------------------------------------------------------------- financials
            with sections[1]:
                hist = rs.annual_history(src.con, cik)
                if hist.empty:
                    st.info("No annual XBRL figures for this filer.")
                else:
                    show = hist.sort_values("year", ascending=False).head(12)
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

                eh = rs.earnings_history(ticker)
                if not eh.empty:
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

            # ----------------------------------------------------------- relationships
            with sections[2]:
                st.write(
                    "Who this company says it depends on — read out of its own annual "
                    "report. Every line carries a passage copied from the filing, and "
                    "each passage is checked against the document in code before it is "
                    "shown. A relationship the filing does not state does not appear "
                    "here, however well known it is."
                )
                tenk = rs.filings(CACHE_DIR / "submissions.zip", cik, forms=["10-K"], limit=1)
                if tenk.empty:
                    st.info(
                        "No 10-K in the local archive for this filer. Foreign issuers "
                        "file a 20-F instead, which this does not read yet."
                    )
                elif not secret("ANTHROPIC_API_KEY"):
                    st.warning("Needs ANTHROPIC_API_KEY in .env.local.")
                else:
                    row = tenk.iloc[0]
                    st.caption(f"From the 10-K filed {row.filed} for the period {row.period}.")
                    if st.button("Read the filing", key="read_10k", use_container_width=True):
                        with st.spinner("Downloading and reading Item 1"):
                            try:
                                text = bz.fetch_filing_text(row.url)
                                found = bz.extract(text, prof["name"])
                            except Exception as exc:
                                found = None
                                st.error(f"Could not read that filing: {exc}")
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
                                st.markdown(
                                    f"<div style='border-left:2px solid {ui.AMBER_DIM};"
                                    f"padding-left:0.7rem;color:{ui.MUTED};"
                                    f"font-size:0.74rem;margin-bottom:0.5rem'>"
                                    f"“{r.quote}”</div>",
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
                                    f"font-size:0.74rem;margin-bottom:0.4rem'>“{n}”</div>",
                                    unsafe_allow_html=True,
                                )
                        st.caption(f"[Open the filing at the SEC]({row.url})")

            # --------------------------------------------------------------- valuation
            with sections[3]:
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

                peer_set = rs.peers(enriched, ticker)
                if not peer_set.empty:
                    st.markdown("#### Comparable companies")
                    st.markdown(ui.grid(pd.DataFrame({
                        "ticker": peer_set.ticker,
                        "name": peer_set.name.astype(str).str.slice(0, 24),
                        "mkt cap": peer_set.market_cap.map(ui.money),
                        "p/e": peer_set.price_to_earnings.map(ui.num),
                        "p/b": peer_set.price_to_book.map(ui.num),
                        "p/s": peer_set.price_to_sales.map(ui.num),
                        "margin": peer_set.net_margin.map(lambda v: ui.pct(v, 1)),
                        "d/e": peer_set.debt_to_equity.map(ui.num),
                    }), numeric={"mkt cap", "p/e", "p/b", "p/s", "d/e"},
                        signed={"margin"}), unsafe_allow_html=True)
                    st.caption(
                        "Same SEC industry, closest in size. Size is part of it: a small "
                        "miner and a large one do not trade on the same multiples.")

            # ----------------------------------------------------------------- options
            with sections[4]:
                spot = (quote or {}).get("price") or prof.get("price")
                if not spot:
                    st.info("No price available, so nothing can be backed out.")
                else:
                    rate = st.slider("Risk-free rate %", 0.0, 8.0, 4.0, 0.25,
                                     key="opt_rate") / 100
                    with st.spinner("Reading the option chain"):
                        surf = op.surface(ticker, spot, max_expiries=10, risk_free=rate)
                    if surf.empty:
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
                            key="opt_expiry",
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
                            stats_now = rs.price_stats(src.con, ticker)
                            cmp_ = op.implied_vs_realised(atm, stats_now.get("volatility"))
                            if cmp_:
                                tone = "up" if cmp_["premium"] > 0 else "down"
                                st.markdown(ui.stats([
                                    ("IMPLIED", f"{cmp_['implied']:.1%}", "amber"),
                                    ("REALISED", f"{cmp_['realised']:.1%}", ""),
                                    ("PREMIUM", f"{cmp_['premium']:+.1%}", tone),
                                ]), unsafe_allow_html=True)
                                st.caption(
                                    "Implied above realised is the normal state — option "
                                    "sellers want paying for the risk. A large gap either "
                                    "way is the interesting case, and neither direction is "
                                    "a recommendation."
                                )

                        # --- the surface, in three dimensions -----------------------
                        try:
                            import plotly.graph_objects as go

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
                                fig.update_layout(
                                    height=470, margin=dict(l=0, r=0, t=10, b=0),
                                    paper_bgcolor=ui.BG, plot_bgcolor=ui.BG,
                                    font=dict(family="IBM Plex Mono", color=ui.MUTED, size=10),
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
                                st.plotly_chart(fig, use_container_width=True)
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

            # ---------------------------------------------------------------- exposure
            with sections[5]:
                sens = rs.commodity_sensitivity(src.con, ticker)
                if sens.empty:
                    st.info("Not enough overlapping price history to measure this.")
                else:
                    st.markdown(ui.grid(pd.DataFrame({
                        "commodity": sens.commodity,
                        "correlation": sens.correlation.map(lambda v: f"{v:+.2f}"),
                        "beta": sens.beta.map(lambda v: f"{v:+.2f}"),
                        "days": sens.days.astype(str),
                    }), numeric={"days"}, signed={"correlation", "beta"},
                        ticker_col="commodity"), unsafe_allow_html=True)
                    top = sens.iloc[0]
                    direction = "with" if top.correlation > 0 else "against"
                    st.caption(
                        f"Strongest link: {top.commodity}, moving {direction} the share "
                        f"(beta {top.beta:+.2f} — about {abs(top.beta):.2f}% per 1% move). "
                        "This is association, not cause. A miner and its metal move "
                        "together because it is the product; an airline and oil move "
                        "apart because it is a cost. The arithmetic cannot tell those "
                        "apart, and a short window invents relationships that are not there."
                    )

            # ---------------------------------------------------------------- analysts
            with sections[6]:
                view = rs.analyst_view(ticker)
                if not view:
                    st.info("No analyst data available for this ticker.")
                else:
                    r = view.get("ratings")
                    if r:
                        st.markdown(ui.stats(
                            [(k.upper(), str(v), "amber" if v else "") for k, v in r.items()]
                        ), unsafe_allow_html=True)
                        st.caption(f"Consensus as of {view.get('ratings_period', '—')}.")
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

            # --------------------------------------------------------------- ownership
            with sections[7]:
                hold = rs.holders(ticker)
                if hold.empty:
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

                div = rs.dividend_history(ticker)
                if not div.empty:
                    st.markdown("#### Dividends")
                    st.bar_chart(div.set_index("date")[["dividend"]], height=170,
                                 color=[ui.AMBER])
                    last12 = div[div.date >= pd.Timestamp.now() - pd.Timedelta(days=365)]
                    if len(last12) and quote and quote.get("price"):
                        y = last12.dividend.sum() / quote["price"]
                        st.caption(
                            f"Paid over the last year: {last12.dividend.sum():,.2f} — "
                            f"{y:.2%} on the current price.")

            # ------------------------------------------------------------------- news
            with sections[8]:
                items = rs.news(ticker)
                if items.empty:
                    st.info("No headlines returned for this ticker.")
                else:
                    for row in items.itertuples():
                        st.markdown(
                            f"**[{row.title}]({row.url})**  \n"
                            f"<span style='color:{ui.MUTED};font-size:0.72rem'>"
                            f"{row.when} · {row.source}</span>",
                            unsafe_allow_html=True,
                        )
                        if row.summary:
                            st.caption(row.summary)
                    st.caption(
                        "Headlines and links only. Follow the link for the article — "
                        "the text belongs to its publisher.")

            # ----------------------------------------------------------------- filings
            with sections[9]:
                fl = rs.filings(CACHE_DIR / "submissions.zip", cik, limit=300)
                if fl.empty:
                    st.info("No filing history in the local SEC archive for this filer.")
                else:
                    kinds = ["everything", "reports", "insider trades", "ownership stakes"]
                    pick = st.radio("Filter", kinds, horizontal=True,
                                    label_visibility="collapsed", key="filing_filter")
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


with tab_options:
    st.markdown("#### Build an options position")
    if not chosen:
        st.info("Open or create a portfolio on the left — legs are saved against it.")
    else:
        u1, u2 = st.columns([2, 1])
        under = u1.text_input("Underlying", value="AAPL", key="opt_under").strip().upper()
        rate_b = u2.slider("Risk-free %", 0.0, 8.0, 4.0, 0.25, key="opt_b_rate") / 100

        spot_q = rs.live_quote(under) if under else None
        spot_b = (spot_q or {}).get("price")
        if not spot_b:
            row_px = src.con.execute(
                "SELECT close FROM prices WHERE upper(ticker) = upper(?) "
                "ORDER BY date DESC LIMIT 1", [under]).fetchone()
            spot_b = float(row_px[0]) if row_px else None

        if not spot_b:
            st.warning(f"No price for {under}.")
        else:
            exps = op.expiries(under)
            if not exps:
                st.warning(f"{under} has no listed options.")
            else:
                st.markdown(ui.stats([
                    ("SPOT", ui.num(spot_b), "amber"),
                    ("EXPIRIES", str(len(exps)), ""),
                ]), unsafe_allow_html=True)

                b1, b2, b3, b4 = st.columns(4)
                exp_b = b1.selectbox("Expiry", exps, index=min(6, len(exps) - 1),
                                     key="opt_b_exp")
                kind_b = b2.selectbox("Type", ["call", "put", "share"], key="opt_b_kind")
                side_b = b3.selectbox("Side", ["buy", "sell"], key="opt_b_side")
                qty_b = b4.number_input("Contracts", 1, 100, 1, key="opt_b_qty")

                days_b = max((pd.Timestamp(exp_b) - pd.Timestamp(dt.date.today())).days, 1)

                if kind_b == "share":
                    strike_b, price_b, iv_b = 0.0, spot_b, 0.0
                    st.caption(f"{qty_b * 100:g} shares at {spot_b:,.2f}.")
                else:
                    ch = op.chain(under, exp_b, spot_b, risk_free=rate_b)
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
                            format_func=lambda k: labels[k], key="opt_b_strike",
                        )
                        picked = side_ch[side_ch.strike == strike_b].iloc[0]
                        price_b, iv_b = float(picked.mid), float(picked.iv)

                if strike_b is not None and st.button(
                    "Add leg", type="primary", use_container_width=True, key="opt_b_add"
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
                    if pc[i % 3].button(name, key=f"preset_{i}", use_container_width=True):
                        store.clear_legs(chosen)
                        for lk, ratio, qty in spec_legs:
                            if lk == "share":
                                store.add_leg(chosen, under, "share", 0.0, exp_b,
                                              qty / 100, spot_b, 0.0, contract_size=100)
                                continue
                            target = spot_b * ratio
                            ch2 = op.chain(under, exp_b, spot_b, risk_free=rate_b)
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

            if st.button("Clear position", key="opt_b_clear", use_container_width=True):
                store.clear_legs(chosen)
                st.rerun()

            under_b = saved.underlying.iloc[0]
            spot_use = spot_b or float(src.con.execute(
                "SELECT close FROM prices WHERE upper(ticker) = upper(?) "
                "ORDER BY date DESC LIMIT 1", [under_b]).fetchone()[0])
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


with tab_strategy:
    st.markdown("#### When to invest")
    st.write(
        "Not which company — when. Money arrives on a schedule; a rule decides when it "
        "goes into the market. Every strategy below gets the same contributions on the "
        "same dates, and whatever is waiting sits in a cash account. Without that, a "
        "dip-buying rule wins automatically by spending money the other strategies had "
        "already committed."
    )

    what = st.text_input(
        "What to buy", value="^SP500TR",
        help="A ticker in the local store. ^SP500TR is the S&P 500 including dividends.",
        key="stg_symbol",
    )
    c1, c2, c3 = st.columns(3)
    amount = c1.number_input("Amount each time", 50.0, 100000.0, 500.0, 50.0)
    freq = c2.selectbox("How often", ["weekly", "fortnightly", "monthly", "quarterly"],
                        index=2)
    cash_rate = c3.slider("Idle cash earns % a year", 0.0, 6.0, 2.0, 0.25) / 100

    c4, c5 = st.columns(2)
    trigger = c4.slider("Dip trigger: % below the running high", 1.0, 30.0, 2.0, 0.5) / 100
    ma_days = c5.slider("Moving average, days", 20, 300, 200, 10)

    series = src.con.execute(
        "SELECT date, close_adj FROM prices WHERE upper(ticker) = upper(?) ORDER BY date",
        [what.strip()],
    ).fetch_df()

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
    """Every company you have a stake in across the app: tracked holdings first, then
    hand-picked names, then whatever the open portfolio's screen currently returns."""
    seen: list[str] = []
    for row in store.list_portfolios().itertuples():
        for t in store.holdings(row.id).ticker.tolist():
            if t not in seen:
                seen.append(t)
        sp = store.get_spec(row.id)
        for t in sp.tickers:
            if t not in seen:
                seen.append(t)
    return seen


with tab_macro:
    st.markdown("#### The curve, and what it prices")
    snap = mc.rate_snapshot(src.con)
    if snap.empty:
        st.info("No rate history stored. Run scripts/build_macro.py.")
    else:
        shape = mc.curve_shape(snap)
        ff = mc.fed_funds_expectation(src.con)
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
            st.line_chart(wide, height=250,
                          color=[ui.AMBER, ui.CYAN, ui.UP, ui.MUTED])

        st.markdown("#### What a company does when rates move")
        rate_tk = st.text_input("Ticker", value="AAPL", key="macro_ticker")
        if rate_tk.strip():
            sens = mc.rate_sensitivity(src.con, rate_tk.strip())
            if sens.empty:
                st.info("Not enough overlapping history for that ticker.")
            else:
                st.markdown(ui.grid(pd.DataFrame({
                    "tenor": sens.tenor,
                    "correlation": sens.correlation.map(lambda v: f"{v:+.2f}"),
                    "move per 1pp": sens.beta_per_1pp.map(lambda v: f"{v:+.1%}"),
                    "days": sens.days.astype(str),
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
        ev_tk = e1.text_input("Company", value=rate_tk or "AAPL", key="ev_ticker")
        ev_date = e2.date_input("Event date", value=dt.date.today() - dt.timedelta(days=60),
                                key="ev_date")
        ev_win = e3.slider("Window, days either side", 1, 20, 5, key="ev_window")
        if st.button("Measure", key="ev_run", use_container_width=True):
            res_ev = mc.event_study(src.con, ev_tk.strip(), ev_date,
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


with tab_commodities:
    st.markdown("#### Commodities and macro series")
    rows = []
    for symbol, label in rs.COMMODITIES.items():
        h = src.con.execute(
            "SELECT date, close_adj FROM prices WHERE ticker = ? ORDER BY date", [symbol]
        ).fetch_df()
        if len(h) < 30:
            continue
        h["date"] = pd.to_datetime(h["date"])
        ser = h.set_index("date")["close_adj"].astype(float)
        row = {"series": label, "symbol": symbol, "last": float(ser.iloc[-1]),
               "as_of": ser.index[-1].date()}
        for lab, d in (("1m", 21), ("3m", 63), ("6m", 126), ("1y", 252)):
            row[lab] = (ser.iloc[-1] / ser.iloc[-d] - 1.0) if len(ser) > d else np.nan
        rets = ser.pct_change().dropna()
        row["volatility"] = rets.std() * np.sqrt(252) if len(rets) > 20 else np.nan
        rows.append(row)

    if not rows:
        st.info("No commodity history stored. Run scripts/build_commodities.py.")
    else:
        table = pd.DataFrame(rows)
        st.markdown(ui.grid(pd.DataFrame({
            "series": table.series,
            "last": table.last.map(ui.num),
            "1m": table["1m"].map(lambda v: ui.pct(v, 1)),
            "3m": table["3m"].map(lambda v: ui.pct(v, 1)),
            "6m": table["6m"].map(lambda v: ui.pct(v, 1)),
            "1y": table["1y"].map(lambda v: ui.pct(v, 1)),
            "vol": table.volatility.map(lambda v: "—" if pd.isna(v) else f"{v:.0%}"),
        }), numeric={"last", "vol"}, signed={"1m", "3m", "6m", "1y"},
            ticker_col="series"), unsafe_allow_html=True)

        chosen_c = st.multiselect(
            "Chart", options=table.series.tolist(),
            default=table.series.tolist()[:3], key="cmd_pick",
        )
        if chosen_c:
            syms = table.set_index("series").loc[chosen_c, "symbol"].tolist()
            hist_c = src.con.execute(
                "SELECT date, ticker, close_adj FROM prices "
                "WHERE ticker IN (SELECT UNNEST(?)) ORDER BY date", [syms]
            ).fetch_df()
            hist_c["date"] = pd.to_datetime(hist_c["date"])
            wide_c = hist_c.pivot_table(index="date", columns="ticker", values="close_adj")
            # Rebased, because a chart of oil in dollars next to copper in cents compares
            # nothing. Index both to 100 and the comparison is about movement.
            wide_c = 100 * wide_c / wide_c.bfill().iloc[0]
            wide_c = wide_c.rename(columns=dict(zip(syms, chosen_c)))
            st.line_chart(wide_c, height=280,
                          color=[ui.AMBER, ui.CYAN, ui.UP, ui.DOWN, ui.MUTED][:len(chosen_c)])
            st.caption("All rebased to 100 at the start, so the lines compare movement.")

        st.markdown("#### Which of your holdings move with them")
        watch_c = _watched()
        pick_c = st.text_input(
            "Companies", value=", ".join(watch_c[:10]) if watch_c else "DEC, NEM, AAL",
            key="cmd_tickers",
        )
        tickers_c = [t.strip().upper() for t in pick_c.replace("\n", ",").split(",") if t.strip()][:12]
        if tickers_c:
            found_rows = []
            for t in tickers_c:
                sens_c = rs.commodity_sensitivity(src.con, t)
                if sens_c.empty:
                    continue
                top = sens_c.iloc[0]
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
                    "The sign tells you which side of the trade the company is on. A "
                    "producer moves with its commodity because it is the product; a "
                    "consumer moves against it because it is a cost. The arithmetic "
                    "cannot tell those apart — you can."
                )
            else:
                st.info("Not enough overlapping history for those companies.")


with tab_calendar:
    st.markdown("#### Who reports next")
    st.write(
        "Estimated locally from each company's own filing rhythm, then confirmed against "
        "the announced date for the soonest names. Every row says which of the two it "
        "is — a projected date presented as a schedule would be a quiet fiction."
    )
    weeks = st.slider("Weeks ahead", 1, 12, 6, key="cal_weeks")
    watch = _watched()
    extra = st.text_input(
        "Companies to include", value=", ".join(watch) if watch else "AAPL, MSFT, NVDA",
        help="Defaults to everything you track or hand-picked.", key="cal_tickers",
    )
    wanted = [t.strip().upper() for t in extra.replace("\n", ",").split(",") if t.strip()]
    if not wanted:
        st.info("Add some tickers, or track a portfolio on the SCREEN tab.")
    else:
        with st.spinner("Reading filing rhythms"):
            cal = rs.earnings_calendar(src.con, CACHE_DIR / "submissions.zip",
                                       wanted, weeks=weeks)
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


with tab_news:
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
                       use_container_width=True, key="brief_go"):
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
            st.markdown(
                f"<div style='border-left:2px solid {ui.AMBER};padding-left:0.9rem'>"
                f"{brief['text']}</div>", unsafe_allow_html=True)
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
                        f"<span style='color:{ui.AMBER};font-weight:600'>{row.tickers}</span>  "
                        f"<span style='color:{ui.MUTED};font-size:0.70rem'>{row.when} · "
                        f"{row.source}</span>", unsafe_allow_html=True)
                    st.markdown(f"**[{row.title}]({row.url})**")
                    if row.summary:
                        st.caption(row.summary)


st.caption(
    "Applies your criteria to public filings and market data. Places no orders, "
    "connects to no broker, recommends no security. "
    f"Costs: {settings.cost.commission:.2%} commission plus a spread that scales with size."
)
