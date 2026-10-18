#!/usr/bin/env bash
# Merge smoke (<= 60 s): the cross-cutting structural checks that targeted lane tests miss and only the full suite caught.
#   scripts/merge_smoke.sh [BASE]    BASE: ref to diff against for "changed files" (default: HEAD^1 when HEAD is a merge
#                                    commit, else the merge-base with $INTEGRATION_BRANCH)
# Steps: structural tests (runner LINE_CAP, persistence strict allowlist, CLI help golden, GRID_V1/loop grid),
# migration numbers unique and gap-free, settings.yaml loads, ruff on changed files, mypy on changed modules.
# Runs every step, prints which failed, exits 1 if any did. Env: MERGE_SMOKE_DRY_RUN=1 prints the steps only.
# `scripts/docs_assemble.py merge` runs this after a successful merge (it warns, it never reverts).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# a worktree has no venv of its own: fall back to the main checkout's (the one `git worktree` shares)
MAIN=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
if [ -f .venv/bin/activate ]; then . .venv/bin/activate; elif [ -f "$MAIN/.venv/bin/activate" ]; then . "$MAIN/.venv/bin/activate"; fi

BRANCH=${INTEGRATION_BRANCH:-claude/gifted-goldberg-lcb7pl}
if [ -n "${1:-}" ]; then BASE=$1
elif git rev-parse -q --verify HEAD^2 >/dev/null 2>&1; then BASE=$(git rev-parse HEAD^1)
else BASE=$(git merge-base HEAD "$BRANCH" 2>/dev/null || git rev-parse HEAD^1 2>/dev/null || echo HEAD); fi

CHANGED=$(git diff --name-only --diff-filter=d "$BASE" HEAD 2>/dev/null)
PY=$(printf '%s\n' "$CHANGED" | grep -E '^(goldbot|tests|scripts)/.*\.py$' || true)
MODS=$(printf '%s\n' "$PY" | grep -E '^goldbot/' || true)

FAILED=()
step() {  # step NAME CMD...
  local name=$1; shift
  if [ "${MERGE_SMOKE_DRY_RUN:-}" = 1 ]; then echo "$name: $* ${STRUCTURAL_TESTS[*]}"; return; fi
  echo "== $name"
  "$@" || FAILED+=("$name")
}

STRUCTURAL_TESTS=(tests/test_engine_structure.py tests/test_persistence_compat.py tests/test_ops_run_cli_golden.py
  tests/test_loop_grid.py tests/test_migrations.py tests/test_config.py)
structural() { pytest -q -p no:cacheprovider -m "not integration" "${STRUCTURAL_TESTS[@]}"; }
migrations_unique() {
  python - <<'PY'
from goldbot.db.migrations import MIGRATIONS
for kind, migs in MIGRATIONS.items():
    versions = [m.version for m in migs]
    assert versions == list(range(1, len(migs) + 1)), f"{kind}: migration numbers clash or skip: {versions}"
    names = [m.name for m in migs]
    assert len(set(names)) == len(names), f"{kind}: duplicate migration name"
PY
}
settings_load() { python -c "from goldbot.config import load_settings; load_settings()"; }
ruff_changed() { if [ -n "$PY" ]; then printf '%s\n' "$PY" | xargs ruff check; fi; }
mypy_changed() { if [ -n "$MODS" ]; then printf '%s\n' "$MODS" | xargs mypy; fi; }

step "structural tests" structural
step "migration numbers" migrations_unique
step "settings load" settings_load
step "ruff (changed files)" ruff_changed
step "mypy (changed modules)" mypy_changed

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo "MERGE SMOKE FAILED: ${FAILED[*]}" >&2
  exit 1
fi
echo "merge smoke ok"
