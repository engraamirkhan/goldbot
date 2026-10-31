"""The `spec_rule` arm: one interpreter that runs any `StrategySpec` of autonomous discovery live (ADR 0058 section 4,
design 2026-10-28 phase 4). After this module is reviewed once, a discovered arm is a data change: a row of
`config/spec_arms.yaml` (hash-frozen like every arm) carrying the canonical spec JSON and its hash.

Same primitives, not a re-implementation. The entry mask, the session window and the side come from
`autodisc.primitives.PrimitiveProvider` (the explore evaluator's MaskProvider), here over the engine's own bars
(`LiveProvider`: the same class with the explore-window cut off, so it can read bars after 2018; the explore pipeline
keeps refusing them). `spec_masks` is the one composition of those leaves, `spec_candidates` turns it into the
candidate frame every arm rule returns, and the stop is the outcome tensor's: `stop_atr` x ATR(14) of the decision
bars' mid, target `1R`/`2R` of that stop, the time barrier `gen_combine.MAX_BARS[timeframe]`
(`outcomes.barrier_exit`). The common filters (no entry 20:45-22:15 UTC, none on Friday from 19:00 UTC; tier-1
blackout is empty live and in explore) apply to every arm and to the mask here, so research and live agree on them.

What a spec arm may be, refused at load (`check`, called by `Arm` validation):
- the row has exactly `spec` (canonical JSON, byte for byte `spec.canonical_json`), `spec_hash` (its sha256) and
  `prereg` (the pre-registered reading rule, path#section); the timeframe is the spec's;
- an exit with a time barrier as its target (`time`) or a structural stop: the engine has no target-free or
  price-level entry yet (exploration orders always carry a target, stop distance from ATR);
- a leaf the code base does not compute (`PrimitiveProvider.check_supported`); a side primitive other than a family's own rule;
- a family rule with no runnable arm of the spec's timeframe in `config/exploration_arms.yaml`, or whose arm needs
  stored bars beyond the engine window (`history_bars`: level_break, channels) or a replica state (`specialist`): the
  live provider has neither, so live and research masks would differ (`exploration_context.needs_context`);
- a family (G1) spec with a session window: the explore evaluator reads a family spec on every session
  (`nulls.MaskCache.entries`), so live would trade a subset research never scored.

Point in time: a mask at decision bar t reads bars <= t only (the primitives' contract, tested again here on live
frames). Warm-up: `required_bars` declares how many bars of the timeframe the leaves need before live equals the
full-history run (the trailing 1000-bar tercile rank, the Hurst band, the family rule's own); `arm_rules.lookback_bars`
passes it to the engine, which can hold only `exploration.LOOKBACK_MAX_1M` minutes of bars, and `check` refuses a
spec the engine cannot warm up (also a hand-written row; registration inherits it). With fewer bars a tercile or Hurst leaf is undefined,
which is False (no entry), never a different signal.

A spec arm runs on demo accounts only: `arms.load_arms(live_class=True)` refuses the rule, caps, pots, `max_stop_usd`
and the RiskGate apply unchanged.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.timeutil import utc_index
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research.arms import Arm, load_arms
from goldbot.research.autodisc.contracts import BoolArr, SideArr
from goldbot.research.autodisc.gen_combine import MAX_BARS
from goldbot.research.autodisc.outcomes import barrier_exit
from goldbot.research.autodisc.primitives import (
    ARMS_YAML,
    RANK_BARS,
    PrimitiveProvider,
    UnsupportedLeaf,
)
from goldbot.research.autodisc.spec import Leaf, StrategySpec, canonical_json, leaves, spec_hash

PARAM_KEYS = frozenset({"spec", "spec_hash", "prereg"})
ATR_BARS = 14
RANK_WARMUP = RANK_BARS + 100            # trailing-rank leaves: the rank window plus the indicator under it (ADX 14, 20-bar vol)
HURST_WARMUP = 1_000                     # arm_rules_bands.LOOKBACK_BARS: the Hurst tercile is defined from bar 378 and settles
NO = np.zeros(0, dtype=np.int64)


class LiveProvider(PrimitiveProvider):
    """The explore MaskProvider over the engine's bars: the decision bars it is handed (positions are the engine's own)
    and the 1m bars they come from; no explore-window cut."""

    _explore_only = False

    def __init__(self, bars: pd.DataFrame, b1: pd.DataFrame, timeframe: str, arms: list[Arm] | None = None) -> None:
        self._setup(b1, arms if arms is not None else family_arms(), None)
        self._bars[timeframe] = bars.reset_index(drop=True)


@lru_cache(maxsize=1)
def _family_arms(path: str) -> tuple[Arm, ...]:
    return tuple(load_arms(Path(path)))


def family_arms() -> list[Arm]:
    """The frozen exploration arms a `family_rule` leaf resolves against (config/exploration_arms.yaml, hash-checked once)."""
    return list(_family_arms(str(ARMS_YAML)))


@lru_cache(maxsize=256)
def _parse(text: str) -> StrategySpec:
    return StrategySpec.model_validate_json(text)


def spec_of(arm: Arm) -> StrategySpec:
    """The arm's strategy spec (its `params.spec`, canonical JSON)."""
    return _parse(str(arm.params["spec"]))


