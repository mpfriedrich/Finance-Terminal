"""Historical simulation of book E (E1), the rules exactly as fixed on 22.10.2026.

Not evidence: the three inputs were already examined in the signal hunt (PLAN.md,
14.10.2026) over 2011-2024. This shows how the fixed rule would have behaved, nothing
more; E1 is not changed because of it.

Rules (PLAN.md → E1): month-end signal, next open fill; score = mean percentile rank of
12-1 momentum, gross profit / total assets, minus split-adjusted share growth over a
year; research universe and eligibility, SSM out, market cap >= $2bn, median traded value
over 60 sessions >= $10m; 20 long / 20 short, kept while inside the best / worst 40,
vacancies filled at 2.5 % of the value each; 0.10 % per side, 3 % a year borrow on the
original short notional; a share whose prices end is closed at its last close, a long at
-30 % (R18). USD, 1,000 at the start, SPY with dividends as the benchmark.

Point-in-time fundamentals come from the quarterly frames of the pit cache (the newest
dated on or before each month end; one year earlier for the share count) — up to three
months older than possible, never newer.
Run: uv run python scripts/e_backtest.py [cache-dir]
"""

import bisect
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from portfolio_lab.config import DATA_DIR, FORWARD_DB, MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.forward import data as dd
from portfolio_lab.forward import ledger as lg
from portfolio_lab.forward import strategies as stg

CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR / "cache/pit/v9/09951ac491"
COST, BORROW, START = 0.001, 0.03, 1000.0

market = open_market(MARKET_DB, read_only=True)
fw = lg.connect(FORWARD_DB, read_only=True)
spy = fw.execute("SELECT day, open, close, close_adj FROM fw_spy ORDER BY day").fetch_df()
fw.close()
spy.index = pd.to_datetime(spy.pop("day"))
meta = market.execute(dd.UNIVERSE_SQL).fetch_df().drop_duplicates("ticker")
names = sorted(set(meta.ticker) - {"SSM"})
data = market.execute(
    "SELECT ticker, date, open, close, close_adj, close_raw, volume FROM prices WHERE "
    "ticker IN (SELECT UNNEST(?)) AND date >= '2009-06-01' ORDER BY ticker, date",
    [names]).fetch_df()
data["date"] = pd.to_datetime(data["date"])
cal = spy.index[spy.index >= pd.Timestamp("2009-06-01")]


def wide(col, data=data):
    return data.pivot(index="date", columns="ticker", values=col).reindex(cal)


close = wide("close").where(lambda v: v > 0)
raw, adj, vol = wide("close_raw"), wide("close_adj"), wide("volume")
opn = wide("open") * adj / close
del data
tickers = list(close.columns)
T = len(tickers)
print(f"panel {len(cal)} sessions x {T} shares", flush=True)

# research eligibility, vectorised (forward/features.eligible)
lr = np.log(close / close.shift())
vol60 = lr.rolling(60, min_periods=60).std() * np.sqrt(252)
changing = lr.abs().gt(1e-8).rolling(60, min_periods=60).sum()
count = close.notna().cumsum()
rawp = raw.where(raw > 0).fillna(close)
elig = (count >= 252) & (rawp >= 5) & changing.ge(45) & (vol60 >= .01) & (vol60 <= 2)
mom = close.shift(21) / close.shift(252) - 1
dollar = (rawp * vol).rolling(60, min_periods=20).median()
last_row = close.notna()[::-1].idxmax()                      # last date with a price

frames = sorted(pd.Timestamp(p.stem) for p in CACHE.glob("*.parquet"))
fcache: dict = {}


def frame(ts):
    if ts not in fcache:
        fcache[ts] = pd.read_parquet(CACHE / f"{ts.date()}.parquet").drop_duplicates(
            "ticker")
    return fcache[ts]


