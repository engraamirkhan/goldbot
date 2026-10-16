"""BACKLOG 59: while the market is closed (weekend, the weekday daily break) the engine's state file keeps moving on
the HOST clock, so health's age rules (bridge_compat `checked_at`, the engine's `ts`) see a live engine; a dead engine
(no writes) still fails, and the heartbeat neither reads the account nor runs a risk or exit step."""
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import runner
from goldbot.engine.runner import Engine
from goldbot.execution.bridge import API_VERSION, MIN_COMPATIBLE
from goldbot.execution.broker import Tick
from goldbot.ops import health
from goldbot.ops.bridge_compat import REQUIRED_METHODS, BridgeInfo, assess
from goldbot.ops.run import apply_bridge_compat, engine_pass, expire_bridge_compat
from tests.test_engine_clock import _engine, _tick

TZ = "NY+7"
SHA = "a" * 40
FRI_LAST = pd.Timestamp("2026-10-16 20:58:30", tz="UTC")     # the last quote before the weekend close (21:00 UTC)
WED_BREAK = pd.Timestamp("2026-10-14 20:58:30", tz="UTC")    # the last quote before the weekday daily break
INFO = BridgeInfo(reachable=True, server_tz=TZ, api_version=API_VERSION, min_compatible=MIN_COMPATIBLE,
                  methods=sorted(REQUIRED_METHODS), commit=SHA)


class _HostTime:
    """`time` as goldbot.engine.runner sees it, with `time()` on the test's host clock (the state file's `ts`)."""

    def __init__(self, host: list[pd.Timestamp]):
        self._host = host

    def time(self) -> float:
        return self._host[0].timestamp()

    def __getattr__(self, name: str) -> Any:
        return getattr(time, name)


@pytest.fixture
def frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    def make(last: pd.Timestamp) -> tuple[Engine, Any, list[pd.Timestamp], list[int]]:
        eng, pb, host = _engine(tmp_path, start=last)
        monkeypatch.setattr(runner, "time", _HostTime(host))
        writes = [0]
        real = eng._write_state

        def spy(*a: Any, **k: Any) -> None:
            writes[0] += 1
            real(*a, **k)

        eng._write_state = spy                                  # type: ignore[method-assign]
        apply_bridge_compat(eng, assess(INFO, TZ, SHA, now=host[0]), now=host[0])
        assert engine_pass(eng, pb, "XAUUSD")                   # the last quote: the start write
        return eng, pb, host, writes
    return make


def _closed_loop(eng: Engine, pb: Any, host: list[pd.Timestamp], seconds: int, step_s: int = 1,
                 engine_alive: bool = True) -> None:
    """The run loop with the market closed: the terminal repeats its last quote (a frozen tick stamp) while the host
    clock moves; the bridge check runs every 60 s on the host clock. `engine_alive` False: no pass at all (dead)."""
    end = host[0] + pd.Timedelta(seconds=seconds)
    last_check = host[0]
    while host[0] < end:
        host[0] += pd.Timedelta(seconds=step_s)
        if not engine_alive:
            continue
        if (host[0] - last_check).total_seconds() >= 60:
            last_check = host[0]
            apply_bridge_compat(eng, assess(INFO, TZ, SHA, now=host[0]), now=host[0])
        expire_bridge_compat(eng, host[0])
        assert engine_pass(eng, pb, "XAUUSD")


def _state(tmp_path: Path) -> dict[str, Any]:
    return json.loads((tmp_path / "engine_icm-demo.json").read_text())


def _hctx(tmp_path: Path, now: pd.Timestamp) -> health.HealthContext:
    return health.HealthContext(state_dir=tmp_path, now=now, get_secret={"mt5-bridge-url-icm-demo": "http://x"}.get,
                                accounts=[health.AccountRef(account_id="icm-demo", server_tz=TZ)],
                                bridge_info=lambda url: INFO, commit=SHA)


def _checks(tmp_path: Path, now: pd.Timestamp) -> tuple[health.Check, health.Check]:
    ctx = _hctx(tmp_path, now)
    bc = health.check_bridge_compat(ctx, ctx.accounts[0])
    assert bc is not None
    return bc, health.check_engine(ctx, "icm-demo")


def test_a_frozen_weekend_tick_still_rewrites_the_state_on_the_host_clock_and_health_stays_ok(tmp_path, frozen):
    eng, pb, host, writes = frozen(FRI_LAST)
    _closed_loop(eng, pb, host, 30 * 60)                        # into the weekend: 30 min past the last quote
    st = _state(tmp_path)
    assert host[0].timestamp() - st["ts"] <= eng.cfg.state_every_s          # written within the last 10 s
    checked = pd.Timestamp(st["bridge_compat"]["checked_at"])
    assert (host[0] - checked).total_seconds() <= 70                          # the 60 s verdict, published
    bc, engine = _checks(tmp_path, host[0])
    assert bc.status == "ok", bc.reason
    assert engine.status == "ok" and "market closed" in engine.reason, engine.reason