def _family_leaves(spec: StrategySpec) -> list[Leaf]:
    out = [x for p in (spec.entry, spec.filter) if p is not None for x in leaves(p) if x.kind == "structure"]
    return out + ([spec.side_primitive] if spec.side_primitive is not None else [])


def check(arm: Arm) -> None:
    """Refuse (ValueError) a `spec_rule` arm that cannot run live exactly as its research read (module docstring)."""
    p = arm.params
    if set(p) != PARAM_KEYS or not all(isinstance(p[k], str) and p[k] for k in PARAM_KEYS):
        raise ValueError(f"a spec_rule arm's params are exactly {sorted(PARAM_KEYS)} (non-empty strings); got {sorted(p)}")
    try:
        spec = _parse(p["spec"])
    except ValueError as exc:
        raise ValueError(f"params.spec is not a StrategySpec: {exc}") from exc
    if canonical_json(spec) != p["spec"]:
        raise ValueError("params.spec is not the canonical JSON of its spec (autodisc.spec.canonical_json)")
    if spec_hash(spec) != p["spec_hash"]:
        raise ValueError(f"params.spec_hash {p['spec_hash']!r} is not the hash of params.spec ({spec_hash(spec)!r})")
    if spec.timeframe != arm.timeframe:
        raise ValueError(f"the arm's timeframe {arm.timeframe!r} is not the spec's {spec.timeframe!r}")
    if spec.family is not None and spec.session_window != "all":
        raise ValueError("a family spec is read by the explore evaluator on every session (`nulls.MaskCache`): its "
                         "session_window must be 'all' for live to equal research")
    if spec.exit.stop == "structural" or spec.exit.target in ("time", "be_trail"):
        raise ValueError("the engine has no structural stop or target-free (time exit) entry: the spec's exit is not "
                         "executable live")
    try:
        for pred in (spec.entry, spec.filter):
            if pred is not None:
                PrimitiveProvider.check_supported(pred)
    except UnsupportedLeaf as exc:
        raise ValueError(str(exc)) from exc
    if spec.side_primitive is not None and (spec.side_primitive.kind, spec.side_primitive.name) != ("structure", "family_rule"):
        raise ValueError("only a family's own rule has a side (side_primitive must be a family_rule leaf)")
    # lazy: the engine imports this module's arm check
    from goldbot.engine.exploration_context import needs_context
    runnable = {(a.rule, a.timeframe): a for a in reversed(family_arms()) if a.status == "run" and a.twin_of is None}
    for leaf in _family_leaves(spec):
        fam = str(leaf.params.get("rule"))
        carrier = runnable.get((fam, arm.timeframe))
        if carrier is None or fam not in R.RULE_FUNCS or fam == "spec_rule":
            raise ValueError(f"no runnable {arm.timeframe} arm carries family {fam!r}")
        if any(needs_context(a) for a in family_arms()          # whichever arm the provider resolves, none may need it
               if (a.rule, a.timeframe, a.status, a.twin_of) == (fam, arm.timeframe, "run", None)):
            raise ValueError(f"family {fam!r} on {arm.timeframe} reads stored bars beyond the engine window or a carried "
                             "replica state, which the live provider does not have: live would differ from research")
    from goldbot.engine.exploration import LOOKBACK_MAX_1M
    reach = LOOKBACK_MAX_1M * 60 // tf_seconds(spec.timeframe)
    if (need := required_bars(spec)) > reach:
        raise ValueError(f"the spec needs {need} {spec.timeframe} bars of warm-up; the engine holds {reach}")


