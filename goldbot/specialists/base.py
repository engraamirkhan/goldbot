"""Specialist = rule-based candidate generator + label spec + exit policy, wrapped in an agent identity
so it can take part in the population tournament (parent, generation, config hash).

A specialist proposes `Candidate`s (side + time). The meta-labelling model then estimates
P(target hit before stop) for each candidate. Nothing here predicts direction on every bar.
"""
from __future__ import annotations

import functools
import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.specialists.loop_filters import apply_filters, validate


class AgentIdentity(FrozenRecord):
    family: str                 # e.g. "session_open"
    config: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = None
    generation: int = 0

    @property
    def agent_id(self) -> str:
        h = hashlib.sha1(json.dumps({"f": self.family, "c": self.config}, sort_keys=True, default=str).encode()).hexdigest()[:10]
        return f"{self.family}-g{self.generation}-{h}"

    def mutate(self, changes: dict[str, Any]) -> "AgentIdentity":
        cfg = {**self.config, **changes}
        if cfg == self.config:
            raise ValueError("a clone must differ from its parent")
        return AgentIdentity(family=self.family, config=cfg, parent_id=self.agent_id, generation=self.generation + 1)


class Candidate(Record):
    idx: int
    ts_utc: pd.Timestamp
    side: int


# config keys that are not trigger/barrier parameters: a clone may carry them (population mutations)
TIMEFRAME_KEY = "timeframe"          # decision timeframe override, one of the family's `timeframes`
FEATURE_SEED_KEY = "feature_seed"    # model trains on a seeded random subset of the eligible features (<= 40)


