#!/usr/bin/env bash
# goldbot deploy: applies a version the OWNER chose, never one by itself (goldbot/ops/deploy.py has the flow).
# Installed root-owned at /usr/local/sbin/goldbot-deploy by the bootstrap scripts (never run from the repo, which the
# service user can write). Run every minute by goldbot-deploy.timer on the brain, or by hand on either VM:
#   sudo goldbot-deploy              # brain: apply the owner's Telegram approval, if any
#   sudo goldbot-deploy latest       # manual: deploy origin/main now (the MT5 box is updated only this way)
#   sudo goldbot-deploy <40-hex sha> # manual: deploy that commit
# The commit must be on main, a fast-forward of what runs, and must have passed CI (latest GitHub Actions runs of
# backend + frontend). After the restart every enabled service must stay up without restarting for 90 s (and on the
# brain the engine and supervisor heartbeats must be fresh), else the previous commit is restored. Any failure after the
# checkout also restores it.
#
# Root never writes or follows anything in a directory the service user can write: the log lives in
# /var/lib/goldbot-deploy (root only) and a read-only copy is published into state/ by rename (which replaces a planted
# symlink instead of following it); the approval file is opened with O_NOFOLLOW, must be a regular file owned by the
# service user, and its content is never echoed.
set -euo pipefail
ROOT=/opt/goldbot
REPO_API=https://api.github.com/repos/engraamirkhan/goldbot
ACTIONS_APP_ID=15368
ROLE=$(cat /etc/goldbot/role 2>/dev/null || echo brain)
VAR=/var/lib/goldbot-deploy
LOG=$VAR/deploys.jsonl
APPROVED=$ROOT/state/deploy/approved.json
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -d -m 700 -o root -g root "$VAR"
exec 9>/run/goldbot-deploy.lock
if ! flock -n 9; then [ -n "${1:-}" ] && echo "another deploy is running; try again in a minute"; exit 0; fi
if [ "$ROLE" = mt5 ]; then OWNER=mt5; else OWNER=goldbot; fi
g() { sudo -u "$OWNER" -H git -C "$ROOT" "$@"; }
is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }

publish_log() {  # read-only copy for Telegram and the health check, replaced by rename (never follows a symlink)
  local tmp; tmp=$(mktemp "$ROOT/state/.deploys.XXXXXX")
  cp "$LOG" "$tmp" && chmod 644 "$tmp" && mv -f -T "$tmp" "$ROOT/state/deploys.jsonl" || rm -f "$tmp"
}
log() {  # result from to detail -- every value is a sha, empty, or fixed text written here
  printf '{"ts":"%s","role":"%s","result":"%s","from":"%s","to":"%s","detail":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$ROLE" "$1" "$2" "$3" "$4" >> "$LOG"
  publish_log
  echo "$1: $4"
}
drop_approval() { [ -n "${MANUAL:-}" ] || rm -f -- "$APPROVED"; }

read_approval() {  # print the approved sha, or nothing (regular file owned by the service user, no symlinks)
  python3 - "$APPROVED" "$OWNER" <<'PY'
import json, os, pwd, re, stat, sys
path, owner = sys.argv[1], sys.argv[2]
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except OSError:
    sys.exit(0)
st = os.fstat(fd)
if not stat.S_ISREG(st.st_mode) or st.st_uid != pwd.getpwnam(owner).pw_uid or st.st_size > 4096:
    sys.exit(0)
try:
    sha = str(json.loads(os.read(fd, 4096)).get("sha", ""))
except ValueError:
    sys.exit(0)
print(sha if re.fullmatch(r"[0-9a-f]{40}", sha) else "")
PY
}

MANUAL=${1:-}
if [ -n "$MANUAL" ]; then
  g fetch -q origin main
  if [ "$MANUAL" = latest ]; then TARGET=$(g rev-parse origin/main); else TARGET=$MANUAL; fi
  is_sha "$TARGET" || { echo "usage: goldbot-deploy [latest|<40-hex commit id>]"; exit 2; }
elif [ "$ROLE" = brain ]; then
  [ -e "$APPROVED" ] || exit 0
  TARGET=$(read_approval)
  if [ -z "$TARGET" ]; then log refused "" "" "invalid approval file (ignored)"; drop_approval; exit 1; fi
else
  exit 0                                              # the MT5 box is updated by hand only
fi

