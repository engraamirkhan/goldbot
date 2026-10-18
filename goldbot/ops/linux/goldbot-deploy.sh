#!/usr/bin/env bash
# goldbot deploy: applies a version the OWNER chose, never one by itself (goldbot/ops/deploy.py has the flow).
# Installed root-owned at /usr/local/sbin/goldbot-deploy by the bootstrap scripts (never run from the repo, which the
# service user can write). Run every minute by goldbot-deploy.timer on the brain, or by hand on either VM:
#   sudo goldbot-deploy                          # brain: apply the owner's Telegram approval, if any
#   sudo goldbot-deploy latest                   # manual: deploy origin/main now (the MT5 box is updated only this way)
#   sudo goldbot-deploy <40-hex sha>             # manual: deploy that commit
#   sudo goldbot-deploy --rollback <40-hex sha>  # manual: go back to an OLDER commit (owner's decision)
# Owner flags for the manual forms:
#   --drop-unknown-local-keys  move settings.local.yaml keys the target code does not know aside (dated backup kept
#                              next to it) instead of refusing; only unknown keys, never an invalid value
#   --interrupt-jobs           deploy even while a scheduler job runs (that job's slot is not re-run)
#
# A forward deploy must be on main, a fast-forward of what runs, and must have passed CI (latest GitHub Actions runs
# of backend + frontend). A rollback must be on main, an ancestor of what runs, and must have passed CI or have run
# here before (deploys.jsonl). Before anything is touched: aux.db's schema must be one the target can open (a target
# from before schema compatibility gets aux.db stepped back 5 -> 4 with every service stopped and every row kept;
# any other unknown version is refused), settings.local.yaml must validate with the TARGET's own Settings model, and
# no scheduler job may be mid-run (the scheduler is stopped first and checked again). After the restart every enabled
# service must stay up without restarting for 90 s (and on the brain the supervisor must have written a new
# heartbeat), else the previous commit is restored the same way. Every outcome is appended to deploys.jsonl.
#
# Root never writes or follows anything in a directory the service user can write: the log lives in
# /var/lib/goldbot-deploy (root only) and its copy in state/ is written BY the service user (root only pipes the
# content), so a planted symlink can only reach the service user's own files; the approval file is opened with
# O_NOFOLLOW | O_NONBLOCK, must be a regular file owned by the service user, and its content is never echoed. Every
# other read or write under /opt/goldbot (approval removal, aux.db, settings, the scheduler status, the temporary
# copy of the target's code for the settings check) runs as the service user via `sudo -u`. Root runs repo code
# never: the Python that reads repo or state data runs as the service user; root's own python3 only parses
# deploys.jsonl (root-owned) and at most 1 MiB of JSON it is piped, with -I (no cwd or environment on sys.path).
# Everything printed to root's terminal is filtered to printable ASCII (no terminal escapes from service-user output).
# git runs as the service user with GIT_NO_REPLACE_OBJECTS=1. The service user controls .git, so ancestry answers
# are trusted only as far as that account is; the installed copy of THIS script is therefore never taken from that
# git (docs/AGENT_STANDARDS.md "Root-run tooling": fetched from GitHub by root and hash-checked off-box).
#
# Optional rollback floor: /etc/goldbot/rollback-floor (root-owned, one 40-hex commit id). `--rollback` refuses any
# target that is not that commit or a descendant of it (for example, never below the auth hardening). Both the
# service user's git and GitHub's compare API (root, curl) must say so: when GitHub cannot confirm (offline, rate
# limit, an unreadable answer) the rollback is refused, and the owner removes the floor deliberately to proceed.
#
# Health baseline (brain): `goldbot run health --out` runs as the service user before the switch and again after the
# 90 s check. A check that got worse (ok < warn < fail) is reported as `deployed_degraded` with its name, on
# Telegram too; it is not rolled back automatically (exits are never gated; the owner decides).
# An engine unit that is disabled or inactive at the baseline (the S13 procedure stops it on purpose) has its
# engine-derived checks (engine, engine_loop, tick_errors for that account; reconcile stays compared, it also reports ledger mismatches) left out of the comparison: the
# baseline still read them from a heartbeat that was fresh a moment before the stop. The detail says "engine stopped
# for the deploy: <account>"; every other worsened check still yields deployed_degraded.
set -euo pipefail
ROOT=/opt/goldbot
REPO_API=https://api.github.com/repos/engraamirkhan/goldbot
ACTIONS_APP_ID=15368
ROLE_FILE=/etc/goldbot/role
ROLLBACK_FLOOR=/etc/goldbot/rollback-floor
VAR=/var/lib/goldbot-deploy
LOG=$VAR/deploys.jsonl
LOCK=/run/goldbot-deploy.lock
APPROVED=$ROOT/state/deploy/approved.json
PY_BRAIN=$ROOT/.venv/bin/python           # run only as the service user
BRIDGE_REQS=goldbot/ops/linux/mt5-bridge-requirements.txt   # MT5 box: hash-pinned from uv.lock (scripts/bridge_requirements.py)
# MT5 box: the Windows Python by absolute path (as $WINPY in mt5_bootstrap.sh), never a bare `python`: Windows runs a
# python.exe from the current directory before PATH
WINPY=/home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/Python311/python.exe
MANUAL=""
MODE=approval
DROP_KEYS=0
INTERRUPT_JOBS=0

