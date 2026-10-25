#!/bin/bash
# Install (or remove) the SEC Filing Butterfly paper tracker (scripts/butterfly_tracker.py).
#   ./scripts/install_butterfly_tracker.sh            -> Tue–Sat 02:00, 03:00, 04:30 Sydney
#   ./scripts/install_butterfly_tracker.sh --remove
#   ./scripts/install_butterfly_tracker.sh --dry-run  -> print the project and the plist,
#                                                        change nothing
#
# Why those hours: decisions need live bids and asks, so the tracker only decides inside
# the US session with 15 minutes off each end, 09:45–15:45 New York. Sydney is 14, 15 or
# 16 hours ahead of New York depending on both countries' daylight saving (AEST/EDT,
# AEDT/EDT, AEDT/EST), so that window is 01:45–05:45 Sydney at its narrowest (01:45 when
# AEDT meets EST, 05:45 when AEST meets EDT). 02:00, 03:00 and 04:30 on Tuesday to
# Saturday lie inside it every day of the year (Monday to Friday in New York);
# tests/test_install_butterfly_tracker.py checks every day. The earlier 01:30 was
# 09:30 New York from November to mid-March, before the window (audit 24.10.2026 F16).
# Three runs a night give a candidate three chances in each of its three entry sessions.
# A Mac asleep at those times runs the job on waking; outside the session the run only
# scans EDGAR and records values, it never decides on stale quotes.
#
# Which folder the job runs in: always the main checkout (~/portfolio-lab), because the
# stores in data/ and .env.local live only there. Called from a git worktree, the script
# used to point launchd at the worktree, which has no data of its own and is deleted
# later (audit 24.10.2026). It now asks git for the main checkout and says so.
set -euo pipefail
MODE="${1:-}"      # read now: `set -- $HM` below overwrites $1
LABEL="com.moritzfriedrich.portfoliolab.butterfly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
# The main checkout is the folder holding the shared .git directory; in the main
# checkout itself that is HERE again. Without git, HERE is used as it is.
COMMON="$(git -C "$HERE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [ -n "$COMMON" ] && [ "$(basename "$COMMON")" = ".git" ]; then
  PROJECT="$(dirname "$COMMON")"
else
  PROJECT="$HERE"
fi
if [ "$PROJECT" != "$HERE" ]; then
  echo "note: called from the worktree $HERE;"
  echo "      installing for the main checkout $PROJECT (data/ and .env.local live there)"
fi

if [ "$MODE" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed"
  exit 0
fi

if [ ! -f "$PROJECT/scripts/butterfly_tracker.py" ]; then
  echo "error: $PROJECT/scripts/butterfly_tracker.py not found." >&2
  echo "       Merge the branch into the main checkout first, then run this from there." >&2
  exit 1
fi

ENTRIES=""
for DAY in 2 3 4 5 6; do          # launchd: 0 = Sunday, so 2..6 = Tuesday..Saturday
  for HM in "2 0" "3 0" "4 30"; do
    set -- $HM
    ENTRIES="$ENTRIES
    <dict><key>Weekday</key><integer>$DAY</integer>
    <key>Hour</key><integer>$1</integer>
    <key>Minute</key><integer>$2</integer></dict>"
  done
done

PLIST_TEXT="$(cat <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME/.local/bin/uv</string>
    <string>run</string>
    <string>--project</string><string>$PROJECT</string>
    <string>python</string>
    <string>$PROJECT/scripts/butterfly_tracker.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartCalendarInterval</key><array>$ENTRIES
  </array>
  <key>StandardOutPath</key><string>$PROJECT/data/cache/butterfly.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/data/cache/butterfly.log</string>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
PLISTEOF
)"

if [ "$MODE" = "--dry-run" ]; then
  echo "project: $PROJECT"
  echo "$PLIST_TEXT"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data/cache"
echo "$PLIST_TEXT" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "scheduled Tue–Sat 02:00, 03:00, 04:30 Sydney (US session 09:45–15:45 New York)"
echo "log: $PROJECT/data/cache/butterfly.log"
