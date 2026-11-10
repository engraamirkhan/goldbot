"""goldbot.telegram.bot: the health pass runs as its own task, one at a time, and never holds the pump. A pass took
~50 s on the brain (telegram CPU attribution 2026-11-10) and `_pump` awaited it, so a proposal card (the owner's 90 s
confirm window on real money) could reach the owner ~50 s late. The pump keeps sending while a pass runs; a pass due
while the last one still runs is skipped; each pass logs its duration and warns over HEALTH_SLOW_S."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from goldbot.ops import health
from goldbot.ops.health import HealthReport
from goldbot.telegram import bot as botmod


def _ok() -> HealthReport:
    return HealthReport(ts=pd.Timestamp.now("UTC"), status="ok", checks=[])


class _DeadMan:
    def observe(self, *a: object, **kw: object) -> None:
        pass


def _bot(tmp_path: Path, poll_s: float) -> botmod.TelegramBot:
    b = object.__new__(botmod.TelegramBot)
    b.owner_ids, b.state_dir, b.poll_s = set(), str(tmp_path), poll_s
    b.health = health.HealthWatch(tmp_path, policy=health.AlertPolicy(confirm_polls=3, confirm_s=300))
    b.deadman = _DeadMan()  # type: ignore[assignment]
    b.outbox = object()  # type: ignore[assignment]
    b.deploy = None
    b._health_task = None
    return b


def _no_op_passes(b: botmod.TelegramBot, monkeypatch: pytest.MonkeyPatch, sent: list[float]) -> None:
    async def outbox_pass(outbox: object, n: int) -> None:
        sent.append(time.monotonic())

    async def nothing() -> None:
        return None
    monkeypatch.setattr(b, "_outbox_pass", outbox_pass)
    for name in ("_automode_pass", "_digest_pass", "_limits_pass"):
        monkeypatch.setattr(b, name, nothing)


def test_a_slow_health_pass_does_not_delay_the_outbox_or_start_twice(tmp_path, monkeypatch):
    release, started = threading.Event(), []

    def slow_checks(ctx: object) -> HealthReport:
        started.append(time.monotonic())
        release.wait(5.0)                                   # a 5 s pass, released when the test is done
        return _ok()
    monkeypatch.setattr(botmod, "run_checks", slow_checks)
    monkeypatch.setattr(botmod.HealthContext, "from_runtime", classmethod(lambda cls, state_dir: None))
    monkeypatch.setattr(botmod, "HEALTH_EVERY_S", 0.1)    # a pass due every pump tick

    async def go() -> list[float]:
        b = _bot(tmp_path, poll_s=0.05)
        sent: list[float] = []
        _no_op_passes(b, monkeypatch, sent)
        pump = asyncio.get_running_loop().create_task(b._pump())
        await asyncio.sleep(1.0)
        pump.cancel()
        release.set()
        assert b._health_task is not None
        await asyncio.wait_for(b._health_task, 5)
        return sent
    t0 = time.monotonic()
    sent = asyncio.run(go())
    assert len(started) == 1                                # never a second pass while one runs
    assert len(sent) >= 10                                  # the outbox kept being pumped every tick
    gaps = [b - a for a, b in zip(sent, sent[1:])]
    assert max(gaps) < 0.5                                  # no outbox gap anywhere near the pass's length
    assert time.monotonic() - t0 < 4.0


def test_each_health_pass_logs_its_duration_and_warns_when_slow(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(botmod, "run_checks", lambda ctx: _ok())
    monkeypatch.setattr(botmod.HealthContext, "from_runtime", classmethod(lambda cls, state_dir: None))
    b = _bot(tmp_path, poll_s=0.05)
    caplog.set_level(logging.INFO, logger=botmod.__name__)
    asyncio.run(b._timed_health_pass())
    assert any("health pass s=" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)
    caplog.clear()
    monkeypatch.setattr(botmod, "HEALTH_SLOW_S", 0.0)
    asyncio.run(b._timed_health_pass())
    assert any("health pass s=" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
