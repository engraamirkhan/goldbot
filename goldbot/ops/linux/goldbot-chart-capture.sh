#!/usr/bin/env bash
# MT5 box, user mt5, every 60 s (goldbot-chart-capture.timer): screenshot the Xvfb display the terminal runs on.
# Read-only on the display; never touches the terminal, the bridge or any order path. Atomic write, so a reader never
# sees half a PNG. The brain pulls /home/mt5/chart/mt5.png (goldbot-chart-serve) and /api/chart/mt5 serves it.
set -euo pipefail
OUT=${CHART_DIR:-/home/mt5/chart}
mkdir -p "$OUT"
tmp=$(mktemp "$OUT/.mt5.XXXXXX.png")
trap 'rm -f "$tmp"' EXIT
# CHART_CROP=WxH+X+Y (optional, set in the unit): crop to the chart area when the terminal is not full screen
# (docs/deploy/mt5-default-gold-chart.md). Anything not matching the geometry pattern is ignored (full screen shot).
crop=()
if [[ ${CHART_CROP:-} =~ ^[0-9]+x[0-9]+[+][0-9]+[+][0-9]+$ ]]; then crop=(-crop "$CHART_CROP" +repage); fi
DISPLAY=${DISPLAY:-:99} /usr/bin/import -window root "${crop[@]}" "$tmp"
[ -s "$tmp" ]
mv -f "$tmp" "$OUT/mt5.png"
trap - EXIT
