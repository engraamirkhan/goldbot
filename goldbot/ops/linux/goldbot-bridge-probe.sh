#!/usr/bin/env bash
# goldbot-bridge-probe <port> <timeout_s>: print exactly one fixed token for the bridge's GET /health, one of
#   ok | not_running | not_connected | not_logged_in | wrong_account | autotrading_off | noanswer
# and nothing else. The body is written by the bridge, which runs as mt5, so it is untrusted here: this script runs
# UNPRIVILEGED (goldbot-bridge-watchdog starts it with setpriv as nobody; security condition 1 of
# docs/proposals/2026-10-mt5-terminal-lifecycle.md), reads at most 1 KiB, never evaluates or prints the body, and
# matches it only against the six literal `"terminal": "<state>"` forms the bridge writes (json.dumps). Exactly one
# must match; anything else (unknown value, two values, over 1 KiB, a NUL, no answer, curl error) is `noanswer`.
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

found=noanswer
n=0
for state in ok not_running not_connected not_logged_in wrong_account autotrading_off; do
  case "$body" in
    *"\"terminal\": \"$state\""*) found=$state; n=$((n + 1)) ;;
  esac
done
if [ "$n" -eq 1 ]; then
  echo "$found"
  exit 0
fi
if [ "$n" -eq 0 ]; then
  case "$body" in
    *'"terminal"'*) ;;                                   # a terminal key with no known value
    '{"ok": true'*) echo ok; exit 0 ;;                   # a bridge older than the terminal state
  esac
fi
echo noanswer
