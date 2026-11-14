#!/usr/bin/env bash
# launchd runner of the daily registry publish (BACKLOG 215; goldbot/ops/macos/com.goldbot.registry-publish.plist.template):
# runs scripts/registry_publish.sh, keeps its full output in ~/tmp/registry-publish/run-<utc>.log, appends one line
# to ~/tmp/registry-publish/publish.log ("<utc> ok|FAIL rc=<n> <last output line>") and exits with the publish's
# status, so a failure shows in `launchctl print` and the log. Mac only: the brain never holds a token that can write
# the release. Missed runs are still caught on the brain: health `feedback_loop` FAILs when outbox rows wait > 36 h.
set -uo pipefail
if [ "$(uname)" != "Darwin" ]; then
  echo "registry_publish_mac: Mac only; the brain's token is read-only by decision (2026-11-01)" >&2
  exit 2
fi
REPO=$(cd "$(dirname "$0")/.." && pwd)
LOGDIR="$HOME/tmp/registry-publish"
mkdir -p "$LOGDIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run="$LOGDIR/run-$stamp.log"
"$REPO/scripts/registry_publish.sh" > "$run" 2>&1
rc=$?
last=$(grep -v '^[[:space:]]*$' "$run" | tail -n 1 | tr -d '\r' | cut -c1-200)
if [ "$rc" -eq 0 ]; then state=ok; else state=FAIL; fi
printf '%s %s rc=%d %s\n' "$stamp" "$state" "$rc" "$last" >> "$LOGDIR/publish.log"
# keep the 30 newest full logs
find "$LOGDIR" -maxdepth 1 -name 'run-*.log' -type f | sort -r | tail -n +31 | while IFS= read -r old; do
  rm -f "$old"
done
exit "$rc"
