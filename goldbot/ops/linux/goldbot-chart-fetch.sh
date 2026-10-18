#!/usr/bin/env bash
# Brain, user goldbot, every 60 s (goldbot-chart-fetch.timer): pull the MT5 box's latest chart screenshot into
# state/chart/mt5.png (mtime = capture time; the API refuses it when older than 5 minutes). A failed pull keeps the
# old file, which then goes stale on its own. Read-only on the MT5 box; the key can run one command (the tar above).
set -euo pipefail
# shellcheck source=/dev/null
. /etc/goldbot/bridge.env   # MT5_HOST
DEST=${CHART_STATE_DIR:-/opt/goldbot/state/chart}
KEY=${CHART_KEY:-/var/lib/goldbot/.ssh/chart_ed25519}
mkdir -p "$DEST"
work=$(mktemp -d "$DEST/.pull.XXXXXX")
trap 'rm -rf "$work"' EXIT
/usr/bin/ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new \
  mt5@"${MT5_HOST:?}" x | /usr/bin/tar -C "$work" -xf -
[ -s "$work/mt5.png" ]
mv -f "$work/mt5.png" "$DEST/mt5.png"
