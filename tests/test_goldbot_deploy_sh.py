"""goldbot-deploy (goldbot/ops/linux/goldbot-deploy.sh) against a temporary git repository, a temporary aux.db and a
stubbed systemctl/sudo/curl (BACKLOG 37-40): the rollback steps aux.db back 5 -> 4 with every service stopped and every
row kept, refuses any other unknown version before anything changes, `--rollback` goes only to an older commit that
passed CI or ran here, settings.local.yaml is validated with the TARGET's own Settings model (keys named, values never
shown; unknown keys moved aside only with the owner's flag), and a running scheduler job blocks the deploy.

The script is sourced (its `main` runs only when executed), so the root-run copy gains no test hooks."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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

def _ps_works() -> bool:
    """The kill-the-filter harness lists processes with ps; sandboxes (CI-parity sandbox-exec, some containers) forbid it."""
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin"}, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


HARNESS = r"""
set -euo pipefail
export HARNESS_PID=$$
source "$SCRIPT"
[ -z "${TEST_POLL_S:-}" ] || POLL_S=$TEST_POLL_S   # fewer fake-clock polls: each one spawns python stubs
ROOT=$T/root; VAR=$T/var; LOG=$VAR/deploys.jsonl; LOCK=$T/lock; ROLE_FILE=$T/role; ROLLBACK_FLOOR=$T/floor
APPROVED=$ROOT/state/deploy/approved.json; PY_BRAIN=$PYTHON
STAGE=$T/stage; STAGE_LOCK=$T/stage.lock; WATCH_FILE=$VAR/watch; UV=$T/bin/uv
if [ -n "${FAKE_HEAD:-}" ]; then
  eval "$(declare -f g | sed '1s/^g ()/real_g ()/')"
  g() { if [ "$1 ${2:-}" = "rev-parse HEAD" ]; then echo "$FAKE_HEAD"; else real_g "$@"; fi; }
fi
id() { echo 0; }
install() { mkdir -p "${@: -1}"; }
flock() { return 0; }
sleep() { echo $(( $(cat "$T/clock") + ${1:-1} )) > "$T/clock"; }      # a fake clock: sleeping only advances it
date() { if [ "${1:-}" = +%s ]; then cat "$T/clock"; else command date "$@"; fi; }
stat() { echo "${STAT_MTIME:-9999999999}"; }    # GNU stat -c %Y, run only through sudo -u (as the owner)
systemd-run() {
  case " $* " in *" --watch "*) echo "$*" >> "$T/watch_spawned"; return 0 ;; esac
  echo "$*" >> "$T/systemd_run.log"
  while [ $# -gt 0 ] && [ "$1" != -- ]; do shift; done
  shift
  "$@"
}
sudo() {
  local who=root; while [ $# -gt 0 ]; do case "$1" in -u) who=$2; shift 2 ;; -H) shift ;; *) break ;; esac; done
  echo "$who $*" >> "$T/sudo.log"
  [ -z "${EVIL:-}" ] || printf 'svc\033]0;owned\007\033[2Jout\n' >&2   # service-user output with terminal escapes
  "$@"
}
curl() { case "$*" in *compare/*) cat "$T/compare.json" 2>/dev/null || return 22 ;;
  *commits/*/check-runs*) local a="$*"; local s=${a#*commits/}; s=${s%%/*}; if [ -f "$T/ci-$s.json" ]; then cat "$T/ci-$s.json"; else cat "$T/ci.json"; fi ;;
  *) cat "$T/ci.json" ;; esac; }
