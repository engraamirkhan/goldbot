"""goldbot-deploy (goldbot/ops/linux/goldbot-deploy.sh) against a temporary git repository, a temporary aux.db and a
stubbed systemctl/sudo/curl (BACKLOG 37-40): the rollback steps aux.db back 5 -> 4 with every service stopped and every
row kept, refuses any other unknown version before anything changes, `--rollback` goes only to an older commit that
passed CI or ran here, settings.local.yaml is validated with the TARGET's own Settings model (keys named, values never
shown; unknown keys moved aside only with the owner's flag), and a running scheduler job blocks the deploy.

The script is sourced (its `main` runs only when executed), so the root-run copy gains no test hooks."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "goldbot" / "ops" / "linux" / "goldbot-deploy.sh"


def _bash() -> str | None:
    for b in ("/opt/homebrew/bin/bash", "/usr/local/bin/bash", shutil.which("bash")):
        if not b or not Path(b).exists():
            continue
        out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout.strip()
        if out.isdigit() and int(out) >= 4:
            return b
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("git") is None,
                                reason="needs bash >= 4 (associative arrays, mapfile) and git, as on the VMs")

HARNESS = r"""
set -euo pipefail
export HARNESS_PID=$$
source "$SCRIPT"
ROOT=$T/root; VAR=$T/var; LOG=$VAR/deploys.jsonl; LOCK=$T/lock; ROLE_FILE=$T/role; ROLLBACK_FLOOR=$T/floor
APPROVED=$ROOT/state/deploy/approved.json; PY_BRAIN=$PYTHON
if [ -n "${FAKE_HEAD:-}" ]; then
  eval "$(declare -f g | sed '1s/^g ()/real_g ()/')"
  g() { if [ "$1 ${2:-}" = "rev-parse HEAD" ]; then echo "$FAKE_HEAD"; else real_g "$@"; fi; }
