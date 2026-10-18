#!/usr/bin/env bash
# Reproduce .github/workflows/ci.yml locally, as close to a GitHub runner as a Mac allows. Local gates passed but CI
# failed because a test called the real `gh` (Mac has a token, CI none) and because a test was timing-flaky under load.
# This runs the same job commands in a hostile, CI-like environment:
#   - `env -i`, HOME = a fresh temp dir (no keychain, no ~/.config/gh), PATH = shim dir of symlinks to only the tools
#     CI has (python venv, node, git, uv, coreutils): NO gh, ssh or curl;
#   - exactly the env CI sets (GOLDBOT_ALLOW_PLAINTEXT=1, CI=true, TZ=UTC), PYTHON_KEYRING_BACKEND=fail, no GH_TOKEN,
#     no proxies, GOLDBOT_NO_NETWORK=1 (tests/conftest.py also fails any test that runs gh/ssh/curl or leaves loopback);
#   - outbound network blocked at the kernel with macOS sandbox-exec (loopback stays open) when it is available;
#   - the jobs side by side: backend-lint (pre-commit, ruff, mypy, api-contract) then frontend, 4 unit shards
#     (GOLDBOT_SHARD=i/4, `pytest -m "not integration" -q --tb=short`, random order like CI) and integration + dry run.
#     Six lanes, each pytest on one xdist worker: at most 6 workers in total.
# Every failing pytest test is re-run alone 5 times with different `-p randomly` seeds: it is reported FLAKY when any
# re-run passes and FAIL when all fail. Exit status is non-zero on any FAIL or FLAKY.
# Usage: scripts/ci_local.sh [--no-web] [--no-e2e]   Logs: ${CI_LOCAL_LOG:-~/tmp/ci_local/<worktree>}
# Not reproduced: the pip install steps (the repo's .venv is used), Linux itself, GitHub's runner speed. `npm ci` runs
# --offline from the local npm cache.
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
L=${CI_LOCAL_LOG:-$HOME/tmp/ci_local/$(basename "$ROOT")}
mkdir -p "$L" "$HOME/tmp"; rm -f "$L"/*.log "$L"/*.status
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
lane_done() { [ -f "$L/$1.steps" ] && ! awk '$2 != 0 {bad=1} END {exit bad}' "$L/$1.steps"; echo $? > "$L/$1.status"; }

# --- the lanes (commands copied from ci.yml) -----------------------------------------------------------------------
lint_lane() {
  : > "$L/lint.log"; rm -f "$L/lint.steps"
  step lint pre-commit -- pre-commit run --all-files --show-diff-on-failure --color never
  step lint backend-lint -- ruff check goldbot tests scripts --output-format concise
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
  if [ "$e2e" = 1 ]; then
    chrome=${PW_CHROMIUM_PATH:-"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"}
    if [ -x "$chrome" ]; then
      step frontend e2e CI=1 PYTHON=python "PW_CHROMIUM_PATH=$chrome" -- bash -c 'cd web && npm run build && npm run test:e2e'
    else
      echo "=== e2e SKIPPED: no Chrome at $chrome (set PW_CHROMIUM_PATH)" >> "$L/frontend.log"
    fi
  else
    echo "=== e2e SKIPPED (--no-e2e)" >> "$L/frontend.log"
  fi
  lane_done frontend
}

start=$(date +%s)
lanes=(unit0 unit1 unit2 unit3 integration lint)
for i in 0 1 2 3; do
  job "unit$i" "GOLDBOT_SHARD=$i/4" -- pytest -m "not integration" -q -n 1 --tb=short -p no:cacheprovider &
done
job integration -- bash -c 'pytest -m integration -q -n 1 --tb=short -p no:cacheprovider && python scripts/dry_run.py 1 | tail -40' &
# the sixth worker: lint, then the frontend (kept sequential so six lanes is the ceiling)
( lint_lane; [ "$web" = 1 ] && frontend_lane ) &
[ "$web" = 1 ] && lanes+=(frontend)
wait

# --- reruns -----------------------------------------------------------------------------------------------------
rerun_log=$L/reruns.log; : > "$rerun_log"
verdicts=()
for lane in unit0 unit1 unit2 unit3 integration; do
  [ "$(cat "$L/$lane.status" 2>/dev/null)" = 0 ] && continue
  ids=$(grep -E '^(FAILED|ERROR) tests/' "$L/$lane.log" | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' | sort -u)
  if [ -z "$ids" ]; then verdicts+=("FAIL  $lane (no failing test id found: collection, crash or timeout; see $L/$lane.log)"); continue; fi
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
  summary=$(grep -E ' passed| failed|Success|error' "$L/$lane.log" | tail -1)
  printf '%-12s %s  %s\n' "$lane" "$([ "$code" = 0 ] && echo PASS || echo FAIL)" "$summary"
  [ "$code" = 0 ] || status=1
  if [ -f "$L/$lane.steps" ]; then awk '$2 != 0 {print "             failed step: " $1}' "$L/$lane.steps"; fi
done
for ((n = 0; n < ${#verdicts[@]}; n++)); do echo "${verdicts[$n]}"; done
echo "total $(( $(date +%s) - start )) s, logs in $L (re-runs: $rerun_log)"
exit $status
