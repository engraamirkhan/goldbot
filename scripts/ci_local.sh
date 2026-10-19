#!/usr/bin/env bash
# Reproduce .github/workflows/ci.yml locally, as close to a GitHub runner as a Mac allows. Local gates passed but CI
# failed because a test called the real `gh` (Mac has a token, CI none) and because a test was timing-flaky under load.
# This runs the same job commands in a hostile, CI-like environment:
#   - `env -i`, HOME = a fresh temp dir (no keychain, no ~/.config/gh), PATH = shim dir of symlinks to only the tools
#     CI has (python venv, node, git, uv, coreutils): NO gh, ssh or curl;
#   - exactly the env CI sets (GOLDBOT_ALLOW_PLAINTEXT=1, CI=true, TZ=UTC), PYTHON_KEYRING_BACKEND=fail, no GH_TOKEN,
#     no proxies, GOLDBOT_NO_NETWORK=1 (tests/conftest.py also fails any test that runs gh/ssh/curl or leaves loopback);
#   - outbound network blocked at the kernel with macOS sandbox-exec (loopback stays open) when it is available;
#   - the same jobs and shards as CI: the matrix sizes (backend-unit, backend-integration shards; e2e projects) are
#     READ FROM ci.yml, so a CI matrix change needs no edit here (tests/test_ci_workflow.py checks the commands).
#     smoke (pre-commit, ruff, collection) runs first, beside backend-lint (mypy, api-contract) ->
#     frontend-checks; when smoke passes the unit and integration shards start (GOLDBOT_SHARD=i/N, same
#     `pytest -m ...` as CI, random order, dry run in the integration shard CI names) and e2e runs per project after
#     frontend-checks. Each pytest lane uses one xdist worker (CI: -n auto per shard). CI runs 6 + 10 shards at once;
#     here at most CI_LOCAL_MAX_JOBS (default 12, the Mac's core count) background lanes run at a time and the rest
#     wait for a free slot, so the machine is never oversubscribed. A failed smoke skips the shards and e2e (CI
#     itself no longer waits for smoke: it runs beside the shards and fails the run).
# Every failing pytest test is re-run alone 5 times with different `-p randomly` seeds (CI re-runs once): it is
# reported FLAKY when any re-run passes and FAIL when all fail. Exit status is non-zero on any FAIL or FLAKY.
# Usage: scripts/ci_local.sh [--no-web] [--no-e2e]   Logs: ${CI_LOCAL_LOG:-~/tmp/ci_local/<worktree>}
# Not reproduced: the install steps (the repo's .venv is used), Linux itself, GitHub's runner speed, and the PR
# prose-only skip (locally everything runs, as on main). `npm ci` runs --offline from the local npm cache.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT=$PWD
web=1; e2e=1
for arg in "$@"; do
  case $arg in
    --no-web) web=0 ;;
    --no-e2e) e2e=0 ;;
    *) echo "usage: scripts/ci_local.sh [--no-web] [--no-e2e]" >&2; exit 2 ;;
  esac
done

