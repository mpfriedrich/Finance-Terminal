# Portfolio Lab: handover for AI sessions

Read this file first. It holds what earlier sessions learned, so you don't have to
rediscover it. `PLAN.md` (German) is the full history: requirements R1–R24, every phase,
every review and measurement. `README.md` is the user-facing setup guide.

## 1. What this is

A local finance terminal. It is a Streamlit app (`app.py`) over DuckDB stores. It screens stocks with point-in-time
fundamentals, backtests the screens honestly, runs paper portfolios, and does research
(valuation, comps, WACC/DCF, precedent deals, insiders, macro, options, world monitor).
It is not a trading system: it never places orders and never words anything as
advice (R23, enforced by `tests/test_wording.py`).

## 2. The user

- **Moritz**, a Master's student at FU Berlin who is also getting a University of Sydney
  account. He is not a programmer and uses the macOS terminal step by step.
- **Speak German with him.** Give one command per step, say what output to expect,
  and ask him to paste the output back. He has confused Cmd+C with Ctrl+C before, and
  once typed commands into a terminal that was still running the app.
- He decides and merges pull requests himself. Don't open a PR unless he asks.
- Never ask him to paste passwords or API keys into the chat. Secrets belong in
  `.env.local` only; tell him to edit it with `open -e .env.local`.

## 3. Where things run

| | |
|---|---|
| Local project | `~/portfolio-lab` on his Mac (Python 3.13, `uv`) |
| Repository | GitHub `mpfriedrich/Finance-Terminal`, default branch `main` |
| Data | `data/`, gitignored and **only on his Mac**: `market.duckdb`, `user.duckdb`, `world.duckdb`, `paid.duckdb`, `cache/`, `alerts_state.json` |
| Secrets | `.env.local`, gitignored |

**Cloud sessions** (claude.ai/code) have GitHub and PyPI access. Yahoo, SEC, FRED,
Nasdaq Data Link, WRDS and the Anthropic API are blocked there, so every real-data run
happens on his Mac. The workflow has been: the cloud session writes code and tests,
pushes to a branch, and he runs `git pull` plus the scripts locally and pastes the output
back. A **local** Claude Code session on his Mac can run everything directly and should
be preferred for anything that touches real data.

Don't let two sessions edit the same branch at once. Before starting: `git pull`.

## 4. Commands

```bash
uv sync                                   # dependencies (incl. openpyxl, psycopg2-binary)
uv run pytest -q                          # ~616 pass, 15 skip without local data (~75 s)
uv run streamlit run app.py               # the app, http://localhost:8501
uv run python scripts/build_prices.py     # daily: prices, FX, world series, FRED, windows,
                                          #   and the US half of the world store
uv run python scripts/morning_briefing.py # scheduled weekday job (install_schedule.sh)
```

First-time / after-schema-change build order for the free store:
`build_fundamentals.py` (SEC companyfacts, long), `build_fx.py`,
`build_listing_ratios.py`, `build_world.py`, `build_fred.py`, `warm_pit_cache.py`
(long; needed again whenever `CACHE_VERSION` changes).

World store (WRDS): `build_wrds.py`. See section 7.

## 5. Map of the code

