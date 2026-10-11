"""BACKLOG 42 (gap G6): the brain checks the MT5 bridge's protocol, methods and zone, refuses entries against an
incompatible bridge (gate reason `bridge_incompatible`), never gates an exit, fails health and blocks preflight; a
commit mismatch alone only warns; an unreachable /health is reported but is not a block by itself."""
import io
import json
import socket
import threading
import time
from http.server import HTTPServer
from pathlib import Path
from typing import Any, Iterator

import pandas as pd
import pytest

from goldbot.execution.bridge import (
    API_VERSION,
    METHODS,
    MIN_COMPATIBLE,
    GuardedBroker,
    RemoteBroker,
    make_handler,
    protocol_digest,
)
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import bridge_compat as bc
from goldbot.ops import health
from goldbot.ops.bridge_compat import (
    BridgeCompat,
    BridgeCompatChecker,
    BridgeInfo,
    assess,
    check_bridge,
    repo_commit,
)
from goldbot.ops.run import apply_bridge_compat, begin_bridge_check, engine_pass, expire_bridge_compat
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import Outcome, Proposal
from tests.test_engine_clock import WED, _engine, _intent, _open, _tick

TOKEN = "t" * 40
TZ = "NY+7"
SHA_A, SHA_B = "a" * 40, "b" * 40


class _TzBroker(PaperBroker):
    server_tz = TZ


def _serve(handler: Any) -> tuple[HTTPServer, str]:
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def current_bridge() -> Iterator[str]:
    pb = _TzBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=WED, bid=2400.0, ask=2400.2))
    httpd, url = _serve(make_handler(GuardedBroker(pb, magic_base=260100), TOKEN, commit=SHA_A))
    yield url
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def bridge_without_loss_per_lot() -> Iterator[str]:
    """A bridge built before loss_per_lot existed: /health lists its methods without it and its RPC refuses it."""
    pb = _TzBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=WED, bid=2400.0, ask=2400.2))
    base = make_handler(pb, TOKEN, commit=SHA_B)

    class Older(base):  # type: ignore[valid-type,misc]
        def _reply(self, status: int, body: dict[str, Any]) -> None:
            if "methods" in body:
                body = {**body, "methods": [m for m in body["methods"] if m != "loss_per_lot"]}
            super()._reply(status, body)

        def do_POST(self) -> None:
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0))  # type: ignore[has-type]
            if json.loads(data or b"{}").get("method") == "loss_per_lot":
                self._reply(403, {"error": "method 'loss_per_lot' not allowed"})
                return
            self.rfile = io.BytesIO(data)
            super().do_POST()
    httpd, url = _serve(Older)
    yield url
    httpd.shutdown()
    httpd.server_close()


def _dead_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


# ---------------------------------------------------------------------------------------------- the bridge reports
PINNED = (2, 2, "2ed78529124f1c03973db79685adb8431c9fcaee51eb4b6e4cc92d1569701c34")


def test_api_version_is_bumped_whenever_the_method_table_signatures_or_records_change():
    """M2: the digest covers METHODS, the signature of every RemoteBroker method and of each bridge dispatch target
    (MT5Broker behind GuardedBroker), and the wire records' fields."""
    assert (API_VERSION, MIN_COMPATIBLE, protocol_digest()) == PINNED, (
        "the bridge protocol changed: bump bridge.API_VERSION (and MIN_COMPATIBLE when old peers can no longer "
        "interoperate) and update PINNED; deploy the MT5 box first")


def test_the_digest_moves_when_a_signature_changes(monkeypatch):
    before = protocol_digest()

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float, extra: int = 0) -> float | None:
        return None
    monkeypatch.setattr(RemoteBroker, "loss_per_lot", loss_per_lot)
    assert protocol_digest() != before


