"""StrategySpec: the typed language of autonomous discovery (design section 2) and its canonical hash.

    spec := timeframe x session_window x side_rule x entry x filter? x exit_template

Predicates are trees of `Group` (and/or) over `Leaf`s (depth <= 3, <= 4 leaves; checked on StrategySpec).
A Leaf is (kind, name, params): `kind` one of the Literal kinds, `name` the primitive (e.g. "rsi_bucket"), `params`
a mapping of scalars (the quantised grid is the generator's job, not validated here).

Signatures (the design leaves them open):
    canonicalise(spec) -> StrategySpec     flatten same-op nesting, drop duplicate children, sort commutative
                                           children, collapse one-child groups, sort params, 2.0 -> 2
    canonical_json(spec) -> str            canonical form as compact sorted-key JSON
    spec_hash(spec) -> str                 sha256 hex of canonical_json; two spellings of one rule share it
Metadata that is not part of the rule (generator, parents, prior_informed flags) is NOT on the spec: it lives
in the ledger row. `family` names the existing family a spec re-expresses (a label for novelty); it is hashed,
so two labels are two specs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Union

from pydantic import Field, model_validator

from goldbot.base import FrozenRecord

Scalar = Union[str, int, float, bool]
Timeframe = Literal["15m", "1h", "4h", "1d"]
SessionWindow = Literal["all", "asia", "london", "ny", "london_ny"]
SideRule = Literal["long", "short", "with", "against"]
MAX_DEPTH = 3
MAX_LEAVES = 4


class Leaf(FrozenRecord):
    kind: Literal["level", "band", "oscillator", "volume", "structure", "regime", "time", "macro"]
    name: str = Field(min_length=1)
    params: dict[str, Scalar] = Field(default_factory=dict)


class Group(FrozenRecord):
    op: Literal["and", "or"]
    children: tuple[Union["Group", Leaf], ...] = Field(min_length=1)


Predicate = Union[Group, Leaf]
Group.model_rebuild()


class ExitTemplate(FrozenRecord):
    """One of the fixed menu: stop {1.0 ATR, 1.5 ATR, structural} x target {1R, 2R, time exit at N bars}. Grammar v2
    (ADR 0058 amendment 2026-11-02) adds the target 3R and `be_trail` (no target: a trail one stop distance behind the
    best price, armed at +1 R, i.e. break-even at +1 R; the time barrier closes the rest)."""

    stop: Literal["atr_1.0", "atr_1.5", "structural"]
    target: Literal["1R", "2R", "3R", "time", "be_trail"]
    time_bars: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _time(self) -> ExitTemplate:
        if (self.target == "time") != (self.time_bars is not None):
            raise ValueError("time_bars is required for the 'time' target and forbidden otherwise")
        return self


def _depth(p: Predicate) -> int:
    return 1 if isinstance(p, Leaf) else 1 + max(_depth(c) for c in p.children)


def leaves(p: Predicate) -> list[Leaf]:
    return [p] if isinstance(p, Leaf) else [x for c in p.children for x in leaves(c)]


class StrategySpec(FrozenRecord):
    timeframe: Timeframe
    session_window: SessionWindow = "all"
    side_rule: SideRule
    side_primitive: Leaf | None = None            # the primitive whose direction `with`/`against` follows
    entry: Predicate
    filter: Predicate | None = None
    exit: ExitTemplate
    family: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> StrategySpec:
        if (self.side_rule in ("with", "against")) != (self.side_primitive is not None):
            raise ValueError("side_primitive is required for with/against and forbidden for long/short")
        for name, p in (("entry", self.entry), ("filter", self.filter)):
            if p is None:
                continue
            c = canonicalise_predicate(p)
            if _depth(c) > MAX_DEPTH:
                raise ValueError(f"{name} depth {_depth(c)} > {MAX_DEPTH}")
            if len(leaves(c)) > MAX_LEAVES:
                raise ValueError(f"{name} has {len(leaves(c))} leaves > {MAX_LEAVES}")
        return self


def _key(x: Predicate) -> str:
    return json.dumps(x.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _canon_scalar(v: Scalar) -> Scalar:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _canon_leaf(leaf: Leaf) -> Leaf:
    return Leaf(kind=leaf.kind, name=leaf.name,
                params={k: _canon_scalar(v) for k, v in sorted(leaf.params.items())})


def canonicalise_predicate(p: Predicate) -> Predicate:
    if isinstance(p, Leaf):
        return _canon_leaf(p)
    flat: list[Predicate] = []
    for c in p.children:
        cc = canonicalise_predicate(c)
        if isinstance(cc, Group) and cc.op == p.op:
            flat.extend(cc.children)           # and(and(a,b),c) == and(a,b,c)
        else:
            flat.append(cc)
    uniq = {_key(c): c for c in flat}          # duplicates removed (and/or are idempotent)
    ordered = [uniq[k] for k in sorted(uniq)]
    return ordered[0] if len(ordered) == 1 else Group(op=p.op, children=tuple(ordered))


def canonicalise(spec: StrategySpec) -> StrategySpec:
    return StrategySpec(
        timeframe=spec.timeframe, session_window=spec.session_window, side_rule=spec.side_rule,
        side_primitive=_canon_leaf(spec.side_primitive) if spec.side_primitive else None,
        entry=canonicalise_predicate(spec.entry),
        filter=canonicalise_predicate(spec.filter) if spec.filter is not None else None,
        exit=spec.exit, family=spec.family)


def canonical_json(spec: StrategySpec) -> str:
    d: dict[str, Any] = canonicalise(spec).model_dump(mode="json")
    return json.dumps(d, sort_keys=True, separators=(",", ":"), allow_nan=False)


def spec_hash(spec: StrategySpec) -> str:
    return hashlib.sha256(canonical_json(spec).encode()).hexdigest()