def scores(i):
    t = cal[i]
    k = bisect.bisect_right(frames, t) - 1
    if k < 4:
        return None
    f = frame(frames[k]).set_index("ticker").reindex(tickers)
    prev = frame(frames[k - 4]).drop_duplicates("cik").set_index("cik")["shares_outstanding"]
    j_now = cal.get_indexer([frames[k]], method="pad")[0]
    j_prev = cal.get_indexer([frames[k - 4]], method="pad")[0]
    shares = pd.to_numeric(f["shares_outstanding"], errors="coerce")
    cap = shares * raw.iloc[i]
    gpa = pd.to_numeric(f["gross_profit_ttm"], errors="coerce") / \
        pd.to_numeric(f["total_assets"], errors="coerce").where(lambda v: v > 0)
    sh_prev = pd.to_numeric(f["cik"].map(prev), errors="coerce")
    basis_now = raw.iloc[j_now] / close.iloc[j_now]
    basis_prev = raw.iloc[j_prev] / close.iloc[j_prev]
    iss = -((shares * basis_now) / (sh_prev * basis_prev) - 1)
    ok = elig.iloc[i] & (cap >= stg.E_MIN_CAP) & (dollar.iloc[i] >= stg.E_MIN_DOLLAR_VOLUME)
    parts = pd.DataFrame({"m": mom.iloc[i], "g": gpa, "s": iss})
    ok &= parts.notna().all(axis=1) & np.isfinite(parts).all(axis=1)
    if ok.sum() < 2 * stg.TOP_N:
        return None
    r = parts[ok].rank(pct=True).mean(axis=1)
    ids = np.array([tickers.index(x) for x in r.index])
    s = r.to_numpy()
    order = ids[np.lexsort((ids, -s))]
    return [tickers[j] for j in order]


month_end = [i for i in range(len(cal) - 1) if cal[i + 1].month != cal[i].month
             and cal[i] >= pd.Timestamp("2011-01-01")]
exec_of = {i + 1: i for i in month_end}
pos: dict = {}            # (ticker, side) -> [stake, entry_adj, entry_index, borrow]
cash = START
rows, trades, turnover = [], [], 0.0
first = month_end[0] + 1
for i in range(first, len(cal)):
    day = cal[i]
    if i > first:                                    # borrow, per calendar day
        days = (day - cal[i - 1]).days
        for key, p in pos.items():
            if key[1] == "short":
                b = BORROW * p[0] * days / 365
                cash -= b
                p[3] += b
    # a share whose prices have ended: realise at its last close (long -30 %)
    for key in [k for k in pos if last_row[k[0]] < day]:
        p = pos.pop(key)
        last = adj.loc[last_row[key[0]], key[0]] * (0.7 if key[1] == "long" else 1.0)
        d = 1 if key[1] == "long" else -1
        value = p[0] * (1 + d * (last / p[1] - 1))
        cash += value - COST * p[0] * last / p[1]
        trades.append((key[0], key[1], cal[p[2]], day, value - p[0] - p[3], "delisted"))
    if i in exec_of:
        rank = scores(exec_of[i])
        if rank is not None:
            o = opn.iloc[i]
            where = {t: k for k, t in enumerate(rank)}
            n = len(rank)
            for key in list(pos):
                k = where.get(key[0])
                keep = k is not None and (k < stg.E_BUFFER if key[1] == "long"
                                          else k >= n - stg.E_BUFFER)
                px = o.get(key[0])
                if keep or not (pd.notna(px) and px > 0):
                    continue
                p = pos.pop(key)
                d = 1 if key[1] == "long" else -1
                value = p[0] * (1 + d * (px / p[1] - 1))
                fee = COST * p[0] * px / p[1]
                cash += value - fee
                turnover += p[0] * px / p[1]
                trades.append((key[0], key[1], cal[p[2]], day, value - fee - p[0] - p[3],
                               "rebalance"))
            held = {t for t, _ in pos}
            value_now = sum(p[0] * (1 + (1 if s == "long" else -1)
                                    * ((o.get(t) if pd.notna(o.get(t)) else p[1])
                                       / p[1] - 1)) for (t, s), p in pos.items())
            equity = cash + value_now
            each = equity * 0.5 / stg.TOP_N
            new = []
            for side, order in (("long", rank), ("short", rank[::-1])):
                need = stg.TOP_N - sum(1 for _, s in pos if s == side)
                for t in order:
                    if need <= 0:
                        break
                    px = o.get(t)
                    if t in held or not (pd.notna(px) and px > 0):
                        continue
                    new.append((t, side, px))
                    held.add(t)
                    need -= 1
            if new:
                scale = min(1.0, cash / (len(new) * each * (1 + COST)))
                for t, side, px in new:
                    stake = each * scale
                    cash -= stake * (1 + COST)
                    turnover += stake
                    pos[(t, side)] = [stake, px, i, 0.0]
    c = adj.iloc[i]
    value = 0.0
    for (t, side), p in pos.items():
        px = c.get(t)
        px = px if pd.notna(px) and px > 0 else p[1]
        value += p[0] * (1 + (1 if side == "long" else -1) * (px / p[1] - 1))
    rows.append((day, cash + value, cash, len(pos)))