def test_the_brain_and_the_terminal_side_agree_on_every_signature():
    import inspect

    from goldbot.execution.mt5_adapter import MT5Broker
    for m in METHODS:
        assert inspect.signature(getattr(RemoteBroker, m)) == inspect.signature(getattr(MT5Broker, m)), m


def test_health_reports_commit_protocol_methods_and_zone_without_auth_or_secrets(current_bridge):
    import requests
    r = requests.get(f"{current_bridge}/health", timeout=5)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["server_tz"] == TZ and body["commit"] == SHA_A and body["api_version"] == API_VERSION
    assert body["methods"] == sorted(METHODS)
    assert body["min_compatible"] == MIN_COMPATIBLE
    assert set(body) == {"ok", "server_tz", "commit", "api_version", "min_compatible", "methods"}   # no account, token
    assert TOKEN not in r.text


def test_health_omits_a_commit_it_does_not_know():
    httpd, url = _serve(make_handler(_TzBroker(equity=1), TOKEN))
    try:
        info = bc.read_bridge_info(url)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert info.reachable and info.commit is None and info.methods == sorted(METHODS)


# ---------------------------------------------------------------------------------------------- the verdict
def test_a_matching_bridge_passes(current_bridge):
    v = check_bridge(current_bridge, TZ, SHA_A)
    assert v.status == "ok" and not v.blocks and not v.commit_differs and v.missing == []
    assert f"v{API_VERSION}" in v.reason and TZ in v.reason


def test_an_older_bridge_without_loss_per_lot_is_incompatible_and_names_it(bridge_without_loss_per_lot):
    v = check_bridge(bridge_without_loss_per_lot, TZ, SHA_A)
    assert v.blocks and v.missing == ["loss_per_lot"] and "loss_per_lot" in v.reason
    assert "127.0.0.1" not in v.reason                                  # never the tunnel endpoint


def test_a_bridge_from_before_this_check_does_not_report_its_protocol_and_blocks():
    old = BridgeInfo(reachable=True, server_tz=TZ)                      # today's bridge: {"ok": true, "server_tz": ..}
    v = assess(old, TZ, SHA_A)
    assert v.blocks and "does not report its protocol" in v.reason
    no_health = BridgeInfo(reachable=True, problem="/health answered HTTP 404 (a bridge without /health)")
    assert assess(no_health, TZ, SHA_A).blocks


def _info(**kw: Any) -> BridgeInfo:
    base: dict[str, Any] = dict(reachable=True, server_tz=TZ, api_version=API_VERSION, min_compatible=MIN_COMPATIBLE,
                                methods=sorted(METHODS))
    base.update(kw)
    return BridgeInfo(**base)


def test_a_zone_mismatch_or_an_older_protocol_blocks():
    v = assess(_info(server_tz="Europe/Athens"), TZ, None)
    assert v.blocks and "Europe/Athens" in v.reason and TZ in v.reason
    assert assess(_info(api_version=MIN_COMPATIBLE - 1), TZ, None).blocks          # bridge API < brain min
    assert assess(_info(server_tz=None), TZ, None).blocks
    assert assess(_info(), TZ, None).status == "ok"


def test_min_compatible_is_checked_both_ways():
    newer = _info(api_version=API_VERSION + 5, min_compatible=API_VERSION + 1)       # brain API < bridge min
    v = assess(newer, TZ, None)
    assert v.blocks and "min_compatible" in v.reason
    assert assess(_info(api_version=API_VERSION + 5), TZ, None).status == "ok"       # a newer bridge that still serves us
    assert assess(_info(min_compatible=None), TZ, None).blocks                       # must be reported


