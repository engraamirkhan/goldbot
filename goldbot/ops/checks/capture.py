"""`learning:broker_m1_capture`: the D-1 fallback (ii) capture guard (goldbot/data/broker_capture.py, ADR 0105 (ii)).

FAIL, the same day, when the reference account's broker M1 for the due session day is missed (fewer than 60 % of the
session's minutes in `bars_1m_broker` after 23:50 UTC), or when a closed month's copy into raw/broker_m1/ is missing
more than 5 days after month-end. Part of the learning-liveness family (named `learning:`), judged by output, never by
a job's completion. Reads the store read-only, at most every CACHE_S. Only on the brain (`on_brain`).
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
CACHE_S = 900.0
_CACHE: dict[str, tuple[pd.Timestamp, Check]] = {}


@register_check("global", block=BLOCK_TAIL, pos=167, when=on_brain)
def check_broker_m1_capture(ctx: HealthContext) -> Check:
    """The capture guard (module docstring)."""
    key = str(ctx.state_dir)
    hit = _CACHE.get(key)
    if hit is not None and 0 <= (ctx.now - hit[0]).total_seconds() < CACHE_S:
        return hit[1]
    if ctx.settings is None:
        return Check(name=NAME, status="warn", reason="settings not loaded: capture can't be checked")
    try:
        from goldbot.data.store import Store
        bars = BC.read_capture(Store(ctx.settings.data_root), BC.REFERENCE_ACCOUNT,
                               start=ctx.now - pd.Timedelta(days=10))
    except Exception as exc:  # noqa: BLE001 - an unreadable store is "can't check", never a pass
        return Check(name=NAME, status="warn", reason=f"bars_1m_broker unreadable: {type(exc).__name__}")
    status, reason = BC.judge(bars, ctx.now, raw_dir=BC.RAW_DIR)
    out = Check(name=NAME, status="fail" if status == "fail" else "ok", reason=reason)
    _CACHE[key] = (ctx.now, out)
    return out
