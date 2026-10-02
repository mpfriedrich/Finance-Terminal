# Portfolio Lab

A chat-driven stock screener with backtesting, built for one person to prepare their own
investment decisions on US equities. You describe what you want in plain language, it
applies those criteria to SEC filings and market data, and it shows what came out —
including what it had to leave out.

It places no orders, connects to no broker and recommends no security. An automated check
in the test suite fails the build if recommendation language appears anywhere in the
interface.

## The rule this is built around

> A backtest that shows numbers that are too good is worse than no backtest, because
> money depends on it.

Where a bias can be corrected, it is corrected. Where it cannot, it is measured and
printed on the screen next to the result rather than left out. Most of the engineering
here is that second sentence.

**What is corrected:** dividends on both sides of every comparison (the benchmark is the
S&P 500 *total return* series, verified to 11 basis points a year against SPY over ten
years); trading costs, charged once at entry and tiered by company size; fills at the
*next* session's open rather than the close the screen read; the earliest filed version of
a fact winning over later restatements; a delisted position taking a stated loss instead
of holding its last price.

**What is not, and is therefore stated on screen:** the universe contains only companies
that still exist. Phase 7 drew twenty companies that stopped filing before 2019 and tried
to reconstruct them — **none of the twenty** could be given a ticker, let alone prices,
from the free data sources. Every backward-looking number here is therefore flattered by
survivors, and the label on every chart says so.

## What it does

| | |
|---|---|
| **Screen** | Describe criteria in chat; a language model fills a closed schema (it cannot name a stock — there is no field for one), and the selection is deterministic. Filters on 25 metrics plus industry, with eligibility floors for price, turnover, trading frequency and filing age. |
| **Research** | One company at a time: figures as first reported, restatements surfaced as findings, valuation history rebuilt from the price on each reporting date, candlestick chart with Bollinger bands. |
| **Options** | Implied volatility solved from option prices, a volatility surface, and the market's own expected range for every day to the last expiry. |
| **Backtest, stage A** | Buy the basket you hold, once, and run it forward. Honest about being hindsight: the names were chosen with today's filings. |
| **Backtest, stage B** | Point-in-time: re-screen at every rebalance date using only filings published by then, fill at the next open. This tests the *rule*, not the names. |
| **Lab** | Describe a trading rule in words — moving-average cross, momentum, mean reversion, stops, position sizing — and test it on the basket you hold. |
| **Tracking** | Freeze today's selection and follow it forward against the index. The only measurement here with no survivorship, no look-ahead and no fitting — it just needs time to pass. |

## Data

* **Fundamentals:** the SEC's XBRL bulk files. 6.1 million facts, every one carrying its
  filing date, which is what makes the point-in-time backtest possible. Usable coverage
  starts in **2011** (measured: 472 filers reported in 2009, 7,721 in 2011).
* **Prices:** 14.9 million daily bars, 2010–2026, for 5,364 securities.
* Three closes are stored, and mixing them up is how a backtest lies:
  `close` as delivered (split-adjusted), `close_raw` reconstructed from the split events
  (the price the stock actually traded at — Apple on 2020-08-28 is 499.23, not 124.81),
  and `close_adj` for total return. Anything multiplied by a share count uses `close_raw`.
* **Field coverage is measured, not assumed.** A filter on revenue runs on a field 67% of
  the universe reports; one on equity, 91%. The screen says so when a thin field is used,
  because a company that never reported it *fails* the filter rather than being ignored.

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```bash
git clone https://github.com/mpfriedrich/Finance-Terminal.git portfolio-lab
cd portfolio-lab
uv sync
```

Put an Anthropic API key in `.env.local` (the chat translator needs it; nothing else
does):

```
ANTHROPIC_API_KEY=sk-ant-...
```

Build the local data store. The first run takes a while and downloads several gigabytes:

```bash
uv run python scripts/build_sic.py            # industry classification
uv run python scripts/build_fundamentals.py   # SEC XBRL facts
uv run python scripts/build_prices.py --since=2010-01-01
uv run python scripts/add_benchmark.py        # S&P 500 TR, the price index, SPY
uv run python scripts/build_ticker_history.py # and how little of it can be recovered
uv run python scripts/build_fred.py           # macro, rail and airline series (no key)
uv run python scripts/build_shipping.py       # ships through the chokepoints (no key)
uv run python scripts/build_air.py            # one count of aircraft in the air (no key)
```

`build_prices.py` keeps the FRED series and the ship counts current once they are stored,
and stores one aircraft count per run. The live aircraft map comes from the OpenSky
Network (non-commercial use, attribution shown in the app).

Then:

```bash
uv run streamlit run app.py
```

Optional, and worth it before using stage B: `uv run python scripts/warm_pit_cache.py`
assembles the point-in-time universe for each rebalance date once, which turns a
multi-minute backtest into a fast one. It changes speed and nothing else — there is a
test for exactly that. It warms the source the app would run on (the one in
`PORTFOLIO_LAB_SOURCE`, free by default); `--source=world` or `--source=paid` picks
another, because frames are cached per source.

## Switching to the paid data source

