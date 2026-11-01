"""Opt-in features (a module with `enable()`, not imported by goldbot.features) stay out of research's default set.

Design rule (goldbot/research/pipeline.py): features that need an external input are not in the default set, so the
default feature version (the one the live engine builds) does not depend on what happens to be loaded or enabled.
`DEFAULT_FEATURE_NAMES` is computed from the registry when the pipeline is first imported, so an opt-in name missing
from `OPT_IN_FEATURES` leaks into the default set whenever something enabled it first (an order-dependent failure).
"""
from __future__ import annotations

import importlib
import pkgutil

import goldbot.features as features_pkg
from goldbot.features import registry


def _opt_in_names() -> dict[str, list[str]]:
    """Every name each opt-in module's `enable()` registers, per module (registry restored afterwards)."""
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    out: dict[str, list[str]] = {}
    try:
        for info in pkgutil.iter_modules(features_pkg.__path__):
            mod = importlib.import_module(f"goldbot.features.{info.name}")
            enable = getattr(mod, "enable", None)
            if callable(enable):
                got = enable()
                out[info.name] = [got] if isinstance(got, str) else list(got)
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
        registry.SIGNED.clear()
        registry.SIGNED.update(signed)
    return out


def test_every_opt_in_feature_is_listed_so_enabling_it_never_changes_the_default_feature_set():
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, OPT_IN_FEATURES
    names = _opt_in_names()
    assert {"positioning", "us_calendar", "tod_periodicity"} <= set(names)      # discovery finds the known modules
    missing = {m: [n for n in ns if n not in OPT_IN_FEATURES] for m, ns in names.items()}
    assert {m: ns for m, ns in missing.items() if ns} == {}
    assert not {n for ns in names.values() for n in ns} & set(DEFAULT_FEATURE_NAMES)
