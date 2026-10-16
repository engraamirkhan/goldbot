#!/usr/bin/env bash
# Lane iteration on affected tests only (pytest-testmon). Merges, releases and the hand-back keep the full gates.
#   scripts/lane_test.sh seed      copy the integrator's .testmondata for this lane's base commit (else: first iter is full)
#   scripts/lane_test.sh iter      pytest --testmon -n ${GATES_WORKERS:-2} -q (no -m: testmon turns selection off under -m)
#   scripts/lane_test.sh final     reminder: run scripts/gates.sh once before handing back
#   scripts/lane_test.sh refresh   integrator: build .testmondata at HEAD and publish it to $TESTMON_SHARED/<sha>/
# Env: TESTMON_SHARED (default ~/tmp/testmon), LANE_BASE (base commit; default merge-base of HEAD with
# $INTEGRATION_BRANCH, default claude/gifted-goldberg-lcb7pl), GATES_WORKERS, LANE_TEST_DRY_RUN=1 (print pytest, don't run).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
[ -f .venv/bin/activate ] && . .venv/bin/activate

SHARED=${TESTMON_SHARED:-$HOME/tmp/testmon}
BRANCH=${INTEGRATION_BRANCH:-claude/gifted-goldberg-lcb7pl}
W=${GATES_WORKERS:-2}

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

# Copy on write where the filesystem supports it (APFS `cp -c`), a plain copy otherwise.
copy() { cp -c -f "$1" "$2" 2>/dev/null || cp -f "$1" "$2"; }

pytest_testmon() {
  local cmd=(pytest --testmon -n "$W" -q)
  if [ "${LANE_TEST_DRY_RUN:-}" = 1 ]; then echo "${cmd[*]}"; return 0; fi
  python -c "import testmon" 2>/dev/null || { echo "pytest-testmon is not installed (uv sync --extra dev)" >&2; return 1; }
  "${cmd[@]}"
}

base_commit() {
  if [ -n "${LANE_BASE:-}" ]; then git rev-parse --verify -q "${LANE_BASE}^{commit}"; return; fi
  git merge-base HEAD "$BRANCH" 2>/dev/null || git merge-base HEAD "origin/$BRANCH"
}

[ $# -eq 1 ] || usage
case "$1" in
  seed)
    base=$(base_commit) || { echo "cannot resolve the lane's base commit (set LANE_BASE)" >&2; exit 1; }
    src="$SHARED/$base/.testmondata"
    if [ -f "$src" ]; then
      copy "$src" .testmondata && echo "seeded .testmondata from $base"
    else
      echo "no shared .testmondata for $base: the first 'iter' runs the full suite and builds it"
    fi
    ;;
  iter)
    pytest_testmon
    ;;
  final)
    echo "before handing back, run the full gates once: scripts/gates.sh (the integration branch and release run them too)"
    ;;
  refresh)
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      echo "refresh needs a clean checkout at the integration tip (tracked files are modified)" >&2; exit 1
    fi
    sha=$(git rev-parse HEAD) || exit 1
    pytest_testmon; code=$?
    [ "${LANE_TEST_DRY_RUN:-}" = 1 ] && { echo "would publish to $SHARED/$sha/.testmondata"; exit 0; }
    [ -f .testmondata ] || { echo "pytest did not write .testmondata" >&2; exit 1; }
    [ $code -eq 0 ] || echo "warning: tests failed (exit $code); testmon reruns them in every lane until fixed" >&2
    mkdir -p "$SHARED/$sha" && copy .testmondata "$SHARED/$sha/.testmondata.tmp" \
      && mv -f "$SHARED/$sha/.testmondata.tmp" "$SHARED/$sha/.testmondata" && echo "published $SHARED/$sha/.testmondata"
    ;;
  -h|--help|help) usage ;;
  *) echo "unknown subcommand: $1" >&2; usage ;;
esac
