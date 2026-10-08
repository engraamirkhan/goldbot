"""Versioned feature registry.

A feature is a named function `f(mid_bars: DataFrame, ctx: dict) -> DataFrame` returning one or more
columns aligned to the input index. Each registration records family, version and the number of
trailing bars it needs. The live path refuses to score a frame whose `feature_version` differs
from the model's. Adding a feature = one decorated function; nothing else changes.
"""
from __future__ import annotations

import hashlib
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

# Signed columns (regex on the column name without its h1_/h4_/d1_/w1_ context prefix -> neutral value). For a
# meta-label model a signed value means opposite things for a long and a short (ret_4 = +0.3% favours a long, hurts a
# short), so models trained with a `side` column see side x (value - neutral): positive always favours the trade.
SIGNED: dict[str, float] = {}
_CONTEXT_PREFIX = re.compile(r"^(h1|h4|d1|w1)_")


def feature(name: str, family: str, *, version: str = "1", lookback: int = 0, tags: tuple[str, ...] = (),
            signed: dict[str, float] | None = None) -> Callable[[FeatureFn], FeatureFn]:
    """`signed`: column-name patterns (full match) of this feature's directional columns -> their neutral value."""
    def deco(fn: FeatureFn) -> FeatureFn:
        if name in FEATURES:
            raise ValueError(f"feature {name!r} already registered")
        SIGNED.update(signed or {})
        FEATURES[name] = FeatureSpec(name=name, family=family, fn=fn, version=version, lookback=lookback, tags=tags,
                                     signed=dict(signed or {}))
        return fn
    return deco


def feature_version(names: list[str]) -> str:
    """Deterministic version string for a chosen feature set: names, versions and the signed-column patterns (a
    side-aligned model reads those columns through side_align, so changing a pattern or neutral value changes what
    the model sees and must change the version)."""
    def one(n: str) -> str:
        sg = FEATURES[n].signed
        return f"{n}@{FEATURES[n].version}" + (f"#{sorted(sg.items())}" if sg else "")
    key = "|".join(one(n) for n in sorted(names))
    return "f-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def build_features(mid_bars: pd.DataFrame, names: list[str] | None = None, ctx: FeatureCtx | None = None) -> pd.DataFrame:
    """Compute the named features on mid bars (columns: ts_utc, open, high, low, close, spread, tick_count)."""
    ctx = ctx or {}
    names = names or list(FEATURES)
    parts = [pd.DataFrame({"ts_utc": pd.DatetimeIndex(pd.to_datetime(mid_bars["ts_utc"], utc=True))}, index=mid_bars.index)]
    for n in names:
        cols = FEATURES[n].fn(mid_bars, ctx)
        cols.index = mid_bars.index
        parts.append(cols)
    out = pd.concat(parts, axis=1)
    out.attrs["feature_version"] = feature_version(names)
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
