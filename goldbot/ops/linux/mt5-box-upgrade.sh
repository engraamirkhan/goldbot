#!/usr/bin/env bash
# mt5-box-upgrade <phase> <sha> [--book-flat --engine-stopped] [--account <id>]
#   phase: precheck | install | post-reboot       sha: the 40-hex commit on main the owner approved
#   --account (or ACCOUNT in the environment): the account this box serves, default icm-demo. One box per account
#   (docs/ops/icm-probe-account-plan.md Option B), so the box layout is the same; only the unit instances differ.
# The attended upgrade of the MT5 box (RUNBOOK 0.5 "Attended MT5 box upgrade"). Run by root from a copy whose sha256
# the owner checked off-box, never from /opt/goldbot (the mt5 user can write that tree).
#   precheck     read-only except the Config/ backup: root, market closed, no engine unit active here, the two
#                attestations, a Config/ copy kept in mt5's 0700 directory (an existing copy is kept, not redone).
#   install      precheck again, then `goldbot-deploy <sha>`, then the units, the watchdog/probe scripts and the
#                terminal unit's AutoTrading script (/usr/local/libexec/goldbot) from root's OWN shallow clone at
#                <sha> (git verifies the content by its id; goldbot-deploy does not install units),
#                stops the transient goldbot-mt5-terminal-check, kills the stray terminal, enables the terminal unit,
#                leaves the watchdog timer OFF, records the boot id. It does not reboot: the PD runs `sudo reboot`.
#   post-reboot  needs a boot id different from install's; checks the cold restart (xvfb, terminal, bridge, exactly
#                one terminal64.exe, /health ok, bridge not crash-looping). Only on all PASS: enables the watchdog
#                timer and sets /etc/goldbot/rollback-floor to <sha>. On any FAIL it changes nothing more.
# Each phase can be run again. It prints a PASS/FAIL table of states only: never Config/ contents, a login, a host, a
# token, a server name. Exit 0 only if every row is PASS.
set -euo pipefail
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin

REPO=https://github.com/engraamirkhan/goldbot.git
ACCOUNT=${ACCOUNT:-icm-demo}
PORT=8765
ROOT=/opt/goldbot
MT5_HOME=/home/mt5
CONFIG_DIR=$MT5_HOME/.wine/drive_c/MT5/ICMarkets/Config
BACKUP_DIR=$MT5_HOME/config-backup
STATE_DIR=/var/lib/goldbot-mt5-upgrade
ROLLBACK_FLOOR=/etc/goldbot/rollback-floor
ROLE_FILE=/etc/goldbot/role
DEPLOY=/usr/local/sbin/goldbot-deploy
UNIT_DIR=/etc/systemd/system
SBIN=/usr/local/sbin
# mt5-experts-enable.sh + mt5-ini.sh, run as mt5 by the terminal unit before each start
LIBEXEC=/usr/local/libexec/goldbot
UNITS=(goldbot-mt5-terminal@.service goldbot-bridge@.service goldbot-bridge-watchdog@.service goldbot-bridge-watchdog@.timer)
MIN_FREE_MB=500

ROWS=()
FAILED=0
PHASE=; SHA=; BOOK_FLAT=0; ENGINE_STOPPED=0; OPS_SRC=

row() {  # row <PASS|FAIL> <check> [detail]: the detail is always one of our own fixed words
  [ "$1" = PASS ] || FAILED=1
  ROWS+=("$1|$2|${3-}")
}
table() {
  local r st chk det
  printf '\n%-6s %-36s %s\n' RESULT CHECK DETAIL
  for r in "${ROWS[@]}"; do
    IFS='|' read -r st chk det <<<"$r"
    printf '%-6s %-36s %s\n' "$st" "$chk" "$det"
  done
  if [ "$FAILED" -eq 0 ]; then echo "ALL PASS"; else echo "FAILED: see RUNBOOK 0.5 'Attended MT5 box upgrade', rollback"; fi
}
die() { echo "refused: $*" >&2; exit 2; }

# overridable in tests (the script is sourced there; main runs only when executed)
now_dowhm() { date -u +%u%H%M; }          # ISO weekday (1=Mon) then HHMM, UTC
boot_id() { head -c 64 /proc/sys/kernel/random/boot_id | tr -cd '0-9a-f-'; }
is_root() { [ "$(id -u)" -eq 0 ]; }