systemctl() { echo "$*" >> "$T/systemctl.log"; "$PYTHON" "$T/systemctl_stub.py" "$@"; }
main "$@"
"""

# units: name -> {"enabled": bool, "active": bool}; "fail_at": services report inactive while HEAD is that commit;
# "migrate_at": a (re)start while HEAD is that commit migrates aux.db 4 -> 5, as the new code does on first open;
# "fail_units": unit -> commit ("*" = any): a (re)start of it fails and it stays inactive (an engine whose bridge is
# not logged in); "fail_after_stop": units that ran before and cannot start again once stopped
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
def failing(n):
    if n in st.get("fail_after_stop", []) and st["units"].get(n, {}).get("stopped"):
        return True
    w = st.get("fail_units", {}).get(n)
    return w is not None and (w == "*" or w == head())
def up(n):
    u = st["units"].get(n, {})
    return bool(u.get("active")) and head() != st.get("fail_at") and not failing(n)
if cmd == "list-units":
    pat = norm(sys.argv[-1])
    for n in st["units"]:
        if fnmatch.fnmatch(n, pat):
            print(f"{n} loaded active running x")
elif cmd == "is-enabled":
    sys.exit(0 if st["units"].get(norm(args[1]), {}).get("enabled") else 1)
elif cmd == "is-active":
    sys.exit(0 if up(norm(args[1])) else 3)
elif cmd == "show":
    if "ActiveState" in sys.argv:
        print("active" if up(norm(args[-1])) else "failed")
    elif "NRestarts" in sys.argv:
        calls = os.path.join(T, "show_calls")
        n = int(open(calls).read()) + 1 if os.path.exists(calls) else 1
        open(calls, "w").write(str(n))
        bump = os.path.exists(os.path.join(T, "bump")) or (os.environ.get("BUMP_AFTER") and n > int(os.environ["BUMP_AFTER"]))
        print(1 if bump else 0)
    else:
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
    if cmd == "stop":
        open(os.path.join(T, "stop_heads"), "a").write(head() + " " + norm(args[1]) + "\n")   # HEAD when it stopped
        st["units"].setdefault(norm(args[1]), {"enabled": False})["stopped"] = True
    st["units"].setdefault(norm(args[1]), {"enabled": False})["active"] = cmd != "stop" and not failing(norm(args[1]))
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
    if cmd != "stop" and failing(norm(args[1])) and "--no-block" not in sys.argv:
        sys.exit(1)                                   # a blocking (re)start of a unit that never becomes ready
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
if src.endswith("after.json"):                      # health_after_<n>.json: the n-th report after the restart, if given
    n = int(open(os.path.join(T, "after_calls")).read()) + 1 if os.path.exists(os.path.join(T, "after_calls")) else 1
    open(os.path.join(T, "after_calls"), "w").write(str(n))
    nth = os.path.join(T, f"health_after_{n}.json")
    src = nth if os.path.exists(nth) else src
if not os.path.exists(src):
    sys.exit(2)
shutil.copy(src, out)
"""

FAKE_NPM = """#!/bin/sh
echo "$PWD $*" >> "$T/npm.log"
case "$1" in
  ci) mkdir -p node_modules ;;
  run) mkdir -p dist && { cat src.txt 2>/dev/null || echo built; } > dist/index.html ;;
esac
"""
FAKE_UV = """#!/bin/sh
echo "$PWD env=$UV_PROJECT_ENVIRONMENT $*" >> "$T/uv.log"
[ -z "$UV_PROJECT_ENVIRONMENT" ] || mkdir -p "$UV_PROJECT_ENVIRONMENT"
"""

UNITS = {"goldbot-supervisor.service": True, "goldbot-engine@icm-demo.service": True, "goldbot-api.service": True,
         "goldbot-scheduler.service": True, "goldbot-telegram.service": True,
         "goldbot-engine@vantage-demo.service": False}          # a loaded but disabled stray instance


