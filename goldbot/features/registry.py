"""Versioned feature registry.

A feature is a named function `f(mid_bars: DataFrame, ctx: dict) -> DataFrame` returning one or more
columns aligned to the input index. Each registration records family, version and the number of
trailing bars it needs. The live path refuses to score a frame whose `feature_version` differs
from the model's. Adding a feature = one decorated function; nothing else changes.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from typing import Any, Callable

import pandas as pd
from pydantic import Field

from goldbot.base import Record

FeatureCtx = dict[str, Any]   # optional inputs: events, macro frame, blackout minutes
FeatureFn = Callable[[pd.DataFrame, FeatureCtx], pd.DataFrame]


class FeatureSpec(Record):
    name: str
    family: str
    fn: FeatureFn
    version: str = "1"
    lookback: int = 0
    tags: tuple[str, ...] = Field(default_factory=tuple)
    signed: dict[str, float] = Field(default_factory=dict)    # directional column patterns -> neutral value


FEATURES: dict[str, FeatureSpec] = {}
# A feature tagged NEEDS_STATE carries unbounded memory (e.g. smc_replica's running FVG threshold and structure
# machine), so a fixed tail of bars cannot reproduce its full-history value. It is built only on a frame the caller
# declares to start the series (ctx[FULL_HISTORY] True: research on the whole series); the engine computes it with a
# carried state instead (goldbot.features.smc_live) and a plain tail is refused (quant review 2026-10-18).
NEEDS_STATE = "needs_state"
FULL_HISTORY = "series_from_start"
# Superseded implementations, kept so a model trained on them still scores exactly the frame it was trained on
# (`versions_for`, `with_versions`): (name, version) -> spec. Not in FEATURES, so research and retrains never build them.
LEGACY_FEATURES: dict[tuple[str, str], FeatureSpec] = {}

# Signed columns (regex on the column name without its h1_/h4_/d1_/w1_ context prefix -> neutral value). For a
# meta-label model a signed value means opposite things for a long and a short (ret_4 = +0.3% favours a long, hurts a
# short), so models trained with a `side` column see side x (value - neutral): positive always favours the trade.
SIGNED: dict[str, float] = {}
_CONTEXT_PREFIX = re.compile(r"^(h1|h4|d1|w1)_")
# Research-only columns (regex on the column name without its context prefix -> why): built from data the live bars
# never carry, so a live model would read NaN forever. `live_feature_check` refuses them in any model that trades or
# shadow-trades (goldbot/research/model_registry.py). Feature modules register their own on import.
RESEARCH_ONLY: dict[str, str] = {}


def feature(name: str, family: str, *, version: str = "1", lookback: int = 0, tags: tuple[str, ...] = (),
            signed: dict[str, float] | None = None, legacy: bool = False) -> Callable[[FeatureFn], FeatureFn]:
    """`signed`: column-name patterns (full match) of this feature's directional columns -> their neutral value.
    `legacy`: a superseded version of `name`, kept only to rebuild frames for models trained on it (LEGACY_FEATURES);
    it must return the same columns as the current version and leaves SIGNED alone."""
    def deco(fn: FeatureFn) -> FeatureFn:
        spec = FeatureSpec(name=name, family=family, fn=fn, version=version, lookback=lookback, tags=tags,
                           signed=dict(signed or {}))
        if legacy:
            if (name, version) in LEGACY_FEATURES:
                raise ValueError(f"legacy feature {name!r}@{version} already registered")
            LEGACY_FEATURES[(name, version)] = spec
            return fn
        if name in FEATURES:
            raise ValueError(f"feature {name!r} already registered")
        SIGNED.update(signed or {})
        FEATURES[name] = spec
        return fn
    return deco


# Per-COLUMN lookbacks in decision bars, the current bar included (quant ruling on PR #472, ADR 0104 4): the oldest bar
# whose perturbation can change the column by >= 1 % of its own scale under either of two perturbations (a continuous
# 3x-vol path; a +5 median-range shift with 1 % spikes of 20 median ranges), declared on 32 seeds. An int
# holds at every timeframe; a dict is per timeframe (session-anchored columns read more bars at 1m). Measured and proved
# by goldbot/research/column_lookback.py (tests/test_column_lookbacks.py: perturbing every bar older than the declared
# lookback stays within the bound, the bar just inside breaks it). A d1_ context column reads its base column's "1d"
# value in daily bars. Additive: the feature-level `lookback` above keeps its meaning (its only reader is
# research/dense_labels.py; the engine reads neither).
COLUMN_LOOKBACKS: dict[str, int | dict[str, int]] = {
    "atr14_pct": 110,                # atr
    "atr_ratio_14_100": 810,         # atr
    "compression_8_96": 97,          # breakout
    "tick_vol_ratio_20": 20,         # breakout
    "em_atr_cost": 110,              # expected_move
    "em_yz_16_cost": 96,             # expected_move
    "em_yz_48_cost": 96,             # expected_move
    "em_yz_4_cost": 96,              # expected_move
    "bars_since_jump": 501,          # jumps
    "jump_z": 63,                    # jumps
    "bb_z_20": 20,                   # mean_reversion
    "dist_vwap48_atr": 86,           # mean_reversion
    "rsi14": 93,                     # mean_reversion
    "spread_atr": 101,               # microstructure
    "spread_rel_median_48": 48,      # microstructure
    "tick_count_z_48": 48,           # microstructure
    "dist_ema50_atr": 163,           # moving_averages
    "slope_ema20": {"1d": 92},       # moving_averages
    "slope_ema50": 165,              # moving_averages
    "rv_ratio": 101,                 # realised_vol
    "vol_tercile": 413,              # realised_vol
    "er_30": 31,                     # regime_stats
    "ret_16": 17,                    # returns
    "ret_4": 5,                      # returns
    "ret_96": 97,                    # returns
    "dow": 1,                        # session
    "hour_utc": 1,                   # session
    "min_since_london_open": 1,      # session
    "min_since_newyork_open": 1,     # session
    "session_id": 1,                 # session
    # session_zones
    "sess_high_dist_atr": {"1m": 491, "5m": 100, "15m": 97, "30m": 89, "1h": 90, "4h": 91, "1d": 90, "1w": 90},
    "sess_low_dist_atr": {"1m": 467, "5m": 99, "15m": 96, "30m": 98, "1h": 97, "4h": 91, "1d": 91, "1w": 91},
    "adx14": 125,                    # trend_strength
    "donchian_pos_20": 20,           # trend_strength
    "tsmom_score": 481,              # tsmom
    "vol_of_vol_60": 80,             # vol_estimators
    "vol_ts_yz_20_60": 61,           # vol_estimators
}


# Columns whose declared lookback may go below the signed floor (tests/fixtures/column_lookbacks_floor.json), each with
# its quant re-review reference (ADR 0104 4: "Declared lookbacks never shrink without a quant re-review").
COLUMN_LOOKBACK_REREVIEW: dict[str, str] = {}


def column_lookback(column: str, tf: str) -> int:
    """The declared lookback of `column` at `tf` in decision bars (`COLUMN_LOOKBACKS`); KeyError when undeclared."""
    v = COLUMN_LOOKBACKS[column]
    if isinstance(v, int):
        return v
    if tf not in v:
        raise KeyError(f"{column}: no lookback declared at {tf}")
    return v[tf]



def spec_of(name: str, version: str | None = None) -> FeatureSpec:
    """The registered implementation of `name` at `version` (None or the current version: FEATURES)."""
    cur = FEATURES[name]
    if version is None or version == cur.version:
        return cur
    try:
        return LEGACY_FEATURES[(name, version)]
    except KeyError:
        raise KeyError(f"feature {name!r} has no registered implementation of version {version!r}") from None


def feature_version(names: list[str], versions: dict[str, str] | None = None) -> str:
    """Deterministic version string for a chosen feature set: names, versions and the signed-column patterns (a
    side-aligned model reads those columns through side_align, so changing a pattern or neutral value changes what
    the model sees and must change the version). `versions`: per-feature overrides (legacy implementations)."""
    vs = versions or {}

    def one(n: str) -> str:
        s = spec_of(n, vs.get(n))
        return f"{n}@{s.version}" + (f"#{sorted(s.signed.items())}" if s.signed else "")
    key = "|".join(one(n) for n in sorted(names))
    return "f-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def versions_for(names: list[str], target: str) -> dict[str, str] | None:
    """The per-feature version overrides (only those differing from the current versions; {} = the current set) under
    which `names` hashes to `target`, or None when no combination of registered implementations reproduces it (a
    model of that version cannot be scored: fail closed)."""
    options = [[(n, None)] + [(n, v) for (m, v) in sorted(LEGACY_FEATURES) if m == n] for n in names]
    for combo in itertools.product(*options):
        over = {n: v for n, v in combo if v is not None}
        if feature_version(names, over) == target:
            return over
    return None


def with_versions(X: pd.DataFrame, mid_bars: pd.DataFrame, names: list[str], versions: dict[str, str],
                  ctx: FeatureCtx | None = None) -> pd.DataFrame:
    """A copy of the frame `build_features(mid_bars, names)` (optionally with context columns merged) whose
    overridden features are recomputed by their `versions` implementations, stamped with that set's version. Equal to
    building the frame at those versions; only the overridden features are recomputed."""
    out = X.copy()
    for n, v in versions.items():
        spec = spec_of(n, v)
        refuse_plain_tail(spec, ctx or {})
        cols = spec.fn(mid_bars, ctx or {})
        cols.index = out.index
        for c in cols.columns:
            out[c] = cols[c].to_numpy()
    out.attrs = dict(X.attrs)
    out.attrs["feature_version"] = feature_version(names, versions)
    return out


def refuse_plain_tail(spec: FeatureSpec, ctx: FeatureCtx) -> None:
    """ValueError for a NEEDS_STATE feature on a frame not declared to start the series (see NEEDS_STATE)."""
    if NEEDS_STATE in spec.tags and ctx.get(FULL_HISTORY) is not True:
        raise ValueError(f"feature {spec.name!r} needs a carried state: it is built only on the whole series "
                         f"(ctx[{FULL_HISTORY!r}] = True) or live through goldbot.features.smc_live, never on a tail")


def build_features(mid_bars: pd.DataFrame, names: list[str] | None = None, ctx: FeatureCtx | None = None,
                   versions: dict[str, str] | None = None) -> pd.DataFrame:
    """Compute the named features on mid bars (columns: ts_utc, open, high, low, close, spread, tick_count).
    `versions`: per-feature overrides selecting a legacy implementation (default: the current ones)."""
    ctx = ctx or {}
    names = names or list(FEATURES)
    vs = versions or {}
    parts = [pd.DataFrame({"ts_utc": pd.DatetimeIndex(pd.to_datetime(mid_bars["ts_utc"], utc=True))}, index=mid_bars.index)]
    for n in names:
        spec = spec_of(n, vs.get(n))
        refuse_plain_tail(spec, ctx)
        cols = spec.fn(mid_bars, ctx)
        cols.index = mid_bars.index
        parts.append(cols)
    out = pd.concat(parts, axis=1)
    out.attrs["feature_version"] = feature_version(names, vs)
    out.attrs["features"] = list(names)
    return out


def families() -> dict[str, list[str]]:
    fam: dict[str, list[str]] = {}
    for s in FEATURES.values():
        fam.setdefault(s.family, []).append(s.name)
    return fam


def live_feature_check(columns: list[str]) -> None:
    """Raise ValueError when a live (or shadow) model's input columns include a research-only column."""
    import goldbot.features.orderflow  # noqa: F401  (registers its RESEARCH_ONLY columns even when not enabled)
    bad = []
    for c in columns:
        base = _CONTEXT_PREFIX.sub("", c)
        why = next((w for pattern, w in RESEARCH_ONLY.items() if re.fullmatch(pattern, base)), None)
        if why is not None:
            bad.append(f"{c} ({why})")
    if bad:
        raise ValueError("research-only feature columns in a live model: " + "; ".join(bad))


def signed_neutral(column: str) -> float | None:
    """Neutral value of a signed column (context-prefixed columns included), None for an unsigned one."""
    base = _CONTEXT_PREFIX.sub("", column)
    for pattern, neutral in SIGNED.items():
        if re.fullmatch(pattern, base):
            return neutral
    return None


def side_align(X: pd.DataFrame) -> pd.DataFrame:
    """Side-aligned copy of a candidate frame that carries a `side` column (+1 long, -1 short): every signed column
    becomes side x (value - neutral). Unsigned columns and `side` itself are unchanged; without `side`, X is returned."""
    if "side" not in X.columns:
        return X
    side = X["side"].astype(float).to_numpy()
    out = X.copy()
    for c in X.columns:
        if c == "side":
            continue
        neutral = signed_neutral(c)
        if neutral is not None:
            out[c] = side * (pd.to_numeric(X[c], errors="coerce").to_numpy(dtype=float) - neutral)
    return out