fi
id() { echo 0; }
install() { mkdir -p "${@: -1}"; }
flock() { return 0; }
sleep() { :; }
stat() { echo 9999999999; }                     # GNU stat -c %Y, run only through sudo -u (as the owner)
sudo() {
  local who=root; while [ $# -gt 0 ]; do case "$1" in -u) who=$2; shift 2 ;; -H) shift ;; *) break ;; esac; done
  echo "$who $*" >> "$T/sudo.log"
  [ -z "${EVIL:-}" ] || printf 'svc\033]0;owned\007\033[2Jout\n' >&2   # service-user output with terminal escapes
  "$@"
}
curl() { case "$*" in *compare/*) cat "$T/compare.json" 2>/dev/null || return 22 ;; *) cat "$T/ci.json" ;; esac; }
systemctl() { echo "$*" >> "$T/systemctl.log"; "$PYTHON" "$T/systemctl_stub.py" "$@"; }
main "$@"
"""

# units: name -> {"enabled": bool, "active": bool}; "fail_at": services report inactive while HEAD is that commit;
# "migrate_at": a (re)start while HEAD is that commit migrates aux.db 4 -> 5, as the new code does on first open
SYSTEMCTL_STUB = r"""
import fnmatch, json, os, sqlite3, subprocess, sys
T = os.environ["T"]
st_path = os.path.join(T, "units.json")
st = json.load(open(st_path))
args = [a for a in sys.argv[1:] if not a.startswith("--") and a not in ("-p",)]
cmd = args[0]
def norm(n): return n if n.endswith(".service") else n + ".service"
def head(): return subprocess.run(["git", "-C", os.path.join(T, "root"), "rev-parse", "HEAD"], capture_output=True,
                                  text=True).stdout.strip()
if cmd == "list-units":
    pat = norm(sys.argv[-1])
    for n in st["units"]:
        if fnmatch.fnmatch(n, pat):
            print(f"{n} loaded active running x")
elif cmd == "is-enabled":
    sys.exit(0 if st["units"].get(norm(args[1]), {}).get("enabled") else 1)
elif cmd == "is-active":
    u = st["units"].get(norm(args[1]), {})
    sys.exit(0 if u.get("active") and head() != st.get("fail_at") else 3)
elif cmd == "show":
    print(0)
elif cmd in ("stop", "start", "restart"):
    marker = os.path.join(T, "killed")
    if cmd == "restart" and os.environ.get("KILL_FILTER") and not os.path.exists(marker):
        # the owner's SSH session drops mid-deploy: the output filter dies and the script gets SIGHUP
        open(marker, "w").close()
        import signal
        hp = int(os.environ["HARNESS_PID"])
        ps = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True).stdout
        procs = {}
        for line in ps.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3:
                procs[int(parts[0])] = (int(parts[1]), parts[2].strip())
        def under(pid):
            while pid in procs and pid > 1:
                pid = procs[pid][0]
                if pid == hp:
                    return True
            return False
        for pid, (_, comm) in procs.items():
            if os.path.basename(comm) == "tr" and under(pid):
                os.kill(pid, signal.SIGTERM)
                open(os.path.join(T, "filter_killed"), "w").close()
        os.kill(hp, signal.SIGHUP)
    st["units"].setdefault(norm(args[1]), {"enabled": False})["active"] = cmd != "stop"
    if cmd != "stop":
        open(os.path.join(T, "restarted"), "w").close()
    if cmd != "stop" and norm(args[1]) == "goldbot-supervisor.service":
        open(os.path.join(T, "root", "state", "supervisor.json"), "w").write("{}")   # its heartbeat
    if cmd != "stop" and head() == st.get("migrate_at"):
        c = sqlite3.connect(os.path.join(T, "root", "state", "aux.db"))
        if c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 4:
            c.execute("INSERT INTO schema_version VALUES (5, 'm5', 0, 4)")
            c.execute("PRAGMA user_version = 5")
            c.commit()
    json.dump(st, open(st_path, "w"))
"""

CONFIG_OLD = '''
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Risk(_Section):
    max_risk: float = 0.01


class Telegram(_Section):
    owner_ids: list[int] = []


class Settings(_Section):
    risk: Risk = Risk()
    telegram: Telegram = Telegram()


def load_yaml(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _deep_merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def settings_dict(path):
    raw = load_yaml(path) or {}
    local = Path(path).with_name("settings.local.yaml")
    return _deep_merge(raw, load_yaml(local) or {}) if local.exists() else raw
'''
CONFIG_NEW = CONFIG_OLD.replace("    max_risk: float = 0.01\n",
                                "    max_risk: float = 0.01\n    min_lot_risk_cap: float | None = None\n")

FAKE_HEALTH = """
import os, shutil, sys
T = os.environ["T"]
args = sys.argv[1:]
assert args[0] == "health", args
out = args[args.index("--out") + 1]
src = os.path.join(T, "health_after.json" if os.path.exists(os.path.join(T, "restarted")) else "health_before.json")
if not os.path.exists(src):
    sys.exit(2)
shutil.copy(src, out)
"""

UNITS = {"goldbot-supervisor.service": True, "goldbot-engine@icm-demo.service": True, "goldbot-api.service": True,
         "goldbot-scheduler.service": True, "goldbot-telegram.service": True,
         "goldbot-engine@vantage-demo.service": False}          # a loaded but disabled stray instance


def _git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
           "HOME": str(cwd)}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def make_aux(path: Path, version: int) -> None:
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT, applied_ns INTEGER, "
              "compat_back_to INTEGER)")
    c.executemany("INSERT INTO schema_version VALUES (?, ?, 0, NULL)", [(v, f"m{v}") for v in range(1, version + 1)])
    c.execute("CREATE TABLE notes (x TEXT)")
    c.executemany("INSERT INTO notes VALUES (?)", [("a",), ("b",), ("c",)])
    c.execute(f"PRAGMA user_version = {version}")
    c.commit()
    c.close()


def aux_state(path: Path) -> tuple[int, int, int]:
    c = sqlite3.connect(path)
    try:
        return (c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0],
                c.execute("PRAGMA user_version").fetchone()[0], c.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
    finally:
        c.close()


class Box:
    def __init__(self, tmp: Path, running: str, aux: int | None = 5) -> None:
        self.t = tmp
        origin = tmp / "origin"
        origin.mkdir(parents=True)
        _git(origin, "init", "-q", "-b", "main")
        base = {"goldbot/__init__.py": "", "goldbot/ops/__init__.py": "", "goldbot/ops/run.py": FAKE_HEALTH,
                "config/settings.yaml": "risk:\n  max_risk: 0.01\n",
                ".gitignore": "config/settings.local.yaml\nstate/\n"}
        self.c1 = _commit(origin, {**base, "goldbot/config.py": CONFIG_OLD,
                                   "goldbot/db/migrations.py": "MIGRATIONS = (1, 2, 3, 4)\n"}, "old")
        self.c2 = _commit(origin, {"goldbot/config.py": CONFIG_NEW,
                                   "goldbot/db/migrations.py": "MIGRATIONS = (1, 2, 3, 4, 5)\ncompat_back_to = 4\n"},
                          "new: state store step 3")
        subprocess.run(["git", "clone", "-q", str(origin), str(tmp / "root")], check=True, capture_output=True)
        self.root = tmp / "root"
        _git(self.root, "checkout", "-q", "--detach", self.c1 if running == "c1" else self.c2)
        (self.root / "state").mkdir()
        self.aux = self.root / "state" / "aux.db"
        if aux is not None:
            make_aux(self.aux, aux)
        (tmp / "role").write_text("brain\n")
        (tmp / "harness.sh").write_text(HARNESS)
        (tmp / "systemctl_stub.py").write_text(SYSTEMCTL_STUB)
        self.units({})
        self.ci("success")

    def units(self, extra: dict) -> None:
        st = {"units": {n: {"enabled": e, "active": e} for n, e in UNITS.items()}, **extra}
        (self.t / "units.json").write_text(json.dumps(st))

    def ci(self, conclusion: str) -> None:
        (self.t / "ci.json").write_text(json.dumps({"check_runs": [{"name": "backend", "conclusion": conclusion},
                                                                   {"name": "frontend", "conclusion": "success"}]}))

    def local(self, text: str) -> Path:
        p = self.root / "config" / "settings.local.yaml"
        p.write_text(text)
        return p

    def run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {"T": str(self.t), "SCRIPT": str(SCRIPT), "PYTHON": sys.executable,
               "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
               "HOME": str(self.t), **(env or {})}
        assert BASH is not None
        return subprocess.run([BASH, str(self.t / "harness.sh"), *args], capture_output=True, text=True, env=env,
                              timeout=120)

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD")

    def calls(self) -> list[str]:
        f = self.t / "systemctl.log"
        return f.read_text().splitlines() if f.exists() else []

    def actions(self) -> list[str]:
        return [c for c in self.calls() if c.split()[0] in ("stop", "start", "restart")]

    def records(self) -> list[dict]:
        f = self.t / "var" / "deploys.jsonl"
        return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_manual_rollback_steps_aux_back_with_services_stopped_and_is_audited(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    p = box.run("--rollback", box.c1)
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.head() == box.c1
    assert aux_state(box.aux) == (4, 4, 3)                                # every row kept
    rec = box.records()[-1]
    assert (rec["result"], rec["from"], rec["to"]) == ("rolled_back_manual", box.c2, box.c1)
    assert json.loads((box.root / "state" / "deploys.jsonl").read_text().splitlines()[-1]) == rec
    acts = box.actions()
    stops = [a for a in acts if a.startswith("stop")]
    first_start = next(i for i, a in enumerate(acts) if not a.startswith("stop"))
    assert all(acts.index(s) < first_start for s in stops)                # every stop before any start
    names = [s.split()[1] for s in stops]
    assert names.index("goldbot-api.service") < names.index("goldbot-supervisor.service") \
        < names.index("goldbot-engine@icm-demo.service")                  # inputs, supervisor, engines last
    restarts = [a.split()[1] for a in acts if a.startswith("restart")]
    assert restarts[0] == "goldbot-supervisor.service"
    assert "goldbot-engine@vantage-demo.service" not in restarts          # disabled units are never started


def test_automatic_rollback_from_a_schema_5_commit_ends_rolled_back(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    box.units({"fail_at": None, "migrate_at": None})
    st = json.loads((tmp_path / "units.json").read_text())
    st["fail_at"] = st["migrate_at"] = box.c2                            # the new code migrates, then is unstable
    (tmp_path / "units.json").write_text(json.dumps(st))
    p = box.run(box.c2)
    assert p.returncode == 1
    assert box.records()[-1]["result"] == "rolled_back", p.stdout + p.stderr
    assert box.head() == box.c1
    assert aux_state(box.aux) == (4, 4, 3)


def test_an_unknown_aux_version_is_refused_before_anything_changes(tmp_path):
    box = Box(tmp_path, running="c2", aux=6)
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1
    assert box.records()[-1]["result"] == "refused" and "schema 6" in box.records()[-1]["detail"]
    assert box.head() == box.c2 and box.actions() == [] and aux_state(box.aux) == (6, 6, 3)


def test_no_step_back_when_the_target_reads_compat_back_to(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.records()[-1]["result"] == "deployed"
    stops = [a for a in box.actions() if a.startswith("stop")]
    assert stops == ["stop goldbot-scheduler"]                            # only the job guard's; the rest restart


def test_rollback_goes_only_to_an_older_commit_and_a_forward_deploy_only_forward(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    p = box.run("--rollback", box.c2)
    assert p.returncode == 1 and "older commit" in box.records()[-1]["detail"]
    box2 = Box(tmp_path / "b", running="c2", aux=5)
    p = box2.run(box2.c1)
    assert p.returncode == 1 and "--rollback" in box2.records()[-1]["detail"]
    assert box.head() == box.c1 and box2.head() == box2.c2


def test_rollback_needs_ci_or_a_previous_run_here(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    box.ci("failure")
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1 and "neither passed CI nor ran here" in box.records()[-1]["detail"]
    assert box.head() == box.c2
    with (tmp_path / "var" / "deploys.jsonl").open("a") as f:           # a refusal's "from" proves nothing
        f.write(json.dumps({"result": "refused", "from": box.c1, "to": box.c2}) + "\n")
        f.write(json.dumps({"result": "deployed", "from": box.c1, "to": box.c2}) + "\n")   # nor a deploy's "from"
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1 and "neither passed CI nor ran here" in box.records()[-1]["detail"]
    with (tmp_path / "var" / "deploys.jsonl").open("a") as f:           # c1 was the result of a deploy
        f.write(json.dumps({"result": "deployed", "from": "0" * 40, "to": box.c1}) + "\n")
    p = box.run("--rollback", box.c1)
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.head() == box.c1


def test_unknown_local_key_refuses_naming_the_key_never_the_value(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    text = "risk:\n  min_lot_risk_cap: 0.004321\ntelegram:\n  owner_ids: [987654321]\n"
    local = box.local(text)
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1
    detail = box.records()[-1]["detail"]
    assert "risk.min_lot_risk_cap" in detail and "--drop-unknown-local-keys" in detail
    out = p.stdout + p.stderr + json.dumps(box.records())
    assert "0.004321" not in out and "987654321" not in out
    assert box.head() == box.c2 and box.actions() == [] and local.read_text() == text
    assert aux_state(box.aux) == (5, 5, 3)


def test_the_owner_flag_moves_unknown_local_keys_aside(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    text = "risk:\n  min_lot_risk_cap: 0.004321\ntelegram:\n  owner_ids: [987654321]\n"
    local = box.local(text)
    p = box.run("--rollback", box.c1, "--drop-unknown-local-keys")
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.head() == box.c1
    import yaml
    assert yaml.safe_load(local.read_text()) == {"telegram": {"owner_ids": [987654321]}}   # the empty risk: is gone
    baks = list(local.parent.glob("settings.local.yaml.*.bak"))
    assert len(baks) == 1 and baks[0].read_text() == text
    assert "moved aside: risk.min_lot_risk_cap" in box.records()[-1]["detail"]


def test_an_invalid_value_is_never_dropped(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    local = box.local("risk:\n  max_risk: lots\n  min_lot_risk_cap: 0.01\n")
    p = box.run("--rollback", box.c1, "--drop-unknown-local-keys")
    assert p.returncode == 1 and "cannot fix" in box.records()[-1]["detail"]
    assert "risk.max_risk" in box.records()[-1]["detail"]
    assert box.head() == box.c2 and not list(local.parent.glob("*.bak"))


def test_a_running_scheduler_job_blocks_the_deploy_unless_the_owner_interrupts_it(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    (box.root / "state" / "scheduler.json").write_text(json.dumps(
        {"jobs": {"retrain": {"last_started": "2026-10-10T06:00:00+00:00", "last_finished": None},
                  "backup": {"last_started": "2026-10-09T22:15:00+00:00", "last_finished": "2026-10-09T22:16:00+00:00"}}}))
    p = box.run(box.c2)
    assert p.returncode == 1 and "retrain" in p.stdout and "backup" not in p.stdout
    assert box.actions() == [] and box.head() == box.c1
    p = box.run(box.c2, "--interrupt-jobs")
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.head() == box.c2


def test_flags_without_a_target_are_a_usage_error(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    assert box.run("--drop-unknown-local-keys").returncode == 2
    assert box.run("--rollback").returncode == 2
    assert box.run("--rollback", "latest").returncode == 2


def test_the_settings_check_works_with_the_real_settings_model(tmp_path):
    """The validator runs the real goldbot/config.py (settings_dict, Settings, load_yaml) of this checkout."""
    repo = tmp_path / "real"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    files = {rel: (REPO / rel).read_text() for rel in ("goldbot/__init__.py", "goldbot/config.py",
                                                         "config/settings.yaml")}
    sha = _commit(repo, files, "real settings model")
    local = repo / "config" / "settings.local.yaml"
    script = (f'source "{SCRIPT}"; ROOT="{repo}"; ROLE=brain; OWNER=x; PY_BRAIN="{sys.executable}"\n'
              'sudo() { while [ $# -gt 0 ]; do case "$1" in -u) shift 2 ;; -H) shift ;; *) break ;; esac; done; "$@"; }\n'
              f'rc=0; settings_problems {sha} || rc=$?; echo "rc=$rc"\n')
    assert BASH is not None

    def check(text: str) -> str:
        local.write_text(text)
        return subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60).stdout

    assert check("risk:\n  min_lot_risk_cap: 0.01\n").strip().endswith("rc=0")
    out = check("risk:\n  not_a_key: 0.75\n")
    assert "unknown risk.not_a_key local" in out and "rc=3" in out and "0.75" not in out


def test_ran_here_counts_rolled_back_from_but_never_refused(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    box.ci("failure")
    (tmp_path / "var").mkdir()
    with (tmp_path / "var" / "deploys.jsonl").open("w") as f:          # c1 ran, then was restored after a failure
        f.write(json.dumps({"result": "rolled_back", "from": box.c1, "to": "f" * 40}) + "\n")
    p = box.run("--rollback", box.c1)
    assert p.returncode == 0, p.stdout + p.stderr


def test_a_running_commit_that_is_not_a_sha_is_refused(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    p = box.run("--rollback", box.c1, env={"FAKE_HEAD": "--output=/etc/x"})
    assert p.returncode == 1 and "running commit" in box.records()[-1]["detail"]
    assert box.head() == box.c2 and box.actions() == []


def test_the_rollback_floor_blocks_older_targets(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    (tmp_path / "floor").write_text(box.c2 + "\n")
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1 and "rollback floor" in box.records()[-1]["detail"]
    assert box.head() == box.c2 and box.actions() == []
    (tmp_path / "floor").write_text("not a sha\n")
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1 and "rollback floor" in box.records()[-1]["detail"]
    (tmp_path / "floor").write_text(box.c1 + "\n")                     # the floor itself is allowed
    (tmp_path / "compare.json").write_text(json.dumps({"status": "identical"}))
    p = box.run("--rollback", box.c1)
    assert p.returncode == 0, p.stdout + p.stderr


def test_a_floor_github_cannot_confirm_refuses_the_rollback(tmp_path):
    """Rate limit or offline: with a floor set, the service user's git alone never decides."""
    box = Box(tmp_path, running="c2", aux=5)
    (tmp_path / "floor").write_text(box.c1 + "\n")                     # git says: allowed
    p = box.run("--rollback", box.c1)                                    # no compare.json: curl fails
    assert p.returncode == 1
    assert "GitHub can't confirm the floor" in box.records()[-1]["detail"]
    assert "remove /etc/goldbot/rollback-floor deliberately" in box.records()[-1]["detail"] or \
        "deliberately to proceed" in box.records()[-1]["detail"]
    assert box.head() == box.c2 and box.actions() == []
    (tmp_path / "compare.json").write_text("{not json")                 # an unreadable answer is not a yes
    assert box.run("--rollback", box.c1).returncode == 1


def test_the_heartbeat_is_statted_as_the_service_user(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    assert box.run(box.c2).returncode == 0
    log = (tmp_path / "sudo.log").read_text().splitlines()
    assert any(line.startswith("goldbot stat -c %Y") and "supervisor.json" in line for line in log)
    assert not any(line.startswith("root ") for line in log)


def test_service_user_output_reaches_roots_terminal_without_escapes(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    p = box.run("--rollback", box.c1, env={"EVIL": "1"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "svc" in p.stdout + p.stderr and "\x1b" not in p.stdout + p.stderr and "\x07" not in p.stdout + p.stderr


def test_a_dropped_session_does_not_kill_a_deploy_midway(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    p = box.run("--rollback", box.c1, env={"KILL_FILTER": "1"})
    assert (tmp_path / "filter_killed").exists()                         # the filter really died mid-run
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.records()[-1]["result"] == "rolled_back_manual" and box.head() == box.c1
    assert aux_state(box.aux) == (4, 4, 3)


def test_github_overrides_the_service_users_git_on_the_floor(tmp_path):
    """The floor is checked against GitHub's compare API too (root, independent of the service user's .git)."""
    box = Box(tmp_path, running="c2", aux=5)
    (tmp_path / "floor").write_text(box.c1 + "\n")
    (tmp_path / "compare.json").write_text(json.dumps({"status": "behind"}))    # GitHub: target is older
    p = box.run("--rollback", box.c1)
    assert p.returncode == 1 and "rollback floor" in box.records()[-1]["detail"]
    (tmp_path / "compare.json").write_text(json.dumps({"status": "identical"}))
    p = box.run("--rollback", box.c1)
    assert p.returncode == 0, p.stdout + p.stderr


def _health(path: Path, **statuses: str) -> None:
    path.write_text(json.dumps({"ts": "2026-10-20T09:30:00Z", "status": "ok",
                                "checks": [{"name": n.replace("__", ":"), "status": st, "reason": "x"}
                                           for n, st in statuses.items()]}))


def test_a_deploy_that_worsens_health_is_reported_degraded_not_rolled_back(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _health(tmp_path / "health_before.json", supervisor="ok", daily_cap__icm="warn", news="fail", tick="ok")
    _health(tmp_path / "health_after.json", supervisor="warn", daily_cap__icm="warn", news="fail", tick="ok",
            engine__icm="fail")
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "deployed_degraded" and box.head() == box.c2           # no automatic rollback
    assert "deployed (degraded: engine:icm,supervisor)" in rec["detail"]
    log = (tmp_path / "sudo.log").read_text()
    assert "goldbot bash -c" in log and "health --out" in log                       # run as the service user


def _stop_engine(box: Box) -> None:
    st = json.loads((box.t / "units.json").read_text())
    st["units"]["goldbot-engine@icm-demo.service"] = {"enabled": False, "active": False}
    (box.t / "units.json").write_text(json.dumps(st))


def test_an_engine_stopped_on_purpose_for_the_deploy_is_not_a_degradation(tmp_path):
    """S13: the engine unit is disabled before goldbot-deploy; its heartbeat reads ok in the baseline, stale 90 s later."""
    box = Box(tmp_path, running="c1", aux=4)
    _stop_engine(box)
    _health(tmp_path / "health_before.json", **{"engine_loop:icm-demo": "ok", "engine:icm-demo": "ok", "supervisor": "ok"})
    _health(tmp_path / "health_after.json", **{"engine_loop:icm-demo": "fail", "engine:icm-demo": "warn", "supervisor": "ok"})
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "deployed" and "engine stopped for the deploy: icm-demo" in rec["detail"]


def test_a_stopped_engine_does_not_hide_another_degraded_check(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _stop_engine(box)
    _health(tmp_path / "health_before.json", **{"engine_loop:icm-demo": "ok", "supervisor": "ok"})
    _health(tmp_path / "health_after.json", **{"engine_loop:icm-demo": "fail", "supervisor": "warn"})
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "deployed_degraded" and "(degraded: supervisor)" in rec["detail"]
    assert "engine stopped for the deploy: icm-demo" in rec["detail"]


def test_a_stopped_engines_reconcile_failure_is_still_degraded(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _stop_engine(box)
    _health(tmp_path / "health_before.json", **{"engine_loop:icm-demo": "ok", "reconcile:icm-demo": "ok"})
    _health(tmp_path / "health_after.json", **{"engine_loop:icm-demo": "fail", "reconcile:icm-demo": "fail"})
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "deployed_degraded" and "(degraded: reconcile:icm-demo)" in rec["detail"]


def test_a_running_engine_that_goes_stale_is_still_degraded(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _health(tmp_path / "health_before.json", **{"engine_loop:icm-demo": "ok"})
    _health(tmp_path / "health_after.json", **{"engine_loop:icm-demo": "fail"})
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "deployed_degraded" and "degraded: engine_loop:icm-demo" in rec["detail"]


def test_an_unchanged_or_better_health_is_a_plain_deploy(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _health(tmp_path / "health_before.json", supervisor="warn", news="fail")
    _health(tmp_path / "health_after.json", supervisor="ok", news="fail")
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.records()[-1]["result"] == "deployed"


def test_the_embedded_comparison_agrees_with_the_health_module(tmp_path):
    import re as _re

    from goldbot.ops import health
    code = _re.search(r"WORSE_PY=\$\(cat <<'PY'\n(.*?)\nPY\n\)", SCRIPT.read_text(), _re.S)
    assert code, "WORSE_PY not found in goldbot-deploy.sh"
    before, after = tmp_path / "b.json", tmp_path / "a.json"
    _health(before, a="ok", b="warn", c="fail", gone="fail")
    _health(after, a="fail", b="ok", c="fail", new="warn", other="ok")
    out = subprocess.run([sys.executable, "-I", "-c", code.group(1), str(before), str(after)], capture_output=True,
                         text=True).stdout.strip()
    expect = health.worsened(health.HealthReport.model_validate_json(after.read_text()),
                             health.HealthReport.model_validate_json(before.read_text()))
    assert out == ",".join(expect) == "a,new"


def test_the_root_reads_the_sha_only_approval_file_the_bot_writes(tmp_path):
    """A15: approved.json holds only {"sha": ...} (who tapped is in the audit log); read_approval needs nothing else."""
    import getpass

    from goldbot.ops.deploy import DeployWatch
    sha = "b" * 40
    w = DeployWatch(tmp_path, tmp_path)
    w.dir.mkdir(parents=True)
    (w.dir / "pending.json").write_text(json.dumps({"sha": sha}))
    assert w.approve(sha)
    assert BASH is not None
    script = (f'source "{SCRIPT}"; APPROVED="{w.dir / "approved.json"}"; OWNER="{getpass.getuser()}"\n'
              'read_approval\n')
    out = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == sha, out.stderr
