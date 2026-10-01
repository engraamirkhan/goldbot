"""Versioned feature registry.

A feature is a named function `f(mid_bars: DataFrame, ctx: dict) -> DataFrame` returning one or more
columns aligned to the input index. Each registration records family, version and the number of
trailing bars it needs. The live path refuses to score a frame whose `feature_version` differs
from the model's. Adding a feature = one decorated function; nothing else changes.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

FeatureFn = Callable[[pd.DataFrame, dict], pd.DataFrame]


@dataclass
class FeatureSpec:
    name: str
    family: str
    fn: FeatureFn
    version: str = "1"
    lookback: int = 0
    tags: tuple[str, ...] = field(default_factory=tuple)


FEATURES: dict[str, FeatureSpec] = {}


def feature(name: str, family: str, *, version: str = "1", lookback: int = 0, tags: tuple[str, ...] = ()):
    def deco(fn: FeatureFn) -> FeatureFn:
        if name in FEATURES:
            raise ValueError(f"feature {name!r} already registered")
        FEATURES[name] = FeatureSpec(name, family, fn, version, lookback, tags)
        return fn
    return deco


def feature_version(names: list[str]) -> str:
    """Deterministic version string for a chosen feature set (names + versions)."""
    key = "|".join(f"{n}@{FEATURES[n].version}" for n in sorted(names))
    return "f-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def build_features(mid_bars: pd.DataFrame, names: list[str] | None = None, ctx: dict | None = None) -> pd.DataFrame:
    """Compute the named features on mid bars (columns: ts_utc, open, high, low, close, spread, tick_count)."""
    ctx = ctx or {}
    names = names or list(FEATURES)
    out = pd.DataFrame(index=mid_bars.index)
    out["ts_utc"] = pd.to_datetime(mid_bars["ts_utc"].values, utc=True)
    for n in names:
        spec = FEATURES[n]
        cols = spec.fn(mid_bars, ctx)
        for c in cols.columns:
            out[c] = cols[c].values
    out.attrs["feature_version"] = feature_version(names)
    out.attrs["features"] = list(names)
    return out


def families() -> dict[str, list[str]]:
    fam: dict[str, list[str]] = {}
    for s in FEATURES.values():
        fam.setdefault(s.family, []).append(s.name)
    return fam