def required_bars(spec: StrategySpec) -> int:
    """Bars of the spec's timeframe its leaves need for the last bar's decision to equal the full-history one."""
    need = ATR_BARS + 1
    for pred in (spec.entry, spec.filter):
        for leaf in leaves(pred) if pred is not None else []:
            if leaf.kind == "regime":
                need = max(need, HURST_WARMUP if leaf.name.startswith("hurst_") else RANK_WARMUP)
    runnable = {(a.rule, a.timeframe): a for a in reversed(family_arms()) if a.status == "run" and a.twin_of is None}
    for leaf in _family_leaves(spec):
        fam = runnable.get((str(leaf.params.get("rule")), spec.timeframe))
        if fam is not None:
            need = max(need, R.lookback_bars(fam, 0))
    return need


def spec_masks(spec: StrategySpec, prov: PrimitiveProvider) -> tuple[BoolArr, SideArr]:
    """(entry mask, side per bar +1 / -1) of `spec` on the provider's bars of its timeframe: entry predicate, filter and
    session window through the provider's leaves, the side rule, and the common filters every arm carries. Point in time."""
    tf = spec.timeframe
    mask = prov.predicate_mask(spec.entry, tf) & prov.session_mask(spec.session_window, tf)
    if spec.filter is not None:
        mask &= prov.predicate_mask(spec.filter, tf)
    n = len(mask)
    if spec.side_rule in ("long", "short"):
        side = np.full(n, 1 if spec.side_rule == "long" else -1, dtype=np.int8)
    else:
        assert spec.side_primitive is not None
        side = prov.side_of(spec.side_primitive, tf)
        if spec.side_rule == "against":
            side = (-side).astype(np.int8)
    close = utc_index(prov.bars(tf)["visible_at"])
    mask = mask & (side != 0) & R.common_filter_ok(close, NO, NO)
    return mask, side


def spec_candidates(arm: Arm, ctx: R.RuleContext) -> pd.DataFrame:
    """RULE_FUNCS["spec_rule"]: the arm's candidates on the decision bars of `ctx` (idx, side, risk_usd, target_r,
    max_bars): entry bars of `spec_masks`, stop = stop_atr x ATR(14) of the mid bars, target 1R / 2R, hold
    MAX_BARS[timeframe] (the outcome tensor's barriers)."""
    spec = spec_of(arm)
    tf = spec.timeframe
    prov = LiveProvider(ctx.bars, ctx.b1, tf)
    mask, side = spec_masks(spec, prov)
    ex = barrier_exit(spec.exit, MAX_BARS[tf])
    assert ex.target_atr is not None
    a = atr(prov.mid_bars(tf), ATR_BARS).to_numpy(dtype=float)
    idx = np.flatnonzero(mask)
    return R._frame(idx, side[idx], ex.stop_atr * a[idx], ex.target_atr / ex.stop_atr, ex.max_bars + 1)
