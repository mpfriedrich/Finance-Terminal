"""Reproduce stored research scores with the forward books' code (read-only check).

B: results/price_ml_inputs/frozen_predictions.csv holds the frozen model's score of every
eligible share on every 2025-26 signal date. This script rebuilds the same signal date
from the market store with src/portfolio_lab/forward and compares share by share.
Run: uv run python scripts/forward_verify.py [signal-date]
"""

import datetime as dt
import sys

import numpy as np
import pandas as pd

from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.forward import data as dd
from portfolio_lab.forward import engine as en
from portfolio_lab.forward import models as md
from portfolio_lab.forward import strategies as stg

REF = pd.read_csv(md.RESEARCH / "results/price_ml_inputs/frozen_predictions.csv",
                  usecols=["signal_date", "ticker", "predicted_five_day_excess"])
want = sys.argv[1] if len(sys.argv) > 1 else "2025-06-02"
day = dt.date.fromisoformat(min(d for d in REF.signal_date.unique() if d >= want))
market = open_market(MARKET_DB, read_only=True)
spy = market.execute("SELECT date, open, close, close_adj FROM prices WHERE ticker='SPY' "
                     "ORDER BY date").fetch_df()
spy.index = pd.to_datetime(spy.pop("date"))
# The research simulation loaded prices from 2023-01-01 (observation counts start there).
panel = dd.Panel(market, dt.date(2023, 1, 1), day, spy, pd.read_parquet(md.ALFRED))
i = int(panel.calendar.get_loc(pd.Timestamp(day)))
X_all, base_ok = en._rows(panel, i)
b = md.load_b()
s, ok = stg.model_scores(X_all, b, base_ok)
ours = pd.Series(s, index=panel.tickers)[ok]

ref = REF[REF.signal_date == str(day)].set_index("ticker")["predicted_five_day_excess"]
common = ours.index.intersection(ref.index)
diff = (ours[common] - ref[common]).abs()
print(f"signal {day}: research {len(ref)} eligible, ours {len(ours)}, common {len(common)}")
print(f"only research: {sorted(set(ref.index) - set(ours.index))[:10]}")
print(f"only ours: {sorted(set(ours.index) - set(ref.index))[:10]}")
print(f"max |score difference| {diff.max():.3e}, median {diff.median():.3e}")
top_r = list(ref.sort_values(ascending=False).index[:20])
top_o = list(ours.sort_values(ascending=False).index[:20])
bot_r = list(ref.sort_values().index[:20])
bot_o = list(ours.sort_values().index[:20])
print(f"top 20 identical: {top_r == top_o}; bottom 20 identical: {bot_r == bot_o}")
print(f"rank correlation {np.corrcoef(ours[common].rank(), ref[common].rank())[0, 1]:.6f}")

# A and C: the same pipeline with peers and vintage-aware macro. factor_combinations_2025
# stored the top and bottom 20 of every variant on every 2025-26 signal date.
import json

fc = md.RESEARCH / "results/factor_combinations_2025"
arts = json.loads((fc / "frozen_models.json").read_text())["variants"]
pred = pd.read_csv(fc / "predictions.csv",
                   usecols=["variant", "signal_date", "ticker", "rank", "score"])
for variant in ("v13", "v29"):
    a = arts[variant]
    s, ok = stg.model_scores(X_all, a, base_ok)
    mine = pd.Series(s, index=panel.tickers)[ok].sort_values(ascending=False)
    ref = pred[(pred.variant == variant) & (pred.signal_date == str(day))].set_index("ticker")
    if ref.empty:
        print(f"{variant}: no stored predictions for {day}")
        continue
    top, bottom = ref[ref["rank"] <= 20], ref[ref["rank"] > 20]
    print(f"{variant}: top 20 identical {list(mine.index[:20]) == list(top.sort_values('rank').index)}; "
          f"bottom 20 identical {set(mine.index[-20:]) == set(bottom.index)}; "
          f"max |score diff| {(mine[ref.index] - ref['score']).abs().max():.3e}; "
          f"eligible ours {len(mine)}")

# D: the first basket of the stored fixed-weight run (signal 2024-12-31, entry 02.01.2025).
if "--d" in sys.argv:
    dtr = pd.read_csv(md.RESEARCH / "results/model_d/no_ssm/fixed_2025_trades.csv")
    first = dtr[dtr.entry_date == dtr.entry_date.min()]
    sday = dt.date.fromisoformat(first.signal_date.iloc[0])
    pd_panel = dd.Panel(market, dt.date(2010, 1, 1), sday, spy)
    k = int(pd_panel.calendar.get_loc(pd.Timestamp(sday)))
    ok_d = np.array([t not in pd_panel.exclude and bool(pd_panel.eligible(t).iloc[k])
                     for t in pd_panel.tickers])
    sd, okd = stg.d_scores(pd_panel, k, ok_d)
    rank = [r["ticker"] for r in stg.ranking(sd, okd, pd_panel.tickers)]
    n = min(20, len(rank) // 2)
    print(f"D {sday}: long identical {set(rank[:n]) == set(first[first.side == 'long'].ticker)}"
          f"; short identical {set(rank[-n:]) == set(first[first.side == 'short'].ticker)}"
          f"; eligible {len(rank)}")

# F: the stored threshold events (rare_signals_2011/all_rule_trades.csv, 5-day horizon).
if "--f" in sys.argv:
    ftr = pd.read_csv(md.RESEARCH / "results/rare_signals_2011/all_rule_trades.csv")
    ftr = ftr[(ftr.horizon == 5) & (ftr.signal_date >= "2025-01-01")]
    for sday in sorted(ftr.signal_date.unique())[-3:]:
        sday = dt.date.fromisoformat(sday[:10])
        fp = dd.Panel(market, dt.date(2010, 1, 1), sday, spy)
        k = int(fp.calendar.get_loc(pd.Timestamp(sday)))
        mine = {(c["ticker"], c["rule"]) for c in stg.rare_candidates(fp, k)}
        theirs = {(r.ticker, r.rule) for r in ftr[ftr.signal_date.str[:10] == str(sday)]
                  .itertuples()}
        print(f"F {sday}: research {sorted(theirs)} ours {sorted(mine)} "
              f"identical {mine == theirs}")