```
app.py                      Streamlit UI, ~3.7k lines. Tabs: SCREEN MONITOR RESEARCH OPTIONS
                            STRATEGY LAB BOOKS DEALS MACRO COMMODITIES CALENDAR NEWS
src/portfolio_lab/
  config.py                 paths (MARKET_DB, PAID_DB, WORLD_DB, USER_DB), Settings, secret()
  data/schema.py            MARKET_MIGRATIONS / USER_MIGRATIONS: append-only, named by what
                            they create; open_market_for_rebuild + finish_rebuild (build on a
                            copy, atomic swap, so builds work while the app is open)
  data/sources.py           PORTFOLIO_LAB_SOURCE = free | paid | world -> which store/source
  data/free.py              FreeSource: universe, screening_frame (fast SQL path), prices
  data/base.py              build_screening_frame: the slow REFERENCE path (must agree)
  data/pit.py               point-in-time fundamentals: _visible, instant_values, ttm_values,
                            share_counts, to_usd (FX at screen date), fundamentals_wide
  data/sec.py               SEC XBRL ingest: US-GAAP + IFRS tags, any currency
  data/listing.py           ADR ratios for foreign SEC filers
  data/history.py           ticker_history, price windows, universe_at, delisting_returns
  data/paid.py              Sharadar (Nasdaq Data Link), untested against the real service
  data/wrds.py              Compustat Global via WRDS -> world store; copy_us; WorldSource
  data/crsp.py              CRSP (CIZ + legacy) + Compustat NA -> US half of the world store
  screen/                   spec, engine (derive_metrics, run_screen, exclusion reasons),
                            translate (chat -> spec via Claude), valuation
  backtest.py stage_b.py    Stage A (buy and hold) and Stage B (point-in-time rebalancing)
  pit_cache.py              per-date screening frames on disk; CACHE_VERSION
  lab/                      single-stock rule backtests (Bollinger, RSI, MACD, ...)
  paper.py                  paper books (simulated portfolios) per tab
  comps.py wacc.py deals.py research.py   valuation tools
  world.py numeraire.py     market monitor, USD/gold unit
  risk.py alerts.py insiders.py fts.py fred.py export.py summary.py macro.py options.py
tests/                      one file per module; fixtures: paid_fixture.py (Sharadar layout),
                            wrds_fixture.py (Compustat Global), crsp_fixture.py (CRSP + NA)
scripts/                    build_* and operational scripts
```

## 6. Rules that must not be broken

These are why the project exists. Each has tests; a change that breaks one is wrong, even
if it makes a number look better.

1. **Point in time (R13).** A fact is visible only if `filed <= asof`. Among several filings
   of the same period, the **first** one wins, across tags, and this is decided *before*
   any age filter. Restated values never replace original ones.
2. **Trades fill at the next session's open (R16).** Screens use data up to close *t*.
3. **Delistings (R18/R19).** A position whose prices stop is realised at the delisting
   return: the real one from `delisting_returns` (CRSP) where it exists, otherwise the
   assumed −30 %. Reports count departures and closed-out positions.
4. **Honesty about survivorship.** The free US data has almost no dead companies (Phase 7
   measured 0 of 20). `survivorship_free` is *measured*, not asserted. Labels on
   charts say which biases are not corrected (R12).
5. **Two frame builders agree.** `FreeSource.screening_frame` (fast) and
   `base.build_screening_frame` (reference) must give the same frame; a test checks it.
   Change both or neither.
6. **Bump `CACHE_VERSION` in `pit_cache.py`** whenever frame assembly changes, otherwise
   stale cached frames are served silently.
7. **Market cap = traded close (`close_raw`) × shares**, never the split-adjusted close.
   Money is converted to USD at the rate of the **screen date** (`pit.to_usd`), never a
   later one; a missing rate excludes the company with its own reason.
8. **Costs** are charged on invested money and tiered by market cap; missing market cap
   pays the most expensive tier (R8).
9. **Secrets never appear** in UI, logs, errors or commits (R24). Connection errors are
   re-raised without URLs or parameters.
10. **No advice wording** in UI strings (R23): no "buy", "recommend", "you should" and similar.
11. **The app has no login.** Never expose it to the internet. Phone access would go
    through Tailscale; the user hasn't decided on that yet.

## 7. Data sources and keys

- **Free (default):** SEC companyfacts and submissions (`SEC_USER_AGENT`), Yahoo via
  yfinance, FRED CSV (no key).
- **Paid:** `NASDAQ_DATA_LINK_API_KEY`, then `build_paid.py`, then
  `PORTFOLIO_LAB_SOURCE=paid`.
