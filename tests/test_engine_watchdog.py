"""First-deploy risk (SRE): a hung engine (alive, its loop stuck) used to show only as a stale engine_<account>.json
after 45 min with the market open, and nothing restarted it. Now:

* the engine loop writes its own heartbeat (state/heartbeat_engine-<account>.json, goldbot/ops/watchdog.py) on every
  pass, ticks or none, and health FAILs when it is older than LOOP_FAIL_S (150 s), whatever the market;
* the loop feeds the systemd watchdog (Type=notify, WatchdogSec=300): only the loop thread pings after READY=1, so a
  stuck loop stops the pings and systemd restarts the engine (restart reconciliation, row X7); the start (reconcile,
  warm start, model load) is fed by a bounded thread, so a long but legitimate start never trips it;
* the MT5 bridge (Wine: no notify socket) gets a root timer that restarts it when GET /health stops answering."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.ops import accounts, health, watchdog
from goldbot.ops import run as run_mod
from goldbot.ops.watchdog import LoopWatch, loop_heartbeat_path, sd_notify
from tests.test_health import NOW, SATURDAY, make_ctx

LINUX = Path(__file__).resolve().parents[1] / "goldbot" / "ops" / "linux"
UNITS = LINUX / "systemd"
BASH = shutil.which("bash") or ""
ACC = accounts.Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None,
                       terminal_path="", server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)
HAS_UNIX_DGRAM = hasattr(socket, "AF_UNIX") and sys.platform != "win32"


class FakeClock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def notify_socket() -> Iterator[tuple[str, socket.socket]]:
    """A real AF_UNIX datagram socket standing in for systemd's NOTIFY_SOCKET (short path: macOS limits it)."""
    d = tempfile.mkdtemp(prefix="sd")
    path = os.path.join(d, "n")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    s.bind(path)
    s.settimeout(0.0)
    try:
        yield path, s
    finally:
        s.close()
        shutil.rmtree(d, ignore_errors=True)


def _recorder(sent: list[str]) -> Callable[[str], bool]:
    def notify(message: str) -> bool:
        sent.append(message)
        return True
    return notify


def _drain(s: socket.socket) -> list[str]:
    out = []
    while True:
        try:
            out.append(s.recv(4096).decode())
        except (BlockingIOError, TimeoutError, socket.timeout):
            return out


def _beat(state: Path, account: str = "icm-demo") -> dict[str, Any]:
    return dict(json.loads(loop_heartbeat_path(state, account).read_text()))


# ------------------------------------------------------------------------------------------------ sd_notify
@pytest.mark.skipif(not HAS_UNIX_DGRAM, reason="needs AF_UNIX datagrams")
def test_sd_notify_sends_to_the_notify_socket(notify_socket):
    path, s = notify_socket
    assert sd_notify("WATCHDOG=1", env={"NOTIFY_SOCKET": path}) is True
    assert _drain(s) == ["WATCHDOG=1"]


def test_sd_notify_is_a_no_op_outside_systemd_and_never_raises(tmp_path):
    assert sd_notify("READY=1", env={}) is False
    assert sd_notify("READY=1", env={"NOTIFY_SOCKET": "vsock:2:1234"}) is False      # not used on these hosts
    # a socket that is gone: logged, False, never an exception into the trading loop
    assert sd_notify("WATCHDOG=1", env={"NOTIFY_SOCKET": str(tmp_path / "gone")}) is False


# ------------------------------------------------------------------------------------------------ LoopWatch
def test_a_new_run_keeps_the_earlier_runs_spans_in_its_heartbeat(tmp_path):
    """The feed reconcile judges a stalled feed on every run of the day, not only the current one (re-verify A2)."""
    t0 = 1_000_000.0
    loop_heartbeat_path(tmp_path, "icm-demo").write_text(json.dumps(
        {"phase": "loop", "started": t0, "ts": t0 + 3600, "runs": [[t0 - 7200, t0 - 3600], [t0 - 4 * 86400, t0 - 3 * 86400]]}))
    w = LoopWatch(tmp_path, "icm-demo", clock=FakeClock(t0 + 7200), notify=lambda m: True)
    w.ready()
    assert _beat(tmp_path)["runs"] == [[t0 - 7200, t0 - 3600], [t0, t0 + 3600]]   # older than 2 days dropped
    assert _beat(tmp_path)["started"] == t0 + 7200


def test_an_unreadable_previous_heartbeat_starts_an_empty_run_history(tmp_path):
    loop_heartbeat_path(tmp_path, "icm-demo").write_text("{")
    w = LoopWatch(tmp_path, "icm-demo", clock=FakeClock(), notify=lambda m: True)
    w.ready()
    assert _beat(tmp_path)["runs"] == []


