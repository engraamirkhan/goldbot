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

Discovered arms on demo, learning class (docs/research/prereg-amendment-2026-11-18-discovered-arms.md, section 4):
* "Arm and control both carry `params.per_trade_bracket: "spec_exit:<spec_hash[:12]>"` ... They carry equal
  `PER_TRADE_SAME` params": `hold_bars` (the spec's time exit, `hold_bars`), `min_stop_usd` MIN_STOP_USD, `max_stop_usd`
  and `end_utc` (`until` 22:00 UTC). `check` accepts these keys on a `spec_rule` row only with the values the
  construction gives, and only under the amendment's prereg (`DISCOVERED_PREREG`), on 1h or 4h (LEARNING_TFS).
* "The construction is one shared function, `arm_rules_spec.bracket(spec, bars, t)`, used for live, replay and the
  twin. It evaluates the spec's exit template (stop, target, time barrier) on the bars closed at the decision." It
  reads no bar after `t` (test T6). `spec_candidates` gives the same numbers on every candidate (tested), and the
  engine recomputes it at the entry (`engine.arm_registry.bracket_refusal`): a difference above BRACKET_TOL, or a
  send more than LATENCY_S after the decision, voids the candidate; `bracket_void` is the same rule for a reader of
  the candidate rows.
* "A dedicated replay-only control row per arm ... It draws random entries at the arm's bar closes: a seeded Bernoulli
  at the arm's explore signal rate, frozen at registration, with side 50/50. The seed is (arm id, bar close)." That is
  `spec_control` (rule SPEC_CONTROL): every candidate is recorded skipped REPLAY_ONLY, so "No control places an
  order".
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.config import tf_seconds
from goldbot.data.resample import mid
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.technical import atr
from goldbot.research import arm_rules as R
from goldbot.research.arms import PER_TRADE_KEY, PER_TRADE_SAME, Arm, arm_hash, load_arms
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
log = logging.getLogger(__name__)

# ---- discovered arms on demo, learning class (prereg-amendment-2026-11-18-discovered-arms.md section 4)
DISCOVERED_PREREG = "docs/research/prereg-amendment-2026-11-18-discovered-arms.md"
SPEC_CONTROL = "spec_control"            # the dedicated replay-only control's rule
BRACKET_PREFIX = "spec_exit:"            # params.per_trade_bracket = BRACKET_PREFIX + spec_hash[:12]
MIN_STOP_USD = 1.0                       # section 4: "`min_stop_usd` 1.00"
LATENCY_S = 60                           # section 4: "`recorded_ns <= decision_ns + 60 s`"
BRACKET_TOL = 1e-9                       # quant ruling 2026-11-18 Q6: "within 1e-9"
# section 2.4 (d) allows 1h, 4h and 1d. 1d is held back: ADR 0081 sends a 1d candidate at 22:30 UTC, 90 minutes after
# its decision close, so it can never meet the 60 s latency guard (raised with the lane's hand-back for a ruling)
LEARNING_TFS = ("1h", "4h")
REPLAY_ONLY = "replay_only"              # every control candidate: recorded and replayed, never sent
LEARNING_KEYS = frozenset({PER_TRADE_KEY, *PER_TRADE_SAME})
CONTROL_KEYS = frozenset({"spec", "spec_hash", "control", "signal_rate", *LEARNING_KEYS})
# section 3, the learning block, as registration writes it and the hot loader requires it
LOSS_CAP_GBP = 60.0                      # "`loss_cap_gbp` **£60**"
LEARNING_DAYS = 42                       # "`until` `min(since + 42 days, 2027-03-31)`"
SUNSET = "2027-03-31"                    # section 7: "No registration under this amendment after 2027-03-31"
CONTROL_SUFFIX = "C"                     # the dedicated control's id: the arm id + "C" (D2611-1 -> D2611-1C)


