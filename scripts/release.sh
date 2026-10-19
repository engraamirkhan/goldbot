#!/usr/bin/env bash
# release.sh: integration branch -> PR -> main -> brain deploy, in about 10 minutes instead of 25.
#   scripts/release.sh "<PR title>" <body-file>      (run from the repo root on claude/gifted-goldberg-lcb7pl)
# 1. pushes the branch and opens (or reuses) the PR with auto-merge;
# 2. runs scripts/ci_local.sh (clean worktree of HEAD) AT THE SAME TIME as GitHub's PR checks; GitHub is the gate
#    (a local failure before the merge still stops the release; after it, the local run is advisory);
# 3. after the merge, deploys at once when main's tree equals the PR head's tree that passed CI (the same code: a
#    second full run on main tells nothing new); if main moved in between, it waits for main CI on the merge commit;
# 4. deploys the FULL main SHA on the brain and checks deploys.jsonl says "deployed". Right after the push the brain
#    pre-builds the PR head (`goldbot-deploy --stage`, uv cache + dashboard, no service touched) while CI runs; the deploy
#    reuses it when the dashboard sources are identical (the merge commit has the PR head's tree), else builds as before.
#    The engine is disabled around the deploy only when the installed script has no --stage (first release); the new
#    script stops every service just before the checkout and starts supervisor, engine, rest (engine down: seconds,
#    printed) and reports once every service has been stable for 30 s with fresh heartbeats; a watcher keeps checking
#    restarts until 90 s after the switch and rolls back on one (look at deploys.jsonl again after that).
# Never deploys a tree that no green CI run tested.
set -euo pipefail
BRANCH=claude/gifted-goldberg-lcb7pl
TITLE=${1:?usage: release.sh "<PR title>" <body-file>}
BODY=${2:?usage: release.sh "<PR title>" <body-file>}
LOG=${RELEASE_LOG:-$HOME/tmp/release}
mkdir -p "$LOG"
say() { echo "$(date -u +%H:%M:%S) $*"; }
die() { say "STOP: $*"; exit 1; }

[ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] || die "not on $BRANCH"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "uncommitted changes"
git fetch -q origin
git merge-base --is-ancestor origin/main HEAD || die "origin/main is not merged into $BRANCH (merge it first)"
# seconds-cheap checks before anything is pushed (mypy is incremental): a type error costs a 2-min GitHub round trip
uv run -q --extra dev ruff check goldbot tests scripts || die "ruff"
uv run -q --extra dev mypy >/dev/null || { uv run -q --extra dev mypy | tail -5; die "mypy"; }
HEAD_SHA=$(git rev-parse HEAD)
HEAD_TREE=$(git rev-parse "HEAD^{tree}")

say "push $HEAD_SHA"
git push -q origin "$BRANCH"
# pre-build on the brain while CI runs (nothing is touched there; an old goldbot-deploy without --stage just says usage)
SSH_BRAIN=(ssh -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes goldbot-brain)
"${SSH_BRAIN[@]}" "sudo /usr/local/sbin/goldbot-deploy --stage $HEAD_SHA" > "$LOG/stage.log" 2>&1 &
STAGE=$!
if ! gh pr view "$BRANCH" --json state -q .state 2>/dev/null | grep -q OPEN; then
  gh pr create --base main --head "$BRANCH" --title "$TITLE" --body-file "$BODY" >/dev/null
fi
gh pr merge --auto --merge "$BRANCH" >/dev/null
PR=$(gh pr view "$BRANCH" --json number -q .number)
say "PR #$PR open, auto-merge on; local CI and GitHub checks run in parallel"

# local CI parity run in a clean worktree of exactly HEAD
WT=$HOME/tmp/ci-rc
git worktree remove -f "$WT" 2>/dev/null || true
git worktree add -f --detach "$WT" "$HEAD_SHA" >/dev/null
NODE22=$(ls -d "$HOME"/.nvm/versions/node/v22*/bin 2>/dev/null | tail -1)
( cd "$WT" && uv sync -q --frozen --extra dev \
    && export PATH="$NODE22:$PATH" \
    && (cd web && npm ci --silent >/dev/null 2>&1) \
    && bash scripts/ci_local.sh ) > "$LOG/ci_local.log" 2>&1 &
