"""The `specialist` arm rule (arms expansion 2026-10-26, section 3: "One new rule `specialist` ... It calls
`SPECIALISTS[name](timeframe=tf, **config).candidates_in_context(...)` and takes the stop from the specialist's own
`label_spec` and `barrier_atr`, exactly as `tsmom_rule` already does"). The rule logic is the specialist's, at the
commit the arm hash is frozen on; the same function runs in the history matrix and live.

An arm row names its specialist in `params`:

    rule: specialist
    timeframe: 15m                        # the decision timeframe, one the family runs on
    params:
      specialist: box_break               # a registered family (goldbot.specialists.SPECIALISTS)
      preset: L0                          # optional: one of the family's named presets
      config: {rung: L0}                  # optional: settings over the preset (explicit wins)
      window_local: {...}                 # the common arm params as every rule (window, min/max_stop_usd)

The stop is `label_spec.stop_atr` x the specialist's `barrier_atr` (ATR(14) of the decision bars when it has none),
read at the signal bar: the structural R of a candidate-R family (box course) or the ATR stop of the others. The
target is `label_spec.target_atr` / `stop_atr` in R, the time barrier `label_spec.max_bars`; a candidate that carries
its own `target_atr` or `max_bars` (H-49, the box course's per-timeframe holds) keeps them, as the research labels do.

Declared deviations (section 3, "0.01 lot, fixed brackets"): barrier form only. A family's `exit_spec` (the partial
and trail of `breakout` and `trend`) is not run: a 0.01 lot cannot be split. Entry is at market after the decision
close for every family (ARM-SPEC section 0), so a next-open family (H-45) fills at the close; the research sample rule
`complete_windows` (asia_drift) is not applied: it reads bars after the decision and live has none.

Refused (at load and at run): an unknown family, a family that needs TradingView values (`requires_tv`, e.g.
`tv_event`: shadow only by design) or whose signal is known only after the close (`entry_delay_s`), an unknown
preset, and a timeframe or config the family's own constructor refuses."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.research.arm_rules import RuleContext, _frame, empty
from goldbot.research.arms import Arm
from goldbot.specialists.base import Specialist

RULE = "specialist"
# families refused by name even when their module is not imported (tv_event is not in goldbot.specialists.__init__)
BLOCKED = {"tv_event": "needs TradingView values (requires_tv): shadow only by design (arms expansion section 3)"}


def refusal(name: str) -> str | None:
    """Why family `name` may not run as an arm, or None."""
    from goldbot.specialists import SPECIALISTS
    if name in BLOCKED:
        return f"specialist {name!r} is blocked: {BLOCKED[name]}"
    cls = SPECIALISTS.get(name)
    if cls is None:
        return f"unknown specialist {name!r}; known: {sorted(SPECIALISTS)}"
    if cls.requires_tv:
        return f"specialist {name!r} is blocked: it needs TradingView values (requires_tv), shadow only"
    if cls.entry_delay_s > 0:
        return f"specialist {name!r} is blocked: its signal is known {cls.entry_delay_s} s after the close"
    return None


def specialist_config(params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The family and its configuration (preset, then `config` over it) an arm's params name; refuses as `refusal`."""
    from goldbot.specialists import SPECIALISTS
    name = params.get("specialist")
    if not isinstance(name, str) or not name:
        raise ValueError("a specialist arm needs params.specialist, a family name")
    why = refusal(name)
    if why is not None:
        raise ValueError(why)
    config = params.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError(f"params.config must be a mapping, got {config!r}")
    preset = params.get("preset")
    if preset is not None:
        presets = SPECIALISTS[name].presets
        if preset not in presets:
            raise ValueError(f"unknown {name} preset {preset!r}; presets: {sorted(presets)}")
        config = {**presets[preset], **config}
    return name, dict(config)


def build_specialist(arm: Arm) -> Specialist:
    """`SPECIALISTS[name](timeframe=arm.timeframe, **config)`: the family's own constructor checks the timeframe and
    the settings (e.g. a box-course rung that is not pre-registered)."""
    from goldbot.specialists import SPECIALISTS
    name, config = specialist_config(arm.params)
    return SPECIALISTS[name](timeframe=arm.timeframe, **config)


def specialist_rule(arm: Arm, ctx: RuleContext) -> pd.DataFrame:
    """The family's own candidates on the arm's decision bars, in barrier form (module docstring). The higher-timeframe
    context is research's (`context_tfs`: every one of 1h/4h/1d longer than the decision bar, resampled from the 1m
    bars), and the point-in-time tables are `ctx.spec_ctx` (e.g. the engine's smc replica state), as
    `research.pipeline.prepare` gives them."""
    from goldbot.data.resample import resample_bars
    from goldbot.research.pipeline import build_decision_frame
    spec = build_specialist(arm)
    context = {TF_LABEL[tf]: resample_bars(ctx.b1, tf) for tf in context_tfs(spec.timeframe)}
    m, x = build_decision_frame(ctx.bars.reset_index(drop=True), context, ctx=ctx.spec_ctx)
    cands = spec.candidates_in_context(m, x, context, ctx.spec_ctx)
    if cands.empty:
        return empty()
    own = spec.barrier_atr(m, context)
    a = (atr(m, 14) if own is None else own).to_numpy(dtype=float)
    ls = spec.label_spec
    idx = cands["idx"].to_numpy(dtype=int)
    target_atr = cands["target_atr"].to_numpy(dtype=float) if "target_atr" in cands else np.full(len(idx), ls.target_atr)
    max_bars = cands["max_bars"].to_numpy(dtype=int) if "max_bars" in cands else np.full(len(idx), ls.max_bars)
    return _frame(idx, cands["side"].to_numpy(), ls.stop_atr * a[idx], target_atr / ls.stop_atr, max_bars + 1)


# Minutes of 1m history a family's inputs need before the decision bar for live to equal history (arms expansion
# lanes L2-L5, measured on their fixtures), when that is more than the engine's LOOKBACK bars. trend: the 4h EMA50
# slope (400 1h bars); mean_reversion: the 1h volatility tercile's 400-bar window + rv_20; session_open: the daily
# ATR(14) filter (30 trading days); eq_liquidity_run: its ATR200 equal-level threshold; the box course: its 1h/4h box
# sets (the whole ~40-day memory; fakey's 4h set needs more than the engine keeps, so live never equals history).
WARMUP_MIN: dict[str, int] = {
    "trend": 24_000, "mean_reversion": 25_200, "session_open": 41_400, "eq_liquidity_run": 15_000,
    "box_break": 60_000, "box_rip": 60_000, "box_retest": 60_000, "trend_level_signal": 60_000, "fakey": 80_000,
}


def warmup_bars(arm: Arm) -> int:
    """The family's warm-up (WARMUP_MIN) in bars of the arm's timeframe, rounded up; 0 when it declares none."""
    from goldbot.config import tf_seconds
    need = WARMUP_MIN.get(str(arm.params.get("specialist")), 0) * 60
    return -(-need // tf_seconds(arm.timeframe))