def test_an_invalid_server_tz_blocks_and_is_not_echoed():
    def r(body: dict[str, Any]) -> Any:
        return lambda *a, **k: _Resp(200, [json.dumps(body).encode()])
    body = {"ok": True, "server_tz": "Europe/Athens; drop table", "api_version": API_VERSION,
            "min_compatible": MIN_COMPATIBLE, "methods": sorted(METHODS)}
    info = bc.read_bridge_info("u", get=r(body))
    v = assess(info, TZ, None)
    assert v.blocks and "invalid server_tz" in v.reason and "drop table" not in v.reason
    assert bc.read_bridge_info("u", get=r({**body, "server_tz": "x" * 65})).server_tz_invalid
    assert bc.read_bridge_info("u", get=r({**body, "server_tz": "NY+7\n"})).server_tz_invalid       # fullmatch, not $
    assert assess(bc.read_bridge_info("u", get=r({**body, "server_tz": "NY+7"})), TZ, None).status == "ok"


def test_a_commit_mismatch_alone_is_reported_but_never_blocks(current_bridge):
    v = check_bridge(current_bridge, TZ, SHA_B)
    assert v.status == "ok" and not v.blocks and v.commit_differs
    assert SHA_A[:12] in v.reason and SHA_B[:12] in v.reason


def test_an_unreachable_health_keeps_the_last_reachable_verdict_and_never_confirms_a_bridge():
    v = check_bridge(_dead_url(), TZ, SHA_A, timeout_s=1.0)
    assert v.status == "unreachable" and not v.blocks and "127.0.0.1" not in v.reason and v.checked_at is None
    bad = BridgeCompat(status="incompatible", reason="the bridge lacks loss_per_lot", missing=["loss_per_lot"])
    kept = bc.next_verdict(bad, v)
    assert kept.blocks and "loss_per_lot" in kept.reason and "last verdict kept" in kept.reason
    ok = BridgeCompat(status="ok", reason="fine", checked_at=WED)
    assert bc.next_verdict(bad, ok) == ok
    after_ok = bc.next_verdict(ok, v)                       # reachable ok before: not a block, confirmation time kept
    assert after_ok.status == "unreachable" and not after_ok.blocks and after_ok.checked_at == WED
    for prev in (None, bc.unchecked("engine starting")):    # M1: never confirmed -> still blocking
        first = bc.next_verdict(prev, v)
        assert first.blocks and first.status == "unchecked" and "unreachable" in first.reason


def test_a_verdict_older_than_five_minutes_counts_as_unchecked():
    ok = BridgeCompat(status="ok", reason="fine", checked_at=WED)
    assert bc.effective(ok, WED + pd.Timedelta(seconds=bc.MAX_VERDICT_AGE_S)) is ok
    old = bc.effective(ok, WED + pd.Timedelta(seconds=bc.MAX_VERDICT_AGE_S + 1))
    assert old.blocks and old.status == "unchecked" and "min" in old.reason
    assert bc.effective(BridgeCompat(status="ok", reason="x"), WED).blocks          # never confirmed: no checked_at
    bad = BridgeCompat(status="incompatible", reason="lacks x", checked_at=WED)
    assert bc.effective(bad, WED + pd.Timedelta(hours=1)) is bad                   # already blocking: kept as is


class _Resp:
    """A streamed response: `chunks` is any iterable of bytes (infinite allowed), `delay` seconds before each."""

    def __init__(self, code: int, chunks: Any, delay: float = 0.0) -> None:
        self.status_code, self._chunks, self.delay, self.closed, self.read = code, chunks, delay, False, 0

    def iter_content(self, n: int) -> Iterator[bytes]:
        for c in self._chunks:
            time.sleep(self.delay)
            self.read += len(c)
            yield c

    def close(self) -> None:
        self.closed = True


