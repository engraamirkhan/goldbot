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
    assert loop.index("while True:") < loop.index("watch.tick()") < loop.index("engine_pass(")


def test_the_bridge_watchdog_is_installed_from_roots_clone_and_enabled():
    boot = (LINUX / "mt5_bootstrap.sh").read_text()
    assert 'install -m 755 -o root -g root "$OPS/goldbot-bridge-watchdog.sh" /usr/local/sbin/goldbot-bridge-watchdog' in boot
    assert '"$OPS"/systemd/goldbot-bridge-watchdog@.service' in boot and '"$OPS"/systemd/goldbot-bridge-watchdog@.timer' in boot
    assert "systemctl enable --now goldbot-bridge-watchdog@icm-demo.timer" in boot
    svc = (UNITS / "goldbot-bridge-watchdog@.service").read_text()
    assert _directive(svc, "ExecStart") == "/usr/local/sbin/goldbot-bridge-watchdog %i 8765"
    assert "8765" in (LINUX / "mt5_bootstrap.sh").read_text()        # the one port the tunnel key may forward to


# ------------------------------------------------------------------------------------------------ bridge watchdog
def _bridge_watchdog(tmp_path: Path, *, active: bool = True, up_s: int = 3600, curl_ok: bool = True,
                     restarted_meanwhile: bool = False, args: tuple[str, ...] = ("icm-demo",)) -> tuple[int, str, str]:
    """The script with fake systemctl / curl / date first on PATH (only the test copy's PATH line is changed)."""
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "calls"
    now = 2_000_000_000
    stamps = ["Sun 2033-05-15 00:00:00 UTC", "Sun 2033-05-15 00:05:00 UTC" if restarted_meanwhile else
              "Sun 2033-05-15 00:00:00 UTC"]
    (tmp_path / "stamp_n").write_text("0")
    scripts = {
        "systemctl": f"""#!/bin/sh
echo "systemctl $*" >> {log}
case "$1" in
  is-active) exit {0 if active else 3} ;;
  show) n=$(cat {tmp_path}/stamp_n); echo $((n + 1)) > {tmp_path}/stamp_n
        if [ "$n" = 0 ]; then echo "{stamps[0]}"; else echo "{stamps[1]}"; fi ;;
esac
exit 0
""",
        "curl": f"""#!/bin/sh
echo "curl $*" >> {log}
exit {0 if curl_ok else 7}
""",
        "date": f"""#!/bin/sh
if [ "$1" = -d ]; then echo {now - up_s}; else echo {now}; fi
""",
        "sleep": "#!/bin/sh\nexit 0\n",
    }
    for name, body in scripts.items():
        (fake / name).write_text(body)
        (fake / name).chmod(0o755)
    src = (LINUX / "goldbot-bridge-watchdog.sh").read_text()
    assert src.count("PATH=/usr/sbin:/usr/bin:/sbin:/bin\n") == 1
    copy = tmp_path / "wd.sh"
    copy.write_text(src.replace("PATH=/usr/sbin:/usr/bin:/sbin:/bin\n", f"PATH={fake}:/usr/bin:/bin\n"))
    r = subprocess.run([BASH, str(copy), *args], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout, log.read_text() if log.exists() else ""


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_bridge_watchdog_leaves_a_healthy_bridge_alone(tmp_path):
    rc, _, calls = _bridge_watchdog(tmp_path)
    assert rc == 0 and calls.count("curl ") == 1 and "restart" not in calls
    assert "http://127.0.0.1:8765/health" in calls


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_bridge_watchdog_restarts_a_bridge_that_never_answers(tmp_path):
    rc, out, calls = _bridge_watchdog(tmp_path, curl_ok=False)
    assert rc == 0 and calls.count("curl ") == 3 and "systemctl restart goldbot-bridge@icm-demo.service" in calls
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