LOCAL=$!

# wait for whichever finishes; a failure on either side stops the release
local_done=0; pr_state=""
while :; do
  if [ $local_done = 0 ] && ! kill -0 $LOCAL 2>/dev/null; then
    if wait $LOCAL && ! grep -qE "^(smoke|lint|frontend|unit[0-9]|integration[0-9]|e2e) +FAIL" "$LOG/ci_local.log"; then
      local_done=1; say "local CI PASS"
    else
      grep -E "^FAIL " "$LOG/ci_local.log" | head -5
      gh pr merge --disable-auto "$BRANCH" >/dev/null 2>&1 || true
      die "local CI failed (log $LOG/ci_local.log); auto-merge disabled"
    fi
  fi
  pr_state=$(gh pr view "$BRANCH" --json state,statusCheckRollup \
    -q '.state + " " + ([.statusCheckRollup[] | select(.conclusion=="FAILURE" or .conclusion=="CANCELLED")] | length | tostring)')
  case "$pr_state" in
    "OPEN "[1-9]*) kill $LOCAL 2>/dev/null || true; die "GitHub PR checks failed (gh pr checks $PR)";;
    MERGED*) break;;   # GitHub's checks are the gate; the local run carries on and reports (see below)
  esac
  sleep 20
done
say "PR #$PR merged"
[ $local_done = 1 ] || say "local CI still running (advisory; log $LOG/ci_local.log): the deploy does not wait for it"

git fetch -q origin
MAIN=$(git rev-parse origin/main)
if [ "$(git rev-parse "$MAIN^{tree}")" = "$HEAD_TREE" ]; then
  say "main $MAIN has the tested tree (PR head $HEAD_SHA, CI green): no second run needed"
else
  say "main moved since the PR checks: waiting for main CI on $MAIN"
  for _ in $(seq 30); do
    RID=$(gh run list --branch main --workflow ci --limit 5 --json databaseId,headSha \
          -q ".[] | select(.headSha==\"$MAIN\") | .databaseId" | head -1)
    [ -n "$RID" ] && break; sleep 10
  done
  [ -n "${RID:-}" ] || die "no CI run for $MAIN"
  gh run watch "$RID" --exit-status >/dev/null 2>&1 || die "main CI not green on $MAIN"
fi

# exit 2 = the installed goldbot-deploy predates --stage (usage): it restarts services only after the install, so
# the engine must be paused around the whole deploy as before. The new script stops services itself, just in time.
STOP_ENGINE=0
wait "$STAGE" && say "brain stage: $(tail -1 "$LOG/stage.log" | cut -c1-120)" || {
  rc=$?
  [ "$rc" != 2 ] || STOP_ENGINE=1
  say "brain stage did not finish (rc $rc, $(tail -1 "$LOG/stage.log" | cut -c1-120)): the deploy builds as before"
}
PAUSE=""; RESUME=""
if [ "$STOP_ENGINE" = 1 ]; then
  say "old goldbot-deploy on the brain: engine paused around the deploy"
  PAUSE="sudo systemctl disable --now goldbot-engine@icm-demo"
  RESUME="sudo systemctl enable --now goldbot-engine@icm-demo >/dev/null 2>&1; sleep 15"
fi
say "deploy $MAIN to the brain"
OUT=$("${SSH_BRAIN[@]}" "
  sudo systemctl reset-failed 'goldbot-deploy-manual-*' 2>/dev/null
  $PAUSE
  sudo systemd-run --wait --unit goldbot-deploy-manual-\$(date +%s) /usr/local/sbin/goldbot-deploy $MAIN >/dev/null 2>&1
  sudo tail -1 /var/lib/goldbot-deploy/deploys.jsonl
  $RESUME
  echo engine=\$(systemctl is-active goldbot-engine@icm-demo)")
echo "$OUT" | cut -c1-220
echo "$OUT" | grep -q "\"result\":\"deployed\".*\"to\":\"$MAIN\"" || die "deploy record does not say deployed $MAIN"
echo "$OUT" | grep -q "engine=active" || die "engine not active after the deploy"
say "DEPLOYED $MAIN"