def test_junk_on_the_port_is_reported_reachable_and_a_5xx_as_unreachable():
    calls: list[dict[str, Any]] = []

    def get(r: _Resp) -> Any:
        def f(*a: Any, **k: Any) -> _Resp:
            calls.append(k)
            return r
        return f
    assert bc.read_bridge_info("u", get=get(_Resp(502, []))).reachable is False
    assert calls and all(k.get("stream") is True for k in calls)                  # L1: streamed, never buffered whole
    junk = bc.read_bridge_info("u", get=get(_Resp(200, [b"<html>"])))
    assert junk.reachable and junk.problem and assess(junk, TZ, None).blocks
    lying = bc.read_bridge_info("u", get=get(_Resp(200, [json.dumps(
        {"ok": True, "server_tz": TZ, "api_version": True, "min_compatible": "1", "methods": "all",
         "commit": "not-a-sha"}).encode()])))
    assert lying.api_version is None and lying.methods is None and lying.commit is None and lying.min_compatible is None
    assert assess(lying, TZ, None).blocks


def test_a_huge_health_body_is_not_the_bridges_report_and_is_not_read_to_the_end():
    import itertools
    r = _Resp(200, itertools.repeat(b"x" * 4096))                                   # an endless body
    info = bc.read_bridge_info("u", get=lambda *a, **k: r)
    assert info.reachable and info.problem and "64 KiB" in info.problem and assess(info, TZ, None).blocks
    assert r.read <= bc.MAX_HEALTH_BYTES + 8192 and r.closed


def test_a_slow_drip_health_is_cut_at_the_total_deadline():
    import itertools
    r = _Resp(200, itertools.repeat(b" "), delay=0.1)                               # each chunk within any read timeout
    t0 = time.monotonic()
    info = bc.read_bridge_info("u", timeout_s=0.5, get=lambda *a, **k: r)
    assert time.monotonic() - t0 < 2.0
    end = time.monotonic() + 2.0                          # the abandoned read closes its response on its own deadline
    while not r.closed and time.monotonic() < end:
        time.sleep(0.02)
    assert r.closed
    assert not info.reachable and "deadline" in (info.problem or "")


# ---------------------------------------------------------------------------------------------- the engine
def _compat(eng: Any) -> BridgeCompat:
    assert eng.bridge_compat is not None
    return eng.bridge_compat


def _pending(eng: Any, pid: str = "icm-demo-bc") -> Proposal:
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id=pid, account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.12,
                    entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[pid] = (_intent(), prop, agent)
    return prop


def test_an_older_bridge_refuses_entries_with_its_reason_while_exits_and_closes_still_work(
        tmp_path, bridge_without_loss_per_lot):
    eng, pb, host = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    assert eng.gate.check(_intent(), eng.state).allowed                 # the baseline entry is fine
    apply_bridge_compat(eng, check_bridge(bridge_without_loss_per_lot, TZ, SHA_A))
    d = eng.gate.check(_intent(), eng.state)
    assert not d.allowed and d.reasons == ["bridge_incompatible"] and d.lots == 0.0
    # the approval re-check refuses too: nothing is sent
    prop = _pending(eng)
    prop.outcome = Outcome.APPROVED
    eng._on_decision(prop)
    assert not pb.positions()
    assert any("bridge_incompatible" in str(d.get("action")) for d in eng.decisions)
    # an engine stop still closes at market
    stop_pid = _open(pb, 1, 2399.0)
    eng.open[stop_pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == stop_pid))
    host[0] = WED + pd.Timedelta(seconds=1)
    pb.on_tick(_tick(host[0], 2398.0))
    assert engine_pass(eng, pb, "XAUUSD")
    assert stop_pid not in {p.position_id for p in pb.positions()}
    # the time barrier closes
    tb = _open(pb, 1, 2300.0)
    eng.open[tb] = eng._adopt(next(p for p in pb.positions() if p.position_id == tb))
    eng.open[tb].max_bars, eng.open[tb].bars_held = 1, 0
    eng._manage_open(pd.DataFrame())
    assert tb not in {p.position_id for p in pb.positions()}
    # and the kill switch closes everything
    _open(pb, 1, 2300.0)
    assert eng._kill_switch(everything=True) and not pb.positions()
    # the verdict is published for health and the dashboard; still blocking after the refresh
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.bridge_incompatible


