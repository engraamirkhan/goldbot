#!/usr/bin/env bash
# goldbot-bridge-watchdog <account> [port]: restart goldbot-bridge@<account> when its HTTP server stops answering, and
# goldbot-mt5-terminal@<account> when the terminal behind it is down for good.
#
# The bridge runs under Wine (a Windows Python), which cannot reach systemd's notify socket, so WatchdogSec is not an
# option there. Its server answers one request at a time (the MetaTrader5 package is not thread-safe): a call stuck in
# the terminal blocks GET /health too, which this probes. Run by goldbot-bridge-watchdog@.timer every 2 minutes, as
# root (systemctl restart needs it). Root here only reads systemd's own properties and one fixed token: the GET runs in
# goldbot-bridge-probe as nobody (setpriv), which prints one of ok|not_running|not_connected|not_logged_in|
# wrong_account|noanswer. Root keeps at most 32 bytes of it, drops every byte outside [a-z_] and matches the rest with
# an exact `case` on that list; anything else is noanswer. The body never reaches root, the log, a path, a unit name
# or an argument, and the only actions are restarts of the two literal units named from the validated <account>
# (docs/proposals/2026-10-mt5-terminal-lifecycle.md, security conditions 1-3). It writes one file, a restart count in
# its own root-only runtime directory (refused when that is a symlink or not root's), and follows no path the mt5
# user can write.
#
# Decisions, in order:
# * bridge stopped (inactive/failed): nothing; Restart=always owns a failure and a stopped bridge was stopped on purpose.
# * bridge restarting, or up less than GRACE_S: nothing, unless it has restarted ATTACH_RESTARTS times without settling
#   (counted since the bridge was last seen settled, not since boot) while the terminal unit has been up
#   TERMINAL_GRACE_S. That is a terminal the bridge cannot attach to (alive, its IPC dead: the bridge exits after two
#   not_running reads 30 s apart and its 60 s re-attach times out): restart the terminal unit (Requires= restarts the
#   bridge with it, which resets NRestarts).
# * otherwise PROBES probes PROBE_GAP_S apart (an order the terminal takes 20-30 s to answer must not trip it):
#   - ok on any probe: nothing;
#   - not_logged_in or wrong_account: logged, never restarted (a restart cannot restore a login; a loop would hide it);
#   - noanswer on every probe: restart the bridge (as before);
#   - not_running/not_connected on every probe, terminal unit up TERMINAL_GRACE_S: restart the terminal unit;
#   - anything mixed: nothing (the next run looks again).
# The terminal grace also limits terminal restarts to one per TERMINAL_GRACE_S. A unit restarted by someone else
# while this probed is left alone. A bridge restart is the ordinary one: the brain's engine sees a lost reply, exits
# and reconciles with the broker on restart (row X7); stops rest with the broker meanwhile.
set -euo pipefail
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
PROBE=/usr/local/sbin/goldbot-bridge-probe
# root-only (RuntimeDirectory=, 0700, kept between runs): the bridge's NRestarts when it was last seen settled
STATE_DIR=/run/goldbot-bridge-watchdog
GRACE_S=${GOLDBOT_BRIDGE_GRACE_S:-300}
TERMINAL_GRACE_S=${GOLDBOT_TERMINAL_GRACE_S:-600}
ATTACH_RESTARTS=${GOLDBOT_BRIDGE_ATTACH_RESTARTS:-3}
PROBES=${GOLDBOT_BRIDGE_PROBES:-3}
PROBE_GAP_S=${GOLDBOT_BRIDGE_PROBE_GAP_S:-20}
PROBE_TIMEOUT_S=${GOLDBOT_BRIDGE_PROBE_TIMEOUT_S:-20}

