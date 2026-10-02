"""Phase 1 acceptance. Every criterion from PLAN.md, measured."""
import time
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.data.free import FreeSource
from portfolio_lab.screen.spec import Filter, PortfolioSpec
from portfolio_lab.screen.engine import run_screen

# Read-only: this script never writes, so it can run while the app is open.
con = open_market(MARKET_DB, read_only=True)
src = FreeSource(con)
asof = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
print(f"Datenstand: {asof}")
print("Snapshot:", src.snapshot().row_counts)

t0 = time.time()
frame = src.screening_frame(asof)
t_build = time.time() - t0
print(f"\nScreening-Tabelle: {len(frame)} Zeilen, aufgebaut in {t_build:.2f}s")

spec = PortfolioSpec(size=10, filters=(
    Filter("price_to_book", "<=", 2.0),
    Filter("market_cap", "<=", 2_000_000_000),
))
t0 = time.time()
res = run_screen(frame, spec)
t_screen = time.time() - t0
print(f"(a) Screen-Laufzeit: {t_screen*1000:.0f} ms  [Kriterium: < 1000 ms]")
print(f"    Universum {res.n_universe} -> zulaessig {res.n_eligible} -> bestanden {res.n_passing}")
print("    Ausschluesse:", res.exclusions)
print("    je Filter einzeln:", res.per_filter_survivors)
print("\n    Top 10:")
cols = ["ticker","name","price","market_cap","price_to_book","price_to_earnings","data_age_days"]
print(res.picks[[c for c in cols if c in res.picks]].to_string(index=False))
con.close()
