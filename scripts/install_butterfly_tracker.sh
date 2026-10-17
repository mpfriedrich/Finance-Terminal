#!/bin/bash
# Install (or remove) the SEC Filing Butterfly paper tracker (scripts/butterfly_tracker.py).
#   ./scripts/install_butterfly_tracker.sh            -> Tue–Sat 01:30, 03:00, 04:30 Sydney
#   ./scripts/install_butterfly_tracker.sh --remove
#
# Why those hours: decisions need live bids and asks, so the tracker only decides inside
# the US session (with 15 minutes off each end). In Sydney the session runs from
# 23:30–01:30 to 06:00–08:00 depending on both countries' daylight saving; 01:30, 03:00
# and 04:30 on Tuesday to Saturday are inside it all year (Monday to Friday in New York).
# Three runs a night give a candidate three chances in each of its three entry sessions.
# A Mac asleep at those times runs the job on waking; outside the session the run only
# scans EDGAR and records values, it never decides on stale quotes.
set -euo pipefail
LABEL="com.moritzfriedrich.portfoliolab.butterfly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data/cache"
ENTRIES=""
for DAY in 2 3 4 5 6; do          # launchd: 0 = Sunday, so 2..6 = Tuesday..Saturday
  for HM in "1 30" "3 0" "4 30"; do
    set -- $HM
    ENTRIES="$ENTRIES
    <dict><key>Weekday</key><integer>$DAY</integer>
    <key>Hour</key><integer>$1</integer>
    <key>Minute</key><integer>$2</integer></dict>"
  done
done

cat > "$PLIST" <<PLISTEOF
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

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "scheduled Tue–Sat 01:30, 03:00, 04:30 (US session); log: $PROJECT/data/cache/butterfly.log"