# Gold closes Fri 21:00 UTC (winter) / 22:00 (summer) and reopens Sun 22:00 / 23:00. The window here is the
# conservative one: Friday from 22:30, all Saturday, Sunday until 21:00.
market_closed() {
  local t dow hm
  t=$(now_dowhm); dow=${t:0:1}; hm=$((10#${t:1:4}))
  [ "$dow" = 6 ] && return 0
  [ "$dow" = 5 ] && [ "$hm" -ge 2230 ] && return 0
  [ "$dow" = 7 ] && [ "$hm" -lt 2100 ] && return 0
  return 1
}

valid_sha() { [[ "${1-}" =~ ^[0-9a-f]{40}$ ]]; }
unit_active() { systemctl is-active --quiet "$1"; }
mt5_head() { sudo -u mt5 -H git -C "$ROOT" rev-parse HEAD 2>/dev/null | tr -cd '0-9a-f' | head -c 40 || true; }

check_common() {  # the refusals shared by precheck and install
  local engines free
  if is_root; then row PASS "running as root"; else row FAIL "running as root" "use sudo"; fi
  if market_closed; then row PASS "market closed (UTC window)"; else row FAIL "market closed (UTC window)" "market may be open"; fi
  if [ "$BOOK_FLAT" = 1 ]; then row PASS "book flat (attested)"; else row FAIL "book flat (attested)" "pass --book-flat after checking"; fi
  if [ "$ENGINE_STOPPED" = 1 ]; then row PASS "engine stopped (attested)"; else row FAIL "engine stopped (attested)" "pass --engine-stopped after stopping it"; fi
  engines=$(systemctl list-units --state=active --plain --no-legend 'goldbot-engine@*' 2>/dev/null | head -c 200 || true)
  if [ -z "$engines" ]; then row PASS "no engine unit active here"; else row FAIL "no engine unit active here" "an engine unit is active"; fi
  if [ -d "$CONFIG_DIR" ] && [ ! -L "$CONFIG_DIR" ]; then row PASS "terminal Config/ present"; else row FAIL "terminal Config/ present" "missing or a symlink"; fi
  free=$(df -Pm / | awk 'NR==2{print $4}')
  if [ "${free:-0}" -ge "$MIN_FREE_MB" ]; then row PASS "free disk >= $MIN_FREE_MB MB"; else row FAIL "free disk >= $MIN_FREE_MB MB" "low"; fi
  if [ -x "$DEPLOY" ] && [ "$(cat "$ROLE_FILE" 2>/dev/null || true)" = mt5 ]; then row PASS "goldbot-deploy installed, role mt5"; else row FAIL "goldbot-deploy installed, role mt5" "not an MT5 box"; fi
}

backup_config() {
  # a copy made earlier is kept (it may be the only good one); the copy is made as mt5 itself, inside its own 0700
  # directory, so root never follows a path mt5 can write
  if [ -d "$BACKUP_DIR/Config" ]; then row PASS "Config/ backup" "exists, kept"; return 0; fi
  if sudo -u mt5 -H sh -c 'umask 077; mkdir -p "$HOME/config-backup" && chmod 700 "$HOME/config-backup" && cp -a "$HOME/.wine/drive_c/MT5/ICMarkets/Config" "$HOME/config-backup/"'; then
    row PASS "Config/ backup" "made"
  else
    row FAIL "Config/ backup" "copy failed"
  fi
}

phase_precheck() {
  check_common
  if [ "$FAILED" -eq 0 ]; then backup_config; fi
}

fetch_ops() {  # root's own clone at exactly <sha> in a root-only directory
  OPS_SRC=$(mktemp -d /root/goldbot-ops.XXXXXX)
  git init -q "$OPS_SRC/src" \
    && git -C "$OPS_SRC/src" fetch -q --depth 1 "$REPO" "$SHA" \
    && git -C "$OPS_SRC/src" checkout -q --detach FETCH_HEAD || return 1
  [ "$(git -C "$OPS_SRC/src" rev-parse HEAD)" = "$SHA" ]
}

phase_install() {
  local ops u prev
  check_common
  [ "$FAILED" -eq 0 ] || return 0
  backup_config
  [ "$FAILED" -eq 0 ] || return 0
  prev=$(mt5_head)
  install -d -m 700 -o root -g root "$STATE_DIR"
  # the version to go back to: renewed by each new upgrade (the recorded sha differs), kept on a re-run of the same one
  if [ "$(cat "$STATE_DIR/sha" 2>/dev/null || true)" != "$SHA" ] && valid_sha "$prev" && [ "$prev" != "$SHA" ]; then
    printf '%s\n' "$prev" > "$STATE_DIR/previous-sha"
  fi

  if "$DEPLOY" "$SHA"; then row PASS "goldbot-deploy <sha>" "done"; else row FAIL "goldbot-deploy <sha>" "refused or failed"; return 0; fi

  trap 'rm -rf -- "${OPS_SRC:-/nonexistent-never}"' EXIT
  if fetch_ops; then row PASS "root clone at sha" "commit id verified"; else row FAIL "root clone at sha" "fetch or id mismatch"; return 0; fi
  ops=$OPS_SRC/src/goldbot/ops/linux
  for u in "${UNITS[@]}"; do
    [ -f "$ops/systemd/$u" ] || { row FAIL "unit file $u" "absent at sha"; return 0; }
  done
  if [ ! -f "$ops/goldbot-bridge-watchdog.sh" ] || [ ! -f "$ops/goldbot-bridge-probe.sh" ] \
     || [ ! -f "$ops/mt5/mt5-experts-enable.sh" ] || [ ! -f "$ops/mt5/mt5-ini.sh" ]; then
    row FAIL "watchdog scripts" "absent at sha"; return 0
  fi
  for u in "${UNITS[@]}"; do install -m 644 -o root -g root "$ops/systemd/$u" "$UNIT_DIR/$u"; done
  install -m 755 -o root -g root "$ops/goldbot-bridge-watchdog.sh" "$SBIN/goldbot-bridge-watchdog"
  install -m 755 -o root -g root "$ops/goldbot-bridge-probe.sh" "$SBIN/goldbot-bridge-probe"
  install -d -m 755 -o root -g root "$LIBEXEC"
  install -m 755 -o root -g root "$ops/mt5/mt5-experts-enable.sh" "$LIBEXEC/mt5-experts-enable.sh"
  install -m 755 -o root -g root "$ops/mt5/mt5-ini.sh" "$LIBEXEC/mt5-ini.sh"
  systemctl daemon-reload
  row PASS "units and watchdog scripts installed" "root-owned"

  # the cold-restart test starts with the watchdog off; it is enabled only after the test passes
  systemctl disable --now "goldbot-bridge-watchdog@$ACCOUNT.timer" >/dev/null 2>&1 || true
  systemctl stop goldbot-mt5-terminal-check.service >/dev/null 2>&1 || true
  systemctl reset-failed goldbot-mt5-terminal-check.service >/dev/null 2>&1 || true
  pkill -u mt5 -f 'terminal64[.]exe' >/dev/null 2>&1 || true
  if systemctl enable "goldbot-mt5-terminal@$ACCOUNT.service" >/dev/null 2>&1 \
     && systemctl is-enabled --quiet "goldbot-mt5-terminal@$ACCOUNT.service"; then
    row PASS "terminal unit enabled" "$ACCOUNT"
  else
    row FAIL "terminal unit enabled" "$ACCOUNT"; return 0
  fi
  if systemctl is-enabled --quiet "goldbot-bridge@$ACCOUNT.service"; then row PASS "bridge unit enabled" "$ACCOUNT"; else row FAIL "bridge unit enabled" "$ACCOUNT"; return 0; fi
  boot_id > "$STATE_DIR/install-boot-id"
  printf '%s\n' "$SHA" > "$STATE_DIR/sha"
  row PASS "ready for reboot" "run: sudo reboot"
}

phase_post_reboot() {
  local s h nr1 nr2 n u floor
  if is_root; then row PASS "running as root"; else row FAIL "running as root" "use sudo"; fi
  if [ -f "$STATE_DIR/sha" ] && [ "$(cat "$STATE_DIR/sha")" = "$SHA" ]; then row PASS "install phase recorded for sha"; else row FAIL "install phase recorded for sha" "run install first"; fi
  if [ -s "$STATE_DIR/install-boot-id" ] && [ "$(cat "$STATE_DIR/install-boot-id")" != "$(boot_id)" ]; then row PASS "machine rebooted since install"; else row FAIL "machine rebooted since install" "reboot first"; fi
  for u in goldbot-xvfb "goldbot-mt5-terminal@$ACCOUNT" "goldbot-bridge@$ACCOUNT"; do
    if unit_active "$u"; then row PASS "active: $u"; else row FAIL "active: $u" "not active"; fi
  done
  n=$(pgrep -c -u mt5 -f '[t]erminal64[.]exe' || true)
  if [ "${n:-0}" = 1 ]; then row PASS "exactly one terminal64.exe"; else row FAIL "exactly one terminal64.exe" "count ${n:-0}"; fi
  h=$("$SBIN/goldbot-bridge-probe" "$PORT" 20 2>/dev/null | head -c 32 || true)
  if [ "$h" = ok ]; then row PASS "bridge /health terminal state" "ok"; else row FAIL "bridge /health terminal state" "${h:-noanswer}"; fi
  nr1=$(systemctl show -p NRestarts --value "goldbot-bridge@$ACCOUNT.service" | tr -cd '0-9')
  sleep "${SETTLE_S:-30}"
  nr2=$(systemctl show -p NRestarts --value "goldbot-bridge@$ACCOUNT.service" | tr -cd '0-9')
  if [ "${nr1:-0}" = "${nr2:-0}" ]; then row PASS "bridge not crash-looping" "restarts steady"; else row FAIL "bridge not crash-looping" "restarts rising"; fi
  s=$(mt5_head)
  if [ "$s" = "$SHA" ]; then row PASS "deployed commit is the sha"; else row FAIL "deployed commit is the sha" "differs"; fi
  [ "$FAILED" -eq 0 ] || return 0

  # the floor is judged before anything is enabled: a conflicting floor leaves the watchdog off
  floor=none
  if [ -e "$ROLLBACK_FLOOR" ] || [ -L "$ROLLBACK_FLOOR" ]; then
    if [ -f "$ROLLBACK_FLOOR" ] && [ ! -L "$ROLLBACK_FLOOR" ] && [ "$(head -c 41 "$ROLLBACK_FLOOR" | tr -d '[:space:]')" = "$SHA" ]; then
      floor=same
    else
      floor=other
    fi
  fi
  if [ "$floor" = other ]; then row FAIL "rollback floor" "a different floor exists: change it deliberately"; return 0; fi

  systemctl enable --now "goldbot-bridge-watchdog@$ACCOUNT.timer" >/dev/null
  row PASS "watchdog timer enabled" "$ACCOUNT"
  if [ "$floor" = same ]; then
    row PASS "rollback floor" "already set"
  else
    install -d -m 755 -o root -g root "$(dirname "$ROLLBACK_FLOOR")"
    printf '%s\n' "$SHA" > "$ROLLBACK_FLOOR.new"
    chmod 644 "$ROLLBACK_FLOOR.new"
    mv -f "$ROLLBACK_FLOOR.new" "$ROLLBACK_FLOOR"
    row PASS "rollback floor" "set to sha"
  fi
}

main() {
  PHASE=${1-}; SHA=${2-}
  shift $(( $# > 2 ? 2 : $# ))
  while [ $# -gt 0 ]; do
    case "$1" in
      --book-flat) BOOK_FLAT=1 ;;
      --engine-stopped) ENGINE_STOPPED=1 ;;
      --account) [ $# -ge 2 ] || die "--account needs an account id"; ACCOUNT=$2; shift ;;
      *) die "unknown option" ;;
    esac
    shift
  done
  case "$PHASE" in
    precheck | install | post-reboot) ;;
    *) die "usage: mt5-box-upgrade <precheck|install|post-reboot> <40-hex sha> [--book-flat --engine-stopped] [--account <id>]" ;;
  esac
  valid_sha "$SHA" || die "the sha must be 40 lowercase hex characters"
  # it goes into unit instance names run by root: lowercase id only, as in config/accounts.yaml
  [[ "$ACCOUNT" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || die "the account id must be lowercase letters, digits and '-'"
  trap '' HUP   # a dropped SSH session must not stop it half-way
  case "$PHASE" in
    precheck) phase_precheck ;;
    install) phase_install ;;
    post-reboot) phase_post_reboot ;;
  esac
  table
  [ "$FAILED" -eq 0 ]
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