The free data has one flaw nothing free can fix: companies that went bankrupt or were
taken over are missing, so every backtest runs on survivors and looks better than it
was (Phase 7 measured it: 0 of 20 vanished companies could be recovered). Sharadar, via
Nasdaq Data Link, carries those companies with their prices and with each fundamental
stamped by its filing date. Switching removes **survivorship bias**; the point-in-time
rules stay exactly as they are.

1. Subscribe to Sharadar Core US Fundamentals and Equity Prices (tables TICKERS, SF1,
   SEP) and put the key in `.env.local`:
   ```
   NASDAQ_DATA_LINK_API_KEY=...
   ```
2. Build the paid store (downloads the three tables, writes `data/paid.duckdb`; the free
   store is left alone, and the index series are copied over from it):
   ```bash
   uv run python scripts/build_paid.py
   ```
3. Choose it in `.env.local` and restart the app:
   ```
   PORTFOLIO_LAB_SOURCE=paid
   ```
4. Warm the cache again (`scripts/warm_pit_cache.py --source=paid`); frames are cached
   per source.

Back to the free data: `PORTFOLIO_LAB_SOURCE=free`, or delete the line. A missing key or
an unbuilt store shows a message saying which step is missing, not a crash.

**Untested against the real service.** The translation is tested on hand-made files in
the published Sharadar layout (and the screener's test series runs unchanged against
it), but no subscription existed when it was written. Check the first build's output —
the number of delisted companies with prices should be in the thousands, not zero.

## Adding the rest of the world (Compustat Global via WRDS)

With a WRDS account (many universities have one; students usually register through the
library), the screen can cover listed companies outside North America as well: every
company Compustat Global lists in the chosen countries, with prices, share counts and
annual and quarterly fundamentals, including companies that stopped trading. The US
half stays the free store; both live in one file, `data/world.duckdb`.

1. Put the login in `.env.local` (never shown, never committed):
   ```
   WRDS_USERNAME=...
   WRDS_PASSWORD=...
   ```
   The password can live in `~/.pgpass` instead. If the account uses two-factor
   authentication, approve the Duo push on the phone when the build connects.
2. Build the world store (first run: long — daily prices for ~20 countries since 2010;
   later runs fetch only the current year):
   ```bash
   uv run python scripts/build_wrds.py
   uv run python scripts/build_wrds.py --countries=DEU,GBR,JPN --since=2015-01-01
   ```
3. Choose it in `.env.local`, restart the app, warm the cache:
   ```
   PORTFOLIO_LAB_SOURCE=world
   ```

How it reads: tickers are the Compustat ticker plus the country (`7203.JPN`, `SAP.DEU`),
every price is in dollars at that day's rate (so returns are what a dollar investor
earned), fundamentals keep their currency and are converted at the screen date. **Report
dates are estimated**: Compustat Global does not record when an annual report came out,
so it counts as published at the legal deadline (period end + 4 months; interim reports
+ 3 months, or the report date where Compustat has one). `build_prices.py` keeps the US
half current; rerun `build_wrds.py` weekly for the rest. Check the licence terms of your
WRDS access before using the data outside your studies.

**The US without survivorship bias (CRSP).** If the WRDS account includes CRSP, the same
build replaces the free US prices with CRSP's: every US common stock since `--since`,
including the ones that went bankrupt or were taken over, and the delisting return
holders actually received, which the backtests then use instead of the assumed -30%.
CRSP ends where the subscription ends (often the previous December); the lines still
trading continue with the free store's daily prices from there, spliced so nothing
jumps. Fundamentals stay the SEC filings (dated by the SEC, kept for companies that no
longer exist); Compustat North America fills only the years before a company's first
XBRL filing. `--us=free` skips CRSP; `--countries=none` builds the US only. Both
CRSP formats are read: the current one (CIZ, `dsf_v2`) and the legacy one, which ends
with December 2024. If CRSP comes through another university's WRDS account than
Compustat Global, add that account as well — the build then connects twice:
```
WRDS_CRSP_USERNAME=...
WRDS_CRSP_PASSWORD=...
```

**Untested against the real service**, like the paid source: the translation is tested
on hand-made files in the Compustat Global and CRSP layouts. Check the counts the first
build prints — per country, and the number of US listings that ended.

## Tests

```bash
uv run pytest -q
```

Roughly 375 of them. The ones worth knowing about:

* every metric formula is pinned to a series whose answer can be worked out with a
  pencil, to one basis point — constant growth, a single fall of known depth, returns
  alternating by a fixed amount;
* the screener's assertions are written once and run against **every** data source — the
  live one, the fixture and the paid (Sharadar) translation — which is what makes "the data layer is exchangeable" a fact
  rather than a claim. That test has caught two real defects so far;
* the point-in-time rules each have a test whose answer is known in advance: a figure
  filed in June is invisible to a screen dated March, a restatement does not displace the
  original, a company not yet listed cannot be bought;
* a fill test hands the engine a 30% gap up on the morning of the trade and fails if the
  run captures it.

## Not investment advice

This applies criteria you supply to public filings and market data. The numbers rest on
free data sources with known defects, several of which are documented in `PLAN.md` and
in the commit history. The decisions are yours.
