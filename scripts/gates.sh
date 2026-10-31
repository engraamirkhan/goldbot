#!/usr/bin/env bash
# Every backend gate from CLAUDE.md, in parallel: unit and integration tests on xdist workers, with ruff, mypy,
# pre-commit and the dry run alongside. Add --web for the frontend gates. Logs: ${GATES_LOG:-~/tmp/gates/<worktree>}.
#   scripts/gates.sh [--web]   the merge gate
#   scripts/gates.sh --quick   ruff + mypy + unit only, for iterating in a lane; NOT the merge gate
# Workers per pytest suite: GATES_WORKERS if set (always wins), else picked from the machine's load so parallel lanes
# do not oversubscribe the cores: max(1, min(4, cores - ceil(load1))). -n auto on every lane drove load past 100.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

quick=0; web=0
for arg in "$@"; do
  case $arg in
    --quick) quick=1 ;;
    --web) web=1 ;;
    *) echo "usage: scripts/gates.sh [--web | --quick]" >&2; exit 2 ;;
  esac
done
if [ "$quick" = 1 ] && [ "$web" = 1 ]; then echo "gates: --quick and --web do not combine" >&2; exit 2; fi

[ -f .venv/bin/activate ] && . .venv/bin/activate
. scripts/lib/common.sh
goldbot_check_env   # exit 2 on a venv that is not 3.11 or differs from uv.lock: no gate result from it is trusted (FR-1)
L=${GATES_LOG:-$HOME/tmp/gates/$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")}   # one log folder per worktree
mkdir -p "$L"; rm -f "$L"/*.log                     # so parallel lanes never delete each other's logs

cores() { sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 1; }
load1() {  # 1-minute load average; macOS prints "{ 9.61 8.20 7.00 }", Linux "9.61 8.20 7.00 ..."
  if [ -r /proc/loadavg ]; then awk '{print $1}' /proc/loadavg
  else sysctl -n vm.loadavg 2>/dev/null | awk '{print $2}'; fi
}
if [ -n "${GATES_WORKERS:-}" ]; then
  workers=$GATES_WORKERS
  echo "gates: $workers workers (GATES_WORKERS set)"
else
  c=$(cores); ld=$(load1); ld=${ld:-0}
  workers=$(awk -v c="$c" -v l="$ld" 'BEGIN { u = int(l); if (u < l) u++; w = c - u;
    if (w > 4) w = 4; if (w < 1) w = 1; print w }')
  echo "gates: $workers workers ($c cores, load $ld)"
fi

if [ "$quick" = 1 ]; then
  echo "gates --quick: ruff + mypy + unit only. NOT the merge gate: run scripts/gates.sh before handing back."
fi

run() { local name=$1; shift; ( "$@" > "$L/$name.log" 2>&1; echo "exit $?" >> "$L/$name.log" ) & }
start=$(date +%s)
run unit pytest -m "not integration" -q -n "$workers"
run ruff ruff check goldbot tests scripts
run mypy mypy --cache-dir .mypy_cache   # incremental: a warm cache rechecks only what changed
if [ "$quick" = 0 ]; then
  run integration pytest -m integration -q -n "$workers"
  run dry_run python scripts/dry_run.py 1
  run pre_commit pre-commit run --all-files
fi
if [ "$web" = 1 ]; then
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
if [ "$quick" = 1 ]; then
  echo "gates --quick: NOT the merge gate (no integration, dry_run, pre-commit or web). Run scripts/gates.sh before handing back."
fi
exit $status