def test_the_weekend_heartbeat_tells_the_truth_about_the_tick_and_the_market(tmp_path, frozen):
    eng, pb, host, writes = frozen(FRI_LAST)
    _closed_loop(eng, pb, host, 20 * 60)
    st = _state(tmp_path)
    assert eng.state.last_tick_age_s < 1.0          # the last account refresh's reading, kept in memory for the gate
    assert st["last_tick_age_s"] == pytest.approx((host[0] - FRI_LAST).total_seconds(), abs=eng.cfg.state_every_s)
    assert st["tick_ahead_s"] == 0.0
    assert st["market_closed"] is True


def test_the_heartbeat_reads_no_account_and_runs_no_risk_or_exit_step(tmp_path, frozen):
    """Trading safety: the host-clock write only republishes; the account refresh (stage machine, kill switch,
    weekend rule) still runs on the tick clock alone, so a frozen tick never drives it."""
    eng, pb, host, writes = frozen(FRI_LAST)
    refreshes = [0]
    real = eng._refresh_account

    def spy(t: Tick) -> None:
        refreshes[0] += 1
        real(t)

    eng._refresh_account = spy
    before = eng.state.model_dump()
    _closed_loop(eng, pb, host, 10 * 60)
    assert refreshes[0] == 0
    assert eng.state.model_dump() == before                     # no EngineState field is touched by the heartbeat


def test_the_write_rate_stays_at_one_per_state_cadence(tmp_path, frozen):
    eng, pb, host, writes = frozen(FRI_LAST)
    start = writes[0]
    _closed_loop(eng, pb, host, 300, step_s=1)                  # the run loop polls far more often than every 10 s
    n = writes[0] - start
    assert 300 // eng.cfg.state_every_s - 2 <= n <= 300 // eng.cfg.state_every_s + 1


def test_a_dead_engine_still_fails_while_the_market_is_closed(tmp_path, frozen):
    eng, pb, host, writes = frozen(FRI_LAST)
    _closed_loop(eng, pb, host, 30 * 60, engine_alive=False)    # no pass, no write: the process is gone
    bc, engine = _checks(tmp_path, host[0])
    assert bc.status == "fail" and "not confirmed" in bc.reason
    # Sunday reopen: still nothing written, the engine check fails once the market has been open ENGINE_FAIL_S
    reopened = pd.Timestamp("2026-10-18 22:02", tz="UTC") + pd.Timedelta(seconds=health.ENGINE_FAIL_S + 60)
    bc2, engine2 = _checks(tmp_path, reopened)
    assert bc2.status == "fail" and engine2.status == "fail" and "while the market is open" in engine2.reason


def test_the_daily_break_keeps_the_state_moving_and_the_reopen_resumes_the_tick_cadence(tmp_path, frozen):
    eng, pb, host, writes = frozen(WED_BREAK)
    _closed_loop(eng, pb, host, 62 * 60)                        # 20:58:30 -> 22:00:30, the whole break
    assert health.DEFAULT_SESSIONS.is_open(pd.DatetimeIndex([host[0]]))[0] == False   # noqa: E712
    bc, engine = _checks(tmp_path, host[0])
    assert bc.status == "ok" and engine.status == "ok", (bc.reason, engine.reason)
    # 22:02: quotes resume and the account refresh runs again on the tick clock
    refreshes = [0]
    real = eng._refresh_account

    def spy(t: Tick) -> None:
        refreshes[0] += 1
        real(t)

    eng._refresh_account = spy
    host[0] = pd.Timestamp("2026-10-14 22:02:30", tz="UTC")
    pb.on_tick(_tick(host[0]))
    assert engine_pass(eng, pb, "XAUUSD")
    assert refreshes[0] == 1
    assert _state(tmp_path)["last_tick_age_s"] == pytest.approx(0.0, abs=1.0)


def test_a_replay_on_the_tick_clock_is_unchanged(tmp_path, frozen):
    """live_clock off (replays, backtests): the tick clock is the only clock, so a frozen tick writes nothing more."""
    eng, pb, host, writes = frozen(FRI_LAST)
    eng.cfg.live_clock = False
    start = writes[0]
    _closed_loop(eng, pb, host, 300)
    assert writes[0] == start