g() { sudo -u "$OWNER" -H env GIT_NO_REPLACE_OBJECTS=1 git -C "$ROOT" "$@"; }
as_owner() { sudo -u "$OWNER" -H "$@"; }
is_sha() { [[ "$1" =~ ^[0-9a-f]{40}$ ]]; }
enabled() { systemctl is-enabled --quiet "$1" 2>/dev/null; }
mtime() { as_owner stat -c %Y -- "$1"; }             # state/ is the service user's: never stat it as root
pypath() { if [ "$ROLE" = mt5 ]; then echo "Z:$1"; else echo "$1"; fi; }   # Wine Python sees / as Z:

publish_log() {  # copy for Telegram and the health check, written as the service user (root never touches state/)
  sudo -u "$OWNER" sh -c 'cat > "$1.tmp" && mv -f "$1.tmp" "$1"' _ "$ROOT/state/deploys.jsonl" < "$LOG" || true
}
log() {  # result from to detail -- every value is a sha, empty, fixed text written here, or a name matched by
  # [A-Za-z0-9_.<>-] (settings keys, job names), so the line is always valid JSON and never holds a value
  printf '{"ts":"%s","role":"%s","result":"%s","from":"%s","to":"%s","detail":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$ROLE" "$1" "$2" "$3" "$4" >> "$LOG"
  publish_log
  echo "$1: $4"
}
drop_approval() { [ -n "${MANUAL:-}" ] || as_owner rm -f -- "$APPROVED"; }
refuse() { log refused "$CUR" "$TARGET" "$1"; drop_approval; exit 1; }
usage() {
  echo "usage: goldbot-deploy [latest | <40-hex commit id> | --rollback <40-hex commit id>]"
  echo "                      [--drop-unknown-local-keys] [--interrupt-jobs]"
  exit 2
}

read_approval() {  # print the approved sha, or nothing (regular file owned by the service user, no symlinks)
  python3 -I - "$APPROVED" "$OWNER" <<'PY'
import json, os, pwd, re, stat, sys
path, owner = sys.argv[1], sys.argv[2]
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)   # no symlink; a FIFO cannot block
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

ci_passed() {  # yes / no: the latest backend and frontend GitHub Actions runs of $1 succeeded
  curl -fsS -H "Accept: application/vnd.github+json" \
    "$REPO_API/commits/$1/check-runs?per_page=100&filter=latest&app_id=$ACTIONS_APP_ID" | python3 -I -c "
import json, sys
runs = {r['name']: r for r in json.load(sys.stdin).get('check_runs', [])}
print('yes' if all(runs.get(n, {}).get('conclusion') == 'success' for n in ('backend', 'frontend')) else 'no')" || echo no
}

ran_here() {  # $1 ran on this box before: the result of a deploy or an owner's rollback, or the version a failed
  # deploy restored; never a refusal (its "from" is only what ran when something was refused)
  [ -f "$LOG" ] || return 1
  python3 -I - "$LOG" "$1" <<'PY'
import json, sys
path, sha = sys.argv[1], sys.argv[2]
for line in open(path, encoding="utf-8"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    result = r.get("result")
    if (r.get("to") == sha and result in ("deployed", "deployed_degraded", "rolled_back_manual")) or \
            (r.get("from") == sha and result in ("rolled_back", "rollback_reverted")):
        sys.exit(0)
sys.exit(1)
PY
}

# ---------------------------------------------------------------- aux.db schema (BACKLOG 37; vps_update.ps1 Sync-AuxSchema)
# A commit from before the state store's step 3 knows aux.db only up to schema 4 and refuses the newer file (its
# dashboard would not start). Schema 5 only added tables, so it is stepped back to 4 with every row kept; the next
# deploy re-applies 5 without losing them. Code from step 3 on records compat_back_to and needs nothing.
aux_version() {  # aux.db schema version (empty when there is no aux.db), read as the service user
  as_owner python3 -I - "$ROOT/state/aux.db" <<'PY'
import os, sqlite3, sys
path = sys.argv[1]
if not os.path.isfile(path):
    sys.exit(0)
conn = sqlite3.connect(path, timeout=30)
has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'").fetchone()
print((conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0) if has else 0)
PY
}

aux_plan() {  # $1 target: "none", "step" (5 -> 4 with services stopped) or "refuse <version>"
  local mig v
  mig=$(g show "$1:goldbot/db/migrations.py" 2>/dev/null) || { echo none; return 0; }   # no state store yet
  if grep -q compat_back_to <<< "$mig"; then echo none; return 0; fi                    # opens newer files itself
  v=$(aux_version 2>/dev/null) || v=unreadable
  [[ "$v" =~ ^[0-9]{0,6}$ ]] || v=unreadable
  case "$v" in
    "" | [0-4]) echo none ;;
    5) echo step ;;
    *) echo "refuse $v" ;;
  esac
}

