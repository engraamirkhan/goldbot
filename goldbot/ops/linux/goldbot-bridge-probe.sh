#!/usr/bin/env bash
# goldbot-bridge-probe <port> <timeout_s>: print exactly one token for the bridge's GET /health and nothing else:
#   the bridge's terminal state (today ok | not_running | not_connected | not_logged_in | wrong_account |
#   autotrading_off; any 1 to 32 bytes of [a-z_]) or noanswer
# The body is written by the bridge, which runs as mt5, so it is untrusted here: this script runs UNPRIVILEGED
# (goldbot-bridge-watchdog starts it with setpriv as nobody; security condition 1 of
# docs/proposals/2026-10-mt5-terminal-lifecycle.md), reads at most 1 KiB, never evaluates or prints the body, and
# prints only a value matching `"terminal": "[a-z_]{1,32}"` followed by , or }. Exactly one "terminal": key must be
# there; anything else (two keys, a value with any other byte, over 1 KiB, a NUL, no answer, curl error) is noanswer.
# A body without any "terminal" key that starts like the bridge's {"ok": true is an older bridge: `ok`, as before.
set -uo pipefail
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
MAX_BYTES=1024

port=${1-}
timeout_s=${2-20}
if ! [[ "$port" =~ ^[0-9]{1,5}$ ]] || ! [[ "$timeout_s" =~ ^[1-9][0-9]{0,5}$ ]]; then
  echo noanswer
  exit 0
fi

# No -f: a 503 carries the terminal state. --max-filesize refuses a declared length over the bound, head bounds an
# undeclared one (one byte over is enough to refuse), and tr turns a NUL into a byte no token contains, so the
# shell never drops it silently. Any failure in the pipeline (curl's own, or its SIGPIPE when head stops) is noanswer.
if ! body=$(curl -sS --max-filesize 1024 -m "$timeout_s" "http://127.0.0.1:$port/health" 2>/dev/null \
            | head -c $((MAX_BYTES + 1)) | tr '\000' '\001'); then
  echo noanswer
  exit 0
fi
if [ "${#body}" -gt "$MAX_BYTES" ]; then
  echo noanswer
  exit 0
fi

# exactly one "terminal": key, whose value is 1 to 32 bytes of [a-z_] closed by a quote and then , or }. ANY such
# token is forwarded, not only the ones this copy knows: goldbot-deploy updates the bridge but not this root-owned
# script, so a newer bridge's token must not read as noanswer (the watchdog restarts the bridge on noanswer).
rest=${body//\"terminal\":/}
n=$(( (${#body} - ${#rest}) / 11 ))                    # 11 = the length of "terminal":
re='"terminal": "([a-z_]{1,32})"[,}]'
if [ "$n" -eq 1 ] && [[ $body =~ $re ]]; then
  echo "${BASH_REMATCH[1]}"
  exit 0
fi
if [ "$n" -eq 0 ]; then
  case "$body" in
    *'"terminal"'*) ;;                                   # a terminal key in any other form
    '{"ok": true'*) echo ok; exit 0 ;;                   # a bridge older than the terminal state
  esac
fi
echo noanswer
