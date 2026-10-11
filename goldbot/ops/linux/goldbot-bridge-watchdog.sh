#!/usr/bin/env bash
# goldbot-bridge-watchdog <account> [port]: restart goldbot-bridge@<account> when its HTTP server stops answering.
#
# The bridge runs under Wine (a Windows Python), which cannot reach systemd's notify socket, so WatchdogSec is not an
# option there. Its server answers one request at a time (the MetaTrader5 package is not thread-safe): a call stuck in
# the terminal blocks GET /health too, which this probes. Run by goldbot-bridge-watchdog@.timer every 2 minutes, as
# root (systemctl restart needs it). Root here only reads: one GET to 127.0.0.1 with the output discarded, and the
# unit's state from systemctl; it writes no file and follows no path the mt5 user can write.
#
# Restart only when: the unit is active, it has been active for at least GRACE_S (the terminal logs in first), and
# PROBES probes PROBE_GAP_S apart all fail (an order the terminal takes 20-30 s to answer must not trip it). A restart
# is the ordinary one: the brain's engine sees a lost reply, exits and reconciles with the broker on restart (row X7);
# stops rest with the broker meanwhile.
set -euo pipefail
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
GRACE_S=${GOLDBOT_BRIDGE_GRACE_S:-300}
PROBES=${GOLDBOT_BRIDGE_PROBES:-3}
PROBE_GAP_S=${GOLDBOT_BRIDGE_PROBE_GAP_S:-20}
PROBE_TIMEOUT_S=${GOLDBOT_BRIDGE_PROBE_TIMEOUT_S:-20}

inst=${1-}
port=${2-8765}
[[ "$inst" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "usage: goldbot-bridge-watchdog <account> [port]"; exit 2; }
[[ "$port" =~ ^[0-9]{1,5}$ ]] && [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { echo "bad port: $port"; exit 2; }
for n in "$GRACE_S" "$PROBE_GAP_S"; do
  [[ "$n" =~ ^[0-9]{1,6}$ ]] || { echo "bad setting: $n"; exit 2; }
done
# at least one probe (0 would restart without probing) and a real timeout (curl -m 0 means none at all)
for n in "$PROBES" "$PROBE_TIMEOUT_S"; do
  [[ "$n" =~ ^[1-9][0-9]{0,5}$ ]] || { echo "bad setting: $n (must be at least 1)"; exit 2; }
done
unit="goldbot-bridge@$inst.service"

# stopped, failed or restarting: Restart=always owns that case, and a stopped bridge was stopped on purpose
systemctl is-active --quiet "$unit" || exit 0

since=$(systemctl show -p ActiveEnterTimestamp --value "$unit")
since_s=$(date -d "$since" +%s 2>/dev/null || echo 0)
up=$(( $(date +%s) - since_s ))
if [ "$since_s" -le 0 ] || [ "$up" -lt "$GRACE_S" ]; then
  exit 0                                       # just (re)started: the terminal is still logging in
fi

i=1
while :; do
  if curl -fsS -o /dev/null -m "$PROBE_TIMEOUT_S" "http://127.0.0.1:$port/health" 2>/dev/null; then
    exit 0
  fi
  [ "$i" -lt "$PROBES" ] || break
  i=$((i + 1))
  sleep "$PROBE_GAP_S"
done

# the unit may have been restarted (deploy, owner) while we probed: only restart the process we judged
now_since=$(systemctl show -p ActiveEnterTimestamp --value "$unit")
[ "$now_since" = "$since" ] || exit 0
echo "goldbot-bridge-watchdog: $unit did not answer GET /health $PROBES times ${PROBE_GAP_S} s apart; restarting it"
systemctl restart "$unit"
