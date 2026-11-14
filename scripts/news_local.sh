#!/usr/bin/env bash
# news_local.sh: a local-only copy of the brain's `news` table for research (BACKLOG 212, for item 211). The news
# features are forward-only and the Mac holds no news table; this copies what the collector has stored. Headlines are
# third-party feed text: the copy stays in raw/news/ (gitignored, refused by the no-market-data pre-commit hook) and is
# never published or released.
#
#   scripts/news_local.sh pull     export the brain's news table to raw/news/news_<UTC date>.parquet (sha256 checked)
#
# Read only on the brain: it reads the store as the goldbot user (Store.read) and writes one Parquet to a staging
# directory in the ssh user's home, removed after the copy. No service is stopped, restarted or written to. Idempotent:
# a second pull the same day replaces that day's file with the same rows plus anything collected since.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${NEWS_OUT:-$ROOT/raw/news}
BRAIN_DATA_ROOT=${BRAIN_DATA_ROOT:-/opt/goldbot/data}
BRAIN_APP=${BRAIN_APP:-/opt/goldbot}
STAGE=goldbot-news-staging
SSH=(ssh -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes goldbot-brain)
SCP=(scp -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes -q)
say() { echo "$(date -u +%H:%M:%S) $*"; }
die() { say "STOP: $*"; exit 1; }

pull() {
  local day name remote_sum local_sum
  day=$(date -u +%Y-%m-%d)
  name="news_${day}.parquet"
  mkdir -p "$OUT"
  "${SSH[@]}" "mkdir -p ~/$STAGE && cd $BRAIN_APP && sudo -n -u goldbot $BRAIN_APP/.venv/bin/python -c \"
import io, sys
from goldbot.data.store import Store
df = Store('$BRAIN_DATA_ROOT').read('news')
buf = io.BytesIO()
df.to_parquet(buf, compression='zstd', index=False)
sys.stdout.buffer.write(buf.getvalue())
sys.stdout.buffer.flush()
print(len(df), 'rows', file=sys.stderr)
\" > ~/$STAGE/$name"
  remote_sum=$("${SSH[@]}" "test -s ~/$STAGE/$name && sha256sum ~/$STAGE/$name" | cut -d' ' -f1) \
    || die "the brain wrote no export"
  "${SCP[@]}" "goldbot-brain:$STAGE/$name" "$OUT/.$name.part"
  local_sum=$(shasum -a 256 "$OUT/.$name.part" | cut -d' ' -f1)
  [ "$local_sum" = "$remote_sum" ] || { rm -f "$OUT/.$name.part"; die "sha256 mismatch ($local_sum vs $remote_sum)"; }
  mv -f "$OUT/.$name.part" "$OUT/$name"
  "${SSH[@]}" "rm -f ~/$STAGE/$name"
  echo "$local_sum  $name" > "$OUT/$name.sha256"
  say "pulled $OUT/$name (sha256 $local_sum)"
}

case "${1:-}" in
  pull) pull ;;
  *) echo "usage: $0 pull" >&2; exit 2 ;;
esac