aux_step_back() {  # schema 5 -> 4, every row kept; exit 3 (nothing changed) at any other version above 4
  as_owner python3 -I - "$ROOT/state/aux.db" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=30)
have = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
if have <= 4:
    sys.exit(0)
if have != 5:
    print(f"aux.db schema version {have}: only 5 -> 4 is known, left unchanged")
    sys.exit(3)
conn.execute("DELETE FROM schema_version WHERE version = 5")
conn.execute("PRAGMA user_version = 4")
conn.commit()
print("aux.db schema 5 -> 4 for the older release (rows kept; the next deploy re-applies 5)")
PY
}

# ---------------------------------------------------------------- settings against the target (BACKLOG 39)
# Settings forbid unknown keys (goldbot/config.py _Section, extra="forbid"): a settings.local.yaml key the target code
# does not know stops every service. The tracked settings.yaml passed CI with the target's own code, so only the local
# overlay is checked: the target's goldbot/ and config/ are extracted (git archive) into a temporary directory owned by
# the service user, settings.local.yaml is copied next to its settings.yaml, and the target's own Settings model
# validates the merge. Output names keys, never values: "<unknown|invalid|unreadable> <key> <local|tracked>".
SETTINGS_PY=$(cat <<'PY'
import os, re, sys
src = sys.argv[1]
sys.path.insert(0, src)
try:
    import yaml
    from pydantic import ValidationError
    import goldbot.config as cfg
except Exception as exc:
    print(f"cannot import the target's settings model ({type(exc).__name__})")
    sys.exit(4)
real = lambda p: os.path.normcase(os.path.realpath(p))
if not real(cfg.__file__).startswith(real(src) + os.sep):
    print("cannot isolate the target's settings model")
    sys.exit(4)
SEG = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}|[0-9]{1,4}")   # a long number (an id used as a key) is masked
def name(loc):
    return ".".join(str(p) if SEG.fullmatch(str(p)) else "<key>" for p in loc) or "<settings>"
def present(d, loc):
    for p in loc:
        if isinstance(d, dict) and p in d:
            d = d[p]
        elif isinstance(d, list) and isinstance(p, int) and p < len(d):
            d = d[p]
        else:
            return False
    return True
base = os.path.join(src, "config", "settings.yaml")
try:
    with open(os.path.join(src, "config", "settings.local.yaml"), encoding="utf-8") as fh:
        local = yaml.safe_load(fh) or {}
except Exception:
    print("unreadable settings.local.yaml local")
    sys.exit(3)
merge = getattr(cfg, "settings_dict", None)          # a target without it does not read settings.local.yaml at all
try:
    cfg.Settings.model_validate(merge(base) if merge else cfg.load_yaml(base))
except ValidationError as e:
    for err in e.errors():
        loc = tuple(err.get("loc", ()))
        kind = "unknown" if err.get("type") == "extra_forbidden" else "invalid"
        print(kind, name(loc), "local" if present(local, loc) else "tracked")
    sys.exit(3)
except Exception:
    print("unreadable settings tracked")
    sys.exit(3)
PY
)