[ -x .venv/bin/python ] || { echo "ci_local: no .venv; run: uv venv --python 3.11 && uv pip install -e '.[dev]'" >&2; exit 2; }
# The CI matrix, from ci.yml itself: "<unit shards> <integration shards> <e2e projects, comma-separated>"
matrix=$(.venv/bin/python - <<'PY'
import yaml
jobs = yaml.safe_load(open(".github/workflows/ci.yml"))["jobs"]
dry = next(s["if"] for s in jobs["backend-integration"]["steps"] if s.get("name") == "dry-run")
print(len(jobs["backend-unit"]["strategy"]["matrix"]["shard"]), len(jobs["backend-integration"]["strategy"]["matrix"]["shard"]),
      ",".join(jobs["e2e"]["strategy"]["matrix"]["project"]), dry.split("matrix.shard ==")[1].split()[0])
PY
) || { echo "ci_local: cannot read the matrix from .github/workflows/ci.yml" >&2; exit 2; }
read -r N_UNIT N_INTEG E2E_PROJECTS DRY_SHARD <<< "$matrix"
MAX_JOBS=${CI_LOCAL_MAX_JOBS:-12}
echo "ci_local: ci.yml matrix: $N_UNIT unit shards, $N_INTEG integration shards (dry run in $DRY_SHARD), e2e projects $E2E_PROJECTS; at most $MAX_JOBS lanes at once"
L=${CI_LOCAL_LOG:-$HOME/tmp/ci_local/$(basename "$ROOT")}
mkdir -p "$L" "$HOME/tmp"; rm -f "$L"/*.log "$L"/*.status "$L"/*.steps "$L"/*.xml
T=$(mktemp -d "$HOME/tmp/ci_local_home.XXXXXX")   # the fake HOME
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/tmp"

# --- PATH shim: only what a CI runner has for these jobs. gh, ssh and curl are deliberately absent. -------------
link() { local p; p=$(command -v "$1" 2>/dev/null) && [ -n "$p" ] && ln -sf "$p" "$T/bin/$1"; }
for t in git uv bash sh env tee tail head sed awk grep tr sort uniq cut wc cat ls cp mv rm mkdir rmdir dirname basename \
         mktemp date sleep xargs find chmod ln diff tar gzip touch pwd uname id which test true false expr tput \
         sysctl nproc readlink realpath perl shasum od stat; do link "$t"; done
nodebin=
if ! node --version 2>/dev/null | grep -q '^v2[2-9]'; then
  n22=$(ls -d "$HOME"/.nvm/versions/node/v22.* 2>/dev/null | sort -V | tail -1)
  [ -n "$n22" ] && nodebin=$n22/bin
fi
if [ "$web" = 1 ]; then
  for t in node npm npx; do
    if [ -n "$nodebin" ]; then ln -sf "$nodebin/$t" "$T/bin/$t"; else link "$t"; fi
  done
fi
CLEAN_PATH="$ROOT/.venv/bin:$T/bin"

# --- network block ----------------------------------------------------------------------------------------------
PROFILE='(version 1)(allow default)(deny network-outbound (remote ip))(allow network-outbound (remote ip "localhost:*"))'
box=()
if command -v sandbox-exec >/dev/null 2>&1 && sandbox-exec -p "$PROFILE" /usr/bin/true 2>/dev/null; then
  box=(sandbox-exec -p "$PROFILE"); echo "ci_local: outbound network blocked with sandbox-exec (loopback open)"
else
  echo "ci_local: sandbox-exec unavailable; network is blocked only by the test guard and the missing gh/ssh/curl"
fi

# The environment of one job: nothing inherited except what is listed here.
ci_env() {  # prints env assignments for `env -i`
  printf '%s\n' "HOME=$T" "PATH=$CLEAN_PATH" "TMPDIR=$T/tmp" "LANG=C.UTF-8" "TZ=UTC" "CI=true" "GITHUB_ACTIONS=true" \
    "GOLDBOT_ALLOW_PLAINTEXT=1" "PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring" "GOLDBOT_NO_NETWORK=1" \
    "PIP_NO_INDEX=1" "NO_PROXY=*" "PRE_COMMIT_HOME=${PRE_COMMIT_HOME:-$HOME/.cache/pre-commit}" \
    "npm_config_cache=$HOME/.npm" "npm_config_offline=true" "VIRTUAL_ENV=$ROOT/.venv" "PYTHONDONTWRITEBYTECODE=1" "$@"
}
# job NAME ENV... -- CMD...: run CMD in the clean env (and the network box); log to $L/NAME.log; record the exit code
job() {
  local name=$1; shift; local extra=()
  while [ "$1" != "--" ]; do extra+=("$1"); shift; done; shift
  local assigns=(); while IFS= read -r l; do assigns+=("$l"); done < <(ci_env "${extra[@]}")
  ${box[@]+"${box[@]}"} env -i "${assigns[@]}" "$@" > "$L/$name.log" 2>&1
  echo $? > "$L/$name.status"
}
step() {  # step NAME ENV... -- CMD: like job, but appended to the lane log and never stops the lane (CI: `if: success() || failure()`)
  local lane=$1 name=$2; shift 2; local extra=()
  while [ "$1" != "--" ]; do extra+=("$1"); shift; done; shift
  local assigns=(); while IFS= read -r l; do assigns+=("$l"); done < <(ci_env "${extra[@]}")
  echo "=== $name" >> "$L/$lane.log"
  ${box[@]+"${box[@]}"} env -i "${assigns[@]}" "$@" >> "$L/$lane.log" 2>&1
  local rc=$?; echo "--- $name exit $rc" >> "$L/$lane.log"
  echo "$name $rc" >> "$L/$lane.steps"
  return 0
}
# status 0 only when the lane ran steps and every one exited 0 (before 2026-10-18 this was inverted: a lane with a
# failed step reported PASS and an all-green lane FAIL)
lane_done() {
  if [ -f "$L/$1.steps" ] && awk '$2 != 0 {bad = 1} END {exit bad ? 1 : 0}' "$L/$1.steps"; then echo 0; else echo 1; fi > "$L/$1.status"
}

# --- the lanes (commands copied from ci.yml; the matrix sizes come from it) ----------------------------------------
smoke_lane() {
  : > "$L/smoke.log"; rm -f "$L/smoke.steps"
  step smoke pre-commit -- pre-commit run --all-files --show-diff-on-failure --color never
  step smoke ruff -- ruff check goldbot tests scripts --output-format concise
  step smoke uv-lock -- uv lock --check --offline
  step smoke collect -- bash -c 'pytest --collect-only -q -p no:randomly -p no:cacheprovider | tail -20'
  lane_done smoke
}
lint_lane() {
  : > "$L/lint.log"; rm -f "$L/lint.steps"
  # CI's backend-lint job installs web/node_modules too (setup-web): gen:api needs openapi-typescript
  [ "$web" = 1 ] && step lint backend-lint-install -- npm --prefix web ci --offline
  step lint backend-typecheck -- mypy
  step lint api-contract -- bash -c 'python scripts/export_openapi.py && npm --prefix web run gen:api && git diff --exit-code -- web/openapi.json web/src/lib/schema.d.ts'
  lane_done lint
}
frontend_lane() {
  : > "$L/frontend.log"; rm -f "$L/frontend.steps"
  step frontend frontend-install -- npm --prefix web ci --offline
  step frontend frontend-lint -- npm --prefix web run lint
  step frontend frontend-typecheck -- npm --prefix web run typecheck
  step frontend frontend-unit -- npm --prefix web test -- --reporter=verbose
  lane_done frontend
}
e2e_lane() {  # one step per Playwright project, as the CI e2e matrix; sequential (both write web/e2e/.server*.json)
  : > "$L/e2e.log"; rm -f "$L/e2e.steps"
  chrome=${PW_CHROMIUM_PATH:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
  if [ ! -x "$chrome" ]; then echo "=== e2e SKIPPED: no Chrome at $chrome (set PW_CHROMIUM_PATH)" >> "$L/e2e.log"; echo 0 > "$L/e2e.status"; return; fi
  step e2e build -- npm --prefix web run build
  local p
  for p in ${E2E_PROJECTS//,/ }; do
    step e2e "e2e-$p" CI=1 PYTHON=python "PW_CHROMIUM_PATH=$chrome" -- bash -c "cd web && npx playwright test --project=$p"
  done
  lane_done e2e
}
skip_lane() { echo "SKIPPED: smoke failed (CI skips this job too)" > "$L/$1.log"; echo skipped > "$L/$1.status"; }

start=$(date +%s)
unit_lanes=(); integ_lanes=()
for ((i = 0; i < N_UNIT; i++)); do unit_lanes+=("unit$i"); done
for ((i = 0; i < N_INTEG; i++)); do integ_lanes+=("integration$i"); done
lanes=(smoke lint)
[ "$web" = 1 ] && lanes+=(frontend)
lanes+=("${unit_lanes[@]}" "${integ_lanes[@]}")
[ "$web" = 1 ] && [ "$e2e" = 1 ] && lanes+=(e2e)

# backend-lint -> frontend-checks -> (once smoke passed) e2e, beside smoke and then the shards
(
  lint_lane
  if [ "$web" = 1 ]; then
    frontend_lane
    if [ "$e2e" = 1 ]; then
      while [ ! -f "$L/smoke.status" ]; do sleep 2; done
      if [ "$(cat "$L/smoke.status")" = 0 ]; then e2e_lane; else skip_lane e2e; fi
    fi
  fi
) &
smoke_lane
if [ "$(cat "$L/smoke.status")" = 0 ]; then
  slot() { while [ "$(jobs -rp | wc -l)" -ge "$MAX_JOBS" ]; do sleep 1; done; }   # a free core before each lane starts
  for ((i = 0; i < N_INTEG; i++)); do   # the heavier lanes first
    slot
    dry=true; [ "$i" = "$DRY_SHARD" ] && dry='python scripts/dry_run.py 1 | tail -40'
    job "integration$i" "GOLDBOT_SHARD=$i/$N_INTEG" -- bash -c "set -o pipefail; pytest -m integration -q -n 1 --tb=short -p no:cacheprovider --junitxml='$L/integration$i.xml'; rc=\$?; $dry || rc=1; exit \$rc" &
  done
  for ((i = 0; i < N_UNIT; i++)); do
    slot
    job "unit$i" "GOLDBOT_SHARD=$i/$N_UNIT" -- pytest -m "not integration" -q -n 1 --tb=short -p no:cacheprovider --junitxml="$L/unit$i.xml" &
  done
else
  for lane in "${unit_lanes[@]}" "${integ_lanes[@]}"; do skip_lane "$lane"; done
fi
wait

# --- reruns -----------------------------------------------------------------------------------------------------
rerun_log=$L/reruns.log; : > "$rerun_log"
verdicts=()
for lane in "${unit_lanes[@]}" "${integ_lanes[@]}"; do
  case "$(cat "$L/$lane.status" 2>/dev/null)" in 0|skipped) continue ;; esac
  ids=$(grep -E '^(FAILED|ERROR) tests/' "$L/$lane.log" | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' | sort -u)
  if [ -z "$ids" ]; then verdicts+=("FAIL  $lane (no failing test id found: collection, crash, timeout or the dry run; see $L/$lane.log)"); continue; fi
  while IFS= read -r id; do
    pass=0; fail=0
    for seed in 11 222 3333 44444 555555; do
      assigns=(); while IFS= read -r l; do assigns+=("$l"); done < <(ci_env)
      if ${box[@]+"${box[@]}"} env -i "${assigns[@]}" pytest "$id" -q -n 0 --tb=short -p no:cacheprovider -p randomly --randomly-seed=$seed >> "$rerun_log" 2>&1
      then pass=$((pass + 1)); else fail=$((fail + 1)); fi
    done
    if [ "$fail" = 5 ]; then verdicts+=("FAIL  $id (failed 5/5 re-runs alone)")
    else verdicts+=("FLAKY $id ($fail/5 re-runs failed alone, $pass passed; the original run failed in $lane)"); fi
  done <<< "$ids"
done

# --- report -----------------------------------------------------------------------------------------------------
status=0
for lane in "${lanes[@]}"; do
  code=$(cat "$L/$lane.status" 2>/dev/null || echo missing)
  summary=$(grep -E ' passed| failed|Success|error|SKIPPED' "$L/$lane.log" 2>/dev/null | tail -1)
  verdict=FAIL; [ "$code" = 0 ] && verdict=PASS; [ "$code" = skipped ] && verdict=SKIP
  printf '%-14s %s  %s\n' "$lane" "$verdict" "$summary"
  [ "$code" = 0 ] || status=1
  if [ -f "$L/$lane.steps" ]; then awk '$2 != 0 {print "               failed step: " $1}' "$L/$lane.steps"; fi
done
for ((n = 0; n < ${#verdicts[@]}; n++)); do echo "${verdicts[$n]}"; done
slowest=$(for f in "$L"/integration*.xml; do [ -f "$f" ] && .venv/bin/python scripts/ci_summary.py summary --title "$(basename "$f" .xml)" "$f" --top 1 | grep -E 'in [0-9]+ s|^\| [0-9]'; done)
[ -n "$slowest" ] && printf 'integration shards (tests, wall, slowest test):\n%s\n' "$slowest"
echo "total $(( $(date +%s) - start )) s, logs in $L (re-runs: $rerun_log)"
exit $status
