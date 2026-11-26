"""D-1 fallback (ii) capture guard (goldbot/data/broker_capture.py, ADR 0105 (ii)): two checks that cannot mask each
other (SRE review of #471).

  learning:broker_m1_capture  FAIL, the same day, when the reference account's broker M1 for the due session day is
                              missed (fewer than 60 % of the session's minutes in `bars_1m_broker` after 23:50 UTC;
                              weekends and CME metals full closures are never due). The reason carries the recovery
                              command (docs/RUNBOOK.md "Broker M1 capture guard").
  learning:broker_m1_copy     FAIL when a closed month's copy into raw/broker_m1/ is missing more than 5 days after
                              month-end, or a month file no longer matches SHA256SUMS.

Part of the learning-liveness family (named `learning:`), judged by output, never by a job's completion. Reads the
store and raw/broker_m1 read-only, each at most every CACHE_S. Only on the brain (`on_brain`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from goldbot.data import broker_capture as BC
from goldbot.ops.checks._base import Check
from goldbot.ops.checks.learning import on_brain
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "learning:broker_m1_capture"
COPY_NAME = "learning:broker_m1_copy"
CACHE_S = 900.0
_CACHE: dict[tuple[str, str], tuple[pd.Timestamp, Check]] = {}


def _cached(ctx: HealthContext, name: str) -> Check | None:
    hit = _CACHE.get((str(ctx.state_dir), name))
    if hit is not None and 0 <= (ctx.now - hit[0]).total_seconds() < CACHE_S:
        return hit[1]
    return None


def _keep(ctx: HealthContext, out: Check) -> Check:
    _CACHE[(str(ctx.state_dir), out.name)] = (ctx.now, out)
    return out


@register_check("global", block=BLOCK_TAIL, pos=167, when=on_brain)
def check_broker_m1_capture(ctx: HealthContext) -> Check:
    """The daily capture (module docstring)."""
    hit = _cached(ctx, NAME)
    if hit is not None:
        return hit
    if ctx.settings is None:
        return Check(name=NAME, status="warn", reason="settings not loaded: capture can't be checked")
    try:
        from goldbot.data.store import Store
        bars = BC.read_capture(Store(ctx.settings.data_root), BC.REFERENCE_ACCOUNT,
                               start=ctx.now - pd.Timedelta(days=10))
    except Exception as exc:  # noqa: BLE001 - an unreadable store is "can't check", never a pass
        return Check(name=NAME, status="warn", reason=f"bars_1m_broker unreadable: {type(exc).__name__}")
    status, reason = BC.judge_capture(bars, ctx.now)
    return _keep(ctx, Check(name=NAME, status="fail" if status == "fail" else "ok", reason=reason))


@register_check("global", block=BLOCK_TAIL, pos=168, when=on_brain)
def check_broker_m1_copy(ctx: HealthContext) -> Check:
    """The month copies and their sums (module docstring)."""
    hit = _cached(ctx, COPY_NAME)
    if hit is not None:
        return hit
    try:
        status, reason = BC.judge_copy(ctx.now, raw_dir=BC.RAW_DIR)
    except OSError as exc:
        return Check(name=COPY_NAME, status="warn", reason=f"raw/broker_m1 unreadable: {type(exc).__name__}")
    return _keep(ctx, Check(name=COPY_NAME, status="fail" if status == "fail" else "ok", reason=reason))