DROP_PY=$(cat <<'PY'
import datetime, os, re, sys
import yaml
path, keys = sys.argv[1], sys.argv[2:]
with open(path, encoding="utf-8") as fh:
    raw = fh.read()
data = yaml.safe_load(raw) or {}
for key in keys:
    parts = key.split(".")
    node, trail = data, []
    for p in parts[:-1]:
        q = int(p) if isinstance(node, list) and re.fullmatch(r"[0-9]+", p) else p
        if isinstance(node, dict) and q in node or isinstance(node, list) and isinstance(q, int) and q < len(node):
            trail.append((node, q))
            node = node[q]
        else:
            node = None
            break
    if isinstance(node, dict):
        node.pop(parts[-1], None)
        for parent, q in reversed(trail):                 # drop sections the removal left empty
            if parent[q] == {} and isinstance(parent, dict):
                del parent[q]
            else:
                break
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
bak = f"{path}.{stamp}.bak"
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)                   # best effort: Linux has it; Windows (Wine) does not
fd = os.open(bak, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write(raw)
tmp = f"{path}.tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | NOFOLLOW, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write(yaml.safe_dump(data, sort_keys=False) if data else "")
os.replace(tmp, path)
print(os.path.basename(bak))
PY
)

target_py() { if [ "$ROLE" = mt5 ]; then echo "env WINEDEBUG=-all DISPLAY=:99 wine $WINPY"; else echo "$PY_BRAIN"; fi; }

settings_problems() {  # $1 target; prints problems; returns 0 valid, 3 invalid, 4 could not check
  local localf=$ROOT/config/settings.local.yaml dir rc=0 py
  as_owner test -f "$localf" || return 0
  read -r -a py <<< "$(target_py)"
  dir=$(as_owner mktemp -d /tmp/goldbot-deploy-check.XXXXXX) || return 4
  if g archive "$1" goldbot config | as_owner tar -x -C "$dir" \
      && as_owner cp -- "$localf" "$dir/config/settings.local.yaml"; then
    as_owner "${py[@]}" -I -c "$SETTINGS_PY" "$(pypath "$dir")" || rc=$?
  else
    rc=4
  fi
  as_owner rm -rf -- "$dir"
  return "$rc"
}

check_settings() {  # refuse (default) or, with --drop-unknown-local-keys, move unknown local keys aside
  local out rc=0 kind key where names=() keys=() droppable=1 list bak py
  out=$(settings_problems "$TARGET") || rc=$?
  [ "$rc" = 0 ] && return 0
  if [ "$rc" != 3 ]; then
    if g diff --quiet "$CUR" "$TARGET" -- pyproject.toml; then
      refuse "settings.local.yaml could not be checked with the target code; nothing changed"
    fi
    echo "note: settings.local.yaml not checked (this commit changes the dependencies); the 90 s check decides"
    return 0
  fi
  while read -r kind key where; do
    [ -n "$kind" ] || continue
    [[ "$key" =~ ^[A-Za-z0-9_.\<\>-]{1,200}$ ]] || key='<key>'
    names+=("$key")
    if [ "$kind" = unknown ] && [ "$where" = local ] && [[ "$key" != *'<'* ]]; then keys+=("$key"); else droppable=0; fi
  done <<< "$out"
  list=$(IFS=,; echo "${names[*]:-<settings>}")
  if [ "$DROP_KEYS" != 1 ]; then
    refuse "settings.local.yaml does not validate with the target code (keys: $list); edit it, or pass --drop-unknown-local-keys to move unknown keys aside; nothing changed"
  fi
  [ "$droppable" = 1 ] && [ "${#keys[@]}" -gt 0 ] \
    || refuse "settings problems --drop-unknown-local-keys cannot fix (keys: $list); only unknown keys in settings.local.yaml are moved; nothing changed"
  read -r -a py <<< "$(target_py)"
  bak=$(as_owner "${py[@]}" -I -c "$DROP_PY" "$(pypath "$ROOT/config/settings.local.yaml")" "${keys[@]}") \
    || refuse "could not move the unknown settings.local.yaml keys aside; nothing changed"
  [[ "$bak" =~ ^settings\.local\.yaml\.[0-9]{8}T[0-9]{6}Z\.bak$ ]] || bak="settings.local.yaml.<date>.bak"
  rc=0; settings_problems "$TARGET" > /dev/null || rc=$?
  [ "$rc" = 0 ] || refuse "settings.local.yaml still does not validate after moving keys aside (original kept as config/$bak)"
  echo "moved aside from settings.local.yaml: $list (original kept as config/$bak)"
  DROPPED="moved aside: $list, original config/$bak; "
}

