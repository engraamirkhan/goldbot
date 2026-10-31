#!/usr/bin/env bash
# Create a lane worktree from a fresh origin/main (ROADMAP 5a FR-4), or refuse while the disk or the tree count is
# at its limit, printing the prune command instead.
#   scripts/new_lane.sh <name>        -> .claude/worktrees/<name> on a new branch worktree-<name>, at origin/main
# Refuses (exit 3) when LANE_MAX_WORKTREES (default 40) worktrees already exist, the main checkout included, or the
# volume holding the repository has less than LANE_MIN_FREE_GB (default 100) GB free. Exit 2: bad name or usage.
# Then runs the new tree's scripts/worktree_env.sh (exit 4 when it fails; a notice when origin/main has none yet).
# Runs on the repository of the current directory; prints the new worktree's path as its last line.
set -euo pipefail

MAX=${LANE_MAX_WORKTREES:-40}
MIN_GB=${LANE_MIN_FREE_GB:-100}
die() { echo "new_lane: $*" >&2; exit "${2:-3}"; }

[ $# -eq 1 ] || die "usage: scripts/new_lane.sh <name>" 2
name=$1
case "$name" in ''|-*|*[!A-Za-z0-9._-]*|*..*) die "lane name must be letters, digits, '.', '_' or '-': $name" 2 ;; esac
case "$MAX$MIN_GB" in *[!0-9]*) die "LANE_MAX_WORKTREES and LANE_MIN_FREE_GB take whole numbers" 2 ;; esac

common=$(git rev-parse --path-format=absolute --git-common-dir)
root=$(dirname "$common")   # the main checkout: lanes live under it, wherever this is run from
dest="$root/.claude/worktrees/$name"
branch="worktree-$name"
prune="(cd $root && scripts/prune_worktrees.sh)   # dry run; add --apply to remove merged trees"

n=0
while IFS= read -r line; do
  case "$line" in "worktree "*) n=$((n + 1)) ;; esac
done < <(git worktree list --porcelain)
[ "$n" -lt "$MAX" ] || die "$n worktrees exist (limit $MAX): prune merged lanes first: $prune"

df_bin=$(command -v df || true)
[ -n "$df_bin" ] || for c in /bin/df /usr/bin/df; do [ -x "$c" ] && df_bin=$c && break; done
[ -n "$df_bin" ] || die "df not found: cannot check free disk" 3
free_kb=$("$df_bin" -Pk "$root" | awk 'NR == 2 {print $4}')
free_gb=$((free_kb / 1048576))
[ "$free_gb" -ge "$MIN_GB" ] || die "only $free_gb GB free on the repository's volume (minimum $MIN_GB): $prune"

[ ! -e "$dest" ] || die "$dest already exists" 2
git rev-parse --verify -q "refs/heads/$branch" >/dev/null && die "branch $branch already exists" 2
if git remote get-url origin >/dev/null 2>&1; then
  git fetch -q origin main || die "fetching origin/main failed"
fi
base=$(git rev-parse --verify -q "origin/main^{commit}") || die "no origin/main"
mkdir -p "$root/.claude/worktrees"
git worktree add -q -b "$branch" "$dest" "$base"
echo "lane $name: branch $branch at origin/main $base ($((n + 1)) of $MAX worktrees, $free_gb GB free)"
# the lane's own .venv on the locked Python (FR-1), by the new tree's copy of the script
if [ -x "$dest/scripts/worktree_env.sh" ]; then
  (cd "$dest" && scripts/worktree_env.sh) || die "scripts/worktree_env.sh failed in $dest: fix it and rerun it there" 4
else
  echo "new_lane: notice: no scripts/worktree_env.sh at origin/main yet; build the venv by hand" >&2
fi
echo "$dest"