class Specialist(ABC):
    family: str = "abstract"
    timeframe: str = "15m"
    timeframes: tuple[str, ...] = ()     # other decision timeframes a clone may move to (empty: fixed)
    default_config: dict[str, Any] = {}
    # the meta-model's declared inputs (column names, h1_/h4_/d1_ context included; at most 39 so `side` fits in 40).
    # Empty: the first eligible columns. Columns missing on a clone's timeframe (e.g. h1_ on 1h) are skipped.
    model_features: tuple[str, ...] = ()
    # walk-forward overrides of the timeframe's windows (research.walkforward.WINDOWS), e.g. an expanding training
    # window and longer test folds for a family with few candidates a year. An evaluation setting, not trade config.
    walkforward: dict[str, Any] = {}
    # defaults layered over default_config on another decision timeframe (e.g. lookbacks and barriers sized for daily
    # bars); explicit overrides still win, and they become part of the configuration (and its agent id)
    timeframe_defaults: dict[str, dict[str, Any]] = {}
    # settings a variant may set that are absent from default_config (absent = off), so adding one leaves the default
    # configuration, its labels and its agent id unchanged
    optional_config: dict[str, Any] = {}
    # named variants (`research_pass.py --variants '["<name>"]'`): a pre-registered configuration spelled out once
    presets: dict[str, dict[str, Any]] = {}
    # True while the family's pre-registered rule-only screen has not passed (e.g. asia_drift, H-04): it is usable by
    # research (`research_pass.py --specialist`) but is not seeded as a default population founder
    # (`Population.ensure_founders`) and is not a member of the pooled models (`pipeline.pooled_members`). A passed
    # walk-forward trial of its exact configuration still becomes a shadow founder (gap_watch `research_ready`)
    screening: bool = False
    # True for a family whose signal needs TradingView values (e.g. tv_event): it is searched only under TV_GRID_V1
    # (research.tv_grid), never by the loop grid (`generator._eligible`, `loop_refusal`), and trades in shadow only
    # ("TradingView indicators: filter features only" until the DESIGN promotion rule is met; engine `_decide`)
    requires_tv: bool = False
    # seconds after the signal bar's close before the signal is known (a TradingView value: up to tv.max_lag_s). > 0:
    # research labels enter at the open of the first 1m bar at/after close + this (`triple_barrier_delayed`, needs the
    # 1m bars: `pipeline.prepare(bars_1m=)`); 0: at the close, as every other family
    entry_delay_s: int = 0
    # True for a rule whose pre-registration enters at the next bar's open rather than at the signal close (H-45, "E:
    # entry at the **next** 1h bar's open"): research labels fill at the first 1m open at/after the signal close
    # (`triple_barrier_delayed` with no delay, needs the 1m bars), so a weekend or session gap is in the fill
    entry_at_next_open: bool = False
    # True for a rule whose pre-registration reads its barriers on 1m bars (H-48/H-49, prereg-kb-2026-10-18.md section 1:
    # "entries/exits on 1m barriers"): research labels enter at the signal close as every family, then search the
    # barriers minute by minute (`triple_barrier_1m`, needs the 1m bars), so the stop counts first only inside a minute
    barriers_on_1m: bool = False
    # columns the candidates frame carries that are inputs too (a value fixed at the signal, e.g. H-40's flags of the
    # candidate's own box and side): `pipeline.prepare` adds them to the candidate features, and a discovery with
    # DiscoveryConfig pool "candidate" screens exactly them, grouped by `candidate_feature_groups` (column -> group)
    candidate_features: tuple[str, ...] = ()
    candidate_feature_groups: dict[str, str] = {}
    # a pre-registered cost gate (H-37, research director ruling 2026-10-18): the screen reports whether the rule's gross
    # mean R reaches this many R per trade (`research.screen.cost_gate`); None: no gate
    cost_gate_r: float | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Every subclass's own `candidates` and `candidates_in_context` pass through the loop's optional filters
        (`specialists.loop_filters`; no filter set: unchanged), so research, the shadow book and the live engine see the
        same candidates whatever entry point they use."""
        super().__init_subclass__(**kwargs)
        for name in ("candidates", "candidates_in_context"):
            fn = cls.__dict__.get(name)
            if fn is not None:
                setattr(cls, name, _filtered(fn))

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        asked = (identity.config if identity is not None else overrides).get(TIMEFRAME_KEY, type(self).timeframe)
        base = {**self.default_config, **self.timeframe_defaults.get(asked, {})}
        unused = self.unused_config({**base, **(identity.config if identity is not None else overrides)})
        base = {k: v for k, v in base.items() if k not in unused}
        cfg = {k: v for k, v in {**base, **overrides}.items() if k not in unused}
        self.identity = identity or AgentIdentity(family=self.family, config=cfg)
        self.config = {**base, **self.identity.config}
        validate(self.config)
        tf = self.config.get(TIMEFRAME_KEY, type(self).timeframe)
        if tf != type(self).timeframe and tf not in self.timeframes:
            raise ValueError(f"{self.family} does not run on {tf}; allowed: {(type(self).timeframe, *self.timeframes)}")
        self.timeframe = tf

    @classmethod
    def unused_config(cls, config: dict[str, Any]) -> set[str]:
        """Keys `config` makes inert (e.g. a schedule a higher-timeframe signal ignores): left out of the configuration
        and its agent id, so a parameter that changes nothing cannot make two ids for one rule. None by default."""
        return set()

    @classmethod
    def dispatch_refusal(cls, root: Path, tracked: Callable[[Path], bool] | None = None) -> str | None:
        """Why a research trial of this family may not be dispatched now, or None. `root`: the repository root a
        committed precondition is read from; `tracked`: whether a path is committed (default: git). Default: None, no
        precondition (a staged pre-registration, e.g. H-49 stage 2, overrides it)."""
        return None

    @property
    def needs_bars_1m(self) -> bool:
        """The research labels read the 1m bars (`pipeline.prepare(bars_1m=)`): a delayed or next-open entry, or
        barriers on 1m bars."""
        return bool(self.entry_delay_s) or self.entry_at_next_open or self.barriers_on_1m

    @property
    def agent_id(self) -> str:
        return self.identity.agent_id

    @property
    def shadow_only(self) -> bool:
        """True for an agent the engine never sends to the broker (share 0; the shadow book trades it): one that needs
        TradingView values (`requires_tv`) or a carried replica state (`replica_params`, engine/replica_context.py)."""
        return self.requires_tv or getattr(self, "replica_params", None) is not None

    @abstractmethod
    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """Return frame with columns idx, side (and optional diagnostics) for signal bars."""

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None,
                              ctx: dict[str, Any] | None = None) -> pd.DataFrame:
        """Candidates when the higher-timeframe bars (`context`, keyed h1/h4/d1, store schema with visible_at) and the
        point-in-time tables (`ctx`, as the features get them, e.g. ctx["macro"], joined only via `asof_join` on
        available_utc) are at hand, as in research (`research.pipeline.prepare`). Default: `candidates`, which sees
        only the decision frame."""
        return self.candidates(mid_bars, features)

    def complete_windows(self, bars_dec: pd.DataFrame, cands: pd.DataFrame) -> pd.DataFrame:
        """Research sample rule applied before labelling (`research.pipeline.prepare`): the candidates whose holding
        window the family's pre-registration accepts, judged on bar timestamps only (never prices). Default: all."""
        return cands

    def barrier_atr(self, mid_bars: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> pd.Series | None:
        """ATR per decision bar that sizes the barriers and the risk (R), read at the signal bar and frozen for the
        trade's life; None: ATR(14) of the decision bars."""
        return None

    @property
    @abstractmethod
    def label_spec(self) -> BarrierSpec: ...

    @property
    def exit_spec(self) -> ExitPolicy | None:
        """The executable exit policy (labels.exit_policy) on top of the barriers, or None for the plain barriers. The
        labels, the shadow book and the live engine all run it, so research measures the exits live trading takes."""
        return None

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier"}

    def describe(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "family": self.family, "timeframe": self.timeframe,
                "config": self.config, "label_spec": self.label_spec.model_dump(), "exit": self.exit_policy(),
                "parent": self.identity.parent_id, "generation": self.identity.generation}


def _filtered(fn: Any) -> Any:
    @functools.wraps(fn)
    def wrapper(self: Specialist, mid_bars: pd.DataFrame, features: pd.DataFrame, *args: Any, **kwargs: Any) -> pd.DataFrame:
        return apply_filters(self.config, fn(self, mid_bars, features, *args, **kwargs), features)
    return wrapper


SPECIALISTS: dict[str, type[Specialist]] = {}


def register(cls: type[Specialist]) -> type[Specialist]:
    SPECIALISTS[cls.family] = cls
    return cls