def learning_until(since: pd.Timestamp) -> str:
    """The learning block's `until` for a registration on UTC day `since`: min(since + 42 days, the sunset)."""
    day = pd.Timestamp(since).tz_convert("UTC").normalize() if pd.Timestamp(since).tzinfo else pd.Timestamp(since)
    return min(f"{day + pd.Timedelta(days=LEARNING_DAYS):%Y-%m-%d}", SUNSET)


class LiveProvider(PrimitiveProvider):
    """The explore MaskProvider over the engine's bars: the decision bars it is handed (positions are the engine's own)
    and the 1m bars they come from; no explore-window cut."""

    _explore_only = False

    def __init__(self, bars: pd.DataFrame, b1: pd.DataFrame, timeframe: str, arms: list[Arm] | None = None) -> None:
        self._setup(b1, arms if arms is not None else family_arms(), None)
        self._bars[timeframe] = bars.reset_index(drop=True)


@lru_cache(maxsize=1)
def _family_arms(path: str) -> tuple[Arm, ...]:
    """The spec's rows, each checked against its frozen hash on its own: a row edited after it was frozen is logged and
    left out (no spec_rule leaf resolves to it), never raised, so one bad row cannot drop every spec_rule arm and
    have their open positions closed as orphans (plan 2026-11-07 WS-1 review)."""
    out = []
    for a in load_arms(Path(path), check_hash=False):
        if a.config_hash != arm_hash(a):
            log.error("arm %s: config_hash %r does not match the arm; left out of the family arms", a.id, a.config_hash)
            continue
        out.append(a)
    return tuple(out)


def family_arms() -> list[Arm]:
    """The frozen exploration arms a `family_rule` leaf resolves against (config/exploration_arms.yaml, hash-checked
    once per row: `_family_arms`)."""
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
    learning = PER_TRADE_KEY in p
    want = PARAM_KEYS | LEARNING_KEYS if learning else PARAM_KEYS
    if set(p) != want or not all(isinstance(p[k], str) and p[k] for k in PARAM_KEYS):
        raise ValueError(f"a spec_rule arm's params are exactly {sorted(want)} (spec, spec_hash, prereg non-empty "
                         f"strings); got {sorted(p)}")
    spec = _spec_of_params(arm)
    if learning:
        if not str(p["prereg"]).startswith(DISCOVERED_PREREG + "#"):
            raise ValueError(f"a per-trade bracket on a spec_rule arm is the learning route of {DISCOVERED_PREREG} "
                             "only (params.prereg must name it)")
        check_learning(arm, spec)
    _check_spec(arm, spec)


def _spec_of_params(arm: Arm) -> StrategySpec:
    """The row's spec, refused (ValueError) unless params.spec is its canonical JSON and params.spec_hash its hash."""
    p = arm.params
    try:
        spec = _parse(str(p["spec"]))
    except ValueError as exc:
        raise ValueError(f"params.spec is not a StrategySpec: {exc}") from exc
    if canonical_json(spec) != p["spec"]:
        raise ValueError("params.spec is not the canonical JSON of its spec (autodisc.spec.canonical_json)")
    if spec_hash(spec) != p["spec_hash"]:
        raise ValueError(f"params.spec_hash {p['spec_hash']!r} is not the hash of params.spec ({spec_hash(spec)!r})")
    return spec