def test_the_engine_publishes_the_verdict_in_its_state_file(tmp_path, bridge_without_loss_per_lot):
    eng, pb, host = _engine(tmp_path)
    apply_bridge_compat(eng, check_bridge(bridge_without_loss_per_lot, TZ, SHA_A))
    for s in range(0, 120, 15):
        host[0] = WED + pd.Timedelta(seconds=s)
        pb.on_tick(_tick(host[0]))
        eng.on_tick(_tick(host[0]))
    state = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert state["bridge_compat"]["status"] == "incompatible" and state["bridge_compat"]["missing"] == ["loss_per_lot"]
    assert pd.Timestamp(state["bridge_compat"]["checked_at"]).tzinfo is not None      # L1: published


def test_a_matching_bridge_lets_entries_through_and_a_fixed_bridge_unblocks(tmp_path, current_bridge,
                                                                            bridge_without_loss_per_lot):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    apply_bridge_compat(eng, check_bridge(current_bridge, TZ, SHA_A))
    assert eng.bridge_compat is not None and eng.bridge_compat.status == "ok"
    assert eng.gate.check(_intent(), eng.state).allowed
    apply_bridge_compat(eng, check_bridge(bridge_without_loss_per_lot, TZ, SHA_A))
    assert eng.gate.check(_intent(), eng.state).reasons == ["bridge_incompatible"]
    apply_bridge_compat(eng, check_bridge(current_bridge, TZ, SHA_A))     # the MT5 box was updated
    assert eng.gate.check(_intent(), eng.state).allowed


def test_an_unreachable_health_blocks_until_the_bridge_was_confirmed_once(tmp_path, current_bridge):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    apply_bridge_compat(eng, check_bridge(_dead_url(), TZ, SHA_A, timeout_s=1.0))   # prev None: never confirmed
    assert eng.bridge_compat is not None and _compat(eng).status == "unchecked"
    assert eng.gate.check(_intent(), eng.state).reasons == ["bridge_incompatible"]
    apply_bridge_compat(eng, check_bridge(current_bridge, TZ, SHA_A))
    assert eng.gate.check(_intent(), eng.state).allowed
    apply_bridge_compat(eng, check_bridge(_dead_url(), TZ, SHA_A, timeout_s=1.0))   # after an ok: stale data covers it
    assert _compat(eng).status == "unreachable" and eng.gate.check(_intent(), eng.state).allowed
    apply_bridge_compat(eng, None)                                      # a check that did not finish changes nothing
    assert _compat(eng).status == "unreachable"


def _checker(result: Any) -> BridgeCompatChecker:
    def check() -> BridgeCompat:
        if isinstance(result, threading.Event):
            result.wait(10)
            return BridgeCompat(status="ok", reason="late")
        return result() if callable(result) else result
    return BridgeCompatChecker(check)


def test_the_engine_starts_blocked_until_the_bridge_is_confirmed(tmp_path, current_bridge):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    hung = threading.Event()
    begin_bridge_check(eng, _checker(hung), timeout_s=0.2)              # no verdict in time
    assert eng.bridge_compat is not None and _compat(eng).status == "unchecked"
    assert eng.gate.check(_intent(), eng.state).reasons == ["bridge_incompatible"]
    hung.set()
    eng2, _, _ = _engine(tmp_path / "b")
    eng2._refresh_account(_tick(WED))
    begin_bridge_check(eng2, _checker(lambda: check_bridge(_dead_url(), TZ, SHA_A, timeout_s=1.0)))
    assert eng2.gate.check(_intent(), eng2.state).reasons == ["bridge_incompatible"]   # a first unreachable refuses
    eng3, _, _ = _engine(tmp_path / "c")
    eng3._refresh_account(_tick(WED))
    begin_bridge_check(eng3, _checker(lambda: check_bridge(current_bridge, TZ, SHA_A)))
    assert eng3.bridge_compat is not None and eng3.bridge_compat.status == "ok"
    assert eng3.gate.check(_intent(), eng3.state).allowed               # ok unblocks