- **World (WRDS):** `WRDS_USERNAME` / `WRDS_PASSWORD` (FU account: Compustat Global).
  Optionally `WRDS_CRSP_USERNAME` / `WRDS_CRSP_PASSWORD` (a second university's account
  for CRSP). Then `build_wrds.py`, then `PORTFOLIO_LAB_SOURCE=world`.
  - `build_wrds.py --countries=DEU,JPN --since=2020-01-01` for a small run,
    `--countries=none` for the US only, `--us=free` to skip CRSP,
    `--from-dir=data/cache/wrds` to build from downloaded parquet without connecting,
    `--us-only` to refresh the US half (build_prices.py does that daily).
  - WRDS enforces Duo MFA: every connection sends a push to the phone, and it must be
    approved within about a minute.
- **AI features:** `ANTHROPIC_API_KEY`. Claude API usage lives in `screen/translate.py`,
  `lab/translate.py`, `summary.py`, `business.py` and `briefing.py`.

**Key spaces in the `cik` column** (all stores share the schema):
SEC CIK `< 1e9` · Compustat Global `1e9 + gvkey` (`wrds.GVKEY_OFFSET`, up to
`GLOBAL_LAST = 2e9−1`) · CRSP lines without an SEC CIK `2e9 + permno`
(`crsp.CRSP_OFFSET`). CRSP lines *with* an SEC CIK use the SEC CIK, so SEC filings apply.
`wrds.is_compustat(cik)` means "no SEC filings to look up".

**Tickers in the world store:** Compustat Global lines are `TIC.CTY` (`7203.JPN`,
`SAP.DEU`), not Yahoo symbols. CRSP lines that are still trading use today's free-store
ticker; ended lines use their last CRSP ticker, with `-permno` appended on a collision.

## 8. Lessons from real runs (don't relearn these)

- `ticker_history` PK collision (a filing window and a price window starting on the same
  day) broke price builds. The key now includes `source`.
- **Compustat Global names differ from North America.** It has `nicon` (not `ni`), `cshoi`
  (not `csho`) and no `gp` (use sale − cogs). Quarterly has `ibq` (not `niq`) and no `rdq`,
  so global filing dates are the legal deadline: period end + 4 months for annual, + 3 for
  interim. `wrds.DURATION`/`INSTANT` hold candidate lists, and the build prints which one it used.
- **WRDS usernames are lowercase**; "PAM authentication failed" otherwise. `connect()`
  lowercases. The same error also appears for a wrong password or an expired or declined
  Duo push, and in that case no push arrives at all.
- **WRDS lists tables an account may not read.** The FU account sees CRSP but gets
  "permission denied for schema crsp_a_stock", and the website shows CRSP grey (only
  "CRSP Sample" is licensed). `crsp.readable()` reads one row first and the build falls
  back to the free US data.
- **ISIN/SEDOL are blank without their own licence.** An all-NULL parquet column comes back
  as INTEGER, so `COALESCE` fails with a BinderException. `untyped_as_text()` fixes this,
  and join keys are cast explicitly.
- **Out of memory on 8.8 million daily rows (16 GB Mac).** Daily files are now views over
  parquet (`load_parquet(view=True)`), intermediate steps are views, and `limit_memory()`
  applies. Never materialise a whole daily table into a temp table.
- **CRSP ended the legacy format (`crsp.dsf`) with December 2024.** The current format is
  CIZ (`crsp.dsf_v2`, `crsp.stksecurityinfohist`). `crsp.py` reads both and prefers CIZ;
  the column names were checked against the `tidyfinance` package. CIZ has no cumulative
  price factor (the split basis is rebuilt from `dlyretx`), and the delisting return sits
  in its own row (`dlydelflg = 'Y'`).
- WTI traded at −$37 in April 2020. The returns around that day are infinite, so drop inf
  before any covariance or beta, or numpy floods the terminal with RuntimeWarnings.
- A Streamlit double-click created duplicate paper books, so `paper._new_book` is
  idempotent for 30 s.
- `research.peers()` must return an empty frame that keeps its columns (`frame.iloc[0:0]`),
  not `pd.DataFrame()`.
- `st.line_chart(color=[...])` needs exactly one colour per column. Slice the list.
- `copy_us` once deleted Compustat's FX rows (every non-US company then had "no exchange
  rate"). It must only replace series that the free store itself supplies.
