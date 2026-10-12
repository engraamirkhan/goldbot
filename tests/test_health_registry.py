"""The split of goldbot/ops/health.py into goldbot/ops/checks/ is a pure move (docs/process speed-up review item 7).

`run_checks` must give exactly the same (name, status, reason) list, in the same order, as the hand-written body it
replaced. tests/data/health_run_checks_golden.json was captured from that body (before the split) on the fixtures
below: the standard healthy fixture of tests/test_health.py, and a two-account state that drives every block of the
report (per-account checks account-major, the separate costs and reconciliation loops, both per-service loops, the
publish_release-only check, a guarded check that raises, warn and fail paths). Regenerate it only for an intended
change in the report:  GOLDBOT_WRITE_HEALTH_GOLDEN=1 pytest tests/test_health_registry.py
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pandas as pd
import pytest

from goldbot.ops import health
from goldbot.ops.health import AccountRef, HealthReport, HealthWatch
from tests.test_health import NOW, SETTINGS, ago, engine, healthy_state, make_ctx, order_row, write

GOLDEN = Path(__file__).parent / "data" / "health_run_checks_golden.json"
B = "icm-b"


def _rows(report: HealthReport, tmp_path: Path) -> list[list[str]]:
    return [[c.name, c.status, c.reason.replace(str(tmp_path), "<tmp>")] for c in report.checks]


def _settings(tmp_path: Path) -> Any:
    return SETTINGS.model_copy(update={
        "data_root": str(tmp_path / "data"),
        "costs": SETTINGS.costs.model_copy(update={"publish_release": True}),
        "research": SETTINGS.research.model_copy(update={"models_dir": str(tmp_path / "models")})})


def _busy_state(tmp_path: Path) -> None:
    """Healthy icm-demo with warnings and failures layered on, plus a second account in poor shape."""
    healthy_state(tmp_path)
    engine(tmp_path, 60, equity=9_000, day_start_equity=10_000, week_start_equity=10_000,
           last_tick_error_at=(NOW - pd.Timedelta(minutes=5)).isoformat(), tick_errors_total=2,
           throttle={"enabled": True, "agents": {"trend-0": {"blocked": ["throttle_loss_streak"], "loss_streak": 3,
                                                             "entries_today": 1, "cooldown_until": None},
                                                 "mr-1": {"blocked": [], "loss_streak": 0, "entries_today": 0}}},
           limits={"heat": {"enabled": True}}, open_risk=None,
           windows={"enabled": {"tier2": True, "holidays": True}, "calendar_unavailable": True,
                    "calendar_unavailable_since": (NOW - pd.Timedelta(hours=2)).isoformat(),
                    "calendar_unavailable_reason": "no store", "holiday_valid_through": "2026-11-30",
                    "holiday_version": "cme-metals-v2"},
           schema_skew={"count": 1, "reads": 3, "refused": False,
                        "entries": [{"source": "risk_<account>.json", "model": "RiskState", "fields": ["new_cap"],
                                     "count": 3, "refused": False}]})
    write(tmp_path / "orders_icm-demo.json",
          {"sent": {"p-bad": order_row("rejected", NOW - pd.Timedelta("1h"))}, "open": {}})
    # the second account: corrupt engine file, a risk file whose timestamp makes its check raise (guarded FAIL)
    (tmp_path / f"engine_{B}.json").write_text("{")
    write(tmp_path / f"risk_{B}.json", {"last_reset": "not a time", "stage": "halted"})
    write(tmp_path / f"costs_{B}.json", {"built_utc": (NOW - pd.Timedelta(days=5)).isoformat()})
    write(tmp_path / f"heartbeat_engine-{B}.json", {"phase": "starting", "ts": ago(10), "started": ago(120)})
    write(health.heartbeat_path(tmp_path, "news"), {"service": "news", "ts": ago(900)})
    write(health.heartbeat_path(tmp_path, "telegram"), {"service": "telegram", "ts": ago(30),
                                                        "schema_skew": {"error": "RuntimeError"}})
    write(health.heartbeat_path(tmp_path, "api"), {"service": "api", "ts": ago(30),
                                                   "schema_skew": {"count": 0, "reads": 0, "refused": False,
                                                                   "entries": []}})
    write(tmp_path / "drift.json", {"ts": NOW.isoformat(), "halted": ["trend-0"]})
    (tmp_path / "deploys.jsonl").write_text(json.dumps({"result": "rolled_back", "to": "abcdef1234", "role": "brain",
                                                        "ts": "2026-10-07T10:00:00Z", "detail": "health worse"}) + "\n")
    write(tmp_path / "agent_spend.json", {f"{NOW:%Y-%m}": 9.5})
    write(tmp_path / "approvals" / "pending" / "p1.json", {"created": ago(900), "window_s": 90})
    write(tmp_path / "control.json", {"halted": True, "by": "owner", "reason": "testing", "ts": ago(600)})
    # closed trades: half of icm-demo's are not net (pnl_basis warns); icm-b's are all net
    from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
    rows: list[tuple[str, Literal["net", "gross"] | None]] = [
        ("icm-demo", "net"), ("icm-demo", "net"), ("icm-demo", "gross"), ("icm-demo", None), (B, "net"), (B, "net")]
    for i, (acc, basis) in enumerate(rows):
        append_closed_trade(tmp_path, ClosedTrade(account_id=acc, broker="icm", mode="demo",
                                                  exit_utc=NOW - pd.Timedelta(days=1), ret=0.0, pnl=1.0,
                                                  equity_before=1000.0, position_id=i, pnl_basis=basis))


def _busy_ctx(tmp_path: Path) -> health.HealthContext:
    secrets = {"telegram-bot-token", "mt5-icm-demo", f"mt5-{B}", "mt5-bridge-url-icm-demo", "deadman-url"}

    def get_secret(k: str) -> str | None:
        if k == "mt5-bridge-url-icm-demo":
            return "http://127.0.0.1:18812"
        if k == "deadman-url":
            return "https://hc-ping.com/abc"
        return "s3cr3t" if k in secrets else None

    def ticks(account_id: str, symbol: str) -> Any:
        if account_id == B:
            raise ValueError("bridge down")
        return SimpleNamespace(ts_utc=NOW - pd.Timedelta(seconds=2))

    def info(url: str) -> Any:
        raise RuntimeError("no answer")

    return make_ctx(tmp_path, settings=_settings(tmp_path), get_secret=get_secret,
                    accounts=[AccountRef(account_id="icm-demo", server_tz="Europe/Athens"),
                              AccountRef(account_id=B, server_tz="Europe/Athens")],
                    bridge_probe=lambda url: None, tick_reader=ticks, bridge_info=info, clock=lambda: NOW,
                    last_run=NOW - pd.Timedelta(minutes=30), commit="abcdef1234")


def _capture(tmp_path: Path) -> dict[str, list[list[str]]]:
    healthy = tmp_path / "healthy"
    healthy.mkdir()
    healthy_state(healthy)
    busy = tmp_path / "busy"
    busy.mkdir()
    _busy_state(busy)
    return {"healthy": _rows(health.run_checks(make_ctx(healthy)), healthy),
            "healthy_static": _rows(health.run_checks(make_ctx(healthy), static_only=True), healthy),
            "busy": _rows(health.run_checks(_busy_ctx(busy)), busy),
            "busy_static": _rows(health.run_checks(_busy_ctx(busy), static_only=True), busy)}


def test_run_checks_gives_the_same_names_statuses_reasons_and_order_as_before_the_split(tmp_path):
    got = _capture(tmp_path)
    if os.environ.get("GOLDBOT_WRITE_HEALTH_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=1) + "\n", encoding="utf-8")
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for key in want:
        assert got[key] == want[key], key


def test_the_golden_fixture_reaches_every_block_and_every_status(tmp_path):
    """Guards the golden itself: a fixture that silently stopped exercising a block would prove nothing."""
    busy = json.loads(GOLDEN.read_text(encoding="utf-8"))["busy"]
    names = [n for n, _, _ in busy]
    assert {s for _, s, _ in busy} == {"ok", "warn", "fail"}
    for prefix in ("engine:", "risk:", "engine_loop:", "tick_errors:", "daily_cap:", "order_failed:", "throttle:",
                   "open_risk:", "entry_windows:", "schema_skew:icm-demo", "bridge:", "bridge_compat:",
                   "server_clock:", "schema_skew:svc:", "heartbeat:", "scheduler", "job:", "costs:icm-demo", "pnl_basis:",
                   "costs:published", "drift", "deploy", "approvals", "agent_spend", "deadman", "risk_state:icm-b"):
        assert any(n.startswith(prefix) for n in names), prefix
    # account-major inside the per-account block, then the separate costs loop
    assert names.index("engine:icm-b") > names.index("server_clock:icm-demo")
    assert names.index("costs:icm-b") == names.index("pnl_basis:icm-demo") + 1 == names.index("costs:icm-demo") + 2


@pytest.mark.parametrize("static_only", [False, True])
def test_every_registered_check_resolves_to_the_name_goldbot_ops_health_exports(static_only):
    """The registry calls each check through goldbot.ops.health, the patch point callers and tests use."""
    from goldbot.ops.checks.registry import blocks
    for _scope, specs in blocks(static_only=static_only):
        for s in specs:
            assert getattr(health, s.fn.__name__) is s.fn, s.fn.__name__


def test_a_check_patched_on_goldbot_ops_health_is_the_one_run_checks_calls(tmp_path, monkeypatch):
    """The guarded FAIL keeps the registered name ("deploy"); before the split the tail loop took the name from the
    function object, so a patched-in function renamed its own failure."""
    def boom(_ctx: object) -> None:
        raise TypeError("patched")
    monkeypatch.setattr(health, "check_deploy", boom)
    got = {c.name: c for c in health.run_checks(make_ctx(tmp_path)).checks}
    assert got["deploy"].status == "fail" and "TypeError" in got["deploy"].reason


def test_alerting_is_unchanged_on_the_busy_fixture(tmp_path):
    _busy_state(tmp_path)
    text = HealthWatch(tmp_path).poll(health.run_checks(_busy_ctx(tmp_path)))
    assert text is not None and "LOSS CAP" in text and "FAILED_EXEC" in text