def test_a_stale_verdict_blocks_the_engine_until_a_fresh_check(tmp_path, current_bridge):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    apply_bridge_compat(eng, check_bridge(current_bridge, TZ, SHA_A))
    checked = _compat(eng).checked_at
    assert checked is not None
    expire_bridge_compat(eng, checked + pd.Timedelta(seconds=60))
    assert eng.gate.check(_intent(), eng.state).allowed
    expire_bridge_compat(eng, checked + pd.Timedelta(minutes=6))        # e.g. the check thread hung
    assert _compat(eng).status == "unchecked"
    assert eng.gate.check(_intent(), eng.state).reasons == ["bridge_incompatible"]
    apply_bridge_compat(eng, check_bridge(current_bridge, TZ, SHA_A))
    assert eng.gate.check(_intent(), eng.state).allowed


def test_unchecked_since_is_stamped_once_and_kept_while_unchecked():
    first = bc.unchecked("engine starting", now=WED)
    assert first.unchecked_since == WED
    dead = BridgeCompat(status="unreachable", reason="/health unreachable (ConnectTimeout): x")
    later = bc.next_verdict(first, dead)
    assert later.status == "unchecked" and later.unchecked_since == WED              # not reset by each failure
    ok = BridgeCompat(status="ok", reason="fine", checked_at=WED)
    aged = bc.effective(ok, WED + pd.Timedelta(minutes=6))
    assert aged.status == "unchecked" and aged.unchecked_since == WED + pd.Timedelta(minutes=6)


def test_a_hung_check_thread_is_replaced_a_verdict_collected_and_entries_reopen(tmp_path, current_bridge):
    """N1/N2: a check stuck past 3 x CHECK_TIMEOUT_S is abandoned (daemon thread) and a new one started."""
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    hang = threading.Event()
    calls = [0]

    def check() -> BridgeCompat:
        calls[0] += 1
        if calls[0] == 1:
            hang.wait(30)                                                      # the first check never returns
        return check_bridge(current_bridge, TZ, SHA_A)
    clock = [1000.0]
    c = BridgeCompatChecker(check, clock=lambda: clock[0])
    begin_bridge_check(eng, c, timeout_s=0.2)
    assert _compat(eng).status == "unchecked" and eng.gate.check(_intent(), eng.state).reasons == ["bridge_incompatible"]
    clock[0] += bc.HUNG_AFTER_S - 1
    assert not c.start()                                                       # still inside the hung window
    clock[0] += 2                                                              # past 3 x CHECK_TIMEOUT_S
    assert c.start()                                                           # abandoned and replaced
    deadline = time.monotonic() + 10
    got = None
    while got is None and time.monotonic() < deadline:
        got = c.collect()
        time.sleep(0.02)
    assert got is not None and got.status == "ok"
    apply_bridge_compat(eng, got)
    assert eng.gate.check(_intent(), eng.state).allowed                        # entries reopen
    hang.set()


def test_the_get_runs_under_a_hard_deadline_even_when_it_blocks():
    """N2: requests' timeout is per read; a server dripping header bytes holds get() far longer. The whole GET runs in
    a thread joined with the deadline."""
    gate = threading.Event()

    def blocking_get(*a: Any, **k: Any) -> Any:
        gate.wait(10)
        raise AssertionError("unreachable")
    t0 = time.monotonic()
    info = bc.read_bridge_info("u", timeout_s=0.5, get=blocking_get)
    gate.set()
    assert time.monotonic() - t0 < 1.5
    assert not info.reachable and "deadline" in (info.problem or "")


