#!/usr/bin/env bash
# goldbot deploy: applies a version the OWNER chose, never one by itself (goldbot/ops/deploy.py has the flow).
# Installed root-owned at /usr/local/sbin/goldbot-deploy by the bootstrap scripts (not run from the repo, which the
# service user can write). Run every minute by goldbot-deploy.timer, or by hand:
#   sudo goldbot-deploy              # apply the owner's Telegram approval (brain) / follow approved versions (mt5)
#   sudo goldbot-deploy latest       # manual: deploy origin/main now
#   sudo goldbot-deploy <40-hex sha> # manual: deploy that commit
# Either way the commit must be on main, a fast-forward of what runs, and must have passed CI (backend + frontend).
# After the restart every service of this machine must be active for 60 s, else the previous commit is restored.
# Every attempt is a line in /opt/goldbot/state/deploys.jsonl (reported on Telegram, health check `deploy`).
set -euo pipefail
ROOT=/opt/goldbot
REPO_API=https://api.github.com/repos/engraamirkhan/goldbot
ROLE=$(cat /etc/goldbot/role 2>/dev/null || echo brain)
LOG=$ROOT/state/deploys.jsonl
APPROVED=$ROOT/state/deploy/approved.json
STAMP=/var/lib/goldbot-deploy/last_github_check
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
mkdir -p /var/lib/goldbot-deploy
exec 9>/run/goldbot-deploy.lock
flock -n 9 || exit 0                                 # one deploy at a time
if [ "$ROLE" = mt5 ]; then OWNER=mt5; else OWNER=goldbot; fi
g() { sudo -u "$OWNER" -H git -C "$ROOT" "$@"; }
is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }
log() {  # result from to detail
  printf '{"ts":"%s","role":"%s","result":"%s","from":"%s","to":"%s","detail":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$ROLE" "$1" "$2" "$3" "$4" >> "$LOG"
  chown "$OWNER" "$LOG" 2>/dev/null || true
  echo "$1: $4"
}

MANUAL=${1:-}
TARGET=""
if [ -n "$MANUAL" ]; then
  g fetch -q origin main
  if [ "$MANUAL" = latest ]; then TARGET=$(g rev-parse origin/main); else TARGET=$MANUAL; fi
elif [ "$ROLE" = brain ]; then
  [ -f "$APPROVED" ] || exit 0
  TARGET=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('sha',''))" "$APPROVED" 2>/dev/null || true)
else                                                  # mt5: follow the versions the owner approved on the brain
  if [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -c %Y "$STAMP") )) -lt 600 ]; then exit 0; fi
  touch "$STAMP"
  TARGET=$(curl -fsS "$REPO_API/deployments?environment=oracle&per_page=1" | python3 -c \
    "import json,sys; d=json.load(sys.stdin); print(d[0]['sha'] if d else '')" 2>/dev/null || true)
  [ -n "$TARGET" ] || exit 0
fi
is_sha "$TARGET" || { log refused "" "$TARGET" "not a commit id"; rm -f "$APPROVED"; exit 1; }

g fetch -q origin main
CUR=$(g rev-parse HEAD)
if [ "$CUR" = "$TARGET" ]; then rm -f "$APPROVED"; [ -n "$MANUAL" ] && echo "already running $TARGET"; exit 0; fi
refuse() { log refused "$CUR" "$TARGET" "$1"; rm -f "$APPROVED"; exit 1; }
g merge-base --is-ancestor "$TARGET" origin/main || refuse "commit is not on main"
g merge-base --is-ancestor "$CUR" "$TARGET" || refuse "not a fast-forward of the running commit"
ci=$(curl -fsS -H "Accept: application/vnd.github+json" "$REPO_API/commits/$TARGET/check-runs?per_page=100" | python3 -c "
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
      | grep -v -e '^goldbot-deploy' -e '^goldbot-bridge-tunnel' | grep -v '^goldbot-vnc'
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

switch_to() {  # $1 from, $2 to
  g checkout -q --detach "$2"
  install_for "$1" "$2"
  local svcs; mapfile -t svcs < <(services)
  # supervisor first: engines block entries until its heartbeat exists. Restarts are safe: engines reconcile on start
  for s in "${svcs[@]}"; do [[ "$s" == goldbot-supervisor* ]] && systemctl restart "$s"; done
  for s in "${svcs[@]}"; do [[ "$s" == goldbot-supervisor* ]] || systemctl restart "$s"; done
}

healthy() {  # every enabled service of this machine still active after 60 s (a crash loop is not)
  sleep 60
  local s; for s in $(services); do
    systemctl is-enabled --quiet "$s" 2>/dev/null || continue
    systemctl is-active --quiet "$s" || return 1
  done
}

switch_to "$CUR" "$TARGET"
rm -f "$APPROVED"
if healthy; then
  log deployed "$CUR" "$TARGET" "all services active${MANUAL:+ (manual)}"
  if [ "$ROLE" = brain ]; then                        # let the MT5 box follow this approved version
    sudo -u goldbot -H env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring bash -c \
      "cd $ROOT && .venv/bin/python -m goldbot.ops.deploy mark $TARGET" || true
  fi
else
  switch_to "$TARGET" "$CUR"
  if healthy; then log rolled_back "$CUR" "$TARGET" "services failed after the update; previous version restored"
  else log failed "$CUR" "$TARGET" "rollback did not recover every service: check journalctl -u 'goldbot-*'"; fi
  exit 1
fi
