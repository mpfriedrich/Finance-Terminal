"""Write the daily briefing and deliver it.

Run by launchd each weekday morning. Three things it does, in order, so a failure at any
step leaves evidence rather than silence:

1. Refresh prices, so the briefing is about today and not last week.
2. Gather the evidence and write the briefing.
3. Save it to disk ALWAYS, then try to send it.

Saving before sending is deliberate. Mail delivery is the part most likely to break — a
changed password, an expired token, no network at 07:00 — and a briefing that exists on
disk can still be read. One that was only ever an email attempt is gone.

Configure delivery in .env.local:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=you@example.com
    SMTP_PASSWORD=an app-specific password, never your account password
    MAIL_TO=where it should arrive
    MAIL_FROM=optional, defaults to SMTP_USER

With no SMTP settings the briefing is still written and a macOS notification points at
the file — useful on its own, and the sane fallback rather than a crash.
"""

from __future__ import annotations

import datetime as dt
import html
import os
import smtplib
import subprocess
import sys
import traceback
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from portfolio_lab import briefing as bfg  # noqa: E402
from portfolio_lab import ui  # noqa: E402
from portfolio_lab.config import MARKET_DB, USER_DB, load_env  # noqa: E402
from portfolio_lab.data.schema import open_market, open_user_snapshot  # noqa: E402
from portfolio_lab.store import Store  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "briefings"


def refresh_prices() -> str:
    """Bring prices up to date. Failure is reported, not fatal — a briefing on
    yesterday's closes beats no briefing at all, as long as it says so."""
    script = Path(__file__).resolve().parent / "build_prices.py"
    try:
        res = subprocess.run(
            [sys.executable, str(script)], capture_output=True, text=True, timeout=900
        )
        return "ok" if res.returncode == 0 else f"failed: {res.stderr.strip()[:200]}"
    except Exception as exc:
        return f"failed: {exc}"


def render_html(text: str, evidence: str, as_of: dt.date, price_note: str) -> str:
    """The briefing as an email, in the same terminal palette as the app."""
    body = html.escape(text)
    for marker, tag in (("## ", "h2"), ("# ", "h1")):
        lines = []
        for line in body.split("\n"):
            if line.startswith(marker):
                lines.append(f"<{tag}>{line[len(marker):]}</{tag}>")
            else:
                lines.append(line)
        body = "\n".join(lines)
    body = body.replace("**", "")
    body = "\n".join(
        f"<p>{line}</p>" if line and not line.startswith("<") else line
        for line in body.split("\n")
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1">
<style>
  body {{ background:{ui.BG}; color:{ui.DATA}; margin:0; padding:20px;
          font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace; font-size:14px;
          line-height:1.55; }}
  .wrap {{ max-width:680px; margin:0 auto; }}
  .bar {{ border-top:2px solid {ui.AMBER}; border-bottom:1px solid {ui.RULE};
          padding:8px 0; margin-bottom:18px; display:flex;
          justify-content:space-between; }}
  .name {{ color:{ui.AMBER}; font-weight:600; letter-spacing:.16em; }}
  .meta {{ color:{ui.MUTED}; font-size:11px; }}
  h1,h2 {{ color:{ui.AMBER}; font-size:13px; text-transform:uppercase;
           letter-spacing:.05em; border-bottom:1px solid {ui.RULE};
           padding-bottom:4px; margin:22px 0 8px; }}
  p {{ margin:0 0 10px; }}
  details {{ margin-top:26px; border:1px solid {ui.RULE}; padding:10px; }}
  summary {{ color:{ui.MUTED}; font-size:12px; cursor:pointer; }}
  pre {{ color:{ui.MUTED}; font-size:11px; white-space:pre-wrap; margin:10px 0 0; }}
  .foot {{ color:{ui.MUTED}; font-size:11px; margin-top:26px;
           border-top:1px solid {ui.RULE}; padding-top:10px; }}
</style></head><body><div class="wrap">
<div class="bar"><span class="name">PORTFOLIO LAB</span>
<span class="meta">{as_of} · prices {html.escape(price_note)}</span></div>
{body}
<details><summary>The evidence this was written from</summary>
<pre>{html.escape(evidence)}</pre></details>
<div class="foot">Written only from the evidence above — headlines actually fetched and
moves actually measured. No recommendations, no forecasts. Generated locally on your
Mac.</div>
</div></body></html>"""


def notify(title: str, message: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {message!r} with title {title!r}'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def send(subject: str, html_body: str, text_body: str) -> str:
    load_env()
    host = os.environ.get("SMTP_HOST")
    to = os.environ.get("MAIL_TO")
    if not host or not to:
        return "not configured"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER", "")
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=60)
        else:
            server = smtplib.SMTP(host, port, timeout=60)
            server.starttls()
        with server:
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return f"sent to {to}"
    except Exception as exc:
        return f"failed: {type(exc).__name__}: {exc}"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    price_note = refresh_prices()
    # Alerts on the fresh closes, before the briefing: they notify on their own.
    try:
        subprocess.run([sys.executable, str(Path(__file__).resolve().parent /
                                            "check_alerts.py")], timeout=600)
    except Exception as exc:                                        # noqa: BLE001
        print(f"alerts not checked: {exc}")

    market = open_market(MARKET_DB, read_only=True)
    # Read a COPY of the portfolio store. DuckDB's write lock excludes readers as well,
    # so opening the live file would fail whenever the app was left running overnight.
    try:
        store = Store(open_user_snapshot(USER_DB))
        followed = store.everything_followed()
    except Exception as exc:
        print(f"could not read the portfolio store: {exc}")
        notify("Portfolio Lab", "Could not read your portfolios — no briefing written.")
        return 1
    if not followed:
        notify("Portfolio Lab", "Nothing followed — no briefing written.")
        return 0

    bundle = bfg.gather(market, followed)
    result = bfg.write(bundle)

    stamp = today.isoformat()
    (OUT_DIR / f"{stamp}.md").write_text(result["text"], encoding="utf-8")
    page = render_html(result["text"], result["evidence"], today, price_note)
    (OUT_DIR / f"{stamp}.html").write_text(page, encoding="utf-8")

    moved = len(bundle["notable"])
    subject = (f"Portfolio Lab · {stamp}"
               + (f" · {moved} unusual move{'s' if moved != 1 else ''}" if moved else ""))
    status = send(subject, page, result["text"])

    if status.startswith("sent"):
        notify("Portfolio Lab", f"Briefing {status}.")
    else:
        notify("Portfolio Lab", f"Briefing saved ({status}). Open data/briefings/.")
    print(f"{stamp}: prices {price_note}; mail {status}; {len(followed)} followed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        notify("Portfolio Lab", "Briefing failed — see the log in data/briefings/.")
        sys.exit(1)
