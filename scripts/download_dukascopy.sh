#!/usr/bin/env bash
# Pull XAUUSD ticks from the free Dukascopy archive with dukascopy-node (needs Node 18+).
# Output: raw/dukascopy/xauusd-tick-<from>-<to>.csv  (timestamp ms, askPrice, bidPrice, askVolume, bidVolume)
# Rate-limited by Dukascopy: a full 2003->today pull takes several hours; run per year and resume.
set -euo pipefail
FROM=${1:-2003-05-04}
TO=${2:-$(date -u +%F)}
mkdir -p raw/dukascopy
npx -y dukascopy-node -i xauusd -from "$FROM" -to "$TO" -t tick -f csv -dir raw/dukascopy -bs 8 -bp 1000 -r 3
echo "done -> raw/dukascopy. Load with: python scripts/build_bars.py --source dukascopy raw/dukascopy/*.csv"
