#!/bin/bash
# Install (or remove) the weekday morning briefing job.
#   ./scripts/install_schedule.sh 07 15    -> every weekday at 07:15
#   ./scripts/install_schedule.sh --remove
set -euo pipefail
LABEL="com.moritzfriedrich.portfoliolab.briefing"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# The checkout this script lives in, so it works on any Mac and under any user name.
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed"
  exit 0
fi

HOUR="${1:-7}"; MIN="${2:-15}"
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data/briefings"

# One entry per weekday. launchd runs a missed job as soon as the Mac wakes, so a
# sleeping machine delays the briefing rather than skipping it.
ENTRIES=""
for DAY in 1 2 3 4 5; do
  ENTRIES="$ENTRIES
    <dict><key>Weekday</key><integer>$DAY</integer>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MIN</integer></dict>"
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
    <string>$PROJECT/scripts/morning_briefing.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartCalendarInterval</key><array>$ENTRIES
  </array>
  <key>StandardOutPath</key><string>$PROJECT/data/briefings/run.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/data/briefings/run.log</string>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "scheduled weekdays at $HOUR:$(printf %02d "$MIN")"
echo "log: $PROJECT/data/briefings/run.log"
