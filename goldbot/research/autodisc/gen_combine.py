"""G1 Combine (ADR 0058 section 2): the finite, enumerated grid of the existing families x at most one filter leaf
(regime, time, opportunity score, macro) x the fixed exit menu, written in the shared spec language. Highest prior of
the generators: it conditions mechanisms we already run on the regime where they should work.

Deterministic order, deduplicated by canonical hash (two spellings of one rule share one hash and one K). Controls
(`random_bracket`, `*_control`), the `specialist` meta-rule and `blocked` rows are not families to combine.
The filter catalogue is quantised (terciles, session windows, signs), so the space is countable. Each leaf wraps a
point-in-time feature that `autodisc.primitives` resolves; no new feature maths lives here.

A G1 spec re-expresses a family as `entry = Leaf(structure, "family_rule", {rule: <name>})`, `side_rule = "with"` that
same leaf, and `family = <name>` (the label novelty reads). ASSUMPTION: `autodisc.primitives` resolves the
`structure/family_rule` leaf to the family's own entry and direction (the evaluator's parity test needs it anyway)."""
from __future__ import annotations

from collections.abc import Iterator, Sequence

from goldbot.research.autodisc.spec import ExitTemplate, Leaf, StrategySpec, spec_hash

GENERATOR = "G1"
VERSION = "1"
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")
# Time barrier (bars) of the 1R/2R exits and the time exit, per timeframe: the frozen arm hold_bars of the 15m/1h/4h
# families (config/exploration_arms.yaml), 10 sessions on 1d. The tensor's exits are `outcomes.default_menu(MAX_BARS[tf])`.
MAX_BARS = {"15m": 96, "1h": 48, "4h": 24, "1d": 10}
EXIT_STOPS = ("atr_1.0", "atr_1.5")
EXIT_TARGETS = ("1R", "2R", "time")
N_EXITS = len(EXIT_STOPS) * len(EXIT_TARGETS)     # 6: the structural stop needs a price level and is not in the tensor
MACRO_TIMEFRAMES = frozenset({"4h", "1d"})
NOT_COMBINABLE = frozenset({"random_bracket", "specialist", "blocked"})


def _leaves(kind: str, names: Sequence[str]) -> tuple[Leaf, ...]:
    return tuple(Leaf(kind=kind, name=n) for n in names)  # type: ignore[arg-type]


# Quantised filter catalogue. Names are the primitives' vocabulary (design section 2), not new feature maths.
FILTERS: tuple[Leaf, ...] = (
    *_leaves("regime", ("rvol_q_lo", "rvol_q_mid", "rvol_q_hi", "hurst_trend", "hurst_mean_revert", "hurst_mid",
                        "trend_strength_lo", "trend_strength_mid", "trend_strength_hi", "wavelet_energy_lo",
                        "wavelet_energy_hi", "day_type_trend", "day_type_range")),
    *_leaves("time", ("asia", "london", "ny", "london_ny_overlap", "mon", "tue", "wed", "thu", "fri",
                      "month_turn", "pre_tier1_release", "post_tier1_release")),
    *_leaves("regime", ("opportunity_t1", "opportunity_t2", "opportunity_t3", "opportunity_top_quartile")),
)
MACRO_FILTERS: tuple[Leaf, ...] = _leaves("macro", ("dxy_chg_up", "dxy_chg_down", "real_yield_up", "real_yield_down",
                                                    "cot_bucket_lo", "cot_bucket_mid", "cot_bucket_hi"))


def existing_family_names() -> tuple[str, ...]:
    """The families G1 combines: every rule with a generator in `arm_rules.RULE_FUNCS` that is not a control, the
    random bracket or the specialist meta-rule. Read from the rules themselves so a new rule joins automatically."""
    from goldbot.research.arm_rules import RULE_FUNCS
    return tuple(sorted(n for n in RULE_FUNCS if n not in NOT_COMBINABLE and not n.endswith("_control")))


def filters_for(timeframe: str) -> tuple[Leaf, ...]:
    return FILTERS + (MACRO_FILTERS if timeframe in MACRO_TIMEFRAMES else ())


def generate(families: Sequence[str] | None = None, timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
             exits: Sequence[ExitTemplate] | None = None, filters: bool = True) -> Iterator[StrategySpec]:
    """Every (family, timeframe, filter or none, exit), once each, in a fixed order. `filters=False` yields only
    the unfiltered baselines (each family's own entry on each exit)."""
    seen: set[str] = set()
    for fam in (families if families is not None else existing_family_names()):
        for tf in timeframes:
            options: tuple[Leaf | None, ...] = (None, *filters_for(tf)) if filters else (None,)
            for flt in options:
                for ex in (exits if exits is not None else exit_menu(tf)):
                    s = family_spec(fam, tf, flt, ex)
                    h = spec_hash(s)
                    if h not in seen:
                        seen.add(h)
                        yield s


def exit_menu(timeframe: str) -> tuple[ExitTemplate, ...]:
    return tuple(ExitTemplate(stop=s, target=t, time_bars=MAX_BARS[timeframe] if t == "time" else None)  # type: ignore[arg-type]
                 for s in EXIT_STOPS for t in EXIT_TARGETS)


def family_spec(family: str, timeframe: str, flt: Leaf | None, exit_: ExitTemplate) -> StrategySpec:
    entry = Leaf(kind="structure", name="family_rule", params={"rule": family})
    return StrategySpec(timeframe=timeframe, side_rule="with", side_primitive=entry, entry=entry,  # type: ignore[arg-type]
                        filter=flt, exit=exit_, family=family)


def grid_size(n_families: int, timeframes: Sequence[str] = DEFAULT_TIMEFRAMES, n_exits: int = N_EXITS) -> int:
    return n_families * n_exits * sum(1 + len(filters_for(tf)) for tf in timeframes)
