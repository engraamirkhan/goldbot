"""BACKLOG 135 (ADR 0056): before any real-money account the MT5 bridge queues closes ahead of reads and orders, drops
abandoned reads, and can send entries on a terminal connection of their own (the order lane), so an engine-side exit
is not held behind an order the terminal is sending (trading-safety review of 5d661e94; was about 45-55 s at
vantage-demo's 35 s order timeout).

A real HTTP bridge on localhost (make_server: requests on threads, one terminal worker per connection) serves a paper
broker standing for the terminal. `_Terminal` records every call that reaches it; its `hold` event keeps the order the
terminal is sending on that connection, as Vantage does for up to 31 s. Acceptance (BACKLOG 135):
(1) concurrent requests, one terminal call at a time per connection, close/modify > reads > place_order;
(2) a request whose client has gone, or a read queued past its client's timeout, is never run; an order queued past
    ORDER_MAX_AGE_S is refused stale;
(3)/(4) with an entry held 35 s on the order lane, an engine-side stop close, the Friday cut and the kill-switch
    flatten reach the terminal within 5 s, with reads flooding the bridge;
(5) the order is never re-sent: a lane that dies with it leaves an ambiguous order the BACKLOG 72 look-up settles;
(6) protocol v7, and a live account refuses entries on an older bridge."""
from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

from goldbot.engine import Engine, EngineConfig, order_send
from goldbot.engine.order_send import Sender
from goldbot.execution import bridge as bridge_mod
from goldbot.execution import bridge_queue
from goldbot.execution.bridge import BridgeError, BridgeUnavailable, RemoteBroker, make_server
from goldbot.execution.bridge_queue import CLOSE, ORDER, READ, LaneLost, TerminalQueue, priority_of
from goldbot.execution.broker import OrderIntent, OrderResult, Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops.bridge_compat import CLOSE_PRIORITY_VERSION, BridgeInfo, assess
from goldbot.ops.run import engine_pass
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Proposal

TOKEN = "t" * 40
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")          # a Wednesday
FRIDAY = pd.Timestamp("2025-03-07 19:30", tz="UTC")      # 21:30 Europe/Athens: the weekend cut
MAGIC = 260100
HOLD_S = 35.0                   # vantage-demo's order timeout: the longest an entry can hold its connection
EXIT_BOUND_S = 5.0              # the acceptance: an exit reaches the terminal within this, order in flight or not
PID = "icm-demo-135-a"


