"""Phase 7: build ticker_history and measure how much of the vanished world survives.

Reads the SEC submissions bulk file for every filer we hold fundamentals for, records
each filer's filing window and its current symbol, adds the windows we observed in our
own price bars, then draws the 20-CIK sample the plan prescribes and prints both rates.

Run: uv run python scripts/build_ticker_history.py
"""
import time

from portfolio_lab.config import CACHE_DIR, MARKET_DB
from portfolio_lab.data import history
from portfolio_lab.data.schema import finish_rebuild, open_market_for_rebuild

t0 = time.time()
con, work = open_market_for_rebuild(MARKET_DB)
if work is not None:
    print("the app is holding the store; building on a copy and swapping it in", flush=True)

stats = history.build_ticker_history(con, CACHE_DIR / "submissions.zip")
print(f"\nticker_history: {stats}", flush=True)

rec = history.recovery_rate(con)
print(f"\nRecovery sample ({rec['n']} filers that stopped filing before 2019):")
print(f"  a symbol at all : {rec['with_ticker']}/{rec['n']}"
      f" = {(rec['ticker_rate'] or 0):.0%}")
print(f"  prices as well  : {rec['with_prices']}/{rec['n']}"
      f" = {(rec['price_rate'] or 0):.0%}")
print(f"  verdict         : stage B universe is "
      f"{'SURVIVOR-BASED' if history.survivor_based(rec) else 'point-in-time'}")
print(rec["sample"].to_string(index=False))

con.execute("DELETE FROM universe_recovery")
con.execute(
    "INSERT INTO universe_recovery (measured_at, n_sample, n_with_ticker, n_with_prices) "
    "VALUES (now(), ?, ?, ?)",
    [rec["n"], rec["with_ticker"], rec["with_prices"]],
)
print(f"\ntotal {time.time() - t0:.0f}s", flush=True)
if finish_rebuild(con, work, MARKET_DB):
    print("swapped the rebuilt store into place; restart the app to see it", flush=True)