inst=${1-}
port=${2-8765}
[[ "$inst" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "usage: goldbot-bridge-watchdog <account> [port]"; exit 2; }
[[ "$port" =~ ^[0-9]{1,5}$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { echo "bad port: $port"; exit 2; }
for n in "$GRACE_S" "$PROBE_GAP_S" "$TERMINAL_GRACE_S"; do
  [[ "$n" =~ ^[0-9]{1,6}$ ]] || { echo "bad setting: $n"; exit 2; }
done
# at least one probe (0 would restart without probing), a real timeout (curl -m 0 means none at all) and at least one
# restart before a terminal is judged unattachable
for n in "$PROBES" "$PROBE_TIMEOUT_S" "$ATTACH_RESTARTS"; do
  [[ "$n" =~ ^[1-9][0-9]{0,5}$ ]] || { echo "bad setting: $n (must be at least 1)"; exit 2; }
done
bridge="goldbot-bridge@$inst.service"
term="goldbot-mt5-terminal@$inst.service"

prop() { systemctl show -p "$1" --value "$2"; }
age_s() {                     # seconds since a systemd timestamp; -1 when there is none
  local s
  s=$(date -d "$1" +%s 2>/dev/null || echo 0)
  if [[ "$s" =~ ^[0-9]{1,12}$ ]] && [ "$s" -gt 0 ]; then echo $(( $(date +%s) - s )); else echo -1; fi
}
log() { echo "goldbot-bridge-watchdog: $*"; }
# NRestarts counts every automatic restart since the unit was last started by hand (or boot), so restarts from days
# ago would add up. Only the restarts since the bridge was last seen settled count: that count is kept as a baseline
# in STATE_DIR, a directory only root may own (a symlink or another owner: nothing is read or written there).
state_ok() { [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ] && [ -O "$STATE_DIR" ]; }
baseline_file="$STATE_DIR/$inst.nrestarts"
set_baseline() {              # $1: a validated count
  state_ok || { log "state directory $STATE_DIR is not a root-owned directory; restart counts not kept"; return 0; }
  printf '%s\n' "$1" > "$STATE_DIR/.$inst.tmp" && mv -f "$STATE_DIR/.$inst.tmp" "$baseline_file"
}
get_baseline() {              # the stored count, or nothing
  local b=""
  if state_ok && [ -f "$baseline_file" ] && [ ! -L "$baseline_file" ]; then
    b=$(head -c 12 "$baseline_file" | tr -cd '0-9')
  fi
  if [[ "$b" =~ ^[0-9]{1,9}$ ]]; then echo "$b"; fi
}
restart_terminal() {          # $1: why, built from fixed text, validated numbers and allow-listed tokens only
  [ "$(prop ActiveEnterTimestamp "$term")" = "$term_since" ] || exit 0      # restarted while we looked
  log "$1; restarting $term"
  systemctl restart "$term"
  exit 0
}

term_since=""
term_up=-1
if [ "$(prop ActiveState "$term")" = active ]; then
  term_since=$(prop ActiveEnterTimestamp "$term")
  term_up=$(age_s "$term_since")
fi

bridge_since=""
bridge_up=-1
case "$(prop ActiveState "$bridge")" in
  active) bridge_since=$(prop ActiveEnterTimestamp "$bridge"); bridge_up=$(age_s "$bridge_since") ;;
  activating) ;;                                             # between automatic restarts
  *) exit 0 ;;                                               # stopped on purpose, or failed: Restart=always owns it
esac

total=$(prop NRestarts "$bridge")
[[ "$total" =~ ^[0-9]{1,9}$ ]] || total=""
if [ "$bridge_up" -lt "$GRACE_S" ]; then                     # just (re)started: the terminal is still logging in
  [ -n "$total" ] || exit 0
  if ! state_ok; then
    log "state directory $STATE_DIR is not a root-owned directory; restart counts not kept"
    exit 0
  fi
  base=$(get_baseline)
  if [ -z "$base" ]; then                                    # first sight: count from now on
    set_baseline "$total"
    exit 0
  fi
  [ "$total" -ge "$base" ] || base=0                         # NRestarts was reset by a start by hand
  restarts=$((total - base))
  if [ "$restarts" -ge "$ATTACH_RESTARTS" ] && [ "$term_up" -ge "$TERMINAL_GRACE_S" ]; then
    restart_terminal "$bridge restarted $restarts times without settling while $term has run $term_up s"
  fi
  exit 0
fi
[ -z "$total" ] || set_baseline "$total"                     # settled: earlier restarts no longer count

probe() {                     # one token from the unprivileged probe; never the body
  setpriv --reuid=nobody --regid=nogroup --clear-groups --no-new-privs --bounding-set=-all --inh-caps=-all -- \
    "$PROBE" "$port" "$PROBE_TIMEOUT_S" \
    2>/dev/null | head -c 32 | tr -cd 'a-z_' || true
}

silent=0
down=0
last=""
i=1
while :; do
  tok=$(probe)
  case "$tok" in
    ok) exit 0 ;;
    not_logged_in|wrong_account)
      log "terminal $tok on $bridge; not restarting (a restart cannot restore a login; RUNBOOK 'Terminal down')"
      exit 0 ;;
    not_running|not_connected) down=$((down + 1)); last=$tok ;;
    *) silent=$((silent + 1)) ;;
  esac
  [ "$i" -lt "$PROBES" ] || break
  i=$((i + 1))
  sleep "$PROBE_GAP_S"
done

if [ "$silent" -eq "$PROBES" ]; then
  # the unit may have been restarted (deploy, owner) while we probed: only restart the process we judged
  [ "$(prop ActiveEnterTimestamp "$bridge")" = "$bridge_since" ] || exit 0
  log "$bridge did not answer GET /health $PROBES times ${PROBE_GAP_S} s apart; restarting it"
  systemctl restart "$bridge"
  exit 0
fi
if [ "$down" -eq "$PROBES" ]; then
  if [ "$term_up" -ge "$TERMINAL_GRACE_S" ]; then
    restart_terminal "terminal $last on $bridge on $PROBES probes ${PROBE_GAP_S} s apart, $term up $term_up s"
  fi
  log "terminal $last on $bridge, but $term is not active or up under $TERMINAL_GRACE_S s; left to its unit"
  exit 0
fi
log "mixed answers from $bridge ($silent without an answer, $down with the terminal down); no restart"
