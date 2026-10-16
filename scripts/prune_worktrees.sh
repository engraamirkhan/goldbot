#!/usr/bin/env bash
# Remove lane worktrees whose work is merged (docs/process/tech-debt.md TD-8; throughput plan lever 4d).
#   scripts/prune_worktrees.sh [--apply] [--into BRANCH] [--min-age-hours N]
# Dry run by default: lists what it would remove, and why every other worktree is kept. A worktree is removed only
# when all of these hold: it is not the main checkout or the current one; it is on a branch (not detached) other
# than BRANCH, and that branch is merged into BRANCH; it is not locked; it is clean (no modified or untracked
# files); and its HEAD, index and reflog are older than N hours (default 24: a lane just reset to the tip looks
# merged and clean). Its branch is deleted with it, as is any merged worktree-agent-* branch without a worktree.
# Runs on the repository of the current directory. Env: INTEGRATION_BRANCH (default claude/gifted-goldberg-lcb7pl).
set -euo pipefail

usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

APPLY=0
INTO=${INTEGRATION_BRANCH:-claude/gifted-goldberg-lcb7pl}
AGE_H=24
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --into) [ $# -ge 2 ] || usage; INTO=$2; shift ;;
    --min-age-hours) [ $# -ge 2 ] || usage; AGE_H=$2; shift ;;
    *) usage ;;
  esac
  shift
done
case "$AGE_H" in ''|*[!0-9]*) echo "--min-age-hours takes whole hours" >&2; exit 2 ;; esac

target=$(git rev-parse --verify -q "refs/heads/$INTO^{commit}") || { echo "no local branch $INTO" >&2; exit 2; }
here=$(git rev-parse --show-toplevel)
n_wt=0
n_br=0
checked_out=""

keep() { printf 'keep    %s  (%s)\n' "$1" "$2"; }

# True when the worktree's HEAD, index or reflog changed in the last AGE_H hours.
recent() {
  local f
  [ "$AGE_H" -eq 0 ] && return 1
  for f in "$1/HEAD" "$1/index" "$1/logs/HEAD"; do
    [ -e "$f" ] || continue
    [ -n "$(find "$f" -maxdepth 0 -mmin "-$((AGE_H * 60))")" ] && return 0
  done
  return 1
}

# One `git worktree list --porcelain` record: $1 index, $2 path, $3 branch ("" when detached), $4 locked, $5 prunable.
decide() {
  local idx=$1 path=$2 branch=$3 locked=$4 prunable=$5 gitdir
  if [ "$idx" -eq 1 ]; then keep "$path" "main checkout"; return; fi
  if [ "$path" = "$here" ]; then keep "$path" "current worktree"; return; fi
  if [ -n "$prunable" ]; then keep "$path" "directory missing; \`git worktree prune\` clears it"; return; fi
  if [ -z "$branch" ]; then keep "$path" "detached HEAD"; return; fi
  if [ "$branch" = "$INTO" ]; then keep "$path" "is $INTO"; return; fi
  if [ -n "$locked" ]; then keep "$path" "locked"; return; fi
  if ! git merge-base --is-ancestor "refs/heads/$branch" "$target"; then
    keep "$path" "$branch not merged into $INTO"; return
  fi
  gitdir=$(git -C "$path" rev-parse --absolute-git-dir)
  if recent "$gitdir"; then keep "$path" "used in the last $AGE_H h"; return; fi
  # --no-optional-locks: a dry run must not refresh the index, or the tree would look recently used
  if [ -n "$(git --no-optional-locks -C "$path" status --porcelain)" ]; then
    keep "$path" "uncommitted or untracked files"; return
  fi
  n_wt=$((n_wt + 1))
  printf 'remove  %s  (%s, merged)\n' "$path" "$branch"
  if [ "$APPLY" -eq 1 ]; then
    git worktree remove "$path"   # no --force: git refuses a dirty tree too
    git branch -q -D "$branch"    # merged into $INTO, checked above
  fi
}

idx=0 path="" branch="" locked="" prunable=""
while IFS= read -r line; do
  case "$line" in
    "worktree "*) path=${line#worktree } ;;
    "branch refs/heads/"*) branch=${line#branch refs/heads/}; checked_out="$checked_out$branch"$'\n' ;;
    locked|"locked "*) locked=1 ;;
    "prunable "*) prunable=1 ;;
    "")
      if [ -n "$path" ]; then
        idx=$((idx + 1))
        decide "$idx" "$path" "$branch" "$locked" "$prunable"
      fi
      path="" branch="" locked="" prunable="" ;;
  esac
done < <(git worktree list --porcelain; echo)

while IFS= read -r b; do
  [ -n "$b" ] || continue
  printf '%s' "$checked_out" | grep -Fxq -- "$b" && continue
  git merge-base --is-ancestor "refs/heads/$b" "$target" || continue
  n_br=$((n_br + 1))
  printf 'delete branch %s  (merged, no worktree)\n' "$b"
  [ "$APPLY" -eq 1 ] && git branch -q -D "$b"
done < <(git for-each-ref --format='%(refname:short)' 'refs/heads/worktree-agent-*')

if [ "$APPLY" -eq 1 ]; then
  git worktree prune
  echo "removed $n_wt worktree(s) and $n_br other branch(es)"
else
  echo "dry run: $n_wt worktree(s) and $n_br other branch(es) would go; run with --apply to remove them"
fi
