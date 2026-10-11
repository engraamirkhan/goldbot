"""BACKLOG 42 (gap G6): the brain checks the MT5 bridge's protocol, methods and zone, refuses entries against an
incompatible bridge (gate reason `bridge_incompatible`), never gates an exit, fails health and blocks preflight; a
commit mismatch alone only warns; an unreachable /health is reported but is not a block by itself."""
import hashlib
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

from goldbot.execution.bridge import API_VERSION, METHODS, GuardedBroker, RemoteBroker, make_handler
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
from goldbot.ops.run import apply_bridge_compat, engine_pass
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
def test_api_version_is_bumped_whenever_the_method_table_changes():
    digest = hashlib.sha256(json.dumps(sorted(METHODS.items())).encode()).hexdigest()
    assert (API_VERSION, digest) == (1, "006a428d89024e2506c37bf6c1780adffa96cfc7824954f2b2c546e47b7890df"), (
        "METHODS changed: bump bridge.API_VERSION and update this pair (the brain refuses entries against an older "
        "bridge; deploy the MT5 box first)")


def test_health_reports_commit_protocol_methods_and_zone_without_auth_or_secrets(current_bridge):
    import requests
    r = requests.get(f"{current_bridge}/health", timeout=5)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["server_tz"] == TZ and body["commit"] == SHA_A and body["api_version"] == API_VERSION
    assert body["methods"] == sorted(METHODS)
    assert set(body) == {"ok", "server_tz", "commit", "api_version", "methods"}      # nothing else (no account, token)
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


def test_a_zone_mismatch_or_an_older_protocol_blocks():
    info = BridgeInfo(reachable=True, server_tz="Europe/Athens", api_version=API_VERSION, methods=sorted(METHODS))
    v = assess(info, TZ, None)
    assert v.blocks and "Europe/Athens" in v.reason and TZ in v.reason
    older = info.model_copy(update={"server_tz": TZ, "api_version": API_VERSION - 1})
    assert assess(older, TZ, None).blocks
    assert assess(info.model_copy(update={"server_tz": None}), TZ, None).blocks


def test_a_commit_mismatch_alone_is_reported_but_never_blocks(current_bridge):
    v = check_bridge(current_bridge, TZ, SHA_B)
    assert v.status == "ok" and not v.blocks and v.commit_differs
    assert SHA_A[:12] in v.reason and SHA_B[:12] in v.reason


def test_an_unreachable_health_is_not_a_block_and_keeps_an_earlier_incompatible_verdict():
    v = check_bridge(_dead_url(), TZ, SHA_A, timeout_s=1.0)
    assert v.status == "unreachable" and not v.blocks and "127.0.0.1" not in v.reason
    bad = BridgeCompat(status="incompatible", reason="the bridge lacks loss_per_lot", missing=["loss_per_lot"])
    kept = bc.next_verdict(bad, v)
    assert kept.blocks and "loss_per_lot" in kept.reason and "last verdict kept" in kept.reason
    ok = BridgeCompat(status="ok", reason="fine")
    assert bc.next_verdict(bad, ok) == ok and bc.next_verdict(None, v) == v and bc.next_verdict(ok, v) == v


def test_junk_on_the_port_is_reported_reachable_and_a_5xx_as_unreachable():
    class R:
        def __init__(self, code: int, body: Any) -> None:
            self.status_code, self._b = code, body

        def json(self) -> Any:
            if isinstance(self._b, Exception):
                raise self._b
            return self._b
    assert bc.read_bridge_info("u", get=lambda *a, **k: R(502, None)).reachable is False
    junk = bc.read_bridge_info("u", get=lambda *a, **k: R(200, ValueError("html")))
    assert junk.reachable and junk.problem and assess(junk, TZ, None).blocks
    lying = bc.read_bridge_info("u", get=lambda *a, **k: R(200, {"ok": True, "server_tz": TZ, "api_version": True,
                                                              "methods": "all", "commit": "not-a-sha"}))
    assert lying.api_version is None and lying.methods is None and lying.commit is None and assess(lying, TZ, None).blocks


# ---------------------------------------------------------------------------------------------- the engine
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


def test_an_unreachable_health_does_not_block_the_engine_by_itself(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    apply_bridge_compat(eng, check_bridge(_dead_url(), TZ, SHA_A, timeout_s=1.0))
    assert eng.bridge_compat is not None and eng.bridge_compat.status == "unreachable"
    assert eng.gate.check(_intent(), eng.state).allowed                 # the stale-data rule covers a dead bridge
    apply_bridge_compat(eng, None)                                      # a check that did not finish changes nothing
    assert eng.bridge_compat.status == "unreachable"


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
