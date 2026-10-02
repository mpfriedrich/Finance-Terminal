"""Record today's option-implied numbers for ~200 liquid US shares, one file per day.

Yahoo serves only the chain as it is now, never a past one. The put-call volatility spread
(options.smile_slope) is documented elsewhere but untested on this app's data, and the
only way to test it here is to keep what it said on each day and compare with what the
shares did afterwards. This script does the keeping.

Written to data/cache/options/YYYY-MM-DD.parquet rather than into a store: the market
store is locked while the app is open, and a day's snapshot is small, append-only and
never revised. Rerunning on the same day overwrites that day's file.

Best run while the US market is open (live bids and asks); outside the session the chain
falls back to last traded prices, which are asynchronous across strikes and make the
put-call spread noisier. The `mode` column records which it was.

Run: uv run python scripts/snapshot_options.py [n_shares]
"""
import datetime as dt
import sys
import time

import pandas as pd

from portfolio_lab import metrics as mx
from portfolio_lab import options as op
from portfolio_lab import volforecast as vfc
from portfolio_lab.config import CACHE_DIR, Settings
from portfolio_lab.data.sources import open_selected

OUT = CACHE_DIR / "options"
N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 200
PAUSE = 0.3          # seconds between shares, to stay polite with Yahoo


def spot_now(symbol: str, fallback: float | None) -> float | None:
    try:
        import yfinance as yf

        price = yf.Ticker(symbol).fast_info.get("last_price")
        return float(price) if price and price > 0 else fallback
    except Exception:                                                # noqa: BLE001
        return fallback


def snapshot(con, ticker: str, rate: float) -> dict | None:
    last = con.execute("SELECT close_raw FROM prices WHERE ticker = ? AND close_raw > 0 "
                       "ORDER BY date DESC LIMIT 1", [ticker]).fetchone()
    spot = spot_now(ticker, float(last[0]) if last else None)
    if not spot:
        return None
    q = op.trailing_dividend_yield(con, ticker)
    today = pd.Timestamp(dt.date.today())
    near = [(e, (pd.Timestamp(e) - today).days) for e in op.expiries(ticker)]
    near = sorted([(e, d) for e, d in near if 7 <= d <= 75],
                  key=lambda x: abs(x[1] - op.SIGNAL_DAYS))[:3]
    chains = [(d, op.chain(ticker, e, spot, rate, q)) for e, d in near]
    chains = [(d, c) for d, c in chains if not c.empty]
    if not chains:
        return None
    slope = op.smile_slope(chains, r=rate, q=q)
    days, nearest = min(chains, key=lambda x: abs(x[0] - op.SIGNAL_DAYS))
    otm = nearest[((nearest.kind == "call") & (nearest.moneyness >= 1.0))
                  | ((nearest.kind == "put") & (nearest.moneyness < 1.0))]
    dist = op.implied_distribution(otm, spot, days, rate, q)
    quant = dist.get("quantiles", {})
    return {
        "ticker": ticker, "spot": spot, "dividend_yield": q, "rate": rate,
        "mode": nearest.attrs.get("mode", ""),
        "smile_slope": slope.get("value"), "slope_days": slope.get("days"),
        "slope_pairs": slope.get("pairs"), "slope_method": slope.get("method"),
        "slope_rejected": slope.get("rejected"), "dist_usable": dist.get("usable", False),
        "dist_days": days, "atm_iv": dist.get("atm_iv"),
        "q10": quant.get(10), "q50": quant.get(50), "q90": quant.get(90),
        "p_down_10": dist.get("p_down_10"), "p_up_10": dist.get("p_up_10"),
        "beyond_quotes": dist.get("beyond_quotes"),
    }


def main() -> int:
    src, _ = open_selected(Settings(), read_only=True)
    asof = src.latest_equity_date()
    rate, _ = mx.current_risk_free(src.con, Settings().risk_free_fallback)
    tickers = vfc.sample_tickers(src.con, asof, N)
    started = dt.datetime.now(dt.timezone.utc)
    rows, failed = [], 0
    for i, t in enumerate(tickers, 1):
        try:
            row = snapshot(src.con, t, rate)
        except Exception as exc:                                     # noqa: BLE001
            print(f"  {t}: {type(exc).__name__}", flush=True)
            row = None
        if row:
            rows.append(row)
        else:
            failed += 1
        if i % 25 == 0:
            print(f"{i}/{len(tickers)} shares, {len(rows)} recorded", flush=True)
        time.sleep(PAUSE)
    if not rows:
        print("nothing recorded — Yahoo unreachable or no chains")
        return 1
    frame = pd.DataFrame(rows).assign(taken_utc=started, store_asof=asof)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{dt.date.today().isoformat()}.parquet"
    frame.to_parquet(path, index=False)
    with_slope = frame.smile_slope.notna().sum()
    print(f"{path.name}: {len(frame)} shares ({with_slope} with a put-call spread), "
          f"{failed} without a usable chain; mode: {frame['mode'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
