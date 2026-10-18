#!/usr/bin/env bash
# MT5 box, user mt5, every 60 s (goldbot-chart-capture.timer): screenshot the Xvfb display the terminal runs on.
# Read-only on the display; never touches the terminal, the bridge or any order path. Atomic write, so a reader never
# sees half a PNG. The brain pulls /home/mt5/chart/mt5.png (goldbot-chart-serve) and /api/chart/mt5 serves it.
set -euo pipefail
OUT=${CHART_DIR:-/home/mt5/chart}
mkdir -p "$OUT"
tmp=$(mktemp "$OUT/.mt5.XXXXXX.png")
trap 'rm -f "$tmp"' EXIT
DISPLAY=${DISPLAY:-:99} /usr/bin/import -window root "$tmp"
[ -s "$tmp" ]
mv -f "$tmp" "$OUT/mt5.png"
trap - EXIT