def test_every_loop_pass_beats_and_feeds_the_watchdog_rate_limited(tmp_path):
    clock, sent = FakeClock(), list[str]()
    w = LoopWatch(tmp_path, "icm-demo", beat_every_s=15, notify_every_s=5, clock=clock,
                  notify=_recorder(sent))
    w.ready()
    assert _beat(tmp_path)["phase"] == "loop" and "READY=1" in sent[0] and sent[-1] == "WATCHDOG=1"
    sent.clear()
    for _ in range(4):                       # 4 passes in 1 s: one write, no extra ping
        clock.t += 0.25
        w.tick()
    assert sent == [] and _beat(tmp_path)["passes"] == 0
    clock.t += 15
    w.tick()
    b = _beat(tmp_path)
    assert b["ts"] == clock.t and b["passes"] == 5 and b["phase"] == "loop" and sent == ["WATCHDOG=1"]
    assert b["service"] == "engine-icm-demo" and b["pid"] == os.getpid()


def test_only_the_loop_feeds_the_watchdog_after_ready_so_a_stuck_loop_stops_the_pings(tmp_path):
    sent: list[str] = []
    w = LoopWatch(tmp_path, "icm-demo", beat_every_s=0.01, notify_every_s=0.01, notify=_recorder(sent))
    w.starting()
    deadline = time.monotonic() + 2.0
    while "WATCHDOG=1" not in sent and time.monotonic() < deadline:      # the start is fed by the thread
        time.sleep(0.01)
    assert "WATCHDOG=1" in sent and _beat(tmp_path)["phase"] == "starting"
    w.ready()
    assert w._thread is None
    sent.clear()
    time.sleep(0.2)                          # 20 notify intervals with no tick(): the loop is "stuck"
    assert sent == [], "something other than the loop fed the watchdog"
    assert _beat(tmp_path)["phase"] == "loop"


def test_a_start_longer_than_startup_max_stops_being_fed(tmp_path):
    sent: list[str] = []
    w = LoopWatch(tmp_path, "icm-demo", beat_every_s=0.01, notify_every_s=0.01, startup_max_s=0.05,
                  notify=_recorder(sent))
    w.starting()
    time.sleep(0.3)
    n = sent.count("WATCHDOG=1")
    time.sleep(0.1)
    assert sent.count("WATCHDOG=1") == n, "a hung start must stop feeding the watchdog (TimeoutStartSec ends it)"
    w.stop()


def test_a_failing_heartbeat_write_never_raises(tmp_path, monkeypatch):
    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(watchdog, "write_atomic", boom)
    w = LoopWatch(tmp_path, "icm-demo", notify=lambda m: True)
    w.ready()
    w.tick()                                 # no exception


# ------------------------------------------------------------------------------------------------ run_engine
class _StopLoop(Exception):
    pass


