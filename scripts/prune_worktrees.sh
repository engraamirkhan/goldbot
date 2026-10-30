#!/usr/bin/env bash
# Remove lane worktrees whose work is merged (docs/process/tech-debt.md TD-8; ROADMAP 5a FR-4).
#   scripts/prune_worktrees.sh [--apply] [--into REF] [--min-age-hours N] [--include-empty] [--no-sizes]
# Dry run by default: lists what it would remove with its size, why every other worktree is kept, and a summary.
# A worktree is removed only when all of these hold: it is not the main checkout or the current one; it is on a
# branch (not detached) merged into REF (default origin/main, fetched first), so it holds no commit missing from
# REF; it has commits of its own (a lane that never committed, e.g. a read-only reviewer, is kept unless
# --include-empty or N is 0); it is not locked; neither its HEAD, index and reflog nor any file in it changed in the
# last N hours (default 24); and it holds no modified tracked file and no untracked or ignored file outside the
# regenerable build and test leftovers (data/, state/, *.egg-info, .venv, node_modules, __pycache__, .pytest_cache,
# .mypy_cache, .ruff_cache, web/dist, web/test-results, web/playwright-report, web/coverage, .coverage,
# .testmondata, *.tsbuildinfo, data_dryrun/, the e2e servers' .server*.json): `git worktree remove` would delete an ignored file such as .env silently.
# Its branch is deleted with it, as is any merged worktree-agent-* branch without a worktree; --apply ends with
# `git worktree prune`. Runs on the repository of the current directory. Env: INTEGRATION_BRANCH overrides REF.
set -euo pipefail

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

APPLY=0
INTO=${INTEGRATION_BRANCH:-origin/main}
AGE_H=24
EMPTY=0
SIZES=1
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --into) [ $# -ge 2 ] || usage; INTO=$2; shift ;;
    --min-age-hours) [ $# -ge 2 ] || usage; AGE_H=$2; shift ;;
    --include-empty) EMPTY=1 ;;
    --no-sizes) SIZES=0 ;;
    *) usage ;;
  esac
  shift
done
case "$AGE_H" in ''|*[!0-9]*) echo "--min-age-hours takes whole hours" >&2; exit 2 ;; esac
[ "$AGE_H" -eq 0 ] && EMPTY=1

