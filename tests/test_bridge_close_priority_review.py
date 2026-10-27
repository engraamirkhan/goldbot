"""Trading-safety review of 49335812 (BACKLOG 135, ADR 0056), conditions of the approval:
(1) MEDIUM: cancelled jobs counted toward MAX_QUEUED, so a close was refused 503 behind four reads whose clients had
    gone. Only live queued jobs count, and close/modify are never refused by the cap.
(2) MEDIUM, PD decision: a live account needs bridge v7 AND its order lane up (`order_lane: separate` on /health);
    entries are refused otherwise, exits keep working. A lane that is `down` FAILs health (Telegram) on any account.
(3) LOW: an order the lane never sent (the lane went down before the write, or the write failed) gets an explicit
    "not sent" reply, a definite reject, so the brain does not book it ambiguous."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.execution import bridge as bridge_mod
from goldbot.execution.bridge_lane import NOT_SENT, ProcessLane
from goldbot.execution.bridge_queue import CLOSE, READ, Job, LaneNotSent, TerminalQueue
from goldbot.ops import accounts, health
from goldbot.ops.bridge_compat import BridgeInfo, assess
from tests.test_bridge_close_priority import PID, _Bridge, _intent, _Lane, _started, _Terminal

TZ = "Europe/Athens"
WED = pd.Timestamp("2026-10-28 10:00", tz="UTC")


# ---------------------------------------------------------------------------------------------- (1) the cap
def _held(q: TerminalQueue) -> threading.Event:
    gate = threading.Event()
    assert q.submit(Job(lambda: gate.wait(5.0), priority=READ, label="held"))
    deadline = time.monotonic() + 2.0
    while q.running is None and time.monotonic() < deadline:
        time.sleep(0.01)
    return gate


def test_a_close_behind_four_cancelled_reads_is_queued_not_refused():
    q = TerminalQueue("t", max_queued=4)
    gate = _held(q)
    reads = [Job(lambda: None, priority=READ, label="read") for _ in range(4)]
    for j in reads:
        assert q.submit(j)
    for j in reads:
        assert q.cancel(j)                                          # their clients went
    close = Job(lambda: "closed", priority=CLOSE, label="close")
    assert q.submit(close)
    assert q.submit(Job(lambda: None, priority=READ, label="read"))   # cancelled jobs free their places
    gate.set()
    assert close.done.wait(5.0) and close.result == "closed"


def test_a_full_queue_refuses_another_read_but_never_a_close_or_a_stop_change():
    q = TerminalQueue("t", max_queued=2)
    gate = _held(q)
    assert q.submit(Job(lambda: None, priority=READ, label="r1"))
    assert q.submit(Job(lambda: None, priority=READ, label="r2"))
    assert not q.submit(Job(lambda: None, priority=READ, label="r3"))
    close = Job(lambda: "closed", priority=CLOSE, label="close")
    modify = Job(lambda: "moved", priority=CLOSE, label="modify")
    assert q.submit(close) and q.submit(modify)
    gate.set()
    assert close.done.wait(5.0) and modify.done.wait(5.0) and q.counts["refused_full"] == 1


# ---------------------------------------------------------------------------------------------- (2) live needs the lane
def _info(lane: str | None, version: int = 7) -> BridgeInfo:
    return BridgeInfo(reachable=True, server_tz=TZ, api_version=version, min_compatible=5,
                      methods=sorted(bridge_mod.METHODS), commit=None, order_lane=lane)


def test_a_live_account_refuses_entries_unless_its_order_lane_is_up():
    for lane in (None, "off", "starting", "down"):
        v = assess(_info(lane), TZ, None, live=True)
        assert v.blocks and "order lane" in v.reason, lane
    assert assess(_info("separate"), TZ, None, live=True).status == "ok"
    assert assess(_info("separate", version=6), TZ, None, live=True).blocks     # and v7 still


def test_a_demo_account_trades_whatever_its_lane_state():
    for lane in (None, "off", "starting", "separate", "down"):
        assert not assess(_info(lane), TZ, None).blocks


def _hctx(tmp_path: Path, info: BridgeInfo, *, live: bool = False) -> health.HealthContext:
    return health.HealthContext(state_dir=tmp_path, now=WED, get_secret={"mt5-bridge-url-icm-demo": "http://x"}.get,
                                accounts=[health.AccountRef(account_id="icm-demo", server_tz=TZ, live=live)],
                                bridge_info=lambda url: info, commit=None)


def test_a_lane_that_is_down_fails_health_and_alerts_on_any_account(tmp_path):
    for live in (False, True):
        ctx = _hctx(tmp_path, _info("down"), live=live)
        c = health.check_bridge_compat(ctx, ctx.accounts[0])
        assert c is not None and c.status == "fail" and "order lane" in c.reason
        text = health.alert_text({}, health.HealthReport(ts=WED, status="fail", checks=[c]))
        assert text is not None and "FAIL bridge_compat:icm-demo" in text


def test_a_live_account_without_its_lane_fails_health_and_a_demo_without_one_is_fine(tmp_path):
    ctx = _hctx(tmp_path, _info("off"), live=True)
    c = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c is not None and c.status == "fail" and "bridge_incompatible" in c.reason
    demo = _hctx(tmp_path, _info("off"))
    d = health.check_bridge_compat(demo, demo.accounts[0])
    assert d is not None and d.status != "fail"


def test_health_and_preflight_mark_live_accounts_live():
    import inspect
    src = inspect.getsource(health) + inspect.getsource(__import__("goldbot.ops.preflight", fromlist=["x"]))
    assert src.count('live=a.mode != "demo"') + src.count('live=acc.mode != "demo"') >= 2


# ---------------------------------------------------------------------------------------------- (3) not sent
def test_a_lane_that_went_down_before_the_write_answers_not_sent():
    lane = ProcessLane(["nope"], spawned_exit=76)
    lane.state = "down"
    try:
        lane.place_order(_intent(), time.time())
        raise AssertionError("an order on a lane that is not up must not be sent")
    except LaneNotSent:
        pass


class _BrokenPipe:
    def write(self, b: bytes) -> None:
        raise BrokenPipeError(32, "broken pipe")

    def flush(self) -> None:
        pass


def test_a_write_that_fails_answers_not_sent_and_takes_the_lane_down():
    lane = ProcessLane(["nope"], spawned_exit=76)
    lane.state = "separate"
    lane._proc = type("P", (), {"stdin": _BrokenPipe(), "kill": lambda self: None})()
    try:
        lane.place_order(_intent(), time.time())
        raise AssertionError("expected LaneNotSent")
    except LaneNotSent:
        pass
    assert lane.state == "down"


class _NotSentLane(_Lane):
    def place_order(self, intent: Any, sent_at: float | None) -> Any:
        self.sent.append(intent.client_order_id)
        self.state = "down"
        raise LaneNotSent("order lane not up")


def test_through_the_bridge_an_order_never_sent_is_a_definite_reject_not_an_ambiguous_order():
    terminal = _started(_Terminal())
    b = _Bridge(terminal, _NotSentLane(terminal))
    try:
        res = b.client(order_timeout_s=5.0).place_order(_intent())
        assert not res.ok and res.retcode == bridge_mod.REFUSED_RETCODE and res.message.startswith(NOT_SENT)
        assert res.position_id is None and "place_order" not in b.methods()
        assert b.lane is not None and b.lane.sent == [PID]
    finally:
        b.close()


# ---------------------------------------------------------------------------------------------- config
def test_only_icm_live_runs_the_order_lane_from_the_box_the_bootstrap_installs():
    accs = accounts.load_accounts(logins=False)
    assert [a for a, acc in accs.items() if acc.order_lane] == ["icm-live"]
    assert accs["icm-live"].terminal_path == r"C:\MT5\ICMarkets\terminal64.exe"
