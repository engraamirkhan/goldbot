#!/usr/bin/env bash
# local_bars.sh: the owner's Mac keeps the Dukascopy XAUUSD 1m bars and feeds the brain (ADR 0048, owner 2026-10-26:
# "use local copy for the data, until we make the repo private"). Dukascopy's terms forbid redistribution, so while
# this repository is public the data-dukascopy workflow builds no gold and nothing new reaches release data-v1.
#
#   scripts/local_bars.sh seed              once: download the frozen data-v1 yearly files into raw/ (personal copy)
#   scripts/local_bars.sh refresh [YEAR..]  rebuild YEAR (default: this UTC year, and last year in the first week of
#                                           January) from Dukascopy into raw/, keeping complete months, never shrinking
#   scripts/local_bars.sh push              copy changed raw/ yearly files to the brain's <data root>/_inbox/bars
#   scripts/local_bars.sh weekly            refresh, then push (run it Friday evening or Saturday before 06:00 UTC: the
#                                           brain's saturday_retrain loads the inbox; adaptive limits need bars < 9 days)
#
# Where the data lives: raw/xauusd_1m_dukascopy_<year>.parquet in this project (gitignored, refused by the
# no-market-data pre-commit hook); research reads it there (read_bar_files; research_pass.py, run_arm_backtest.py, count_owed_events.py default to --bars raw). On the brain the files land in
# /opt/goldbot/data/_inbox/bars (BRAIN_DATA_ROOT to override), owned by goldbot; goldbot.data.release.sync_release loads
# the inbox instead of data-v1 at the next Saturday retrain. The push only writes files (`.<name>.part`, then an atomic
# rename, sha256 checked on both sides): no service is stopped, restarted or written to. Idempotent: unchanged files are
# skipped. Needs ssh to goldbot-brain (~/.ssh/goldbot.conf) with sudo, like scripts/release.sh.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RAW="$ROOT/raw"
BRAIN_DATA_ROOT=${BRAIN_DATA_ROOT:-/opt/goldbot/data}
INBOX="$BRAIN_DATA_ROOT/_inbox/bars"
STAGE=goldbot-bars-staging            # in the ssh user's home on the brain
SSH=(ssh -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes goldbot-brain)
SCP=(scp -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes -q)
say() { echo "$(date -u +%H:%M:%S) $*"; }
die() { say "STOP: $*"; exit 1; }

seed() {
  mkdir -p "$RAW"
  (cd "$ROOT" && uv run -q python scripts/fetch_data_release.py --download-only)
}

refresh() {
  local years=("$@") y out
  if [ ${#years[@]} -eq 0 ]; then
    y=$(date -u +%Y); years=("$y")
    if [ "$(date -u +%m)" = "01" ] && [ "$(date -u +%d)" -le 7 ]; then years=("$((y - 1))" "$y"); fi
  fi
  mkdir -p "$RAW"
  for y in "${years[@]}"; do
    [[ "$y" =~ ^20[0-9]{2}$ ]] || die "year must be YYYY: $y"
    out="$RAW/xauusd_1m_dukascopy_${y}.parquet"
    # build next to the file and rename: a failed or interrupted build never leaves a half-written year behind
    (cd "$ROOT" && uv run -q python scripts/dukascopy_year.py "$y" --existing "$out" --out "$out.new" \
        --report "$RAW/coverage-${y}.md") || { rm -f "$out.new"; die "build of $y failed; $out unchanged"; }
    [ -s "$out.new" ] || die "build of $y wrote nothing; $out unchanged"
    mv -f "$out.new" "$out"
    (cd "$ROOT" && uv run -q python scripts/check_coverage.py "$out" --report "$RAW/daycov-${y}.md") \
      || say "gap days in $y: see $RAW/daycov-${y}.md"
    say "built $out"
  done
}

push() {
  local files=() f name local_sum remote_sums todo=()
  for f in "$RAW"/xauusd_1m_dukascopy_*.parquet; do [ -f "$f" ] && files+=("$f"); done
  [ ${#files[@]} -gt 0 ] || die "no yearly files in $RAW (run: $0 seed)"
  remote_sums=$("${SSH[@]}" "sudo sh -c 'sha256sum $INBOX/xauusd_1m_dukascopy_*.parquet 2>/dev/null || true'")
  for f in "${files[@]}"; do
    name=$(basename "$f")
    local_sum=$(shasum -a 256 "$f" | cut -d' ' -f1)
    if grep -q "^$local_sum  $INBOX/$name\$" <<< "$remote_sums"; then continue; fi
    todo+=("$f")
  done
  if [ ${#todo[@]} -eq 0 ]; then say "brain inbox already has every file"; return 0; fi
  "${SSH[@]}" "mkdir -p ~/$STAGE && sudo install -d -o goldbot -g goldbot -m 0750 $INBOX"
  for f in "${todo[@]}"; do
    name=$(basename "$f")
    local_sum=$(shasum -a 256 "$f" | cut -d' ' -f1)
    "${SCP[@]}" "$f" "goldbot-brain:$STAGE/$name"
    # check the staged copy, install it under a name the loader ignores, then rename (atomic within the inbox)
    "${SSH[@]}" "set -e; echo '$local_sum  $STAGE/$name' | sha256sum -c --quiet -; \
      sudo install -o goldbot -g goldbot -m 0640 ~/$STAGE/$name $INBOX/.$name.part; \
      sudo mv -f $INBOX/.$name.part $INBOX/$name; rm -f ~/$STAGE/$name"
    say "pushed $name"
  done
  say "the brain loads them at the next saturday_retrain (goldbot.data.release.sync_release)"
}

case "${1:-}" in
  seed) seed ;;
  refresh) shift; refresh "$@" ;;
  push) push ;;
  weekly) refresh; push ;;
  *) echo "usage: $0 seed | refresh [YEAR...] | push | weekly" >&2; exit 2 ;;
esac