def check_learning(arm: Arm, spec: StrategySpec) -> None:
    """The per-trade bracket params of a discovered learning arm or its control (section 4), refused (ValueError)
    unless each is the value the shared construction gives: the key `bracket_key(spec_hash)`, `hold_bars` the spec's,
    `min_stop_usd` MIN_STOP_USD, `max_stop_usd` above it, `end_utc` an instant in UTC; a 1h or 4h spec."""
    p = arm.params
    if arm.timeframe not in LEARNING_TFS:
        raise ValueError(f"a discovered learning arm runs on {LEARNING_TFS}, not {arm.timeframe}")
    if p.get(PER_TRADE_KEY) != bracket_key(str(p.get("spec_hash"))):
        raise ValueError(f"params.{PER_TRADE_KEY} must be {bracket_key(str(p.get('spec_hash')))!r}")
    if p.get("hold_bars") != hold_bars(spec):
        raise ValueError(f"params.hold_bars must be the spec's time barrier, {hold_bars(spec)} bars")
    if p.get("min_stop_usd") != MIN_STOP_USD:
        raise ValueError(f"params.min_stop_usd must be {MIN_STOP_USD}")
    top = p.get("max_stop_usd")
    if isinstance(top, bool) or not isinstance(top, int | float) or not top > MIN_STOP_USD:
        raise ValueError("params.max_stop_usd must be a number above min_stop_usd")
    end = p.get("end_utc")
    if not isinstance(end, str) or not end.endswith("Z"):
        raise ValueError("params.end_utc must be an ISO instant in UTC (ending Z)")
    try:
        pd.Timestamp(end)
    except ValueError as exc:
        raise ValueError(f"params.end_utc {end!r} is not an instant") from exc


def check_control(arm: Arm) -> None:
    """Refuse (ValueError) a `spec_control` row that is not a discovered arm's replay-only control: exactly
    CONTROL_KEYS, the spec canonical and hashed on the row's timeframe, `control` "learning:<one arm id>",
    `signal_rate` in (0, 1], and the per-trade bracket params of `check_learning`."""
    p = arm.params
    if set(p) != CONTROL_KEYS:
        raise ValueError(f"a {SPEC_CONTROL} arm's params are exactly {sorted(CONTROL_KEYS)}; got {sorted(p)}")
    spec = _spec_of_params(arm)
    if spec.timeframe != arm.timeframe:
        raise ValueError(f"the arm's timeframe {arm.timeframe!r} is not the spec's {spec.timeframe!r}")
    named = p.get("control")
    if not isinstance(named, str) or not named.startswith("learning:") or "," in named or len(named) <= 9:
        raise ValueError("params.control must name one learning arm: 'learning:<arm id>'")
    rate = p.get("signal_rate")
    if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 < float(rate) <= 1:
        raise ValueError("params.signal_rate must be in (0, 1]")
    check_learning(arm, spec)


def _check_spec(arm: Arm, spec: StrategySpec) -> None:
    """The spec runs live exactly as its research read (module docstring)."""
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


class Bracket(FrozenRecord):
    """A candidate's per-trade bracket (USD per oz, seconds), as its `exploration_obs` row carries it."""
    stop_dist: float
    target_dist: float
    max_hold_s: int


def bracket_key(spec_hash_: str) -> str:
    """`params.per_trade_bracket` of a discovered learning arm and of its control (section 4)."""
    return BRACKET_PREFIX + spec_hash_[:12]


def hold_bars(spec: StrategySpec) -> int:
    """The spec's time barrier in bars of its timeframe, as the engine holds it (`spec_candidates`' max_bars + 1)."""
    return barrier_exit(spec.exit, MAX_BARS[spec.timeframe]).max_bars + 1


def bracket(spec: StrategySpec, bars: pd.DataFrame, t: pd.Timestamp) -> Bracket | None:
    """THE construction (section 4): the spec's exit template on ATR(ATR_BARS) of the mid of `bars` (the decision
    timeframe's bars, store schema) closed at or before `t` (`visible_at <= t`), at the last such bar: the stop
    stop_atr x ATR, the target its R multiple of that stop, the hold `hold_bars`. No bar after `t` is read (T6). None
    when no bar closed by `t`, or its ATR is not finite and positive."""
    ex = barrier_exit(spec.exit, MAX_BARS[spec.timeframe])
    if ex.target_atr is None:
        return None
    closed = bars[np.asarray(utc_index(bars["visible_at"]) <= pd.Timestamp(t))]
    if closed.empty:
        return None
    a = float(atr(mid(closed).reset_index(drop=True), ATR_BARS).iloc[-1])
    if not (np.isfinite(a) and a > 0):
        return None
    stop = ex.stop_atr * a
    return Bracket(stop_dist=stop, target_dist=(ex.target_atr / ex.stop_atr) * stop,
                   max_hold_s=hold_bars(spec) * tf_seconds(spec.timeframe))


