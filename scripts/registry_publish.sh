#!/usr/bin/env bash
# Publish the trial registry from the Mac. The brain's github-token has no `Contents: write` and is not widened
# (decision 2026-11-01), so goldbot.research.registry_sync.sync leaves the registry it could not upload in
# $BRAIN_STATE/registry_outbox/ (INFO log). This reads that outbox over ssh (read-only: sudo cat, nothing renamed or
# deleted on the brain), unions it with the release copy through the same code the brain runs, and uploads with the
# Mac's own gh (compare-and-swap, registry_sync.save_rows). The brain clears an entry on its next sync once the release
# holds all its rows, so there is nothing to acknowledge. Idempotent: rows already on the release are not repeated.
#   scripts/registry_publish.sh [EXTRA.jsonl ...]    EXTRA: row files made on the Mac (e.g. ~/tmp/..._registry_rows.jsonl)
# Needs ssh to goldbot-brain (~/.ssh/goldbot.conf, sudo), gh with write access, the repo venv (scripts/worktree_env.sh).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
BRAIN_STATE=${BRAIN_STATE:-/opt/goldbot/state}
OUTBOX="$BRAIN_STATE/registry_outbox"
SSH=(ssh -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes goldbot-brain)
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="uv run -q python"

work=$(mktemp -d "${TMPDIR:-/tmp}/registry-publish.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$work/outbox"

names=$("${SSH[@]}" "sudo ls '$OUTBOX' 2>/dev/null || true" | grep -E '^[0-9a-f]{16}\.jsonl$' || true)
for n in $names; do
  "${SSH[@]}" "sudo cat '$OUTBOX/$n'" > "$work/outbox/$n"
done
echo "outbox entries read from the brain: $(echo "$names" | grep -c . || true)"

extra=()
for f in "$@"; do extra+=(--extra "$f"); done
cd "$ROOT"
# shellcheck disable=SC2086
$PY -m goldbot.research.registry_sync publish-outbox "$work/outbox" "$work/registry.jsonl" ${extra[@]+"${extra[@]}"}
