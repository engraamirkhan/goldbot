#!/usr/bin/env bash
# Install the daily registry publish as a launchd user agent on the owner's Mac (BACKLOG 215; docs/RUNBOOK.md
# "Registry publish from the Mac").
#   scripts/install_registry_publish_launchd.sh [--apply | --uninstall]
# Dry run by default: prints the rendered plist and the commands it would run, and writes nothing. --apply writes
# ~/Library/LaunchAgents/com.goldbot.registry-publish.plist (09:15 local daily, a missed slot runs on wake;
# scripts/registry_publish_mac.sh; logs in ~/tmp/registry-publish) and loads it; --uninstall unloads and removes it.
# Run it from the main checkout, not a worktree: the agent runs the scripts of the checkout it was installed from.
set -euo pipefail

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
MODE=dry
case "${1:-}" in
  "") ;;
  --apply) MODE=apply ;;
  --uninstall) MODE=uninstall ;;
  *) usage ;;
esac
[ $# -le 1 ] || usage
if [ "$(uname)" != "Darwin" ]; then
  echo "install_registry_publish_launchd: Mac only; the brain's token is read-only by decision (2026-11-01)" >&2
  exit 2
fi

REPO=$(cd "$(dirname "$0")/.." && pwd)
LABEL=com.goldbot.registry-publish
LOGDIR="$HOME/tmp/registry-publish"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
plist=$(sed -e "s#__REPO__#$REPO#g" -e "s#__LOGDIR__#$LOGDIR#g" -e "s#__HOME__#$HOME#g" \
  "$REPO/goldbot/ops/macos/$LABEL.plist.template")

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
    echo "installed $TARGET: daily at 09:15 local (and on wake after a missed slot), logs in $LOGDIR"
    echo "run once now: launchctl kickstart $DOMAIN/$LABEL ; then: tail -n 1 $LOGDIR/publish.log"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$TARGET"
    echo "removed $TARGET"
    ;;
esac
