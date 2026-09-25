"""Check the alert rules against the stored prices (and EDGAR for new filings) and
notify on the Mac. Run after a price refresh; the morning job runs it for you.

Run: uv run python scripts/check_alerts.py
"""
import subprocess
import sys

from portfolio_lab import alerts
from portfolio_lab.config import ALERTS_STATE, MARKET_DB, USER_DB
from portfolio_lab.data.schema import open_market, open_user_snapshot
from portfolio_lab.insiders import _get_text


def notify(title: str, message: str) -> None:
    try:
        subprocess.run(["osascript", "-e",
                        f"display notification {message!r} with title {title!r}"],
                       capture_output=True, timeout=10)
    except Exception:                                               # noqa: BLE001
        pass


def main() -> int:
    market = open_market(MARKET_DB, read_only=True)
    # A snapshot of the portfolio store: the app may be holding it open.
    user = open_user_snapshot(USER_DB)
    events, state = alerts.run(user, market, alerts.load_state(ALERTS_STATE),
                               getter=_get_text)
    alerts.save_state(ALERTS_STATE, state)
    for e in events:
        print(f"{e['on']}  {e['message']}")
        notify("Portfolio Lab alert", e["message"])
    if not events:
        print("no alert fired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
