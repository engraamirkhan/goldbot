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
# Superseded implementations, kept so a model trained on them still scores exactly the frame it was trained on
# (`versions_for`, `with_versions`): (name, version) -> spec. Not in FEATURES, so research and retrains never build them.
LEGACY_FEATURES: dict[tuple[str, str], FeatureSpec] = {}

# Signed columns (regex on the column name without its h1_/h4_/d1_/w1_ context prefix -> neutral value). For a
# meta-label model a signed value means opposite things for a long and a short (ret_4 = +0.3% favours a long, hurts a
# short), so models trained with a `side` column see side x (value - neutral): positive always favours the trade.
SIGNED: dict[str, float] = {}
_CONTEXT_PREFIX = re.compile(r"^(h1|h4|d1|w1)_")


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
        cols = spec_of(n, v).fn(mid_bars, ctx or {})
        cols.index = out.index
        for c in cols.columns:
            out[c] = cols[c].to_numpy()
    out.attrs = dict(X.attrs)
    out.attrs["feature_version"] = feature_version(names, versions)
    return out


def build_features(mid_bars: pd.DataFrame, names: list[str] | None = None, ctx: FeatureCtx | None = None,
                   versions: dict[str, str] | None = None) -> pd.DataFrame:
    """Compute the named features on mid bars (columns: ts_utc, open, high, low, close, spread, tick_count).
    `versions`: per-feature overrides selecting a legacy implementation (default: the current ones)."""
    ctx = ctx or {}
    names = names or list(FEATURES)
    vs = versions or {}
    parts = [pd.DataFrame({"ts_utc": pd.DatetimeIndex(pd.to_datetime(mid_bars["ts_utc"], utc=True))}, index=mid_bars.index)]
    for n in names:
        cols = spec_of(n, vs.get(n)).fn(mid_bars, ctx)
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