def _run_engine_passes(monkeypatch, tmp_path: Path, passes: int, env: dict[str, str] | None = None) -> None:
    """run_engine against the paper broker that has NO tick at all (a dead feed / closed market), for `passes` loop
    passes (time.sleep raises after that)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {"icm-demo": ACC})
    monkeypatch.setattr(accounts, "bridge_endpoint", lambda aid: None)
    monkeypatch.setattr(run_mod.sys, "platform", "linux")       # the paper broker branch, wherever the test runs
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    n = {"sleeps": 0}

    def sleep(_s: float) -> None:
        n["sleeps"] += 1
        if n["sleeps"] >= passes:
            raise _StopLoop
    monkeypatch.setattr(run_mod.time, "sleep", sleep)
    with pytest.raises(_StopLoop):
        run_mod.run_engine("icm-demo")
    monkeypatch.undo()


@pytest.mark.skipif(not HAS_UNIX_DGRAM, reason="needs AF_UNIX datagrams")
def test_the_engine_loop_beats_and_notifies_with_no_ticks(tmp_path, monkeypatch, notify_socket):
    path, s = notify_socket
    _run_engine_passes(monkeypatch, tmp_path, passes=5, env={"NOTIFY_SOCKET": path})
    b = _beat(tmp_path / "state")
    assert b["phase"] == "loop" and time.time() - b["ts"] < 60
    msgs = _drain(s)
    assert any(m.startswith("READY=1") for m in msgs) and "WATCHDOG=1" in msgs, msgs
    assert msgs.index("STATUS=starting") < next(i for i, m in enumerate(msgs) if m.startswith("READY=1"))
    c = health.check_engine_loop(make_ctx(tmp_path / "state", now=pd.Timestamp.now("UTC")), "icm-demo")
    assert c.status == "ok", c.reason


def test_a_watchdog_restart_resumes_the_heartbeat_and_health_recovers(tmp_path, monkeypatch):
    """Restart test: the first process stops beating (stuck, then killed by the watchdog); health FAILs on the stale
    beat; the restarted process (same state dir, the stale file of the old pid in place) starts, beats "starting",
    then "loop", and health is ok again. The restart itself reconciles orders and positions (row X7,
    tests/test_restart_reconcile*.py); nothing here blocks or replaces that."""
    state = tmp_path / "state"
    _run_engine_passes(monkeypatch, tmp_path, passes=3)
    first = _beat(state)
    stuck_at = pd.Timestamp(first["ts"], unit="s", tz="UTC") + pd.Timedelta(seconds=health.LOOP_FAIL_S + 30)
    c = health.check_engine_loop(make_ctx(state, now=stuck_at), "icm-demo")
    assert c.status == "fail" and "loop silent" in c.reason and "watchdog" in c.reason, c.reason
    time.sleep(0.01)
    _run_engine_passes(monkeypatch, tmp_path, passes=3)
    second = _beat(state)
    assert second["phase"] == "loop" and second["started"] > first["started"] and second["ts"] > first["ts"]
    c = health.check_engine_loop(make_ctx(state, now=pd.Timestamp(second["ts"], unit="s", tz="UTC")), "icm-demo")
    assert c.status == "ok", c.reason


def test_a_refused_start_stops_the_startup_feeder(tmp_path, monkeypatch):
    started: list[LoopWatch] = []
    real = run_mod.start_loop_watch

    def spy(account_id: str, state_dir: str | Path = "state") -> LoopWatch:
        w = real(account_id, state_dir)
        started.append(w)
        return w
    monkeypatch.setattr(run_mod, "start_loop_watch", spy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(accounts, "load_accounts", lambda path=None: {})
    with pytest.raises(KeyError):
        run_mod.run_engine("icm-demo")
    assert started and started[0]._thread is None and started[0]._stop.is_set()


# ------------------------------------------------------------------------------------------------ health
def _write_beat(state: Path, age_s: float, now: pd.Timestamp = NOW, phase: str = "loop", up_s: float = 3600) -> None:
    state.mkdir(parents=True, exist_ok=True)
    t = now.timestamp()
    loop_heartbeat_path(state, "icm-demo").write_text(json.dumps(
        {"service": "engine-icm-demo", "phase": phase, "ts": t - age_s, "started": t - up_s, "passes": 1, "pid": 1}))


@pytest.mark.parametrize("now", [NOW, SATURDAY], ids=["market_open", "weekend"])
def test_health_fails_on_a_stale_loop_heartbeat_whatever_the_market(tmp_path, now):
    _write_beat(tmp_path, age_s=10, now=now)
    assert health.check_engine_loop(make_ctx(tmp_path, now=now), "icm-demo").status == "ok"
    _write_beat(tmp_path, age_s=health.LOOP_WARN_S + 5, now=now)
    assert health.check_engine_loop(make_ctx(tmp_path, now=now), "icm-demo").status == "warn"
    _write_beat(tmp_path, age_s=health.LOOP_FAIL_S + 1, now=now)
    c = health.check_engine_loop(make_ctx(tmp_path, now=now), "icm-demo")
    assert c.status == "fail" and "150 s" in c.reason and "stops rest with the broker" in c.reason, c.reason


def test_loop_fail_threshold_is_a_few_minutes_and_inside_the_watchdog():
    unit = (UNITS / "goldbot-engine@.service").read_text()
    watchdog_s = int(_directive(unit, "WatchdogSec"))
    assert 120 <= health.LOOP_FAIL_S <= 180 < watchdog_s, "health alerts before systemd restarts"
    assert health.LOOP_FAIL_S >= 5 * watchdog.LOOP_BEAT_EVERY_S, "several missed beats before a FAIL"
    assert watchdog_s >= 10 * watchdog.NOTIFY_EVERY_S


def test_health_on_a_slow_start_warns_then_fails(tmp_path):
    _write_beat(tmp_path, age_s=5, phase="starting", up_s=120)
    c = health.check_engine_loop(make_ctx(tmp_path), "icm-demo")
    assert c.status == "warn" and "starting" in c.reason
    _write_beat(tmp_path, age_s=5, phase="starting", up_s=health.LOOP_STARTING_FAIL_S + 60)
    assert health.check_engine_loop(make_ctx(tmp_path), "icm-demo").status == "fail"
    _write_beat(tmp_path, age_s=health.LOOP_FAIL_S + 1, phase="starting", up_s=200)      # the start itself hung
    c = health.check_engine_loop(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "start silent" in c.reason


def test_health_missing_or_unreadable_loop_heartbeat(tmp_path):
    c = health.check_engine_loop(make_ctx(tmp_path), "icm-demo")
    assert c.status == "warn" and "never ran" in c.reason
    loop_heartbeat_path(tmp_path, "icm-demo").write_text("{not json")
    assert health.check_engine_loop(make_ctx(tmp_path), "icm-demo").status == "fail"
    loop_heartbeat_path(tmp_path, "icm-demo").write_text(json.dumps({"ts": "x"}))
    assert health.check_engine_loop(make_ctx(tmp_path), "icm-demo").status == "fail"


def test_run_checks_includes_the_loop_check_and_the_file_is_out_of_engine_globs(tmp_path):
    _write_beat(tmp_path, age_s=health.LOOP_FAIL_S + 1)
    report = health.run_checks(make_ctx(tmp_path))
    assert {c.name: c.status for c in report.checks}["engine_loop:icm-demo"] == "fail"
    assert not list(tmp_path.glob("engine_*.json")), "the supervisor and dashboards glob engine_*.json"


# ------------------------------------------------------------------------------------------------ systemd units
def _directive(unit: str, key: str) -> str:
    vals = [ln.split("=", 1)[1].strip() for ln in unit.splitlines() if ln.startswith(f"{key}=")]
    assert len(vals) == 1, f"{key}: {vals}"
    return vals[0]


def test_the_engine_unit_is_type_notify_with_a_generous_watchdog():
    unit = (UNITS / "goldbot-engine@.service").read_text()
    assert _directive(unit, "Type") == "notify" and _directive(unit, "NotifyAccess") == "main"
    assert 240 <= int(_directive(unit, "WatchdogSec")) <= 600
    assert int(_directive(unit, "TimeoutStartSec")) >= watchdog.STARTUP_MAX_S
    assert _directive(unit, "Restart") == "always" and _directive(unit, "LimitCORE") == "0"
    # NotifyAccess=main: python itself must be the main process (no shell wrapper in between)
    assert _directive(unit, "ExecStart").startswith("/opt/goldbot/.venv/bin/python -m goldbot.ops.run engine")


def test_the_loop_feeds_the_watchdog_on_every_pass_before_the_tick_read():
    import inspect
    src = inspect.getsource(run_mod._run_engine)
    loop = src[src.index("watch.ready()"):]
    # `resolve(engine_pass)(...)`: the loop calls engine_pass through goldbot.ops.run (the patch point) since the split
    assert loop.index("while True:") < loop.index("watch.tick()") < loop.index("resolve(engine_pass)(")


def test_the_bridge_watchdog_is_installed_from_roots_clone_and_enabled():
    boot = (LINUX / "mt5_bootstrap.sh").read_text()
    assert 'install -m 755 -o root -g root "$OPS/goldbot-bridge-watchdog.sh" /usr/local/sbin/goldbot-bridge-watchdog' in boot
    assert '"$OPS"/systemd/goldbot-bridge-watchdog@.service' in boot and '"$OPS"/systemd/goldbot-bridge-watchdog@.timer' in boot
    assert "systemctl enable --now goldbot-bridge-watchdog@icm-demo.timer" in boot
    svc = (UNITS / "goldbot-bridge-watchdog@.service").read_text()
    assert _directive(svc, "ExecStart") == "/usr/local/sbin/goldbot-bridge-watchdog %i 8765"
    assert "8765" in (LINUX / "mt5_bootstrap.sh").read_text()        # the one port the tunnel key may forward to


# ------------------------------------------------------------------------------------------------ bridge watchdog
LEGACY_OK = b'{"ok": true, "api_version": 5, "methods": ["account"]}'


def _health(state: str, extra: bytes = b"") -> bytes:
    """A /health body as the bridge writes it (json.dumps separators), with the terminal state."""
    ok = b"true" if state == "ok" else b"false"
    return b'{"ok": ' + ok + b', "terminal": "' + state.encode() + b'", "terminal_for_s": 30' + extra + b"}"


def _bridge_watchdog(tmp_path: Path, *, active: bool = True, up_s: int = 3600, curl_ok: bool = True,
                     restarted_meanwhile: bool = False, args: tuple[str, ...] = ("icm-demo",),
                     env: dict[str, str] | None = None, bodies: list[bytes | None] | None = None,
                     bridge_state: str | None = None, term_active: bool = True, term_up_s: int = 3600,
                     term_restarted_meanwhile: bool = False, nrestarts: int = 0) -> tuple[int, str, str]:
    """The root script (and its unprivileged probe) with fake systemctl / curl / date / setpriv first on PATH; only
    the test copies' PATH line and the probe's install path are changed. `bodies`: what GET /health returns on each
    probe in turn (None: no answer); default: `curl_ok` -> the legacy {"ok": true} body, else no answer."""
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "calls"
    now = 2_000_000_000
    if bodies is None:
        bodies = [LEGACY_OK if curl_ok else None] * 10
    for n, b in enumerate(bodies):
        if b is not None:
            (tmp_path / f"body_{n}").write_bytes(b)
    (tmp_path / "curl_n").write_text("0")
    (tmp_path / "B_n").write_text("0")
    (tmp_path / "T_n").write_text("0")
    states = {"B": bridge_state or ("active" if active else "inactive"), "T": "active" if term_active else "inactive"}
    later = {"B": "B1" if restarted_meanwhile else "B0", "T": "T1" if term_restarted_meanwhile else "T0"}
    scripts = {
        "systemctl": f"""#!/bin/sh