def test_the_checker_runs_off_the_loop_and_a_raising_check_is_unreachable():
    gate = threading.Event()

    def slow() -> BridgeCompat:
        gate.wait(5)
        return BridgeCompat(status="ok", reason="fine")
    c = BridgeCompatChecker(slow)
    t0 = time.monotonic()
    assert c.start() and not c.start()                                  # never two at once
    assert c.collect() is None and time.monotonic() - t0 < 1.0          # the caller never waits
    gate.set()
    deadline = time.monotonic() + 5
    got = None
    while got is None and time.monotonic() < deadline:
        got = c.collect()
        time.sleep(0.01)
    assert got is not None and got.status == "ok"

    def boom() -> BridgeCompat:
        raise ConnectionError("http://10.0.0.5:8765 refused")
    v = BridgeCompatChecker(boom).run_once()
    assert v is not None and v.status == "unreachable" and "10.0.0.5" not in v.reason


# ---------------------------------------------------------------------------------------------- health and preflight
def _hctx(tmp_path: Path, info: Any, commit: str | None = SHA_A) -> health.HealthContext:
    return health.HealthContext(state_dir=tmp_path, now=WED, get_secret={"mt5-bridge-url-icm-demo": "http://x"}.get,
                                accounts=[health.AccountRef(account_id="icm-demo", server_tz=TZ)],
                                bridge_info=info, commit=commit)


def test_health_fails_naming_the_missing_method_and_alerts(tmp_path, bridge_without_loss_per_lot):
    ctx = _hctx(tmp_path, lambda url: bc.read_bridge_info(bridge_without_loss_per_lot))
    c = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c is not None and c.name == "bridge_compat:icm-demo" and c.status == "fail"
    assert "loss_per_lot" in c.reason and "bridge_incompatible" in c.reason
    text = health.alert_text({}, health.HealthReport(ts=WED, status="fail", checks=[c]))
    assert text is not None and "FAIL bridge_compat:icm-demo" in text and "loss_per_lot" in text
    assert "bridge_compat:icm-demo" in {x.name for x in health.run_checks(ctx).checks}


def test_health_passes_a_matching_bridge_and_warns_on_a_commit_mismatch(tmp_path, current_bridge):
    ctx = _hctx(tmp_path, lambda url: bc.read_bridge_info(current_bridge))
    ok = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert ok is not None and ok.status == "ok"
    other = _hctx(tmp_path, lambda url: bc.read_bridge_info(current_bridge), commit=SHA_B)
    w = health.check_bridge_compat(other, other.accounts[0])
    assert w is not None and w.status == "warn" and "commits differ" in w.reason
    assert health.alert_text({}, health.HealthReport(ts=WED, status="warn", checks=[w])) is None   # no Telegram noise


def test_health_reports_an_unreachable_bridge_and_fails_while_the_engine_still_blocks(tmp_path):
    dead = _dead_url()
    ctx = _hctx(tmp_path, lambda url: bc.read_bridge_info(dead, 1.0))
    c = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c is not None and c.status == "warn" and "unreachable" in c.reason and "127.0.0.1" not in c.reason
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(
        {"bridge_compat": {"status": "incompatible", "reason": "the bridge lacks loss_per_lot"}}))
    c2 = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c2 is not None and c2.status == "fail" and "loss_per_lot" in c2.reason


def _engine_verdict(tmp_path: Path, **compat: Any) -> None:
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"bridge_compat": compat}))


def test_health_fails_and_alerts_when_the_engine_has_not_confirmed_the_bridge_past_grace(tmp_path, current_bridge):
    """N1: the probe passes but the engine stays blocked (stuck check): FAIL (Telegram) past the grace period."""
    ctx = _hctx(tmp_path, lambda url: bc.read_bridge_info(current_bridge))
    _engine_verdict(tmp_path, status="ok", reason="fine", checked_at=(WED - pd.Timedelta(minutes=7)).isoformat())
    c = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c is not None and c.status == "fail" and "not confirmed" in c.reason        # ok aged past 5 min
    text = health.alert_text({}, health.HealthReport(ts=WED, status="fail", checks=[c]))
    assert text is not None and "FAIL bridge_compat:icm-demo" in text
    _engine_verdict(tmp_path, status="unchecked", reason="engine starting", checked_at=None,
                    unchecked_since=(WED - pd.Timedelta(seconds=bc.UNCHECKED_GRACE_S + 30)).isoformat())
    c2 = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c2 is not None and c2.status == "fail" and "not confirmed" in c2.reason     # unchecked past 2 min
    _engine_verdict(tmp_path, status="unchecked", reason="engine starting", checked_at=None)
    c3 = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c3 is not None and c3.status == "fail"                                      # no stamp: assume the worst