class _Terminal(PaperBroker):
    """The MT5 terminal behind the bridge. Records each call that reaches it (`calls`: method and time); `read_s`
    makes every read take that long on the terminal (a busy box). With `hold_orders` set, an order waits on `hold`
    (at most HOLD_S), as a broker taking its time to fill; `in_order` is set once the terminal is sending one."""

    def __init__(self, *, read_s: float = 0.0, hold_orders: bool = False) -> None:
        super().__init__(equity=10_000)
        self.read_s, self.hold_orders = read_s, hold_orders
        self.hold, self.in_order = threading.Event(), threading.Event()
        self.calls: list[tuple[str, float]] = []
        self.closed_at: dict[int, float] = {}
        self._inner = threading.local()         # the paper broker's own reads inside an order are not calls

    def _note(self, method: str) -> None:
        if not getattr(self._inner, "on", False):
            self.calls.append((method, time.monotonic()))

    def _read(self, method: str) -> None:
        if getattr(self._inner, "on", False):
            return
        self._note(method)
        if self.read_s:
            time.sleep(self.read_s)

    def _send(self, fn: Any, *args: Any) -> OrderResult:
        self._inner.on = True
        try:
            return fn(*args)
        finally:
            self._inner.on = False

    def last_tick(self, symbol: str) -> Tick:
        self._read("last_tick")
        return super().last_tick(symbol)

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        self._read("positions")
        return super().positions(magic_prefix)

    def account_positions(self) -> list[Position]:
        self._read("account_positions")
        return super().account_positions()

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        self._read("copy_ticks")
        return pd.DataFrame({"ts_utc": [T0], "bid": [2400.0], "ask": [2400.2]})

    def place_order(self, intent: OrderIntent) -> OrderResult:
        self._note("place_order")
        if self.hold_orders and intent.client_order_id == PID:
            self.in_order.set()
            self.hold.wait(HOLD_S)
        return self._send(super().place_order, intent)

    def close(self, position_id: int, lots: float | None = None) -> OrderResult:
        self._note("close")
        res = self._send(super().close, position_id, lots)
        if res.ok:
            self.closed_at[position_id] = time.monotonic()
        return res

    def modify(self, position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        self._note("modify")
        return self._send(super().modify, position_id, sl, tp)

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


class _Lane:
    """The order lane (bridge_lane.ProcessLane's surface) on a connection of its own: its orders hold `terminal.hold`
    without touching the bridge's connection. `lose` makes it die with the order in it."""

    def __init__(self, terminal: _Terminal, *, lose: bool = False) -> None:
        self.terminal, self.lose = terminal, lose
        self.state = "separate"
        self.sent: list[str] = []
        self.spawned_exit = 76

    def ready(self) -> bool:
        return self.state == "separate"

    def place_order(self, intent: OrderIntent, sent_at: float | None) -> OrderResult:
        self.sent.append(intent.client_order_id)
        self.terminal.in_order.set()
        self.terminal.hold.wait(HOLD_S)
        if self.lose:
            self.state = "down"
            raise LaneLost("order lane exited")
        return self.terminal._send(PaperBroker.place_order, self.terminal, intent)


class _Bridge:
    def __init__(self, terminal: _Terminal, lane: _Lane | None) -> None:
        self.terminal, self.lane = terminal, lane
        self.queue = TerminalQueue("terminal")
        self.httpd = make_server(terminal, "127.0.0.1", 0, TOKEN, terminal=self.queue, lane=lane)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def client(self, **kw: Any) -> RemoteBroker:
        return RemoteBroker(self.url, TOKEN, **kw)

    def methods(self) -> list[str]:
        """The calls that reached the terminal, flood reads left out."""
        return [m for m in self.terminal.methods() if m != "copy_ticks"]

    def close(self) -> None:
        self.terminal.hold.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def _started(terminal: _Terminal, at: pd.Timestamp = T0, bid: float = 2400.0) -> _Terminal:
    PaperBroker.on_tick(terminal, Tick(ts_utc=at, bid=bid, ask=bid + 0.2))
    return terminal


def _intent(cid: str = PID) -> OrderIntent:
    return OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=0.1, sl=2390.0, tp=2500.0, magic=MAGIC,
                       comment=cid)


