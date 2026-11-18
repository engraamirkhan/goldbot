#!/usr/bin/env bash
# Publish the trial registry from the Mac. The brain's github-token has no `Contents: write` and is not widened
# (decision 2026-11-01), so goldbot.research.registry_sync.sync leaves the registry it could not upload in
# $BRAIN_STATE/registry_outbox/ (INFO log). This reads that outbox and the brain's whole registry over ssh (read-only:
# sudo cat, nothing renamed or deleted on the brain), unions them with the release copy through the same code the brain
# runs, and uploads with the Mac's own gh (compare-and-swap, registry_sync.save_rows). The brain's daily
# `registry_outbox` job clears the outbox once the release holds its rows, so there is nothing to acknowledge.
# Idempotent: rows already on the release are not repeated.
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

# A missing outbox on the brain is an empty one (the remote `|| true`); an ssh failure is not: it stops the publish
# with a non-zero status, so the launchd runner logs FAIL instead of a quiet "0 entries" (code review 2026-11-14)
if ! listing=$("${SSH[@]}" "sudo ls '$OUTBOX' 2>/dev/null || true"); then
  echo "registry_publish: ssh to goldbot-brain failed; nothing read, nothing published" >&2
  exit 3
fi
names=$(printf '%s\n' "$listing" | grep -E '^[0-9a-f]{16}\.jsonl$' || true)
for n in $names; do
  "${SSH[@]}" "sudo cat '$OUTBOX/$n'" > "$work/outbox/$n"
done
echo "outbox entries read from the brain: $(echo "$names" | grep -c . || true)"

# The outbox is a snapshot taken at the brain's last sync; rows written since (incident 2026-11-18: six learning-class
# K rows of 15 Nov, scripts/record_learning_k_rows.py) are only in the registry itself. Read it whole and union it in,
# so a publish covers every brain row. A failed read stops the publish: the snapshot alone is the bug this fixes
REGISTRY="$BRAIN_STATE/research_registry.jsonl"
if ! "${SSH[@]}" "sudo cat '$REGISTRY'" > "$work/brain_registry.jsonl"; then
  echo "registry_publish: could not read $REGISTRY on goldbot-brain; nothing published" >&2
  exit 3
fi
echo "brain registry rows read: $(grep -c . "$work/brain_registry.jsonl" || true)"

extra=(--extra "$work/brain_registry.jsonl")
for f in "$@"; do extra+=(--extra "$f"); done
cd "$ROOT"
# shellcheck disable=SC2086
$PY -m goldbot.research.registry_sync publish-outbox "$work/outbox" "$work/registry.jsonl" ${extra[@]+"${extra[@]}"}