echo "systemctl $*" >> {log}
[ "$1" = show ] || exit 0
case "$5" in goldbot-bridge@*) u=B ;; goldbot-mt5-terminal@*) u=T ;; *) echo "unexpected unit $5" >&2; exit 1 ;; esac
case "$3" in
  ActiveState) if [ $u = B ]; then echo {states["B"]}; else echo {states["T"]}; fi ;;
  NRestarts) echo {nrestarts} ;;
  ActiveEnterTimestamp) n=$(cat {tmp_path}/${{u}}_n); echo $((n + 1)) > {tmp_path}/${{u}}_n
     if [ "$n" = 0 ]; then echo "${{u}}0 stamp"
     elif [ $u = B ]; then echo "{later["B"]} stamp"; else echo "{later["T"]} stamp"; fi ;;
esac
exit 0
""",
        "curl": f"""#!/bin/sh
echo "curl $*" >> {log}
n=$(cat {tmp_path}/curl_n); echo $((n + 1)) > {tmp_path}/curl_n
[ -f {tmp_path}/body_$n ] || exit 7
cat {tmp_path}/body_$n
""",
        "date": f"""#!/bin/sh
if [ "$1" = -d ]; then
  case "$2" in "B0 stamp") echo {now - up_s} ;; "T0 stamp") echo {now - term_up_s} ;; *) echo {now - 1} ;; esac