- In `app.py`, `listing` is a local variable name. Import `data.listing` names directly
  (`MARKET_NAMES`).
- Every screening frame has `country`: the ISO-3 code of the market where the line
  trades (USA for SEC/CRSP lines, the Compustat country otherwise). It is filterable in
  chat and in the sidebar.

## 9. How to work here

- **Every fix gets a test that fails without the fix.** Check it by stashing only the
  source file and running the test. Tests use hand-computed values and hand-made
  fixtures in the vendor's layout, and never touch the network.
- Match the existing style. Docstrings and comments explain *why*, cite the measurement
  or incident behind a choice, and stay honest about what is untested.
- pandas 3, DuckDB ≥ 1.5. Keep SQL in DuckDB where it is large.
- Schema changes: append a migration to `MARKET_MIGRATIONS`/`USER_MIGRATIONS` and never
  edit old ones. Multi-statement migrations are allowed.
- **Update `PLAN.md` (German)** with every substantial change: what, why, what is measured,
  what is untested. Update `README.md` when setup steps change.
- Before pushing: run `uv run pytest -q` and `uvx ruff check <changed files>`, and re-read
  the diff.
- **Checking the UI without real data:** build demo stores from the fixtures into `data/`,
  start Streamlit on a spare port, and drive it with Playwright. In the cloud:
  `uv run --with playwright python script.py`, with
  `executable_path=glob("/opt/pw-browsers/chromium-*/chrome-linux*/chrome")[0]`. A new
  `user.duckdb` needs a portfolio before the tabs appear (create "Demo" via the sidebar).
  Afterwards kill the server and `rm -rf data`, so demo data is never committed.
- Don't `pkill -f` a pattern that also matches your own shell command. It kills the shell.
  Kill by PID.

## 10. Status and open items (as of 26.09.2026)

**Done and merged** (PRs #1–#4): phases 1–10, the Stage B reviews, world stages 1–2,
analyst tools 1–12.

**On branch `claude/jolly-archimedes-y8v5cz`, not merged yet:** world stage 3
(Compustat Global), the CRSP US half, CIZ support, the second WRDS account, the gold
toggle in MONITOR, memory-safe ingest, and several fixes. Tests pass.

**World store: fully built 26.09.2026** (local session). All 21 default countries
since 2010: 22,139 Compustat companies, 58.9M price rows (0 without an FX rate),
16.0M facts (83% on estimated filing dates), plus the copied US half (5,364 lines);
10.5 GB. Measurements in PLAN.md → „Gemessen am 26.09.2026“. The first download run
died on a WRDS SSL drop after ~12 h; the restart resumed from the per-year cache.
Not done yet: `PORTFOLIO_LAB_SOURCE=world` in `.env.local`, an app check on real
data, and re-warming the pit cache for the world source.

**Waiting on the user:**
1. His USYD WRDS account (credentials already in `.env.local` as `WRDS_CRSP_*`,
   but the CRSP licence/activation is still pending — first attempt failed with
   PAM authentication, no Duo push). Once CRSP shows **blue** on his WRDS page:
   `uv run python scripts/build_wrds.py --countries=none`. Check the `CRSP: {...}`
   line: ended listings should number in the thousands. Then re-warm the pit cache.
2. Merging the branch once he's satisfied.

**Known gaps:**
- CRSP and the Sharadar paid source are untested against the real services.
- Stage B has not been timed against its 5-minute budget on the full store.
- US price floors ($5, $1m turnover) exclude many European and Japanese small caps.
- Phone access (Tailscale) hasn't been decided.
- The morning briefing does not refresh the world store's non-US part; rerun
  `build_wrds.py` weekly.

**Planned, not started (user decides when):** leverage with margin calls, short selling in
LAB, leveraged-ETF simulation, option-strategy backtests (Black-Scholes approximation or
OptionMetrics via WRDS), futures positions. The spec is in PLAN.md → "Geplant: Hebel und
Derivate".
