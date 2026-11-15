"""The red-box week's review features in the engine (docs/research/prereg-amendment-2026-11-15-redbox-week.md rule 5:
"RECORD the MFI state, swing trend, golden-zone flag, break depth (% of box height), session and box height on every
trade as features for the review"; owner 2026-11-15: "Record on each trade which higher-timeframe box (1h/4h) the
price sits in, if any, as a review feature only (not a filter)").

`journal` is called by `exploration._signal` for an RB*/RBC* arm after its rule ran: for every candidate on the bar
that just closed (queued, skipped by the stop band, NO_NEXT_BOX or REPLAY_ONLY) it journals one
`exploration_redbox_features` row keyed by the candidate id the shadow log (`exploration_obs`) and the order carry. The
rule's own columns (`arm_rules_redbox.FEATURES`), the session of the decision close and, for each timeframe above the
arm's (1h, 4h), the red box of that timeframe the close sits in, from the engine's complete 1m bars through the close.
Read-only and best effort: nothing here decides, gates, sizes or sends; an error is logged and the signal stands."""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.research.arm_rules_redbox import FEATURES, REDBOX_RULES

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration
    from goldbot.engine.runner import Engine

log = logging.getLogger(__name__)

ACTION = "exploration_redbox_features"
HTF = ("1h", "4h")
# UTC sessions of the decision close (review strata, never a filter): [from, to) hours
SESSIONS = (("asia", 22, 7), ("london", 7, 12), ("overlap", 12, 16), ("newyork", 16, 22))


def session_label(ts: pd.Timestamp) -> str:
    """The UTC session holding `ts` (SESSIONS): asia 22-07, london 07-12, overlap 12-16, newyork 16-22."""
    h = pd.Timestamp(ts).tz_convert("UTC").hour
    for name, lo, hi in SESSIONS:
        if (lo <= h < hi) if lo < hi else (h >= lo or h < hi):
            return name
    return "asia"


def htf_box(complete_1m: pd.DataFrame, tf: str, through: pd.Timestamp, price: float) -> list[float] | None:
    """[bottom, top] of the `tf` red box in force at the last `tf` bar closed by `through` that holds `price`, None
    when no box holds it or the bars are too few for the channel state (`smc_replica.context_bars`)."""
    from goldbot.data.resample import mid, resample_bars
    from goldbot.features import smc_replica as smc
    from goldbot.research.arm_rules_redbox import _boxes
    need = smc.context_bars() + 1
    src = complete_1m[complete_1m["visible_at"] <= through]
    bars = resample_bars(src.tail((need + 2) * tf_seconds(tf) // 60), tf)
    bars = bars[bars["visible_at"] <= through].reset_index(drop=True)
    if len(bars) < need:
        return None
    tops, bots = _boxes(mid(bars).reset_index(drop=True))[-1]
    inside = np.flatnonzero((np.asarray(bots) <= price) & (price <= np.asarray(tops)))
    if not len(inside):
        return None
    k = int(inside[np.argmin(np.asarray(tops)[inside] - np.asarray(bots)[inside])])
    return [round(float(bots[k]), 3), round(float(tops[k]), 3)]


def _clean(v: Any) -> Any:
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return round(v, 4) if isinstance(v, float) else v


def journal(eng: Engine, ex: Exploration, arm: Arm, kept: pd.DataFrame, skipped: pd.DataFrame, bars: pd.DataFrame,
            through: pd.Timestamp) -> int:
    """One ACTION row per candidate of `arm` on the bar closing at `through` (module docstring); the count journalled.
    Never raises."""
    from goldbot.engine.exploration import _pending, pending_id
    if arm.spec.rule not in REDBOX_RULES:
        return 0
    try:
        last = len(bars) - 1
        rows = pd.concat([kept.assign(skip=None), skipped.rename(columns={"reason": "skip"})], ignore_index=True)
        rows = rows[rows["idx"] == last]
        if rows.empty:
            return 0
        price = float(bars["bid_close"].iloc[-1] + bars["ask_close"].iloc[-1]) / 2.0 \
            if {"bid_close", "ask_close"} <= set(bars.columns) else float(bars["close"].iloc[-1])
        complete = eng.bars_1m[eng.bars_1m["visible_at"] <= through]
        htf = {tf: htf_box(complete, tf, through, price) for tf in HTF if tf_seconds(tf) > tf_seconds(arm.tf)}
        n = 0
        for (_, r), p in zip(rows.iterrows(), _pending(arm, rows, through), strict=True):
            feats = {f: _clean(r.get(f)) for f in FEATURES}
            feats |= {"session": session_label(through), "close": round(price, 3),
                      **{f"htf_{tf}_box": box for tf, box in htf.items()}}
            eng.decisions.append({"ts": through.isoformat(), "agent": arm.tag, "action": ACTION, "arm": arm.arm_id,
                                  "tf": arm.tf, "candidate": pending_id(eng, arm, p), "side": int(r["side"]),
                                  "stop_usd": _clean(float(r["risk_usd"])),
                                  "target_usd": _clean(float(r["target_r"]) * float(r["risk_usd"])),
                                  "skip": None if pd.isna(r["skip"]) else str(r["skip"]), "features": feats})
            n += 1
        return n
    except Exception as exc:                                    # noqa: BLE001 - review data, never a failed close
        log.warning("redbox features of %s at %s not journalled (%s)", arm.arm_id, through, type(exc).__name__)
        return 0
