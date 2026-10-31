# shellcheck shell=bash disable=SC2034
# Shared by scripts/*.sh (source it: `. "$(dirname "$0")/lib/common.sh"`). FR-6 / FR-1, ADR 0060.
#   GOLDBOT_BASE            the ref every lane is based on and compared with: origin/main. INTEGRATION_BRANCH
#                           overrides it for the rare script run against another base (it used to default to a
#                           dead integration branch, so lane_test diffed against an 11-day-old base).
#   goldbot_check_env [py]  run scripts/check_env.py with py (default: python on PATH, the activated venv's); the
#                           calling script exits 2 when the interpreter or a package differs from uv.lock.

GOLDBOT_BASE=${INTEGRATION_BRANCH:-origin/main}

goldbot_check_env() {
  local py=${1:-python} here
  here=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
  "$py" "$here/scripts/check_env.py" || exit 2
}

# --- release.sh rules (FR-7, FR-8, FR-10) -------------------------------------------------------------------------------
# goldbot_changed_lines [base]         insertions + deletions of HEAD against the merge-base with base (default GOLDBOT_BASE)
# goldbot_pr_size_check HOTFIX [base]  refuse (return 1) a PR under RELEASE_MIN_LINES (default 50) changed lines unless
#                                      HOTFIX is 1: ADR 0060 decision 6, small fixes ride the next lane PR in the same area.
# goldbot_timing_append FILE k=v...    one JSON line: all-digit values are numbers, the rest strings (no quotes inside).
goldbot_changed_lines() {
  git diff --numstat "${1:-$GOLDBOT_BASE}...HEAD" | awk '{ if ($1 != "-") n += $1 + $2 } END { print n + 0 }'
}

goldbot_pr_size_check() {
  local hotfix=${1:?hotfix flag 0|1} min=${RELEASE_MIN_LINES:-50} n
  n=$(goldbot_changed_lines "${2:-$GOLDBOT_BASE}")
  if [ "$n" -lt "$min" ] && [ "$hotfix" != 1 ]; then
    echo "STOP: this PR changes $n lines, under the $min-line minimum (ADR 0060: one PR per lane, small fixes ride the" \
         "next lane PR in the same area). A money-path emergency (exit, RiskGate, engine down, stuck deploy) ships" \
         "alone with: scripts/release.sh --hotfix ..." >&2
    return 1
  fi
}

goldbot_timing_append() {
  local file=$1 kv key val sep="" line="{"
  shift
  for kv in "$@"; do
    key=${kv%%=*}; val=${kv#*=}
    case $val in
      ''|*[!0-9]*) line+="$sep\"$key\": \"$val\"" ;;
      *) line+="$sep\"$key\": $val" ;;
    esac
    sep=", "
  done
  mkdir -p "$(dirname "$file")"
  echo "$line}" >> "$file"
}
