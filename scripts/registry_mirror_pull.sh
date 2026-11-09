#!/usr/bin/env bash
# Refresh the Mac's read-only mirror of the trial registry from the brain, the registry of record (PD ruling
# 2026-11-09). Reads the brain's file over ssh as the goldbot user (nothing is written there), refuses a copy that
# lacks any row the local mirror holds, then replaces state/research_registry.jsonl atomically, 0444, and writes the
# marker state/research_registry.jsonl.mirror (source, sha256, rows, pulled_at). With the marker present every
# registry writer on this machine refuses (goldbot.research.registry.RegistryReadOnlyMirror).
#   scripts/registry_mirror_pull.sh [LOCAL]     LOCAL defaults to state/research_registry.jsonl in this checkout
# Needs ssh to goldbot-brain via ~/.ssh/goldbot.conf (host and address live there, never in this repo).
# Env: BRAIN_REGISTRY (path on the brain), BRAIN_HOST (ssh alias).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOCAL=${1:-$ROOT/state/research_registry.jsonl}
PY=("$ROOT/.venv/bin/python")
[ -x "${PY[0]}" ] || PY=(uv run -q python)
args=(--ssh-config "$HOME/.ssh/goldbot.conf" --host "${BRAIN_HOST:-goldbot-brain}")
[ -n "${BRAIN_REGISTRY:-}" ] && args+=(--remote "$BRAIN_REGISTRY")
cd "$ROOT"
"${PY[@]}" -m goldbot.research.registry_sync pull-brain "$LOCAL" "${args[@]}"
