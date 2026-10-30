#!/usr/bin/env bash
# Install the owner's Mac nightly discovery search as a launchd user agent (ADR 0058 decision 6; docs/RUNBOOK.md
# "Autonomous discovery on the Mac").
#   scripts/install_autodisc_launchd.sh [--apply | --uninstall]
# Dry run by default: prints the rendered plist and the commands it would run, and writes nothing. --apply writes
# ~/Library/LaunchAgents/com.goldbot.autodisc.plist (01:30 local; scripts/autodisc_nightly_mac.sh: 15m, 1h, 4h in
# turn; logs in ~/tmp/autodisc) and loads it; --uninstall unloads and removes it. Mac only (never the brain).
set -euo pipefail

usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
MODE=dry
case "${1:-}" in
  "") ;;
  --apply) MODE=apply ;;
  --uninstall) MODE=uninstall ;;
  *) usage ;;
esac
[ $# -le 1 ] || usage
if [ "$(uname)" != "Darwin" ]; then
  echo "install_autodisc_launchd: Mac only; the brain never runs the discovery search (ADR 0058)" >&2
  exit 2
fi

REPO=$(cd "$(dirname "$0")/.." && pwd)
LABEL=com.goldbot.autodisc
LOGDIR="$HOME/tmp/autodisc"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
plist=$(sed -e "s#__REPO__#$REPO#g" -e "s#__LOGDIR__#$LOGDIR#g" "$REPO/goldbot/ops/macos/$LABEL.plist.template")

case $MODE in
  dry)
    echo "dry run (pass --apply to install): would write $TARGET and run:"
    echo "  mkdir -p $LOGDIR"
    echo "  launchctl bootout $DOMAIN/$LABEL   # if loaded"
    echo "  launchctl bootstrap $DOMAIN $TARGET"
    echo "--- $TARGET"
    printf '%s\n' "$plist"
    ;;
  apply)
    mkdir -p "$LOGDIR" "$(dirname "$TARGET")"
    printf '%s\n' "$plist" > "$TARGET"
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$TARGET"
    echo "installed $TARGET: nightly at 01:30 local, logs in $LOGDIR"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$TARGET"
    echo "removed $TARGET"
    ;;
esac