def _open(terminal: _Terminal, cid: str, *, magic: int = MAGIC, lots: float = 0.1) -> int:
    r = terminal._send(PaperBroker.place_order, terminal, OrderIntent(
        client_order_id=cid, symbol="XAUUSD", side=1, lots=lots, sl=2300.0, tp=2500.0, magic=magic, comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


@pytest.fixture
def laned() -> Iterator[_Bridge]:
    terminal = _started(_Terminal())
    b = _Bridge(terminal, _Lane(terminal))
    yield b
    b.close()


@pytest.fixture
def shared() -> Iterator[_Bridge]:
    b = _Bridge(_started(_Terminal(hold_orders=True)), None)
    yield b
    b.close()


def _order_in_flight(b: _Bridge, timeout_s: float = HOLD_S) -> dict[str, Any]:
    """An entry sent the way the engine sends it (its own client, the account's order timeout) on a thread; returns
    a box with its result or error once it answers."""
    box: dict[str, Any] = {}

    def send() -> None:
        try:
            box["result"] = b.client(order_timeout_s=timeout_s).place_order(_intent())
        except Exception as exc:                      # noqa: BLE001 - asserted by the tests
            box["error"] = exc
    threading.Thread(target=send, daemon=True).start()
    assert b.terminal.in_order.wait(5.0), "the order never reached the terminal"
    return box


def _answered(box: dict[str, Any], timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while not box and time.monotonic() < deadline:
        time.sleep(0.02)
    assert box, "the order never answered"
    return box


def _flood(b: _Bridge, stop: threading.Event, clients: int = 6) -> list[threading.Thread]:
    """Reads on every client the brain runs beside the tick thread (history, M1 bars, sessions, health ...)."""
    def loop() -> None:
        rb = b.client(timeout_s=2.0)
        rb.read_attempts = 1
        while not stop.is_set():
            try:
                rb.copy_ticks("XAUUSD", T0, 10)
            except (BridgeError, BridgeUnavailable):
                pass
    threads = [threading.Thread(target=loop, daemon=True) for _ in range(clients)]
    for t in threads:
        t.start()
    return threads


# ---------------------------------------------------------------------------------------------- (1) the queue
def test_closes_and_stop_changes_run_first_then_reads_then_orders():
    assert priority_of("close") == priority_of("modify") == CLOSE < READ < ORDER == priority_of("place_order")
    assert all(priority_of(m) == READ for m, (_, retry) in bridge_mod.METHODS.items() if retry)


def test_queued_requests_run_closes_first_then_reads_then_the_order_one_terminal_call_at_a_time(shared):
    """Without an order lane: while the terminal is held by one call, an order, reads and a close queue up; the close
    runs first, the reads next, the order last, and never two terminal calls at once."""
    b = shared
    pos = _open(b.terminal, "held")
    b.terminal.read_s = 0.05
    gate = threading.Event()
    real = b.terminal.symbol_info

    def slow_symbol_info(symbol: str) -> Any:     # the call holding the terminal while the others queue
        gate.wait(5.0)
        return real(symbol)
    b.terminal.symbol_info = slow_symbol_info
    threading.Thread(target=lambda: b.client().symbol_info("XAUUSD"), daemon=True).start()
    time.sleep(0.2)
    done: dict[str, Any] = {}

    def run(name: str, fn: Any) -> threading.Thread:
        t = threading.Thread(target=lambda: done.__setitem__(name, fn()), daemon=True)
        t.start()
        time.sleep(0.1)                            # arrival order: order, read, read, close
        return t
    ts = [run("order", lambda: b.client().place_order(_intent("icm-demo-135-b"))),
          run("read1", lambda: b.client().last_tick("XAUUSD")),
          run("read2", lambda: b.client().positions()),
          run("close", lambda: b.client().close(pos))]
    gate.set()
    for t in ts:
        t.join(10.0)
    assert b.methods() == ["close", "last_tick", "positions", "place_order"]
    assert done["close"].ok and done["order"].ok
    starts = [t for _, t in b.terminal.calls]
    assert starts == sorted(starts)


def test_a_read_whose_client_gave_up_is_never_run_and_the_close_behind_it_runs_next(shared):
    b = shared
    pos = _open(b.terminal, "held")
    box = _order_in_flight(b)
    impatient = b.client(timeout_s=0.3)
    impatient.read_attempts = 1
    for _ in range(3):
        with pytest.raises(BridgeUnavailable):
            impatient.last_tick("XAUUSD")          # each gives up behind the order: its read must never run
    time.sleep(0.3)
    closer = threading.Thread(target=lambda: b.client(order_timeout_s=10.0).close(pos), daemon=True)
    closer.start()
    time.sleep(0.2)
    b.terminal.hold.set()
    closer.join(5.0)
    assert b.methods() == ["place_order", "close"], b.methods()
    assert b.queue.counts["cancelled"] == 3
    assert _answered(box)["result"].ok


def test_a_read_queued_past_the_timeout_its_client_sent_is_dropped_unrun(shared):
    """A client that stays connected but said how long it waits (protocol v7 `timeout_s`) is not served late."""
    b = shared
    _order_in_flight(b)
    hdr = {"Authorization": f"Bearer {TOKEN}"}
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.__setitem__("r", requests.post(
        f"{b.url}/rpc", json={"method": "last_tick", "args": ["XAUUSD"], "sent_at": time.time(), "timeout_s": 0.2},
        headers=hdr, timeout=10)), daemon=True)
    t.start()
    time.sleep(0.5)
    b.terminal.hold.set()
    t.join(5.0)
    assert out["r"].status_code == 504 and "dropped" in out["r"].json()["error"]
    assert "last_tick" not in b.methods()


def test_an_order_that_waited_in_the_queue_past_its_age_limit_is_refused_stale_and_never_sent(shared, monkeypatch):
    monkeypatch.setattr(bridge_mod, "ORDER_MAX_AGE_S", 0.5)
    b = shared
    _order_in_flight(b)
    out: dict[str, Any] = {}

    def second() -> None:
        try:
            b.client(order_timeout_s=10.0).place_order(_intent("icm-demo-135-b"))
        except BridgeError as exc:
            out["error"] = exc
    t = threading.Thread(target=second, daemon=True)
    t.start()
    time.sleep(0.8)
    b.terminal.hold.set()
    t.join(5.0)
    assert "409" in str(out["error"]) and "stale" in str(out["error"])
    assert b.methods() == ["place_order"]                          # only the first one reached the terminal


def test_an_order_whose_client_went_while_it_waited_is_never_sent(shared):
    b = shared
    _order_in_flight(b)
    with pytest.raises(BridgeUnavailable):
        b.client(order_timeout_s=0.3).place_order(_intent("icm-demo-135-b"))
    time.sleep(0.2)
    b.terminal.hold.set()
    time.sleep(0.3)
    assert b.methods() == ["place_order"] and b.queue.counts["cancelled"] == 1


def test_a_full_queue_refuses_a_further_request_with_503_and_runs_nothing(shared, monkeypatch):
    b = shared
    b.queue.max_queued = 1
    _order_in_flight(b)
    first = threading.Thread(target=lambda: b.client(timeout_s=10.0).last_tick("XAUUSD"), daemon=True)
    first.start()
    time.sleep(0.2)
    with pytest.raises(BridgeError, match="503"):
        b.client().positions()
    b.terminal.hold.set()
    first.join(5.0)
    assert b.methods() == ["place_order", "last_tick"]


def test_without_an_order_lane_a_close_lands_as_soon_as_the_held_order_lets_go(shared):
    """The shared connection's bound: the order's hold plus one call, not a read cycle on top (was 2 x 10 s), and not
    the flood of reads queued with it (each takes 0.2 s on the terminal: in arrival order the close would wait > 1 s)."""
    b = shared
    pos = _open(b.terminal, "held")
    b.terminal.read_s = 0.2
    _order_in_flight(b)
    stop = threading.Event()
    flooders = _flood(b, stop)
    closer = threading.Thread(target=lambda: b.client(order_timeout_s=10.0).close(pos), daemon=True)
    closer.start()
    time.sleep(1.0)
    released = time.monotonic()
    b.terminal.hold.set()
    closer.join(5.0)
    stop.set()
    for t in flooders:
        t.join(5.0)
    assert b.terminal.closed_at[pos] - released < 0.5
    assert b.methods().index("close") == 1                         # next after the order, ahead of every read


# ---------------------------------------------------------------------------------------------- (4) the order lane
def test_a_close_reaches_the_terminal_within_5_s_while_the_order_lane_holds_an_entry_for_35_s(laned):
    b = laned
    pos = _open(b.terminal, "held")
    box = _order_in_flight(b)
    stop = threading.Event()
    flooders = _flood(b, stop)
    t0 = time.monotonic()
    res = b.client().close(pos)
    elapsed = time.monotonic() - t0
    stop.set()
    for t in flooders:
        t.join(5.0)
    assert res.ok and elapsed < EXIT_BOUND_S, elapsed
    assert not box, "the entry was still in flight when the close landed"
    assert b.lane is not None and b.lane.sent == [PID] and "place_order" not in b.methods()


def test_while_the_order_lane_is_down_entries_go_back_to_the_bridges_own_connection(laned):
    b = laned
    assert b.lane is not None
    b.lane.state = "down"
    assert b.client().place_order(_intent("icm-demo-135-b")).ok
    assert b.methods() == ["place_order"] and b.lane.sent == []
    assert requests.get(f"{b.url}/health", timeout=5).json()["order_lane"] == "down"


def test_an_order_lost_with_its_lane_gets_no_reply_so_the_engine_books_it_ambiguous_and_never_resends_it(tmp_path):
    terminal = _started(_Terminal())
    lane = _Lane(terminal, lose=True)
    b = _Bridge(terminal, lane)
    try:
        terminal.hold.set()
        with pytest.raises(BridgeUnavailable):                     # no answer at all, not an error answered
            b.client(order_timeout_s=5.0).place_order(_intent("icm-demo-135-raw"))
        lane.state, lane.sent = "separate", []
        eng = _engine(tmp_path, b)
        eng.order_sender = Sender(b.client(order_timeout_s=5.0), wait_s=5.0)
        res = _send_entry(eng)
        assert res is not None and order_send.unanswered(res)
        assert eng._orders[PID].status == "ambiguous"              # the BACKLOG 72 look-up settles it
        assert not eng.state.tick_error
        assert _send_entry(eng) is None                            # the same entry is never sent again
        assert lane.sent == [PID] and "place_order" not in b.methods()   # nor inline behind the dead lane
        assert requests.get(f"{b.url}/health", timeout=5).json()["order_lane"] == "down"
    finally:
        b.close()


# ---------------------------------------------------------------------------------------------- engine-level exits
def _engine(tmp_path: Path, b: _Bridge, *, halt_checks: bool = True) -> Engine:
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=1,
                              halt_checks=halt_checks), b.client(), [], {}, ApprovalCenter({1}))
    eng.order_sender = Sender(b.client(order_timeout_s=HOLD_S), wait_s=0.05)
    return eng


