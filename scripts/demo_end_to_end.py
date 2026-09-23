"""Chat message -> filters -> real stocks. The whole chain, once."""
import warnings; warnings.simplefilter("ignore")
from portfolio_lab.config import MARKET_DB
from portfolio_lab.data.schema import open_market
from portfolio_lab.data.free import FreeSource
from portfolio_lab.screen.spec import PortfolioSpec
from portfolio_lab.screen import translate as T
from portfolio_lab.screen.engine import run_screen

# Read-only: this script never writes, so it can run while the app is open.
con = open_market(MARKET_DB, read_only=True); src = FreeSource(con)
asof = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
frame = src.screening_frame(asof)

spec = PortfolioSpec()
for msg in ["portfolio aus 10 aktien us markt, price to profit max 1 und price book max 2",
            "lass cap nicht über USD 2bn sein"]:
    print(f"\n>>> {msg}")
    t = T.translate(msg, spec); spec = t.spec
    print("   verstanden:", t.changed or ["(keine Änderung)"])
    if spec.unsupported:
        print("   NICHT verstanden:", list(spec.unsupported))

res = run_screen(frame, spec)
print(f"\nUniversum {res.n_universe} -> zulässig {res.n_eligible} -> bestanden {res.n_passing}")
print("je Filter einzeln:", res.per_filter_survivors)
for n in res.notes: print("  Hinweis:", n)
cols = ["ticker","name","price","market_cap","price_to_book","price_to_earnings","weight"]
print("\n" + res.picks[[c for c in cols if c in res.picks]].to_string(index=False))
con.close()
