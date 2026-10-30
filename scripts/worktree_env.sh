#!/usr/bin/env bash
# Build this worktree's .venv on Python 3.11 from uv.lock (FR-1, ADR 0060): never an ad-hoc venv, never 3.12.
#   scripts/worktree_env.sh [--web]   (run in the worktree; --web also runs `npm ci` in web/)
# `uv venv --python 3.11` then `uv sync --frozen --extra dev`: the lock as written, no re-resolve. Ends by running
# scripts/check_env.py, so a venv this script leaves behind is one the gates accept. Rebuilds a venv on another minor.
set -euo pipefail
cd "$(dirname "$0")/.."
web=0
for arg in "$@"; do
  case $arg in
    --web) web=1 ;;
    *) echo "usage: scripts/worktree_env.sh [--web]" >&2; exit 2 ;;
  esac
done
want=$(tr -d '[:space:]' < .python-version)
if [ -x .venv/bin/python ] && ! .venv/bin/python -c "import sys; sys.exit(0 if '%d.%d' % sys.version_info[:2] == '$want' else 1)"; then
  echo "worktree_env: replacing a Python $(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])') venv"
  rm -rf .venv
fi
[ -x .venv/bin/python ] || uv venv --python "$want" .venv
uv sync --frozen --extra dev
if [ "$web" = 1 ]; then (cd web && npm ci --silent); fi
.venv/bin/python scripts/check_env.py && echo "worktree_env: .venv is on $want and matches uv.lock"