def _send_entry(eng: Engine) -> OrderResult | None:
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id=PID, account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1, entry=2400.2,
                    stop=2390.0, target=2500.0, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    return eng._execute(prop, agent, 0.1, 2390.0, 2500.0, requested=2400.2)


def _entry(eng: Engine) -> None:
    """An entry the way the engine sends it: on the order worker, the tick thread back after wait_s."""
    res = _send_entry(eng)
    assert res is not None and order_send.unanswered(res)          # still in flight after wait_s


def test_an_engine_stop_close_reaches_the_terminal_within_5_s_of_the_stop_crossing_with_a_35_s_entry_in_flight(
        laned, tmp_path):
    b = laned
    pos = _open(b.terminal, "held", magic=MAGIC + 50)
    eng = _engine(tmp_path, b)
    eng._reconcile(None)
    assert pos in eng.open
    eng.open[pos].sl = 2398.0                                       # the engine's stop, tighter than the broker's
    _entry(eng)
    assert b.terminal.in_order.wait(5.0)
    stop = threading.Event()
    flooders = _flood(b, stop)
    _started(b.terminal, T0 + pd.Timedelta(seconds=1), bid=2397.5)   # the market goes through the engine's stop
    crossed = time.monotonic()
    while pos not in b.terminal.closed_at and time.monotonic() - crossed < 2 * EXIT_BOUND_S:
        engine_pass(eng, eng.broker, "XAUUSD")
    stop.set()
    for t in flooders:
        t.join(5.0)
    assert pos in b.terminal.closed_at, "the engine stop never closed"
    assert b.terminal.closed_at[pos] - crossed < EXIT_BOUND_S
    assert eng.order_sender is not None and eng.order_sender.busy()   # the entry was still held throughout
    assert not eng.state.tick_error and eng.tick_errors_total == 0