def bracket_mismatch(stored: Bracket, recomputed: Bracket | None) -> str | None:
    """Why `stored` is not `recomputed` within BRACKET_TOL (the hold exactly), None when it is."""
    if recomputed is None:
        return "bracket_unreadable: no recompute from the bars closed at the decision"
    for k in ("stop_dist", "target_dist"):
        if not abs(float(getattr(stored, k)) - float(getattr(recomputed, k))) <= BRACKET_TOL:
            return f"bracket_mismatch: {k} {getattr(stored, k)!r} is not the recompute {getattr(recomputed, k)!r}"
    if int(stored.max_hold_s) != int(recomputed.max_hold_s):
        return f"bracket_mismatch: max_hold_s {stored.max_hold_s} is not the recompute {recomputed.max_hold_s}"
    return None


def _stored(row: Mapping[str, object]) -> Bracket | None:
    stop, target, hold = (row.get(k) for k in ("stop_dist", "target_dist", "max_hold_s"))
    if not isinstance(stop, int | float) or not isinstance(target, int | float) or not isinstance(hold, int | float):
        return None
    return Bracket(stop_dist=float(stop), target_dist=float(target), max_hold_s=int(hold))


def bracket_void(row: Mapping[str, object], recomputed: Bracket | None,
                 inserted: Mapping[str, object] | None = None) -> str | None:
    """Why a candidate row's bracket is void (section 4; quant ruling 2026-11-18 Q6), None when it stands. It must
    have all three fields in its INSERT (`inserted`: the row as first written, when the reader has it; a field the
    INSERT lacked was set later by UPDATE), unchanged since; `recorded_ns <= decision_ns + LATENCY_S`;
    `recorded_ns < entry_ns` when taken; and equal `bracket(spec, bars closed <= decision_ns, t)` (`recomputed`) within
    BRACKET_TOL. Without `inserted`, an UPDATE that changed a field is still caught by the recompute: the recompute
    reads no bar after the decision, so a bracket equal to it was fixed before any outcome either way."""
    first = row if inserted is None else inserted
    if _stored(first) is None:
        return "bracket_not_in_insert: stop_dist, target_dist and max_hold_s must all be set in the INSERT"
    stored = _stored(row)
    if stored is None or stored != _stored(first):
        return "bracket_updated: the bracket was changed after its INSERT"
    decision, recorded, entry = row.get("decision_ns"), row.get("recorded_ns"), row.get("entry_ns")
    if not isinstance(decision, int) or not isinstance(recorded, int):
        return "bracket_unreadable: no decision_ns or recorded_ns"
    if recorded > decision + LATENCY_S * 1_000_000_000:
        return f"bracket_late: recorded {(recorded - decision) / 1e9:.1f} s after the decision (> {LATENCY_S} s)"
    if entry is not None and not (isinstance(entry, int) and recorded < entry):
        return "bracket_late: recorded at or after the entry"
    return bracket_mismatch(stored, recomputed)


def spec_masks(spec: StrategySpec, prov: PrimitiveProvider,
               filter_close: pd.DatetimeIndex | None = None) -> tuple[BoolArr, SideArr]:
    """(entry mask, side per bar +1 / -1) of `spec` on the provider's bars of its timeframe: entry predicate, filter and
    session window through the provider's leaves, the side rule, and the common filters every arm carries, judged at
    `filter_close` when given, else at each bar's close; a 1d/1w bar always at its entry (`arm_rules.entry_after`, ADR
    0081, enforced). Point in time."""
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
    close = utc_index(prov.bars(tf)["visible_at"]) if filter_close is None else filter_close
    if tf in R.LONG_TFS and filter_close is None:    # ADR 0081, enforced: a 1d/1w bar is judged at its entry
        close = R.entry_after(close, tf)
    mask = mask & (side != 0) & R.common_filter_ok(close, NO, NO)
    return mask, side


