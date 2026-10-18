"""Brain rollout guards for the TradingView feeds (ADR 0030; RUNBOOK 2.7b/2.7c): one pull pass at a time per host,
and the opt-in webhook / tv-pull services are watched by heartbeat once they run, as a WARN that tells the owner, never
a FAIL (TradingView must never block trading)."""
from __future__ import annotations

import ast
from pathlib import Path

from goldbot.data import tv_pull
from goldbot.ops import health
from tests.test_health import ago, make_ctx, write
from tests.test_tv_pull import T_BACKFILL, Clock, ReplayNetwork, run

ROOT = Path(__file__).resolve().parents[1]


def test_a_second_pass_on_the_same_host_is_skipped_without_a_request_or_a_record(tmp_path):
    clock = Clock(T_BACKFILL)
    net = ReplayNetwork("backfill_15m.json")
    with tv_pull.single_pass(tmp_path) as mine:
        assert mine
        rep = run(tmp_path, net, clock)
    assert rep.skipped and not rep.ok and "another tv-pull pass" in rep.reason
    assert net.gets == [] and net.sent == []
    assert tv_pull.PullLog(tmp_path / "tv.db").last() is None
    rep2 = run(tmp_path, ReplayNetwork("backfill_15m.json"), clock)          # the lock went with the first holder
    assert rep2.ok, rep2.reason


def test_the_pass_lock_is_free_again_after_a_pass_that_failed(tmp_path):
    clock = Clock(T_BACKFILL)
    run(tmp_path, ReplayNetwork("backfill_15m.json", fail_get=ConnectionError("reset")), clock)
    with tv_pull.single_pass(tmp_path) as mine:
        assert mine


def _names(tmp_path: Path) -> dict[str, health.Check]:
    return {c.name: c for c in health.run_checks(make_ctx(tmp_path)).checks}


def test_hosts_that_never_ran_the_tradingview_services_see_no_new_check(tmp_path):
    names = _names(tmp_path)
    assert "heartbeat:webhook" not in names and "heartbeat:tv-pull" not in names


def test_a_silent_webhook_warns_and_notifies_the_owner_but_never_fails(tmp_path):
    write(tmp_path / "heartbeat_webhook.json", {"service": "webhook", "ts": ago(10 * 60)})
    got = _names(tmp_path)
    assert got["heartbeat:webhook"].status == "warn" and "silent" in got["heartbeat:webhook"].reason
    assert got["heartbeat:tv-pull"].status == "ok" and "not running" in got["heartbeat:tv-pull"].reason
    assert health._alerting("heartbeat:webhook", "warn") and health._alerting("heartbeat:tv-pull", "warn")


def test_a_fresh_tv_pull_heartbeat_is_ok(tmp_path):
    write(tmp_path / "heartbeat_tv-pull.json", {"service": "tv-pull", "ts": ago(30)})
    assert _names(tmp_path)["heartbeat:tv-pull"].status == "ok"


def _calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_both_services_start_their_heartbeat():
    assert "start_heartbeat" in _calls(ROOT / "goldbot" / "ops" / "services" / "webhook.py")
    assert "start_heartbeat" in _calls(ROOT / "goldbot" / "data" / "tv_pull.py")