def _git_env(home: Path) -> dict[str, str]:
    """A git that reads nothing of the machine it runs on: no global or system config (a runner's or a developer's
    `safe.directory`, hooks path, templates, credential helper), no inherited GIT_DIR / GIT_INDEX_FILE (set when pytest
    runs from a git hook), HOME and the tool PATH pinned."""
    return {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(home), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _git(cwd: Path, *args: str) -> str:
    return _run_git(["git", "-C", str(cwd), *args], _git_env(cwd))


def _run_git(argv: list[str], env: dict[str, str]) -> str:
    """Run git; a failure names the command and git's own words (CI #167, #182, #217 failed a bare `git clone` with
    only "exit status 128": the stderr was captured and thrown away, so the cause was never seen)."""
    r = subprocess.run(argv, capture_output=True, text=True, env=env)
    if r.returncode:
        raise AssertionError(f"{' '.join(argv)} exited {r.returncode}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _clone(origin: Path, root: Path) -> None:
    """The brain box's checkout: a copy of `origin` sharing nothing with it (no hard links into the origin's objects,
    which a protected-hardlinks or cross-device tmp refuses) and run under the same hermetic git as `_git`."""
    _run_git(["git", "clone", "-q", "--no-hardlinks", str(origin), str(root)], _git_env(root.parent))


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
        _clone(origin, tmp / "root")
        self.root = tmp / "root"
        _git(self.root, "checkout", "-q", "--detach", self.c1 if running == "c1" else self.c2)
        (self.root / "state").mkdir()
        self.aux = self.root / "state" / "aux.db"
        if aux is not None:
            make_aux(self.aux, aux)
        (tmp / "role").write_text("brain\n")
        (tmp / "clock").write_text(str(int(time.time())))
        (tmp / "bin").mkdir()
        for name, body in (("npm", FAKE_NPM), ("uv", FAKE_UV)):
            (tmp / "bin" / name).write_text(body)
            (tmp / "bin" / name).chmod(0o755)
        self.origin = origin
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
        # TEST_POLL_S 10 (script: 2): the 90 s fake window is ~45 polls, twice for a failed deploy's recovery, each poll
        # spawning python stubs: ~85 s of real CPU per test, past the 120 s timeout under load. Same fake-clock semantics.
        env = {"T": str(self.t), "TEST_POLL_S": "10", "SCRIPT": str(SCRIPT), "PYTHON": sys.executable,
               "PATH": f"{self.t / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
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
    assert stops[0] == "stop goldbot-scheduler" and len(stops) == 6      # the job guard's, then one stop per enabled unit


def test_rollback_goes_only_to_an_older_commit_and_a_forward_deploy_only_forward(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    p = box.run("--rollback", box.c2)
    assert p.returncode == 1 and "older commit" in box.records()[-1]["detail"]
    box2 = Box(tmp_path / "b", running="c2", aux=5)
    p = box2.run(box2.c1)
    assert p.returncode == 1 and "--rollback" in box2.records()[-1]["detail"]
    assert box.head() == box.c1 and box2.head() == box2.c2


def _pr_merge(box: Box, *, main_moved: bool) -> tuple[str, str]:
    """A PR head on a branch off c2, merged into main with a merge commit (GitHub's merge method); with main_moved
    main got another commit first, so the merge's tree is not the PR head's."""
    o = box.origin
    _git(o, "checkout", "-q", "-b", "pr", box.c2)
    head = _commit(o, {"docs/note.md": "pr\n"}, "pr head")
    _git(o, "checkout", "-q", "main")
    if main_moved:
        _commit(o, {"docs/other.md": "main\n"}, "main moved")
    _git(o, "merge", "-q", "--no-ff", "--no-edit", "pr")
    return head, _git(o, "rev-parse", "HEAD")


def test_a_pr_merge_commit_with_the_heads_tree_deploys_on_the_heads_green_ci(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    head, merge = _pr_merge(box, main_moved=False)
    box.ci("failure")                                                    # no CI on the merge commit itself
    (box.t / f"ci-{head}.json").write_text(json.dumps({"check_runs": [{"name": "backend", "conclusion": "success"},
                                                                      {"name": "frontend", "conclusion": "success"}]}))
    p = box.run(merge)
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.records()[-1]["result"] == "deployed" and box.head() == merge


def test_a_merge_commit_whose_tree_differs_from_the_pr_head_needs_its_own_ci(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    head, merge = _pr_merge(box, main_moved=True)
    box.ci("failure")
    (box.t / f"ci-{head}.json").write_text(json.dumps({"check_runs": [{"name": "backend", "conclusion": "success"},
                                                                      {"name": "frontend", "conclusion": "success"}]}))
    p = box.run(merge)
    assert p.returncode == 1 and "CI has not passed" in box.records()[-1]["detail"]
    assert box.head() == box.c2


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


@pytest.mark.skipif(not _ps_works(), reason="needs `ps` to find and kill the output filter; the sandbox forbids it")
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


def test_service_listing_uses_plain_so_a_failed_or_activating_unit_bullet_is_never_read_as_a_name():
    # systemctl prefixes non-active units with "●"; without --plain, awk '{print $1}' returned "●" as a unit name and
    # every deploy was judged unstable and rolled back (2026-10-18, chart-fetch oneshot "activating" every 60 s)
    src = (Path(__file__).resolve().parents[1] / "goldbot/ops/linux/goldbot-deploy.sh").read_text()
    listings = [ln for ln in src.splitlines() if "systemctl list-units" in ln and "awk '{print $1}'" in ln]
    assert listings and all("--plain" in ln for ln in listings)


def test_the_stability_check_skips_timer_driven_oneshot_units():
    # goldbot-chart-fetch.service is a static oneshot fired every 60 s; requiring it "active" for 90 s rolled back
    # every brain deploy (2026-10-18 13:51, 13:55, 14:13 UTC)
    src = (REPO / "goldbot/ops/linux/goldbot-deploy.sh").read_text()
    body = src.split("healthy() {", 1)[1].split("\n}\n", 1)[0]
    assert 'systemctl show -p Type --value "$s")" = oneshot ] && continue' in body
    assert body.index("= oneshot ] && continue") < body.index('systemctl is-active --quiet "$s"')


# ------------------------------------------------------------------ staging, adaptive health window, order
def _web_commit(box: Box, text: str, lock: str | None = None) -> str:
    files = {"web/src.txt": text, "web/package.json": "{}"}
    if lock is not None:
        files["uv.lock"] = lock
    return _commit(box.origin, files, f"web {text}")


def _clock(box: Box) -> int:
    return int((box.t / "clock").read_text())


def _lines(box: Box, name: str) -> list[str]:
    f = box.t / name
    return f.read_text().splitlines() if f.exists() else []


def _last(box: Box) -> str:
    return box.records()[-1]["result"]


def test_stage_prebuilds_without_touching_a_service_and_is_idempotent(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    sha = _web_commit(box, "v1", lock="lock-1")
    before = box.head()
    p = box.run("--stage", sha)
    assert p.returncode == 0, p.stdout + p.stderr
    assert box.calls() == [] and box.head() == before and box.records() == []      # no service, no checkout, no log
    d = tmp_path / "stage" / sha
    manifest = dict(x.split("=", 1) for x in (d / "manifest").read_text().splitlines())
    assert manifest["dist"] == "yes" and len(manifest["dist_sha256"]) == 64
    assert (d / "web" / "dist" / "index.html").read_text().strip() == "v1" and not (d / "web" / "node_modules").exists()
    uv = _lines(box, "uv.log")
    assert not (d / ".venv").exists() and len(uv) == 1 and "sync -q --frozen --extra live" in uv[0]
    # the build ran in a memory-capped cgroup as the service user, and nothing in the stage was written by root
    assert "MemoryMax=512M" in _lines(box, "systemd_run.log")[0]
    assert not any(line.startswith("root ") for line in (tmp_path / "sudo.log").read_text().splitlines())
    builds = len(_lines(box, "npm.log"))
    again = box.run("--stage", sha)
    assert again.returncode == 0 and "already staged" in again.stdout and len(_lines(box, "npm.log")) == builds


def test_stage_refuses_anything_but_a_sha_and_a_non_brain_box_and_a_non_fast_forward(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    for bad in ("latest", "abc", "../../etc", "G" * 40, box.c2.upper()):
        assert box.run("--stage", bad).returncode != 0
    assert box.run("--stage").returncode == 2                                  # usage
    assert not (tmp_path / "stage").exists() or not list((tmp_path / "stage").iterdir())
    p = box.run("--stage", box.c1)                                             # older than what runs
    assert p.returncode == 1 and "not a fast-forward" in p.stdout
    (tmp_path / "role").write_text("mt5\n")
    assert box.run("--stage", box.c2).returncode == 1
    assert box.calls() == [] and _lines(box, "npm.log") == []


def test_the_deploy_reuses_a_verified_stage_even_for_the_merge_commit_with_the_same_tree(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    sha = _web_commit(box, "v1")
    assert box.run("--stage", sha).returncode == 0
    _git(box.origin, "commit", "-q", "--allow-empty", "-m", "merge commit with the PR head's tree")
    merge = _git(box.origin, "rev-parse", "HEAD")
    assert merge != sha and _git(box.origin, "rev-parse", f"{merge}^{{tree}}") == _git(box.origin, "rev-parse", f"{sha}^{{tree}}")
    builds = len(_lines(box, "npm.log"))
    p = box.run(merge)
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last(box) == "deployed" and box.head() == merge
    assert len(_lines(box, "npm.log")) == builds                                # no second npm ci / build
    assert "reused the verified stage" in p.stdout
    assert (box.root / "web" / "dist" / "index.html").read_text().strip() == "v1"


def test_a_tampered_or_missing_stage_is_never_used(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    sha = _web_commit(box, "v1")
    assert box.run("--stage", sha).returncode == 0
    (tmp_path / "stage" / sha / "web" / "dist" / "index.html").write_text("evil")      # content no longer matches
    builds = len(_lines(box, "npm.log"))
    p = box.run(sha)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "reused" not in p.stdout and len(_lines(box, "npm.log")) == builds + 2         # npm ci + build as before
    assert (box.root / "web" / "dist" / "index.html").read_text().strip() == "v1"
    # a symlink inside the stage dist is refused too
    box2 = Box(tmp_path / "b", running="c1", aux=4)
    sha2 = _web_commit(box2, "v2")
    assert box2.run("--stage", sha2).returncode == 0
    (tmp_path / "b" / "stage" / sha2 / "web" / "dist" / "link").symlink_to("/etc/passwd")
    p2 = box2.run(sha2)
    assert p2.returncode == 0 and "reused" not in p2.stdout


def test_the_adaptive_window_passes_after_30_s_and_the_deploy_reports_how_long(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    t0 = _clock(box)
    assert box.run(box.c2).returncode == 0
    waited = _clock(box) - t0
    rec = box.records()[-1]
    assert rec["result"] == "deployed" and 30 <= waited < 45 and "after 3" in rec["detail"]
    assert "stable for 90 s" not in rec["detail"]


def test_a_manual_rollback_and_the_recovery_still_wait_the_full_90_s(tmp_path):
    box = Box(tmp_path, running="c2", aux=5)
    t0 = _clock(box)
    assert box.run("--rollback", box.c1).returncode == 0
    assert _clock(box) - t0 >= 90
    assert not (tmp_path / "watch_spawned").exists()                            # no watcher for a rollback


def test_a_stale_heartbeat_since_the_restart_fails_the_window_and_rolls_back(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    t0 = _clock(box)
    p = box.run(box.c2, env={"STAT_MTIME": "1"})                                # nothing writes a fresh heartbeat
    assert p.returncode == 1
    assert _last(box) in ("rolled_back", "failed")
    assert _clock(box) - t0 >= 90                                               # it waited its 90 s before giving up


def test_a_restart_inside_the_window_rolls_back_at_once(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    t0 = _clock(box)
    p = box.run(box.c2, env={"BUMP_AFTER": "9"})                                # NRestarts rises at the first poll
    assert p.returncode == 1 and _last(box) == "rolled_back" and box.head() == box.c1
    assert _clock(box) - t0 < 60 + 90                                           # fast fail, then the recovery's full wait


def test_the_engine_restarts_right_after_the_supervisor_and_before_the_other_services(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    assert box.run(box.c2).returncode == 0
    order = [a.split()[1] for a in box.actions() if a.startswith("restart")]
    assert order[0] == "goldbot-supervisor.service" and order[1] == "goldbot-engine@icm-demo.service"
    assert set(order[2:]) == {"goldbot-api.service", "goldbot-scheduler.service", "goldbot-telegram.service"}
    assert "goldbot-engine@vantage-demo.service" not in order                  # a disabled stray is never started


def test_the_report_starts_a_watcher_that_rolls_back_a_later_restart_and_otherwise_ends_quietly(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    assert box.run(box.c2).returncode == 0
    assert "--watch" in (tmp_path / "watch_spawned").read_text()
    watch = (tmp_path / "var" / "watch").read_text().splitlines()
    assert watch[0].split()[:2] == [box.c2, box.c1] and "goldbot-engine@icm-demo.service 0" in watch
    assert not any(w.startswith("goldbot-engine@vantage") for w in watch)
    saved = (tmp_path / "var" / "watch").read_text()
    # quiet: the watcher runs to its deadline and changes nothing
    t0 = _clock(box)
    assert box.run("--watch").returncode == 0
    assert _last(box) == "deployed" and box.head() == box.c2 and not (tmp_path / "var" / "watch").exists()
    assert _clock(box) - t0 >= 50
    # a restart after the report: rolled back, audited
    first, rest = saved.split("\n", 1)
    target, previous, _ = first.split()
    (tmp_path / "var" / "watch").write_text(f"{target} {previous} {_clock(box) + 60}\n{rest}")      # a fresh deadline
    (tmp_path / "bump").write_text("")
    p = box.run("--watch")
    assert p.returncode == 1, p.stdout + p.stderr
    assert box.head() == box.c1 and _last(box) in ("rolled_back", "failed")


def test_a_watcher_for_a_version_that_no_longer_runs_does_nothing(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    (tmp_path / "var").mkdir()
    (tmp_path / "var" / "watch").write_text(f"{box.c2} {box.c1} 9999999999\ngoldbot-api.service 0\n")
    assert box.run("--watch").returncode == 0 and box.actions() == [] and box.records() == []
    (tmp_path / "var" / "watch").write_text("garbage\n")
    assert box.run("--watch").returncode == 0 and box.actions() == []
    assert box.run("--watch", "extra").returncode == 2


def test_the_heartbeat_service_list_matches_the_health_checks():
    src = SCRIPT.read_text()
    listed = set(next(ln for ln in src.splitlines() if ln.startswith("BEAT_SERVICES=")).split('"')[1].split())
    from goldbot.ops.checks._base import HEARTBEAT_SERVICES, OPTIONAL_HEARTBEAT_SERVICES
    assert listed == set(HEARTBEAT_SERVICES) | set(OPTIONAL_HEARTBEAT_SERVICES)


def test_services_stop_before_the_checkout_and_start_after_it_engine_last_out_first_in(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    heads = [x.split() for x in _lines(box, "stop_heads")]
    assert heads and all(h == box.c1 for h, _ in heads)                         # every stop happened on the OLD commit
    names = [n for _, n in heads]
    assert set(names) == {"goldbot-api.service", "goldbot-scheduler.service", "goldbot-telegram.service",
                          "goldbot-supervisor.service", "goldbot-engine@icm-demo.service"}      # enabled units only
    assert names[-1] == "goldbot-engine@icm-demo.service" and names.index("goldbot-supervisor.service") > names.index("goldbot-api.service")
    acts = [a.split()[0] for a in box.actions()]
    assert acts == ["stop"] * len(names) + ["restart"] * len(acts[len(names):])    # all stops, then all (re)starts
    m = re.search(r"engine down for (\d+) s", p.stdout)
    assert m and int(m.group(1)) <= 10 and "engine down" in box.records()[-1]["detail"]


def test_the_rollback_stops_before_restoring_the_old_commit_too(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    box.units({"fail_at": box.c2})                                              # the new commit never becomes healthy
    assert box.run(box.c2).returncode == 1 and _last(box) == "rolled_back"
    acts = [a.split()[0] for a in box.actions()]
    n = acts.index("restart")
    second = acts[n:]
    assert "stop" in second and second.index("stop") < second.index("restart", second.index("stop"))
    heads = [x.split()[0] for x in _lines(box, "stop_heads")]
    assert heads[0] == box.c1 and heads[-1] == box.c2                           # the restore stops on the commit it leaves


def test_the_deploy_check_itself_is_never_a_degradation(tmp_path):
    # the `deploy` check warns about the PREVIOUS deploy record: a degraded deploy made every later one degraded
    box = Box(tmp_path, running="c1", aux=4)
    _health(tmp_path / "health_before.json", supervisor="ok", deploy="ok")
    _health(tmp_path / "health_after.json", supervisor="ok", deploy="warn")
    assert box.run(box.c2).returncode == 0
    assert _last(box) == "deployed"
    box2 = Box(tmp_path / "b", running="c1", aux=4)                                     # a real one is still named
    _health(tmp_path / "b" / "health_before.json", supervisor="ok", deploy="ok")
    _health(tmp_path / "b" / "health_after.json", supervisor="warn", deploy="warn")
    assert box2.run(box2.c2).returncode == 0
    assert box2.records()[-1]["result"] == "deployed_degraded" and "degraded: supervisor)" in box2.records()[-1]["detail"]


def test_a_transient_engine_warn_right_after_the_restart_is_not_a_degradation(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _health(tmp_path / "health_before.json", supervisor="ok", engine__icm="ok")
    _health(tmp_path / "health_after_1.json", supervisor="ok", engine__icm="warn")     # stale bars right after restart
    _health(tmp_path / "health_after_2.json", supervisor="ok", engine__icm="ok")       # a decision bar later
    _health(tmp_path / "health_after.json", supervisor="ok", engine__icm="ok")
    assert box.run(box.c2).returncode == 0
    assert _last(box) == "deployed"


def test_an_engine_warn_that_persists_after_the_rechecks_is_still_degraded(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _health(tmp_path / "health_before.json", supervisor="ok", engine__icm="ok")
    _health(tmp_path / "health_after.json", supervisor="ok", engine__icm="warn")
    assert box.run(box.c2).returncode == 0
    assert _last(box) == "deployed_degraded" and "engine:icm" in box.records()[-1]["detail"]


# ---------------------------------------------------------------- incident 2026-10-20: a rollback left services stopped
# (docs/incidents/2026-10-20-deploy-rollback-left-services-stopped.md)
FULL_BOX = {"goldbot-news.service": True, "goldbot-tv-pull.service": True}
INPUTS = ("goldbot-api.service", "goldbot-scheduler.service", "goldbot-telegram.service", "goldbot-news.service",
          "goldbot-tv-pull.service")


def _incident_units(box: Box, fail: dict[str, str], probe: bool = True) -> None:
    """The brain at 11:48: every service running, engine@icm-probe enabled but not yet active (newly enabled)."""
    st: dict[str, Any] = {"units": {n: {"enabled": e, "active": e} for n, e in {**UNITS, **FULL_BOX}.items()},
                          "fail_units": fail}
    if probe:
        st["units"]["goldbot-engine@icm-probe.service"] = {"enabled": True, "active": False}
    (box.t / "units.json").write_text(json.dumps(st))


def _active(box: Box) -> dict[str, bool]:
    st = json.loads((box.t / "units.json").read_text())
    return {n: bool(u.get("active")) and n not in st.get("fail_units", {}) for n, u in st["units"].items()}


def test_incident_a_newly_enabled_engine_blocked_by_its_bridge_does_not_roll_back_or_stop_anything(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _incident_units(box, {"goldbot-engine@icm-probe.service": "*"})
    _health(tmp_path / "health_before.json", supervisor="ok", **{"engine_loop:icm-demo": "ok", "bridge:icm-demo": "ok"})
    _health(tmp_path / "health_after.json", supervisor="ok", **{"engine_loop:icm-demo": "ok", "bridge:icm-demo": "ok",
                                                                 "bridge:icm-probe": "ok", "terminal:icm-probe": "fail",
                                                                 "bridge_compat:icm-probe": "fail"})
    p = box.run(box.c2)
    assert p.returncode == 0, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "deployed_degraded" and box.head() == box.c2
    assert "engine@icm-probe blocked by its bridge (terminal:icm-probe,bridge_compat:icm-probe)" in rec["detail"]
    act = _active(box)
    assert all(act[u] for u in (*INPUTS, "goldbot-supervisor.service", "goldbot-engine@icm-demo.service"))
    restarts = [a.split()[-1] for a in box.actions() if a.startswith("restart")]
    assert restarts[-1] == "goldbot-engine@icm-probe.service"                   # the new engine last, never blocking
    assert "restart --no-block goldbot-engine@icm-probe.service" in box.calls()
    assert set(INPUTS) <= set(restarts)
    watch = (tmp_path / "var" / "watch").read_text().splitlines()               # the watcher does not judge it either
    assert not any(w.startswith("goldbot-engine@icm-probe") for w in watch)
    assert "pre goldbot-api.service" in watch and "pre goldbot-engine@icm-probe.service" not in watch


def test_a_newly_enabled_engine_down_with_its_bridge_ok_rolls_back_without_being_started_again(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _incident_units(box, {"goldbot-engine@icm-probe.service": "*"})
    _health(tmp_path / "health_before.json", supervisor="ok")
    _health(tmp_path / "health_after.json", supervisor="ok", **{"bridge:icm-probe": "ok", "terminal:icm-probe": "ok"})
    p = box.run(box.c2)
    assert p.returncode == 1, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "rolled_back" and box.head() == box.c1
    assert "newly enabled engine@icm-probe did not start and its bridge is ok" in rec["detail"]
    assert "not running: none" in rec["detail"]
    probe = [i for i, a in enumerate(box.actions()) if "icm-probe" in a and a.split()[0] in ("start", "restart")]
    assert len(probe) == 1                                                      # the forward start only, never the rollback's
    act = _active(box)
    assert all(act[u] for u in (*INPUTS, "goldbot-supervisor.service", "goldbot-engine@icm-demo.service"))


def test_a_rollback_after_a_real_failure_restarts_every_service_that_ran_before(tmp_path):
    """The incident's rollback: one engine cannot start on either commit. Before the fix the first failed restart
    ended the start loop, leaving api, scheduler, telegram, news and tv-pull stopped."""
    box = Box(tmp_path, running="c1", aux=4)
    _incident_units(box, {}, probe=False)
    st = json.loads((box.t / "units.json").read_text())
    st["fail_after_stop"] = ["goldbot-engine@icm-demo.service"]                 # ran before, fails from the stop on
    (box.t / "units.json").write_text(json.dumps(st))
    _health(tmp_path / "health_before.json", supervisor="ok")
    _health(tmp_path / "health_after.json", supervisor="ok")
    p = box.run(box.c2)
    assert p.returncode == 1, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "failed" and box.head() == box.c1
    assert "not running: engine@icm-demo" in rec["detail"]
    assert "running: supervisor,api,scheduler,telegram,news,tv-pull; not running: engine@icm-demo" in rec["detail"]
    st = json.loads((box.t / "units.json").read_text())
    assert all(st["units"][u]["active"] for u in (*INPUTS, "goldbot-supervisor.service"))
    assert not st["units"]["goldbot-engine@icm-demo.service"]["active"]
    assert "goldbot-engine@vantage-demo.service" not in " ".join(box.actions())  # a disabled stray stays untouched


def test_an_engine_that_was_healthy_before_and_is_not_after_still_rolls_back(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _incident_units(box, {}, probe=False)
    st = json.loads((box.t / "units.json").read_text())
    st["fail_units"] = {"goldbot-engine@icm-demo.service": box.c2}              # only the new code fails
    (box.t / "units.json").write_text(json.dumps(st))
    _health(tmp_path / "health_before.json", supervisor="ok", **{"bridge_compat:icm-demo": "ok"})
    _health(tmp_path / "health_after.json", supervisor="ok", **{"bridge_compat:icm-demo": "fail"})
    p = box.run(box.c2)
    assert p.returncode == 1, p.stdout + p.stderr
    rec = box.records()[-1]
    assert rec["result"] == "rolled_back" and box.head() == box.c1             # the bridge excuse is for new engines only
    assert "not running: none" in rec["detail"]
    st = json.loads((box.t / "units.json").read_text())
    assert all(st["units"][u]["active"] for u in (*INPUTS, "goldbot-supervisor.service",
                                                  "goldbot-engine@icm-demo.service"))


def test_the_watchers_rollback_brings_back_what_ran_before_and_not_a_new_engine(tmp_path):
    box = Box(tmp_path, running="c1", aux=4)
    _incident_units(box, {"goldbot-engine@icm-probe.service": "*"})
    _health(tmp_path / "health_before.json", supervisor="ok")
    _health(tmp_path / "health_after.json", supervisor="ok", **{"bridge_compat:icm-probe": "fail"})
    assert box.run(box.c2).returncode == 0
    saved = (tmp_path / "var" / "watch").read_text()
    first, rest = saved.split("\n", 1)
    target, previous, _ = first.split()
    (tmp_path / "var" / "watch").write_text(f"{target} {previous} {_clock(box) + 60}\n{rest}")
    (tmp_path / "bump").write_text("")
    n = len(box.actions())
    p = box.run("--watch")
    assert p.returncode == 1, p.stdout + p.stderr
    assert box.head() == box.c1 and _last(box) in ("rolled_back", "failed")
    assert not any("icm-probe" in a for a in box.actions()[n:] if a.split()[0] in ("start", "restart"))
    st = json.loads((box.t / "units.json").read_text())
    assert all(st["units"][u]["active"] for u in (*INPUTS, "goldbot-supervisor.service",
                                                  "goldbot-engine@icm-demo.service"))


def test_a_failed_git_command_says_why(tmp_path):
    with pytest.raises(AssertionError, match=r"exited 128: .*(does not exist|No such file)"):
        _run_git(["git", "clone", "-q", str(tmp_path / "nope"), str(tmp_path / "root")], _git_env(tmp_path))


def test_the_box_checkout_reads_nothing_of_the_machines_git(tmp_path, monkeypatch):
    """A runner or a hook can export GIT_DIR / GIT_INDEX_FILE or point GIT_CONFIG_GLOBAL at a file that makes any git
    call fail (the bare `git clone` of CI #167, #182, #217 inherited all of it); the box builds on its own."""
    (tmp_path / "bad.gitconfig").write_text("[core\n")                       # unparsable: a bare git exits 128
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "bad.gitconfig"))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "nowhere.idx"))
    box = Box(tmp_path / "box", running="c1")
    assert _git(box.root, "rev-parse", "HEAD") == box.c1
    bare = subprocess.run(["git", "clone", "-q", str(box.origin), str(tmp_path / "bare")], capture_output=True)
    assert bare.returncode != 0                                              # the old call, on the same poisoned env