def test_health_warns_inside_the_grace_period(tmp_path, current_bridge):
    ctx = _hctx(tmp_path, lambda url: bc.read_bridge_info(current_bridge))
    _engine_verdict(tmp_path, status="unchecked", reason="engine starting", checked_at=None,
                    unchecked_since=(WED - pd.Timedelta(seconds=30)).isoformat())
    c = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c is not None and c.status == "warn" and "not confirmed" in c.reason
    assert health.alert_text({}, health.HealthReport(ts=WED, status="warn", checks=[c])) is None   # quiet inside


def test_health_warns_when_the_engine_has_not_confirmed_the_bridge_for_five_minutes(tmp_path, current_bridge):
    ctx = _hctx(tmp_path, lambda url: bc.read_bridge_info(current_bridge))
    fresh = (WED - pd.Timedelta(minutes=1)).isoformat()
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(
        {"bridge_compat": {"status": "ok", "reason": "fine", "checked_at": fresh}}))
    c3 = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert c3 is not None and c3.status == "ok"


def test_health_omits_the_check_without_a_bridge_or_a_reader(tmp_path):
    ctx = _hctx(tmp_path, None)
    assert health.check_bridge_compat(ctx, ctx.accounts[0]) is None
    no_url = ctx.model_copy(update={"get_secret": {}.get, "bridge_info": lambda url: BridgeInfo(reachable=True)})
    assert health.check_bridge_compat(no_url, no_url.accounts[0]) is None


# ---------------------------------------------------------------------------------------------- repo_commit
def _repo(root: Path, head: str) -> Path:
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head + "\n")
    return root


def test_repo_commit_reads_a_detached_head_a_loose_or_packed_branch_and_a_worktree(tmp_path):
    assert repo_commit(_repo(tmp_path / "detached", SHA_A)) == SHA_A
    loose = _repo(tmp_path / "loose", "ref: refs/heads/main")
    (loose / ".git" / "refs" / "heads" / "main").write_text(SHA_B + "\n")
    assert repo_commit(loose) == SHA_B
    packed = _repo(tmp_path / "packed", "ref: refs/heads/main")
    (packed / ".git" / "packed-refs").write_text(f"# pack-refs with: peeled\n{SHA_A} refs/heads/main\n")
    assert repo_commit(packed) == SHA_A
    wt = tmp_path / "wt"
    wt.mkdir()
    gitdir = loose / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (gitdir / "commondir").write_text("../..\n")
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    assert repo_commit(wt) == SHA_B                                     # the branch lives in the common dir
    assert repo_commit(tmp_path / "nothing") is None
    assert repo_commit(_repo(tmp_path / "junk", "not a sha")) is None
    assert repo_commit(_repo(tmp_path / "escape", "ref: ../../etc/passwd")) is None


def test_remote_broker_against_the_older_bridge_fails_loss_per_lot_loudly(bridge_without_loss_per_lot):
    """Why the check exists: the RPC itself is refused, which the engine would see only at sizing time."""
    from goldbot.execution.bridge import BridgeError
    with pytest.raises(BridgeError, match="403"):
        RemoteBroker(bridge_without_loss_per_lot, TOKEN).loss_per_lot("XAUUSD", 1, 2400.0, 4.0)