def test_the_kill_switch_flattens_the_whole_account_within_5_s_with_an_entry_in_flight_and_reads_flooding(
        laned, tmp_path):
    b = laned
    eng = _engine(tmp_path, b, halt_checks=False)
    ids = [_open(b.terminal, "own", magic=MAGIC + 50, lots=1.0), _open(b.terminal, "manual", magic=7)]
    eng._reconcile(None)
    _entry(eng)
    assert b.terminal.in_order.wait(5.0)
    stop = threading.Event()
    flooders = _flood(b, stop)
    t0 = time.monotonic()
    eng._kill_switch(everything=True)
    while any(p not in b.terminal.closed_at for p in ids) and time.monotonic() - t0 < 2 * EXIT_BOUND_S:
        eng._kill_switch(everything=True)
    stop.set()
    for t in flooders:
        t.join(5.0)
    assert all(p in b.terminal.closed_at for p in ids), b.terminal.closed_at
    assert max(b.terminal.closed_at[p] for p in ids) - t0 < EXIT_BOUND_S
    assert eng.order_sender is not None and eng.order_sender.busy()


def test_the_friday_cut_closes_losers_within_5_s_with_an_entry_in_flight_and_reads_flooding(tmp_path):
    terminal = _started(_Terminal(), FRIDAY - pd.Timedelta(minutes=30), bid=2400.0)
    b = _Bridge(terminal, _Lane(terminal))
    try:
        loser = _open(terminal, "loser", magic=MAGIC + 50)
        eng = _engine(tmp_path, b)
        eng._reconcile(None)
        _entry(eng)
        assert terminal.in_order.wait(5.0)
        stop = threading.Event()
        flooders = _flood(b, stop)
        _started(terminal, FRIDAY + pd.Timedelta(minutes=1), bid=2399.0)   # past the cut, under the entry price
        tick = eng.broker.last_tick("XAUUSD")
        t0 = time.monotonic()
        done = eng._weekend_rule(tick)
        stop.set()
        for t in flooders:
            t.join(5.0)
        assert done and loser in terminal.closed_at
        assert terminal.closed_at[loser] - t0 < EXIT_BOUND_S
        assert eng.order_sender is not None and eng.order_sender.busy()
    finally:
        b.close()


