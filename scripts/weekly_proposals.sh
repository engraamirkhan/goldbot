#!/usr/bin/env bash
# The research director's inbox of the brain's weekly roll-up proposals, from the Mac (BACKLOG 216; the brain's
# research_director job is off without an Anthropic key). Runs goldbot.research.weekly_inbox on the brain over ssh as
# the goldbot user (so state files keep their owner):
#   scripts/weekly_proposals.sh                              list unread roll-ups with proposals (reads only)
#   scripts/weekly_proposals.sh --mark weekly-<day>.json "DECISION"
#                                                            mark it read with the decision (first record stands)
# The roll-up name is checked here before anything leaves the Mac; the decision travels on stdin, never in the remote
# command line. Marking runs nothing: a proposal to test still needs its own pre-registration. Needs ssh to
# goldbot-brain (~/.ssh/goldbot.conf, sudo without a password) and the module deployed there.
set -euo pipefail
BRAIN_DIR=${BRAIN_DIR:-/opt/goldbot}
SSH=(ssh -F "$HOME/.ssh/goldbot.conf" -o BatchMode=yes goldbot-brain)
RUN="cd '$BRAIN_DIR' && sudo -n -u goldbot .venv/bin/python -m goldbot.research.weekly_inbox"

usage() { sed -n '5,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

case "${1:-}" in
  "")
    [ $# -eq 0 ] || usage
    "${SSH[@]}" "$RUN list --state-dir '$BRAIN_DIR/state'"
    ;;
  --mark)
    [ $# -eq 3 ] || usage
    name=$2
    decision=$3
    if ! [[ $name =~ ^weekly-[0-9]{4}-[0-9]{2}-[0-9]{2}\.json$ ]]; then
      echo "weekly_proposals: not a roll-up name: '$name' (weekly-YYYY-MM-DD.json)" >&2
      exit 2
    fi
    if [ -z "${decision//[[:space:]]/}" ]; then
      echo "weekly_proposals: an empty decision is not recorded" >&2
      exit 2
    fi
    printf '%s' "$decision" | "${SSH[@]}" "$RUN mark $name --state-dir '$BRAIN_DIR/state' --by research_director"
    ;;
  *) usage ;;
esac