g fetch -q origin main
CUR=$(g rev-parse HEAD)
if [ "$CUR" = "$TARGET" ]; then drop_approval; [ -n "$MANUAL" ] && echo "already running $TARGET"; exit 0; fi
refuse() { log refused "$CUR" "$TARGET" "$1"; drop_approval; exit 1; }
g merge-base --is-ancestor "$TARGET" origin/main || refuse "commit is not on main"
g merge-base --is-ancestor "$CUR" "$TARGET" || refuse "not a fast-forward of the running commit"
ci=$(curl -fsS -H "Accept: application/vnd.github+json" \
  "$REPO_API/commits/$TARGET/check-runs?per_page=100&filter=latest&app_id=$ACTIONS_APP_ID" | python3 -c "
import json, sys
runs = {r['name']: r for r in json.load(sys.stdin).get('check_runs', [])}
print('yes' if all(runs.get(n, {}).get('conclusion') == 'success' for n in ('backend', 'frontend')) else 'no')" || echo no)
[ "$ci" = yes ] || refuse "CI has not passed on this commit"

if [ "$ROLE" = brain ] && compgen -G "$ROOT/state/approvals/pending/*.json" > /dev/null; then
  [ -n "$MANUAL" ] && { echo "an entry awaits your approval; deploy again after it is decided"; exit 1; }
  exit 0                                              # keep the approval; apply once the entry is decided
fi

services() {
  if [ "$ROLE" = mt5 ]; then
    systemctl list-units --type=service --all --no-legend 'goldbot-bridge@*' | awk '{print $1}'
  else
    systemctl list-units --type=service --all --no-legend 'goldbot-*' | awk '{print $1}' \
      | grep -v -e '^goldbot-deploy' -e '^goldbot-bridge-tunnel' -e '^goldbot-vnc' || true
  fi
}

install_for() {  # reinstall Python deps / rebuild the dashboard only when they changed between $1 and $2
  local changed; changed=$(g diff --name-only "$1" "$2")
  if [ "$ROLE" = brain ]; then
    if grep -q '^pyproject.toml$' <<< "$changed"; then
      sudo -u goldbot -H bash -c "cd $ROOT && /var/lib/goldbot/.local/bin/uv pip install -q --python .venv/bin/python -e '.[live]'"
    fi
    if grep -q '^web/' <<< "$changed"; then sudo -u goldbot -H bash -c "cd $ROOT/web && npm ci --silent && npm run build"; fi
  elif grep -q '^pyproject.toml$' <<< "$changed"; then
    sudo -u mt5 -H env WINEDEBUG=-all DISPLAY=:99 wine python -m pip install -q --no-deps -e "Z:$ROOT"
  fi
}

declare -A RESTARTS
switch_to() {  # $1 from, $2 to; returns non-zero on any failure (the caller rolls back)
  g checkout -q --detach "$2" || return 1
  install_for "$1" "$2" || return 1
  local svcs s; mapfile -t svcs < <(services)
  # supervisor first: engines block entries until its heartbeat exists. Restarts are safe: engines reconcile on start
  for s in "${svcs[@]}"; do [[ "$s" == goldbot-supervisor* ]] && { systemctl restart "$s" || return 1; }; done
  for s in "${svcs[@]}"; do [[ "$s" == goldbot-supervisor* ]] || { systemctl restart "$s" || return 1; }; done
  for s in "${svcs[@]}"; do RESTARTS[$s]=$(systemctl show -p NRestarts --value "$s"); done
  return 0
}

fresh() {  # file modified within the last $2 seconds
  [ -f "$1" ] && [ $(( $(date +%s) - $(stat -c %Y "$1") )) -lt "$2" ]
}

healthy() {  # every enabled service up, and not restarted by systemd since our restart, for 90 s; fresh heartbeats
  sleep 90
  local s; for s in $(services); do
    systemctl is-enabled --quiet "$s" 2>/dev/null || continue
    systemctl is-active --quiet "$s" || return 1
    [ "$(systemctl show -p NRestarts --value "$s")" = "${RESTARTS[$s]:-0}" ] || return 1   # a crash loop restarts
  done
  if [ "$ROLE" = brain ]; then
    fresh "$ROOT/state/supervisor.json" 60 || return 1
    for s in $(services); do
      [[ "$s" == goldbot-engine@* ]] || continue
      local acc=${s#goldbot-engine@}; acc=${acc%.service}
      fresh "$ROOT/state/engine_$acc.json" 120 || return 1
    done
  fi
}

set +e                                                # from here every failure leads to a rollback, never an abort
switch_to "$CUR" "$TARGET" && healthy
ok=$?
drop_approval
if [ "$ok" = 0 ]; then
  log deployed "$CUR" "$TARGET" "all services stable for 90 s${MANUAL:+ (manual)}"
  exit 0
fi
switch_to "$TARGET" "$CUR" && healthy
if [ $? = 0 ]; then log rolled_back "$CUR" "$TARGET" "the update failed or a service was unstable; previous version restored"
else log failed "$CUR" "$TARGET" "rollback did not recover every service: check journalctl -u 'goldbot-*'"; fi
exit 1