# ---------------------------------------------------------------- scheduler-job guard (BACKLOG 40; vps_update.ps1 steps 3-4)
JOBS_PY=$(cat <<'PY'
import json, re, sys
from datetime import datetime
try:
    jobs = json.loads(sys.stdin.read()).get("jobs", {})
except (ValueError, AttributeError):
    sys.exit(0)
def later(a, b):
    try:
        return datetime.fromisoformat(str(a)) > datetime.fromisoformat(str(b))
    except (ValueError, TypeError):
        return str(a) > str(b)
running = [n for n, s in jobs.items() if isinstance(s, dict) and s.get("last_started")
           and (not s.get("last_finished") or later(s["last_started"], s["last_finished"]))]
print(",".join(n if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", str(n)) else "<job>" for n in running))
PY
)
running_jobs() {  # scheduler jobs mid-run: aux.db through the running code, else scheduler.json (as the service user)
  as_owner bash -c 'cd -- "$1" && { "$2" -m goldbot.db scheduler-status --state-dir state 2>/dev/null \
    || cat state/scheduler.json 2>/dev/null; }' _ "$ROOT" "$PY_BRAIN" | head -c 1048576 | python3 -I -c "$JOBS_PY" || true
}

guard_scheduler() {  # brain: refuse while a job runs; stop the scheduler first, then check again
  [ "$ROLE" = brain ] && [ "$INTERRUPT_JOBS" != 1 ] || return 0
  systemctl is-active --quiet goldbot-scheduler 2>/dev/null || return 0
  local jobs; jobs=$(running_jobs)
  if [ -n "$jobs" ]; then
    [ -n "$MANUAL" ] || exit 0                         # keep the approval; apply once the job has finished
    echo "scheduler job $jobs is running; deploy again after it finishes, or pass --interrupt-jobs"; exit 1
  fi
  systemctl stop goldbot-scheduler || true
  jobs=$(running_jobs)
  if [ -n "$jobs" ]; then
    systemctl start goldbot-scheduler || true
    [ -n "$MANUAL" ] || exit 0
    echo "scheduler job $jobs started while the scheduler was stopping; scheduler restarted, nothing else changed; try again later"
    exit 1
  fi
}

# ---------------------------------------------------------------- switch, check
services() {
  if [ "$ROLE" = mt5 ]; then
    systemctl list-units --type=service --all --no-legend --plain 'goldbot-bridge@*' | awk '{print $1}'
  else
    systemctl list-units --type=service --all --no-legend --plain 'goldbot-*' | awk '{print $1}' \
      | grep -v -e '^goldbot-deploy' -e '^goldbot-bridge-tunnel' -e '^goldbot-vnc' || true
  fi
}

stop_all() {  # inputs first, then the supervisor, engines and bridges last (exits run until the last moment)
  local s
  for s in "$@"; do case "$s" in goldbot-supervisor* | goldbot-engine@* | goldbot-bridge@*) ;; *) systemctl stop "$s" || return 1 ;; esac; done
  for s in "$@"; do case "$s" in goldbot-supervisor*) systemctl stop "$s" || return 1 ;; esac; done
  for s in "$@"; do case "$s" in goldbot-engine@* | goldbot-bridge@*) systemctl stop "$s" || return 1 ;; esac; done
  return 0
}

install_for() {  # reinstall Python deps / rebuild the dashboard only when they changed between $1 and $2
  local changed; changed=$(g diff --name-only "$1" "$2")
  if [ "$ROLE" = brain ]; then
    if grep -qE '^(pyproject.toml|uv.lock)$' <<< "$changed"; then     # exact versions from uv.lock, as the bootstrap
      sudo -u goldbot -H bash -c "cd $ROOT && /var/lib/goldbot/.local/bin/uv sync -q --frozen --extra live --python 3.11" || return 1
    fi
    if grep -q '^web/' <<< "$changed"; then sudo -u goldbot -H bash -c "cd $ROOT/web && npm ci --silent && npm run build" || return 1; fi
  elif grep -qE "^(uv.lock|$BRIDGE_REQS)\$" <<< "$changed" && as_owner test -f "$ROOT/$BRIDGE_REQS"; then
    # MT5 box: exact versions + sha256 from uv.lock, wheels only (scripts/bridge_requirements.py). A commit from before
    # the file existed (a rollback) keeps the installed packages: older code never pinned them anyway. The bridges stop
    # first: Wine cannot replace a .pyd a running bridge has loaded (half-installed package); switch_to starts them.
    local svcs; mapfile -t svcs < <(services)
    stop_all "${svcs[@]}" || return 1
    sudo -u mt5 -H env WINEDEBUG=-all DISPLAY=:99 wine "$WINPY" -m pip install -q --require-hashes --no-deps \
      --only-binary :all: -r "Z:$ROOT/$BRIDGE_REQS" || return 1
  fi
  return 0
}