# ---------------------------------------------------------------------------------------------- (6) protocol
def test_the_protocol_is_v7_and_old_peers_stay_compatible():
    assert bridge_mod.API_VERSION == CLOSE_PRIORITY_VERSION == 7
    assert bridge_mod.MIN_COMPATIBLE == 5                           # a v6 bridge ignores timeout_s; a v6 brain omits it


def test_the_brain_sends_its_read_timeout_with_every_request():
    bodies: list[str] = []

    def post(url: str, data: str, **kw: Any) -> Any:
        bodies.append(data)
        r = requests.Response()
        r.status_code = 200
        r._content = b'{"result": 1.0}'
        return r
    RemoteBroker("http://bridge", TOKEN, timeout_s=3.0, post=post).margin_required("XAUUSD", 1, 0.1, 2400.0)
    RemoteBroker("http://bridge", TOKEN, order_timeout_s=35.0, post=post).close(1)
    import json
    assert [json.loads(x)["timeout_s"] for x in bodies] == [3.0, 35.0]


def _info(version: int) -> BridgeInfo:
    return BridgeInfo(reachable=True, server_tz="Europe/Athens", api_version=version, min_compatible=5,
                      methods=sorted(bridge_mod.METHODS), commit=None, order_lane="separate")   # live also needs its lane up (review of 49335812)


def test_a_live_account_refuses_entries_on_a_bridge_without_close_priority_and_demo_does_not():
    assert assess(_info(6), "Europe/Athens", None, live=True).blocks
    assert "BACKLOG 135" in assess(_info(6), "Europe/Athens", None, live=True).reason
    assert not assess(_info(6), "Europe/Athens", None).blocks         # demo: MIN_COMPATIBLE only
    assert assess(_info(7), "Europe/Athens", None, live=True).status == "ok"


def test_the_health_report_names_the_order_lane_state_as_a_fixed_token(laned):
    body = requests.get(f"{laned.url}/health", timeout=5).json()
    assert body["order_lane"] == "separate" and body["api_version"] == 7


def test_a_stuck_health_read_never_holds_a_close_queued_behind_it():
    """/health is served by the same worker (the terminal is not thread-safe) but after closes."""
    q = TerminalQueue("t")
    order: list[str] = []
    gate = threading.Event()
    jobs = [bridge_queue.Job(lambda: gate.wait(5.0), priority=ORDER, label="held"),
            bridge_queue.Job(lambda: order.append("health"), priority=bridge_queue.HEALTH, label="health"),
            bridge_queue.Job(lambda: order.append("close"), priority=CLOSE, label="close")]
    for j in jobs:
        assert q.submit(j)
        time.sleep(0.05)
    gate.set()
    for j in jobs:
        assert j.done.wait(5.0)
    assert order == ["close", "health"]