def spec_candidates(arm: Arm, ctx: R.RuleContext) -> pd.DataFrame:
    """RULE_FUNCS["spec_rule"]: the arm's candidates on the decision bars of `ctx` (idx, side, risk_usd, target_r,
    max_bars): entry bars of `spec_masks`, stop = stop_atr x ATR(14) of the mid bars, target 1R / 2R, hold
    MAX_BARS[timeframe] (the outcome tensor's barriers)."""
    spec = spec_of(arm)
    tf = spec.timeframe
    prov = LiveProvider(ctx.bars, ctx.b1, tf)
    mask, side = spec_masks(spec, prov, ctx.filter_close)
    ex = barrier_exit(spec.exit, MAX_BARS[tf])
    assert ex.target_atr is not None
    a = atr(prov.mid_bars(tf), ATR_BARS).to_numpy(dtype=float)
    if "end_utc" in arm.params:          # section 5.1 (2): "No candidate is decided at or after `end_utc`"
        mask = mask & np.asarray(utc_index(ctx.bars["visible_at"]) < pd.Timestamp(str(arm.params["end_utc"])))
    idx = np.flatnonzero(mask)
    return R._frame(idx, side[idx], ex.stop_atr * a[idx], ex.target_atr / ex.stop_atr, ex.max_bars + 1)


def _draws(arm_id: str, close: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """(u, v) per bar close, each in [0, 1): sha256 of (SEED_PREFIX, the rule, the served arm id, the close in epoch
    ns), so history, replay and live draw alike and a restart draws nothing new (section 4: "The seed is (arm id, bar
    close)")."""
    u, v = np.empty(len(close)), np.empty(len(close))
    for i, ns in enumerate(epoch_ns(close)):
        d = hashlib.sha256(f"{R.SEED_PREFIX}|{SPEC_CONTROL}|{arm_id}|{int(ns)}".encode()).digest()
        u[i] = int.from_bytes(d[:8], "big") / 2.0 ** 64
        v[i] = int.from_bytes(d[8:16], "big") / 2.0 ** 64
    return u, v


def spec_control(arm: Arm, ctx: R.RuleContext) -> pd.DataFrame:
    """RULE_FUNCS["spec_control"] (section 4): at each decision bar's close an entry with probability
    `params.signal_rate` (the served arm's explore signal rate, frozen at registration), long or short 50/50, seeded
    on (the served arm id, the bar close); the common filters and `end_utc` as for the arm; the bracket the shared
    construction at its own decision bar (`bracket`, vectorised here: Wilder ATR is causal, so each value is the
    truncated read's; tested). Every candidate is skipped REPLAY_ONLY: recorded and replayed, never sent."""
    spec = _spec_of_params(arm)
    served = str(arm.params["control"]).removeprefix("learning:")
    close = ctx.close_ts
    u, v = _draws(served, close)
    take = (u < float(arm.params["signal_rate"])) & R.common_filter_ok(ctx.filter_ts, NO, NO)
    take &= np.asarray(close < pd.Timestamp(str(arm.params["end_utc"])))
    ex = barrier_exit(spec.exit, MAX_BARS[spec.timeframe])
    assert ex.target_atr is not None
    a = atr(ctx.mid, ATR_BARS).to_numpy(dtype=float)
    idx = np.flatnonzero(take)
    out = R._frame(idx, np.where(v[idx] < 0.5, 1, -1), ex.stop_atr * a[idx], ex.target_atr / ex.stop_atr,
                   ex.max_bars + 1)
    return out.assign(skip=REPLAY_ONLY)
