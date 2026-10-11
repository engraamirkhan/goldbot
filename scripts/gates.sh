#!/usr/bin/env bash
# Every backend gate from CLAUDE.md, in parallel: unit and integration tests on GATES_WORKERS xdist workers each (default
# 4: both suites run at once, and -n auto on each oversubscribed the cores and slowed every lane),
# with ruff, mypy, pre-commit and the dry run alongside. Add --web for the frontend gates. Logs: ${GATES_LOG:-~/tmp/gates}.
#   scripts/gates.sh [--web]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
[ -f .venv/bin/activate ] && . .venv/bin/activate
L=${GATES_LOG:-$HOME/tmp/gates}; mkdir -p "$L"; rm -f "$L"/*.log
run() { local name=$1; shift; ( "$@" > "$L/$name.log" 2>&1; echo "exit $?" >> "$L/$name.log" ) & }
start=$(date +%s)
run unit pytest -m "not integration" -q -n "${GATES_WORKERS:-4}"
run integration pytest -m integration -q -n "${GATES_WORKERS:-4}"
run dry_run python scripts/dry_run.py 1
run ruff ruff check goldbot tests scripts
run mypy mypy
run pre_commit pre-commit run --all-files
if [ "${1:-}" = "--web" ]; then
  # The frontend needs Node 22 (CI); an older default node fails vitest workers. Prefer nvm's newest 22.x if present.
  if ! node --version 2>/dev/null | grep -q "^v2[2-9]"; then
    n22=$(ls -d "$HOME"/.nvm/versions/node/v22.* 2>/dev/null | sort -V | tail -1)
    [ -n "$n22" ] && export PATH="$n22/bin:$PATH"
  fi
  run web bash -c "cd web && npm ci --silent && npm run lint && npm run typecheck && npm test && npm run build"
fi
wait
status=0
for f in "$L"/*.log; do
  code=$(tail -1 "$f" | awk '{print $2}')
  summary=$(grep -E "passed|failed|error|Success|All checks|lookahead|exit" "$f" | grep -v "^exit" | tail -1)
  printf '%-12s %s  %s\n' "$(basename "$f" .log)" "$([ "$code" = 0 ] && echo PASS || echo FAIL)" "$summary"
  [ "$code" = 0 ] || status=1
done
echo "total $(( $(date +%s) - start )) s"
exit $status