declare -A RESTARTS
switch_to() {  # $1 from, $2 to; returns non-zero on any failure (the caller rolls back)
  local aux svcs s
  aux=$(aux_plan "$2")
  if [[ "$aux" == refuse* ]]; then echo "aux.db schema ${aux#refuse }: $2 cannot open it and no step-back is known"; return 1; fi
  g checkout -q --detach "$2" || return 1
  install_for "$1" "$2" || return 1
  mapfile -t svcs < <(services)
  if [ "$aux" = step ]; then                          # nothing may hold aux.db open while its version changes
    stop_all "${svcs[@]}" || return 1
    aux_step_back || return 1
  fi
  STARTED=$(date +%s)
  # supervisor first: engines block entries until its heartbeat exists. Restarts are safe: engines reconcile on start.
  # Only enabled units: a loaded but disabled instance (a stray engine) is never started by a deploy.
  for s in "${svcs[@]}"; do [[ "$s" == goldbot-supervisor* ]] && enabled "$s" && { systemctl restart "$s" || return 1; }; done
  for s in "${svcs[@]}"; do [[ "$s" == goldbot-supervisor* ]] || ! enabled "$s" || { systemctl restart "$s" || return 1; }; done
  for s in "${svcs[@]}"; do RESTARTS[$s]=$(systemctl show -p NRestarts --value "$s"); done
  return 0
}

written_since_restart() {  # the heartbeat file was written by the restarted process (not the old one)
  local t; t=$(mtime "$1" 2>/dev/null) || return 1
  [[ "$t" =~ ^[0-9]+$ ]] && [ "$t" -gt "${STARTED:-0}" ]
}

healthy() {  # every enabled service up, and not restarted by systemd since our restart, for 90 s; a new supervisor heartbeat
  sleep 90
  local s; for s in $(services); do
    enabled "$s" || continue
    systemctl is-active --quiet "$s" || return 1
    [ "$(systemctl show -p NRestarts --value "$s")" = "${RESTARTS[$s]:-0}" ] || return 1   # a crash loop restarts
  done
  # the supervisor writes its heartbeat every few seconds whatever the market; engines write only on ticks (none at
  # weekends), so for them "active and not restarted for 90 s" is the check
  if [ "$ROLE" = brain ] && enabled goldbot-supervisor; then
    written_since_restart "$ROOT/state/supervisor.json" || return 1
  fi
}

floor_allows() {  # $1 target. 0: no floor, or the floor itself or a descendant of it (git AND GitHub agree);
  # 1: below the floor, or an unreadable floor; 2: GitHub cannot confirm (offline, rate limit, unreadable answer)
  [ -e "$ROLLBACK_FLOOR" ] || [ -L "$ROLLBACK_FLOOR" ] || return 0
  [ -f "$ROLLBACK_FLOOR" ] && [ ! -L "$ROLLBACK_FLOOR" ] || return 1
  local floor; floor=$(head -c 41 "$ROLLBACK_FLOOR" | tr -d '[:space:]')
  is_sha "$floor" && g merge-base --is-ancestor "$floor" "$1" 2>/dev/null || return 1
  case "$(github_descends "$floor" "$1")" in
    yes) return 0 ;;
    no) return 1 ;;
    *) return 2 ;;
  esac
}

github_descends() {  # $1 floor, $2 target: yes / no / unknown, from GitHub's compare API (root, not the service user's
  # .git, which that account can rewrite); unknown when offline or rate-limited, which refuses (floor_allows)
  curl -fsS -m 20 -H "Accept: application/vnd.github+json" "$REPO_API/compare/$1...$2?per_page=1" 2>/dev/null \
    | head -c 8388608 | python3 -I -c "
import json, sys
try:
    s = json.load(sys.stdin).get('status')
except Exception:
    s = None
print({'ahead': 'yes', 'identical': 'yes', 'behind': 'no', 'diverged': 'no'}.get(s, 'unknown'))" || echo unknown
}