else echo {now}; fi
""",
        "setpriv": f"""#!/bin/sh
echo "setpriv $*" >> {log}
while [ "$1" != -- ]; do shift; done
shift
exec "$@"
""",
        "sleep": "#!/bin/sh\nexit 0\n",
    }
    for name, body in scripts.items():
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    path_line = "PATH=/usr/sbin:/usr/bin:/sbin:/bin\n"
    probe_src = (LINUX / "goldbot-bridge-probe.sh").read_text()
    assert probe_src.count(path_line) == 1
    probe = tmp_path / "probe.sh"
    probe.write_text(probe_src.replace(path_line, f"PATH={fake}:/usr/bin:/bin\n"))
    probe.chmod(0o755)
    src = (LINUX / "goldbot-bridge-watchdog.sh").read_text()
    assert src.count(path_line) == 1 and src.count("PROBE=/usr/local/sbin/goldbot-bridge-probe\n") == 1
    copy = tmp_path / "wd.sh"
    copy.write_text(src.replace(path_line, f"PATH={fake}:/usr/bin:/bin\n")
                    .replace("PROBE=/usr/local/sbin/goldbot-bridge-probe\n", f"PROBE={probe}\n"))
    r = subprocess.run([BASH, str(copy), *args], capture_output=True, timeout=30, env={**os.environ, **(env or {})})
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return r.returncode, out, log.read_text() if log.exists() else ""


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_bridge_watchdog_leaves_a_healthy_bridge_alone(tmp_path):
    rc, _, calls = _bridge_watchdog(tmp_path)
    assert rc == 0 and calls.count("curl ") == 1 and "restart" not in calls
    assert "http://127.0.0.1:8765/health" in calls


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_bridge_watchdog_restarts_a_bridge_that_never_answers(tmp_path):
    rc, out, calls = _bridge_watchdog(tmp_path, curl_ok=False)
    assert rc == 0 and calls.count("curl ") == 3 and _restarted(calls) == ["goldbot-bridge@icm-demo.service"]
    assert "restarting" in out


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("kw", [dict(active=False), dict(up_s=60), dict(restarted_meanwhile=True)],
                         ids=["inactive", "in_start_grace", "restarted_while_probing"])
def test_bridge_watchdog_does_not_restart_when_it_should_not(tmp_path, kw):
    rc, _, calls = _bridge_watchdog(tmp_path, curl_ok=False, **kw)
    assert rc == 0 and "restart" not in calls, calls


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("args", [("",), ("../x",), ("icm demo",), ("icm-demo", "99999"), ("icm-demo", "80;id")])
def test_bridge_watchdog_refuses_bad_arguments(tmp_path, args):
    rc, _, calls = _bridge_watchdog(tmp_path, args=args)
    assert rc == 2 and calls == ""


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("env", [{"GOLDBOT_BRIDGE_PROBES": "0"}, {"GOLDBOT_BRIDGE_PROBE_TIMEOUT_S": "0"},
                                 {"GOLDBOT_BRIDGE_PROBES": "007"}, {"GOLDBOT_TERMINAL_GRACE_S": "x"},
                                 {"GOLDBOT_BRIDGE_ATTACH_RESTARTS": "0"}],
                         ids=["no_probes", "zero_timeout", "leading_zero", "bad_terminal_grace", "zero_attach"])
def test_bridge_watchdog_needs_at_least_one_probe_with_a_timeout(tmp_path, env):
    """Review LOW-3: 0 probes would restart without probing; a 0 s timeout means no timeout at all for curl."""
    rc, _, calls = _bridge_watchdog(tmp_path, curl_ok=False, env=env)
    assert rc == 2 and "restart" not in calls, calls


# ---- docs/proposals/2026-10-mt5-terminal-lifecycle.md section 4: the terminal state on /health (tests 1-8)
TERMINAL_UNIT = "goldbot-mt5-terminal@icm-demo.service"


def _restarted(calls: str) -> list[str]:
    return [ln.split()[-1] for ln in calls.splitlines() if ln.startswith("systemctl restart ")]


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_t1_terminal_ok_restarts_nothing(tmp_path):
    rc, out, calls = _bridge_watchdog(tmp_path, bodies=[_health("ok")])
    assert rc == 0 and _restarted(calls) == [] and calls.count("curl ") == 1
    assert "setpriv --reuid=nobody --regid=nogroup --clear-groups --no-new-privs -- " in calls   # the probe is unprivileged


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("state", ["not_running", "not_connected"])
def test_t2_a_terminal_down_on_every_probe_restarts_the_terminal_unit_not_the_bridge(tmp_path, state):
    rc, out, calls = _bridge_watchdog(tmp_path, bodies=[_health(state)] * 3, term_up_s=3600)
    assert rc == 0 and _restarted(calls) == [TERMINAL_UNIT], calls
    assert f"terminal {state}" in out and "restarting goldbot-mt5-terminal@icm-demo.service" in out


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("kw", [dict(term_up_s=300), dict(term_active=False)], ids=["under_10_min", "unit_inactive"])
def test_t3_a_young_or_inactive_terminal_unit_is_left_to_its_own_restart(tmp_path, kw):
    rc, _, calls = _bridge_watchdog(tmp_path, bodies=[_health("not_running")] * 3, **kw)
    assert rc == 0 and _restarted(calls) == [], calls


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("state", ["not_logged_in", "wrong_account"])
def test_t4_a_login_problem_is_logged_and_never_restarted(tmp_path, state):
    rc, out, calls = _bridge_watchdog(tmp_path, bodies=[_health(state)] * 3)
    assert rc == 0 and _restarted(calls) == [], calls
    assert f"terminal {state}" in out and "not restarting" in out


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("body", [
    b'{"ok": false, "terminal": "rebooting"}',                                   # unknown value
    b'{"ok": false, "terminal": "not_running"}' + b" " * 1100,                   # over 1 KiB
    b'{"ok": false, "terminal": "not_running", "terminal": "ok"}',               # two known values
    b'{"ok": false, "terminal": "not_running$(touch /tmp/gbx)"}',                # metacharacters
    b'{"ok": false, "terminal": "not_running`id`"}',
    b'{"ok": false, "terminal": "not_\x00running"}',                            # NUL
    b'{"ok": false, "terminal": "not_running\n"}',                               # newline
    b'{"ok": false, "terminal": "\x1b[31mnot_running\x1b[0m"}',                 # ANSI
    b'{"error": "not found"}',
    b"",
], ids=["unknown", "over_1k", "two_values", "dollar_paren", "backtick", "nul", "newline", "ansi", "404", "empty"])
def test_t5_any_other_body_is_no_answer_and_never_evaluated(tmp_path, body):
    rc, out, calls = _bridge_watchdog(tmp_path, bodies=[body] * 3)
    assert rc == 0 and _restarted(calls) == ["goldbot-bridge@icm-demo.service"], calls   # "no answer": today's rule
    assert not Path("/tmp/gbx").exists()


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("payload", [b"\nINJECTED line", b"\x00INJECTED", b"\x1b[2J\x1b[31mINJECTED",
                                     b"$(echo INJECTED)", b"`echo INJECTED`"], ids=["newline", "nul", "ansi",
                                                                                    "dollar_paren", "backtick"])
@pytest.mark.parametrize("state", ["not_logged_in", "not_running", "rebooting"])
def test_security_4_no_body_bytes_reach_the_log(tmp_path, payload, state):
    """Security conditions 2 and 4: whatever the (mt5-controlled) body holds, the journal gets only a fixed token."""
    body = _health(state, b', "x": "' + payload + b'"')
    rc, out, calls = _bridge_watchdog(tmp_path, bodies=[body] * 3)
    assert rc == 0
    assert "INJECTED" not in out and "\x1b" not in out and "\x00" not in out and "rebooting" not in out
    assert "INJECTED" not in "".join(ln for ln in calls.splitlines() if not ln.startswith("curl "))
    assert all(ln.startswith("goldbot-bridge-watchdog: ") for ln in out.splitlines()), out


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("bodies", [[_health("not_running"), None, _health("not_running")],
                                    [_health("not_running"), _health("not_running"), None],
                                    [None, None, _health("not_connected")]], ids=["gap", "last_silent", "first_two"])
def test_t6_mixed_probes_restart_nothing(tmp_path, bodies):
    rc, _, calls = _bridge_watchdog(tmp_path, bodies=bodies)
    assert rc == 0 and _restarted(calls) == [], calls


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_t7_a_terminal_restarted_while_probing_is_not_restarted_again(tmp_path):
    rc, _, calls = _bridge_watchdog(tmp_path, bodies=[_health("not_running")] * 3, term_restarted_meanwhile=True)
    assert rc == 0 and _restarted(calls) == [], calls


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("kw,restarts", [
    (dict(bridge_state="activating", nrestarts=3), True),
    (dict(up_s=60, nrestarts=5), True),                          # active, but inside its start grace: same rule
    (dict(bridge_state="activating", nrestarts=2), False),
    (dict(bridge_state="activating", nrestarts=3, term_up_s=300), False),
    (dict(bridge_state="activating", nrestarts=3, term_active=False), False),
], ids=["attach_loop", "attach_loop_young", "few_restarts", "young_terminal", "terminal_inactive"])
def test_a_bridge_that_cannot_attach_restarts_the_terminal_once_it_has_run_10_min(tmp_path, kw, restarts):
    """A hung terminal (process alive, IPC dead): the bridge exits (two not_running reads) and its re-attach times
    out, so it restarts without ever settling and /health is never up long enough to probe. NRestarts says so."""
    rc, out, calls = _bridge_watchdog(tmp_path, **kw)
    assert rc == 0 and _restarted(calls) == ([TERMINAL_UNIT] if restarts else []) and "curl " not in calls, calls
    if restarts:
        assert "restarted" in out and "times without settling" in out


def test_the_watchdog_restarts_only_the_two_literal_units():
    src = (LINUX / "goldbot-bridge-watchdog.sh").read_text()
    restarts = [ln.strip() for ln in src.splitlines() if "systemctl restart" in ln and not ln.lstrip().startswith("#")]
    assert sorted(restarts) == ['systemctl restart "$bridge"', 'systemctl restart "$term"'], restarts
    assert 'bridge="goldbot-bridge@$inst.service"' in src and 'term="goldbot-mt5-terminal@$inst.service"' in src
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for banned in ("eval", "grep", "$body", "source ", "read "):
        assert banned not in code, banned
    probe = (LINUX / "goldbot-bridge-probe.sh").read_text()
    assert "--max-filesize 1024" in probe and "-m " in probe and "eval" not in probe


# ---- test 8: the units and their install
def test_t8_the_terminal_unit_is_the_only_launcher_and_the_bridge_requires_it():
    term = (UNITS / "goldbot-mt5-terminal@.service").read_text()
    assert _directive(term, "User") == "mt5" and _directive(term, "Restart") == "always"
    assert _directive(term, "RestartSec") == "15" and _directive(term, "StartLimitIntervalSec") == "0"
    assert _directive(term, "RestartSteps") == "5" and _directive(term, "RestartMaxDelaySec") == "10min"
    assert _directive(term, "ExecStart") == "/usr/bin/wine /home/mt5/.wine/drive_c/MT5/ICMarkets/terminal64.exe"
    pre = _directive(term, "ExecStartPre")
    assert "pgrep -u mt5 -f" in pre and "[t]erminal64" in pre and "exit 1" in pre and "$" not in pre
    assert "goldbot-xvfb.service" in _directive(term, "Requires") and "DISPLAY=:99" in term
    bridge = (UNITS / "goldbot-bridge@.service").read_text()
    assert "goldbot-mt5-terminal@%i.service" in _directive(bridge, "Requires")
    assert "goldbot-mt5-terminal@%i.service" in _directive(bridge, "After")
    wait = _directive(bridge, "ExecStartPre")        # never let initialize(path=...) launch a second terminal
    assert wait.startswith("/usr/bin/timeout 60 ") and "[t]erminal64" in wait and "$" not in wait
    assert "starts the terminal itself" not in bridge


def test_t8_bootstrap_installs_the_terminal_unit_and_the_probe_from_roots_clone():
    boot = (LINUX / "mt5_bootstrap.sh").read_text()
    assert '"$OPS"/systemd/goldbot-mt5-terminal@.service' in boot
    assert 'install -m 755 -o root -g root "$OPS/goldbot-bridge-probe.sh" /usr/local/sbin/goldbot-bridge-probe' in boot
    assert "systemctl enable goldbot-mt5-terminal@icm-demo" in boot
    assert boot.index("goldbot-mt5-terminal@.service") < boot.index("systemctl daemon-reload")


def test_runbook_retires_the_hand_started_terminal():
    text = (Path(__file__).resolve().parents[1] / "docs" / "RUNBOOK.md").read_text()
    assert "wine 'C:\\MT5\\ICMarkets\\terminal64.exe' &" not in text
    assert "sudo systemctl start goldbot-mt5-terminal@icm-demo" in text
    for needle in ("Terminal down", "Cold-restart test", "umask 077", "never off-box"):
        assert needle in text, needle

# ------------------------------------------------------------------------------------------------ review follow-ups
def test_the_heartbeat_records_pass_durations_and_warns_on_a_slow_pass(tmp_path, caplog):
    """Review LOW-1: how close real passes come to WatchdogSec, measured on the host (last and max pass)."""
    clock, mono = FakeClock(), FakeClock(50.0)
    w = LoopWatch(tmp_path, "icm-demo", beat_every_s=0, notify_every_s=5, clock=clock, monotonic=mono,
                  notify=_recorder([]))
    w.ready()
    w.tick()
    mono.t += 0.5
    w.tick()
    b = _beat(tmp_path)
    assert b["last_pass_s"] == pytest.approx(0.5) and b["max_pass_s"] == pytest.approx(0.5)
    mono.t += 42.0
    with caplog.at_level("WARNING", logger="goldbot.ops.watchdog"):
        w.tick()
    b = _beat(tmp_path)
    assert b["last_pass_s"] == pytest.approx(42.0) and b["max_pass_s"] == pytest.approx(42.0)
    assert any("42" in r.getMessage() and "pass" in r.getMessage() for r in caplog.records), caplog.text
    caplog.clear()
    mono.t += 1.0
    with caplog.at_level("WARNING", logger="goldbot.ops.watchdog"):
        w.tick()
    b = _beat(tmp_path)
    assert b["last_pass_s"] == pytest.approx(1.0) and b["max_pass_s"] == pytest.approx(42.0) and not caplog.records
    assert watchdog.SLOW_PASS_S == 30


BOOT = {"brain": LINUX / "brain_bootstrap.sh", "mt5": LINUX / "mt5_bootstrap.sh"}


def test_brain_bootstrap_seeds_the_rollback_floor_right_after_the_units():
    """Review MEDIUM-1: a release older than the watchdog never sends READY=1, so under Type=notify it trades for up
    to TimeoutStartSec and is then stopped, over and over. The brain's floor is seeded with the bootstrap commit
    (root-verified) when there is none, root-owned 0644, never through a symlink."""
    text = BOOT["brain"].read_text()
    units = text.index('install -m 644 "$OPS/systemd/goldbot-$u.service" /etc/systemd/system/')
    block = text[units:text.index("systemctl enable \"$s\"", units)]
    assert "/etc/goldbot/rollback-floor" in block
    assert '[ -e /etc/goldbot/rollback-floor ] || [ -L /etc/goldbot/rollback-floor ]' in block   # kept as it is
    assert "install -m 644 -o root -g root" in block and 'install -d -m 755 -o root -g root /etc/goldbot' in block
    assert '^[0-9a-f]{40}$' in block                         # only a full commit id is written
    assert 'rev-parse HEAD' in block and '"$OPS_SRC/src"' in block   # without a ref: root's own clone, never $ROOT
    assert "$ROOT" not in block.replace('"$OPS/systemd/goldbot-$u.service"', "")


@pytest.mark.parametrize("role", sorted(BOOT))
def test_bootstraps_turn_off_core_dumps_and_crash_reports(role):
    """Review LOW-2: a watchdog SIGABRT must not leave the engine's memory (tokens, secrets) in a core file or an
    apport crash report; a pipe core_pattern ignores LimitCORE=0."""
    text = BOOT[role].read_text()
    i = text.index("kernel.core_pattern=|/bin/false")
    assert "/etc/sysctl.d/" in text[i - 400:i + 400] and "/proc/sys/kernel/core_pattern" in text
    assert "apport" in text[i - 800:i + 800]
    first_service = text.index("systemctl enable")
    assert i < first_service or role == "mt5" and i < text.index("systemctl enable goldbot-bridge@")


def test_runbook_says_what_an_older_release_does_under_type_notify():
    text = (Path(__file__).resolve().parents[1] / "docs" / "RUNBOOK.md").read_text()
    assert "restart in a loop" not in text
    assert "trades for up to 15 min" in text


def test_first_deploy_has_watchdog_acceptance_and_the_fallback():
    text = (Path(__file__).resolve().parents[1] / "docs" / "deploy" / "first-deploy.md").read_text()
    for needle in ("systemctl show -p ActiveState,WatchdogUSec,StatusText goldbot-engine@icm-demo",
                   "engine loop running", "engine_loop", "kill -STOP", "Watchdog timeout", "WatchdogSec=0",
                   "/proc/sys/kernel/core_pattern"):
        assert needle in text, needle
