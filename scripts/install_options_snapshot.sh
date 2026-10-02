#!/bin/bash
# Install (or remove) the daily options snapshot (scripts/snapshot_options.py).
#   ./scripts/install_options_snapshot.sh            -> Tue–Sat at 05:00 Sydney time
#   ./scripts/install_options_snapshot.sh --remove
#
# Why 05:00, Tuesday to Saturday: the put-call spread needs live quotes (last traded
# prices are from different moments and were measured as noise). In Sydney the US session
# runs from 23:30–01:30 to 06:00–08:00 depending on both countries' daylight saving, so
# 05:00 is inside it all year, and Tuesday–Saturday morning here is Monday–Friday there.
# A Mac asleep at 05:00 runs the job on waking; if the US market has closed by then the
# snapshot records the chain mode and no spread, rather than a wrong one.
set -euo pipefail
LABEL="com.moritzfriedrich.portfoliolab.options"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="/Users/moritzfriedrich/portfolio-lab"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "removed"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data/cache/options"
ENTRIES=""
for DAY in 2 3 4 5 6; do          # launchd: 0 = Sunday, so 2..6 = Tuesday..Saturday
  ENTRIES="$ENTRIES
    <dict><key>Weekday</key><integer>$DAY</integer>
    <key>Hour</key><integer>5</integer>
    <key>Minute</key><integer>0</integer></dict>"
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
    <string>$PROJECT/scripts/snapshot_options.py</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartCalendarInterval</key><array>$ENTRIES
  </array>
  <key>StandardOutPath</key><string>$PROJECT/data/cache/options/run.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/data/cache/options/run.log</string>
  <key>ProcessType</key><string>Background</string>
</dict></plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "scheduled Tue–Sat at 05:00 (US session); log: $PROJECT/data/cache/options/run.log"
