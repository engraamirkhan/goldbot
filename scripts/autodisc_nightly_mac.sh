#!/usr/bin/env bash
# The owner's Mac nightly discovery search (ADR 0058 decision 6; launchd agent com.goldbot.autodisc at 01:30 local,
# installed by scripts/install_autodisc_launchd.sh). Runs `scripts/autodisc.py nightly` for 15m, 1h and 4h in turn
# (sequential: one search at a time on the Mac), each logged to ~/tmp/autodisc/<day>-<tf>.log. A timeframe that fails
# is logged and the next still runs; the exit status is non-zero when any failed. Never on the brain (Mac only).
set -uo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "autodisc nightly: the discovery search runs on the owner's Mac only, never on the brain (ADR 0058)" >&2
  exit 2
fi
cd "$(dirname "$0")/.." || exit 1
LOGDIR="$HOME/tmp/autodisc"
mkdir -p "$LOGDIR"
PY=.venv/bin/python
[ -x "$PY" ] || { echo "autodisc nightly: no .venv in $PWD" >&2; exit 2; }
day=$(date -u +%Y-%m-%d)
status=0
for tf in 15m 1h 4h; do
  log="$LOGDIR/$day-$tf.log"
  echo "$(date -u +%FT%TZ) start $tf" >> "$LOGDIR/nightly.log"
  if "$PY" scripts/autodisc.py nightly --timeframe "$tf" >> "$log" 2>&1; then
    echo "$(date -u +%FT%TZ) done $tf: $(tail -n 1 "$log")" >> "$LOGDIR/nightly.log"
  else
    echo "$(date -u +%FT%TZ) FAILED $tf (see $log)" >> "$LOGDIR/nightly.log"
    status=1
  fi
done
exit "$status"
