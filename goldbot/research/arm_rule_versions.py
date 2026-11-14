"""The rule-code version of every exploration arm rule (BACKLOG 105).

`arms.arm_hash` covers an arm's id, rule, timeframe, params and max_open, not the code of its rule: a change to a frozen
rule's code (e.g. the S1 midnight cut correction, commit 8d777955, voided after the fact as `spec-bug-midnight`) left
the hash unchanged, so nothing in the evidence said which rows came from which code. Here every rule of
`arm_rules.RULE_FUNCS` carries an explicit version, bumped by hand with a dated note in
docs/research/arms-2026-10-25.md ("Rule-code versions"). tests/test_arm_rule_versions.py pins the sha256 of each rule's
implementation source per version, so a source change without a bump fails CI. The hash covers the rule's own function,
every helper in a goldbot.research.arm_rules* module it reaches (derived from its code, transitively: a hand list
would rot silently, the failure this fixes) and the common post-filter every rule passes through (COMMON: `_screen`,
the decision window, the common filters); quant review 2026-11-14: "source_sha must also cover the shared helpers". The engine writes the version in force
at each candidate's decision to `exploration_obs.arm_rule_version` (live and shadow), the H-60 history run to its
trades; readers filter on it (`exploration_data.read_obs(rule_version=...)`) and the daily review reports an arm whose
rows span more than one version. The arm ids and `arm_hash` are unchanged (no re-freeze)."""
from __future__ import annotations

import hashlib
import importlib
import inspect
import types
from collections.abc import Callable
from typing import Any

# every rule at version 1 on 2026-11-14 (the code at the commit that introduced this table); bump one entry, with its
# dated note, whenever that rule's code changes
RULE_VERSIONS: dict[str, int] = {
    "random_bracket": 1, "session_vwap_fade": 1, "opening_range_break": 1, "sweep_absorb": 1, "box_break_close": 1,
    "ema_pullback": 1, "tsmom": 1, "specialist": 1, "pdc_break": 1, "ob_retest": 1, "level_break": 1,
    "squeeze_release": 1, "donchian_break": 1, "low30_touch": 1, "low30_control": 1,
    "friday_long": 1, "clock_anchor": 1, "clock_anchor_control": 1, "box_next": 1,
    "hurst_band_break": 1, "hurst_band_break_control": 1, "htf_stretch_break": 1, "htf_stretch_break_control": 1,
    "tick_surge": 1, "run_fade": 1, "round_break": 1, "round_reject": 1, "extreme_break": 1, "nr4_break": 1,
    "halfhour_persist": 1, "absorb_fade": 1, "htf_reclaim": 1, "three_horizon": 1, "scalp_control": 1,
    "spec_rule": 1, "session_range_break": 1, "session_range_control": 1,
}

# RULE_FUNCS entries that dispatch to a family module: (dispatcher name in arm_rules, module, its table)
_DISPATCH = (("channel", "arm_rules_channels", "RULES"), ("clock_box", "arm_rules_clock_box", "RULE_FUNCS"),
             ("band_rule", "arm_rules_bands", "RULES"), ("scalp_rule", "arm_rules_scalp", "RULE_FUNCS"))
# RULE_FUNCS entries that are lambdas importing their rule: (module, function)
_LAMBDA = {"level_break": ("arm_rules_levels", "level_break"), "spec_rule": ("arm_rules_spec", "spec_candidates"),
           "session_range_break": ("arm_rules_growth", "session_range_break"),
           "session_range_control": ("arm_rules_growth", "session_range_control")}


_PREFIX = "goldbot.research.arm_rules"     # the modules whose helpers a rule's version covers
# the post-filter `arm_rules.screened_frame` applies to every rule's candidates (declared: no rule calls it itself)
COMMON = ("screened_frame", "_screen", "arm_window", "common_filter_ok", "long_tf_context")


def rule_version(rule: str) -> int | None:
    """The rule-code version of `rule` in force now; None for a rule this table does not know (never guessed)."""
    return RULE_VERSIONS.get(rule)


def rule_impl(rule: str) -> Callable[..., Any]:
    """The function that holds `rule`'s own code: the family module's entry behind a dispatcher or a lambda, else the
    RULE_FUNCS function itself."""
    from goldbot.research import arm_rules as R
    if rule in _LAMBDA:
        mod, name = _LAMBDA[rule]
        return getattr(importlib.import_module(f"goldbot.research.{mod}"), name)
    f = R.RULE_FUNCS[rule]
    for dispatcher, mod, table in _DISPATCH:
        if f is getattr(R, dispatcher):
            return getattr(importlib.import_module(f"goldbot.research.{mod}"), table)[rule]
    return f


def _names(code: types.CodeType) -> set[str]:
    out = set(code.co_names)
    for c in code.co_consts:
        if isinstance(c, types.CodeType):
            out |= _names(c)
    return out


def _ours(obj: object) -> bool:
    return inspect.isfunction(obj) and str(getattr(obj, "__module__", "")).startswith(_PREFIX)


def _reached(fn: Callable[..., Any]) -> list[Callable[..., Any]]:
    """The arm_rules* functions `fn`'s code names: its module's globals, a module it reaches by attribute
    (`R.helper`) and one it imports inside the function (`from ... import helper`)."""
    names = _names(fn.__code__)
    spaces: list[dict[str, Any]] = [fn.__globals__]
    for n in names:
        mod = fn.__globals__.get(n) if n in fn.__globals__ else (
            importlib.import_module(n) if n.startswith(_PREFIX) else None)
        if isinstance(mod, types.ModuleType) and mod.__name__.startswith(_PREFIX):
            spaces.append(vars(mod))
    found: dict[int, Callable[..., Any]] = {}
    for n in names:
        for space in spaces:
            obj = space.get(n)
            if _ours(obj):
                found[id(obj)] = obj  # type: ignore[assignment]
    return list(found.values())


def helpers(rule: str) -> list[Callable[..., Any]]:
    """Every arm_rules* function `rule`'s code reaches, transitively, and the COMMON post-filter; the rule's own
    implementation excluded. Ordered by module and name, so the hash is stable."""
    from goldbot.research import arm_rules as R
    impl = rule_impl(rule)
    seen: dict[int, Callable[..., Any]] = {}
    todo = [impl, *(getattr(R, n) for n in COMMON)]
    while todo:
        f = todo.pop()
        if id(f) in seen:
            continue
        seen[id(f)] = f
        todo.extend(_reached(f))
    seen.pop(id(impl), None)
    return sorted(seen.values(), key=lambda f: (f.__module__, f.__qualname__))


def _source(fn: Callable[..., Any]) -> str:
    return inspect.getsource(fn)


def source_sha(rule: str) -> str:
    """sha256[:16] of `rule_impl(rule)`'s source and every helper's (`helpers`), each named by module and qualname
    (the pin tests/test_arm_rule_versions.py compares)."""
    parts = [_source(rule_impl(rule))]
    parts += [f"# {f.__module__}.{f.__qualname__}\n{_source(f)}" for f in helpers(rule)]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]
