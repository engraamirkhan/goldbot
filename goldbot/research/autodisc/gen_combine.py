"""G1 Combine (ADR 0058 section 2): the finite, enumerated grid of the existing families x at most one filter leaf
(regime, time, opportunity score, macro) x the fixed exit menu, written in the shared spec language. Highest prior of
the generators: it conditions mechanisms we already run on the regime where they should work.

Deterministic order, deduplicated by canonical hash (two spellings of one rule share one hash and one K). Controls
(`random_bracket`, `*_control`), the `specialist` meta-rule and `blocked` rows are not families to combine.
The filter catalogue is quantised (terciles, session windows, signs), so the space is countable. Each leaf wraps a
point-in-time feature that `autodisc.primitives` resolves; no new feature maths lives here.

Spec type: `contracts.StubSpec` until `autodisc.spec.StrategySpec` lands (REPLACE AT INTEGRATION)."""
from __future__ import annotations

from collections.abc import Iterator, Sequence

from goldbot.research.autodisc.contracts import EXIT_MENU, Leaf, StubSpec

GENERATOR = "G1"
VERSION = "1"
DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")
MACRO_TIMEFRAMES = frozenset({"4h", "1d"})
NOT_COMBINABLE = frozenset({"random_bracket", "specialist", "blocked"})


def _leaves(kind: str, names: Sequence[str]) -> tuple[Leaf, ...]:
    return tuple(Leaf(kind=kind, name=n) for n in names)


# Quantised filter catalogue. Names are the primitives' vocabulary (design section 2), not new feature maths.
FILTERS: tuple[Leaf, ...] = (
    *_leaves("regime", ("rvol_q_lo", "rvol_q_mid", "rvol_q_hi", "hurst_trend", "hurst_mean_revert", "hurst_mid",
                        "trend_strength_lo", "trend_strength_mid", "trend_strength_hi", "wavelet_energy_lo",
                        "wavelet_energy_hi", "day_type_trend", "day_type_range")),
    *_leaves("time", ("asia", "london", "ny", "london_ny_overlap", "mon", "tue", "wed", "thu", "fri",
                      "month_turn", "pre_tier1_release", "post_tier1_release")),
    *_leaves("opportunity", ("score_t1", "score_t2", "score_t3", "score_top_quartile")),
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
             exits: Sequence[str] = EXIT_MENU, filters: bool = True) -> Iterator[StubSpec]:
    """Every (family, timeframe, filter or none, exit), once each, in a fixed order. `filters=False` yields only
    the unfiltered baselines (each family's own entry on each exit)."""
    seen: set[str] = set()
    for fam in (families if families is not None else existing_family_names()):
        for tf in timeframes:
            options: tuple[Leaf | None, ...] = (None, *filters_for(tf)) if filters else (None,)
            for flt in options:
                for ex in exits:
                    s = StubSpec(timeframe=tf, entry=Leaf(kind="family", name=fam), filter=flt, exit_template=ex)
                    h = s.spec_hash()
                    if h not in seen:
                        seen.add(h)
                        yield s


def grid_size(n_families: int, timeframes: Sequence[str] = DEFAULT_TIMEFRAMES, n_exits: int = len(EXIT_MENU)) -> int:
    return n_families * n_exits * sum(1 + len(filters_for(tf)) for tf in timeframes)


def parents_of(spec: StubSpec) -> tuple[str, ...]:
    """Ledger parent labels of a G1 spec: the family it conditions."""
    return (spec.entry.name,)