curve = pd.DataFrame(rows, columns=["date", "equity", "cash", "positions"]).set_index("date")
b = spy.loc[curve.index]
bench = START * b["close_adj"] / (b["open"].iloc[0] * b["close_adj"].iloc[0]
                                  / b["close"].iloc[0])
bench = bench.ffill()           # Yahoo's newest row can arrive without a close yet
curve["spy"] = bench


def stats(s):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"total": s.iloc[-1] / s.iloc[0] - 1 if s is bench else s.iloc[-1] / START - 1,
            "cagr": (s.iloc[-1] / START) ** (1 / yrs) - 1,
            "max_dd": (s / np.maximum.accumulate(np.r_[START, s.to_numpy()])[1:] - 1).min(),
            "sharpe": r.mean() / r.std() * np.sqrt(252), "vol": r.std() * np.sqrt(252)}


e, m = stats(curve["equity"]), stats(curve["spy"])
print(f"\nE1 {curve.index[0].date()} .. {curve.index[-1].date()} "
      f"({(curve.index[-1] - curve.index[0]).days / 365.25:.1f} years)")
print(f"  E1: end {curve['equity'].iloc[-1]:,.2f}, total {e['total']:+.1%}, p.a. "
      f"{e['cagr']:+.2%}, vol {e['vol']:.1%}, Sharpe {e['sharpe']:.2f}, max DD "
      f"{e['max_dd']:.1%}")
print(f"  SPY: end {curve['spy'].iloc[-1]:,.2f}, total {m['total']:+.1%}, p.a. "
      f"{m['cagr']:+.2%}, vol {m['vol']:.1%}, Sharpe {m['sharpe']:.2f}, max DD "
      f"{m['max_dd']:.1%}")
corr = curve["equity"].pct_change().corr(curve["spy"].pct_change())
yrs_total = (curve.index[-1] - curve.index[0]).days / 365.25
print(f"  correlation with SPY {corr:.2f}; trades {len(trades)}; one-way turnover "
      f"{turnover / curve['equity'].mean() / yrs_total:.1f}x a year of average value")
print("\nyear   E1        SPY       gap pp")
prev_e = prev_s = START
for y, g in curve.groupby(curve.index.year):
    re_, rs = g["equity"].iloc[-1] / prev_e - 1, g["spy"].iloc[-1] / prev_s - 1
    flag = " (partial)" if y in (curve.index[0].year, curve.index[-1].year) else ""
    print(f"{y}  {re_:+8.1%}  {rs:+8.1%}  {100 * (re_ - rs):+7.1f}{flag}")
    prev_e, prev_s = g["equity"].iloc[-1], g["spy"].iloc[-1]
for lo, hi in (("2011-01-01", "2024-12-31"), ("2025-01-01", "2026-12-31")):
    part = curve.loc[lo:hi]
    rr = part["equity"].pct_change().dropna()
    print(f"{lo[:4]}–{hi[:4]}: E1 {part['equity'].iloc[-1] / part['equity'].iloc[0] - 1:+.1%},"
          f" SPY {part['spy'].iloc[-1] / part['spy'].iloc[0] - 1:+.1%}, E1 Sharpe "
          f"{rr.mean() / rr.std() * np.sqrt(252):.2f}")
curve.to_csv(DATA_DIR / "e1_backtest_daily.csv")
pd.DataFrame(trades, columns=["ticker", "side", "entry", "exit", "pnl", "why"]).to_csv(
    DATA_DIR / "e1_backtest_trades.csv", index=False)
print(f"\nfiles: data/e1_backtest_daily.csv, data/e1_backtest_trades.csv; frames {CACHE.name}")