case "$INTO" in
  origin/*)
    if git remote get-url origin >/dev/null 2>&1; then
      git fetch -q origin "${INTO#origin/}" 2>/dev/null || echo "warning: fetching $INTO failed; using the local copy" >&2
    fi ;;
esac
target=$(git rev-parse --verify -q "$INTO^{commit}") || { echo "no such branch or ref $INTO" >&2; exit 2; }
into_branch=${INTO#origin/}
here=$(git rev-parse --show-toplevel)
# Commits that were themselves a tip of REF: a lane whose tip is one of them has no commits of its own.
# Lists are newline-framed strings matched with `case`, never `| grep -q` (SIGPIPE under pipefail).
first_parent=$'\n'"$(git rev-list --first-parent "$target")"$'\n'
checked_out=$'\n'
kept_reasons=""
n_wt=0
n_kept=0
n_br=0
kb_rm=0
kb_kept=0

size_kb() {  # disk use of a tree in KiB (0 with --no-sizes)
  local kb=0
  if [ "$SIZES" -eq 1 ]; then kb=$(du -sk "$1" 2>/dev/null | awk '{print $1}') || true; fi
  echo "${kb:-0}"
}
human() { awk -v k="$1" 'BEGIN { if (k >= 1048576) printf "%.1fG", k/1048576; else if (k >= 1024) printf "%.0fM", k/1024; else printf "%dK", k }'; }

keep() {  # $1 path, $2 reason; the summary groups reasons by their text before the first ':'
  local kb=0
  case "$2" in "main checkout"|"current worktree"|directory\ missing*) ;; *) kb=$(size_kb "$1") ;; esac
  kb_kept=$((kb_kept + kb)); n_kept=$((n_kept + 1))
  kept_reasons="$kept_reasons${2%%:*}"$'\n'
  printf 'keep    %s  %s  (%s)\n' "$(human "$kb")" "$1" "$2"
}

# True when the worktree's HEAD, index or reflog, or any file in its tree, changed in the last AGE_H hours.
recent() {
  local f gitdir=$1 path=$2 mins=$((AGE_H * 60))
  [ "$AGE_H" -eq 0 ] && return 1
  for f in "$gitdir/HEAD" "$gitdir/index" "$gitdir/logs/HEAD"; do
    [ -e "$f" ] || continue
    [ -n "$(find "$f" -maxdepth 0 -mmin "-$mins")" ] && return 0
  done
  # a lane at work writes files (sources, test caches, builds) before it commits; -quit stops at the first one
  [ -n "$(find "$path" \( -name node_modules -o -name .venv -o -name .git \) -prune -o -type f -mmin "-$mins" -print -quit 2>/dev/null)" ]
}

leftover() {  # $1 a repo-relative path from `git status`: true for a regenerable build or test leftover
  case "/${1%/}/" in
    */.venv/*|*/node_modules/*|*/__pycache__/*|*/.pytest_cache/*|*/.mypy_cache/*|*/.ruff_cache/*) return 0 ;;
    /data/*|/state/*|*.egg-info/*|/web/dist/*|/web/test-results/*|/web/playwright-report/*|/web/coverage/*) return 0 ;;
    /.coverage/|/.testmondata/|*.tsbuildinfo/) return 0 ;;
    # scripts/dry_run.py's store, and the e2e servers' info files (a throwaway state dir's port and setup code)
    /data_dryrun/*|/web/e2e/.server.json/|/web/e2e/.server-auth.json/|/web/shots/.server.json/) return 0 ;;
  esac
  return 1
}

# Why a worktree's files forbid removal ("" when nothing would be lost). --no-optional-locks: a dry run must not
# refresh the index, or the tree would look recently used.
unsafe_files() {
  local line p dirty="" ignored=""
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    p=${line:3}
    case "$line" in
      "!! "*) leftover "$p" || ignored="$ignored${ignored:+, }$p" ;;
      "?? "*) leftover "$p" || dirty="$dirty${dirty:+, }$p" ;;
      *) dirty="$dirty${dirty:+, }$p" ;;
    esac
  # matching + all: each path as it matched an ignore rule (pkg/__pycache__/), never a collapsed parent (pkg/)
  done < <(git --no-optional-locks -C "$1" status --porcelain --ignored=matching --untracked-files=all)
  if [ -n "$dirty" ]; then echo "uncommitted or untracked files: ${dirty:0:160}"
  elif [ -n "$ignored" ]; then echo "ignored files: ${ignored:0:160}"
  fi
}

remove_tree() {  # $1 path, $2 branch: plain remove; --force only for a tree still found to hold nothing but leftovers
  local why
  if ! git worktree remove "$1" 2>/dev/null; then
    why=$(unsafe_files "$1")
    if [ -n "$why" ]; then echo "  not removed: $1 changed meanwhile ($why)" >&2; return 0; fi
    git worktree remove --force "$1"
  fi
  git branch -q -D "$2"    # merged into REF, checked above
}

# One `git worktree list --porcelain` record: $1 index, $2 path, $3 branch ("" when detached), $4 locked, $5 prunable.
decide() {
  local idx=$1 path=$2 branch=$3 locked=$4 prunable=$5 gitdir tip why kb
  if [ "$idx" -eq 1 ]; then keep "$path" "main checkout"; return; fi
  if [ "$path" = "$here" ]; then keep "$path" "current worktree"; return; fi
  if [ -n "$prunable" ]; then keep "$path" "directory missing; \`git worktree prune\` clears it"; return; fi
  if [ -z "$branch" ]; then keep "$path" "detached HEAD"; return; fi
  if [ "$branch" = "$INTO" ] || [ "$branch" = "$into_branch" ]; then keep "$path" "is $INTO"; return; fi
  if [ -n "$locked" ]; then keep "$path" "locked"; return; fi
  if ! git merge-base --is-ancestor "refs/heads/$branch" "$target"; then
    keep "$path" "not merged into $INTO: $branch has commits missing from it"; return
  fi
  tip=$(git rev-parse "refs/heads/$branch")
  case "$first_parent" in
    *$'\n'"$tip"$'\n'*)
      if [ "$EMPTY" -eq 0 ]; then keep "$path" "no commits of its own; --include-empty to remove"; return; fi ;;
  esac
  gitdir=$(git -C "$path" rev-parse --absolute-git-dir)
  if recent "$gitdir" "$path"; then keep "$path" "used in the last $AGE_H h"; return; fi
  why=$(unsafe_files "$path")
  if [ -n "$why" ]; then keep "$path" "$why"; return; fi
  kb=$(size_kb "$path")
  n_wt=$((n_wt + 1)); kb_rm=$((kb_rm + kb))
  printf 'remove  %s  %s  (%s, merged)\n' "$(human "$kb")" "$path" "$branch"
  if [ "$APPLY" -eq 1 ]; then remove_tree "$path" "$branch"; fi
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
  case "$checked_out" in *$'\n'"$b"$'\n'*) continue ;; esac
  git merge-base --is-ancestor "refs/heads/$b" "$target" || continue
  n_br=$((n_br + 1))
  printf 'delete branch %s  (merged, no worktree)\n' "$b"
  if [ "$APPLY" -eq 1 ]; then git branch -q -D "$b"; fi
done < <(git for-each-ref --format='%(refname:short)' 'refs/heads/worktree-agent-*')

echo "kept $n_kept worktree(s), $(human "$kb_kept"), by reason:"
printf '%s' "$kept_reasons" | sort | uniq -c | sort -rn | sed 's/^/  /'
if [ "$APPLY" -eq 1 ]; then
  git worktree prune
  echo "removed $n_wt worktree(s) ($(human "$kb_rm")) and $n_br other branch(es)"
else
  echo "dry run: $n_wt worktree(s) ($(human "$kb_rm")) and $n_br other branch(es) would go; run with --apply to remove them"
fi
