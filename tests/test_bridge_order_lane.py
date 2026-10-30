"""BACKLOG 135 (ADR 0056): the order lane's process (goldbot/execution/bridge_lane.py). The bridge starts a child that
holds a terminal connection of its own and sends `place_order` only; here the child is a real Python process serving a
paper broker behind GuardedBroker, so the pipe, the wire lines, the ready handshake and every failure path run for
real. Rules: one order at a time; the child's GuardedBroker refuses what the bridge's would; an error comes back as
the bridge's HTTP 500 would; a child that dies or goes silent with an order in it raises LaneLost (no reply to the
brain: an ambiguous order, never re-sent) and leaves the lane down; a child that launched a terminal of its own ends
the bridge (spawned_exit); output that is not the lane's on its stdout is ignored."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from goldbot.execution.bridge_lane import ProcessLane
from goldbot.execution.bridge_queue import LaneError, LaneLost, LaneNotSent
from goldbot.execution.broker import OrderIntent

pytestmark = pytest.mark.real_clock(reason="the child process and the file mtimes it is compared with read the OS clock")

ROOT = Path(__file__).resolve().parents[1]
SPAWNED = 76

CHILD = """
import sys, time
sys.path.insert(0, {root!r})
import pandas as pd
from goldbot.execution.bridge import GuardedBroker
from goldbot.execution.bridge_lane import serve_lane
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
mode = {mode!r}
out = sys.stdout.buffer
print("terminal banner on stdout", flush=True)             # the MetaTrader5 package's own output: ignored
if mode == "spawned":
    sys.exit({spawned})
if mode == "silent":
    time.sleep(60)
pb = PaperBroker(equity=10_000)
pb.on_tick(Tick(ts_utc=pd.Timestamp("2025-03-05 10:00", tz="UTC"), bid=2400.0, ask=2400.2))
if mode == "dies":
    def die(intent):
        sys.exit(3)
    pb.place_order = die
if mode == "hangs":
    def hang(intent):
        time.sleep(60)
    pb.place_order = hang
serve_lane(GuardedBroker(pb, magic_base=260100), sys.stdin.buffer, out)
"""


def _lane(mode: str = "ok", **kw: object) -> ProcessLane:
    argv = [sys.executable, "-c", CHILD.format(root=str(ROOT), mode=mode, spawned=SPAWNED)]
    return ProcessLane(argv, spawned_exit=SPAWNED, **kw)  # type: ignore[arg-type]


def _intent(cid: str = "icm-demo-135-a", magic: int = 260150, lots: float = 0.1) -> OrderIntent:
    return OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=lots, sl=2390.0, tp=2500.0, magic=magic,
                       comment=cid)


def test_the_lane_sends_the_order_through_its_own_process_and_returns_the_terminals_result():
    lane = _lane()
    try:
        assert lane.start() and lane.ready() and lane.state == "separate"
        res = lane.place_order(_intent(), time.time())
        assert res.ok and res.position_id is not None and res.filled_lots == pytest.approx(0.1)
        again = lane.place_order(_intent("icm-demo-135-b"), time.time())
        assert again.ok and again.position_id != res.position_id   # one at a time, in order
    finally:
        lane.stop()


def test_the_lanes_guarded_broker_refuses_what_the_bridges_would_and_says_why():
    lane = _lane()
    try:
        assert lane.start()
        with pytest.raises(LaneError, match="PermissionError: magic 7"):
            lane.place_order(_intent(magic=7), time.time())
        with pytest.raises(LaneError, match="lots outside"):
            lane.place_order(_intent(lots=5.0), time.time())
        assert lane.ready()                                         # an answered error leaves the lane up
    finally:
        lane.stop()


def test_an_order_that_aged_out_before_the_childs_send_is_refused_stale_not_sent():
    lane = _lane()
    try:
        assert lane.start()
        res = lane.place_order(_intent(), time.time() - 60)
        assert not res.ok and res.message.startswith("bridge_stale_order")
    finally:
        lane.stop()


def test_a_child_that_dies_with_the_order_in_it_raises_lane_lost_and_the_lane_stays_down():
    lane = _lane("dies")
    assert lane.start()
    with pytest.raises(LaneLost):
        lane.place_order(_intent(), time.time())
    assert lane.state == "down" and not lane.ready()
    with pytest.raises(LaneNotSent):                                # a dead lane sends nothing: a definite "not sent"
        lane.place_order(_intent("icm-demo-135-b"), time.time())


def test_a_child_that_goes_silent_with_the_order_in_it_is_given_up_after_the_cap():
    lane = _lane("hangs", order_max_s=1.0)
    assert lane.start()
    t0 = time.monotonic()
    with pytest.raises(LaneLost):
        lane.place_order(_intent(), time.time())
    assert time.monotonic() - t0 < 5.0 and lane.state == "down"


def test_a_child_with_no_ready_line_leaves_the_lane_down_and_orders_on_the_shared_connection():
    lane = _lane("silent", ready_timeout_s=1.0)
    assert not lane.start()
    assert lane.state == "down" and not lane.ready()


def test_a_child_whose_initialize_launched_a_terminal_ends_the_bridge():
    calls: list[str] = []
    lane = _lane("spawned", ready_timeout_s=5.0, on_spawned=lambda: calls.append("stop"))
    assert not lane.start()
    deadline = time.monotonic() + 5.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert calls == ["stop"] and lane.state == "down"


def test_a_child_that_cannot_be_started_leaves_the_lane_down_without_raising():
    def popen(*a: object, **k: object) -> object:
        raise FileNotFoundError("python.exe")
    lane = ProcessLane(["nope"], spawned_exit=SPAWNED, popen=popen)
    assert not lane.start() and lane.state == "down"


def test_the_lane_child_command_is_the_bridge_order_lane_service_on_this_interpreter():
    from goldbot.execution.bridge_lane import lane_argv
    assert lane_argv("vantage-demo") == [sys.executable, "-m", "goldbot.ops.run", "bridge-order-lane", "vantage-demo"]


def test_the_lane_drill_probe_lists_each_slow_or_failed_read_and_never_the_bridge_address():
    import importlib.util

    import pandas as pd
    spec = importlib.util.spec_from_file_location("bridge_latency_probe", ROOT / "scripts" / "bridge_latency_probe.py")
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    t = pd.Timestamp("2026-10-28 10:00", tz="UTC")
    lines = probe.summarise([(t, 0.05), (t, 0.07), (t, 2.4), (t, None)], 1.0)
    assert lines[0] == "reads 4, answered 3, failed 1"
    assert lines[1].startswith("latency p50 0.070 s") and lines[1].endswith("max 2.400 s")
    assert lines[2:] == [f"slow {t.isoformat()} 2.40 s", f"failed {t.isoformat()}"]
    assert not any("127.0.0.1" in x or "http" in x for x in lines)


def test_a_child_that_exits_while_idle_takes_the_lane_down_before_any_order_is_sent_into_it():
    lane = _lane("dies")
    assert lane.start()
    proc = lane._proc
    assert proc is not None
    proc.stdin.close()                                              # the child reads EOF and exits, idle
    deadline = time.monotonic() + 5.0
    while lane.state == "separate" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert lane.state == "down" and not lane.ready()               # the bridge sends the next order inline