# ---------------------------------------------------------------- health baseline (BACKLOG 41)
WORSE_PY=$(cat <<'PY'
import json, re, sys
RANK = {"ok": 0, "warn": 1, "fail": 2}
def load(path):
    with open(path, encoding="utf-8") as fh:
        return {str(c["name"]): RANK.get(c.get("status"), 0) for c in json.loads(fh.read(8388608))["checks"]}
before, after = load(sys.argv[1]), load(sys.argv[2])
stopped = [a for a in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", a)]
SKIP = {p + ":" + a for a in stopped for p in ("engine", "engine_loop", "tick_errors")}
worse = sorted(n for n, r in after.items() if r > before.get(n, 0))
skipped = [n for n in worse if n in SKIP]
worse = [n for n in worse if n not in SKIP]
print(",".join(n if re.fullmatch(r"[A-Za-z0-9_.:@-]{1,64}", n) else "<check>" for n in worse))
if skipped:
    print("engine stopped for the deploy: " + ",".join(sorted({n.split(":", 1)[1] for n in skipped})))
PY
)
HEALTH_BEFORE=logs/health_before_deploy.json
HEALTH_AFTER=logs/health_after_deploy.json

health_out() {  # $1 file under $ROOT: `goldbot run health --out`, as the service user, with the code that runs now
  local t=(); command -v timeout > /dev/null && t=(timeout 180)
  as_owner bash -c 'cd -- "$1" && mkdir -p logs && rm -f -- "$3" && "${@:4}" "$2" -m goldbot.ops.run health --out "$3" > /dev/null 2>&1; test -s "$3"' \
    _ "$ROOT" "$PY_BRAIN" "$1" "${t[@]}"
}

ENGINES_PY=$(cat <<'PY'
import json, re, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    names = [str(c["name"]) for c in json.loads(fh.read(8388608))["checks"]]
print(" ".join(sorted({n.split(":", 1)[1] for n in names if n.startswith("engine_loop:") and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", n.split(":", 1)[1])})))
PY
)
STOPPED=""
stopped_engines() {  # accounts whose engine unit is disabled or inactive now (comma list); called at the baseline
  local a out=""
  for a in $(as_owner bash -c 'cd -- "$1" && python3 -I -c "$2" "$3"' _ "$ROOT" "$ENGINES_PY" "$HEALTH_BEFORE" 2>/dev/null | head -c 2000); do
    [[ "$a" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || continue
    if ! enabled "goldbot-engine@$a.service" || ! systemctl is-active --quiet "goldbot-engine@$a.service"; then out+="${out:+,}$a"; fi
  done
  echo "$out"
}

health_worse() {  # names of checks worse after than before ("" when none or when either report is missing); a second
  local out       # line "engine stopped for the deploy: <accounts>" when stopped engines hid a worsened check
  out=$(as_owner bash -c 'cd -- "$1" && python3 -I -c "$2" "$3" "$4" "$5"' _ "$ROOT" "$WORSE_PY" "$HEALTH_BEFORE" "$HEALTH_AFTER" "$STOPPED" \
    2>/dev/null | head -c 2000) || return 0
  [[ "$out" =~ ^[A-Za-z0-9_.:@,\<\>\ $'\n'-]*$ ]] && echo "$out"
}

main() {
  [ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
  # A dropped SSH session (SIGHUP) or a dead output filter (SIGPIPE) must never stop a deploy half-way (services
  # stopped, aux.db stepped back): both are ignored from here on, children inherit that, and when the filter dies
  # (its terminal is gone) `cat` keeps draining the pipe so no write fails. deploys.jsonl is the authoritative
  # record; the tail of the terminal output can be lost.
  trap '' PIPE HUP
  exec > >(LC_ALL=C tr -cd '[:print:]\n\t'; cat > /dev/null) 2>&1   # no terminal escapes reach root's terminal
  ROLE=$(cat "$ROLE_FILE" 2>/dev/null || echo brain)
  if [ "$ROLE" = mt5 ]; then OWNER=mt5; else OWNER=goldbot; fi
  TARGET=""
  DROPPED=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --rollback) [ $# -ge 2 ] && [ "$MODE" = approval ] || usage; MODE=rollback; TARGET=$2; shift ;;
      --drop-unknown-local-keys) DROP_KEYS=1 ;;
      --interrupt-jobs) INTERRUPT_JOBS=1 ;;
      -*) usage ;;
      *) [ "$MODE" = approval ] || usage; MODE=deploy; TARGET=$1 ;;
    esac
    shift
  done
  if [ "$MODE" = approval ] && { [ "$DROP_KEYS" = 1 ] || [ "$INTERRUPT_JOBS" = 1 ]; }; then usage; fi
  [ "$MODE" = approval ] || MANUAL=1

  install -d -m 700 -o root -g root "$VAR"
  exec 9>"$LOCK"
  if ! flock -n 9; then [ -n "$MANUAL" ] && echo "another deploy is running; try again in a minute"; exit 0; fi

  if [ -n "$MANUAL" ]; then
    g fetch -q origin main
    if [ "$TARGET" = latest ] && [ "$MODE" = deploy ]; then TARGET=$(g rev-parse origin/main); fi
    is_sha "$TARGET" || usage
  elif [ "$ROLE" = brain ]; then
    as_owner test -e "$APPROVED" || exit 0
    TARGET=$(read_approval)
    CUR=""
    if [ -z "$TARGET" ]; then log refused "" "" "invalid approval file (ignored)"; drop_approval; exit 1; fi
  else
    exit 0                                            # the MT5 box is updated by hand only
  fi

  g fetch -q origin main
  CUR=$(g rev-parse HEAD)
  is_sha "$CUR" || { CUR=""; refuse "the running commit could not be read as a commit id; nothing changed"; }
  if [ "$CUR" = "$TARGET" ]; then drop_approval; [ -n "$MANUAL" ] && echo "already running $TARGET"; exit 0; fi
  g merge-base --is-ancestor "$TARGET" origin/main || refuse "commit is not on main"
  if [ "$MODE" = rollback ]; then
    g merge-base --is-ancestor "$TARGET" "$CUR" \
      || refuse "a rollback goes only to an older commit of the running one (for a newer one: goldbot-deploy <sha>)"
    local frc=0; floor_allows "$TARGET" || frc=$?
    case "$frc" in
      0) ;;
      2) refuse "GitHub can't confirm the floor; remove $ROLLBACK_FLOOR deliberately to proceed" ;;
      *) refuse "target is below the rollback floor ($ROLLBACK_FLOOR), or the floor is not a known commit id" ;;
    esac
    if [ "$(ci_passed "$TARGET")" != yes ] && ! ran_here "$TARGET"; then
      refuse "rollback target neither passed CI nor ran here before"
    fi
  else
    g merge-base --is-ancestor "$CUR" "$TARGET" \
      || refuse "not a fast-forward of the running commit (to go back: goldbot-deploy --rollback <sha>)"
    [ "$(ci_passed "$TARGET")" = yes ] || refuse "CI has not passed on this commit"
  fi

  if [ "$ROLE" = brain ] && as_owner sh -c 'ls -- "$1"/*.json' _ "$ROOT/state/approvals/pending" > /dev/null 2>&1; then
    [ -n "$MANUAL" ] && { echo "an entry awaits your approval; deploy again after it is decided"; exit 1; }
    exit 0                                            # keep the approval; apply once the entry is decided
  fi

  local aux; aux=$(aux_plan "$TARGET")
  [[ "$aux" != refuse* ]] \
    || refuse "aux.db is at schema ${aux#refuse }, which the target cannot open and no step-back is known; nothing changed"
  check_settings
  BASELINE=0
  if [ "$ROLE" = brain ]; then
    if health_out "$HEALTH_BEFORE"; then BASELINE=1; STOPPED=$(stopped_engines); else echo "note: no health baseline (health did not run); no comparison after the deploy"; fi
  fi
  guard_scheduler
  if [ "$MODE" = rollback ] && as_owner test -e "$APPROVED"; then   # a newer approved version must not follow
    as_owner rm -f -- "$APPROVED"
    echo "a pending Telegram deploy approval was cancelled by this rollback"
  fi

  set +e                                              # from here every failure leads to a rollback, never an abort
  switch_to "$CUR" "$TARGET" && healthy
  local ok=$?
  drop_approval
  if [ "$ok" = 0 ]; then
    local worse="" note="" raw=""
    if [ "$BASELINE" = 1 ]; then
      health_out "$HEALTH_AFTER" && raw=$(health_worse)
      worse=${raw%%$'\n'*}
      [ "$raw" = "$worse" ] || note="${raw#*$'\n'}; "
    fi
    if [ "$MODE" = rollback ]; then
      log rolled_back_manual "$CUR" "$TARGET" "${DROPPED}${note}${worse:+degraded: $worse; }older version restored on the owner's request; all services stable for 90 s"
    elif [ -n "$worse" ]; then
      log deployed_degraded "$CUR" "$TARGET" "${DROPPED}${note}deployed (degraded: $worse); all services stable for 90 s${MANUAL:+ (manual)}; not rolled back: goldbot run health"
    else
      log deployed "$CUR" "$TARGET" "${DROPPED}${note}all services stable for 90 s${MANUAL:+ (manual)}"
    fi
    exit 0
  fi
  switch_to "$TARGET" "$CUR" && healthy
  if [ $? = 0 ]; then
    if [ "$MODE" = rollback ]; then
      log rollback_reverted "$CUR" "$TARGET" "${DROPPED}the rollback failed or a service was unstable; the version that ran before is restored"
    else
      log rolled_back "$CUR" "$TARGET" "${DROPPED}the update failed or a service was unstable; previous version restored"
    fi
  else
    log failed "$CUR" "$TARGET" "${DROPPED}rollback did not recover every service: check journalctl -u 'goldbot-*'"
  fi
  exit 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
