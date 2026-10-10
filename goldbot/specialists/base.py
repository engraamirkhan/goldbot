"""Specialist = rule-based candidate generator + label spec + exit policy, wrapped in an agent identity
so it can take part in the population tournament (parent, generation, config hash).

A specialist proposes `Candidate`s (side + time). The meta-labelling model then estimates
P(target hit before stop) for each candidate. Nothing here predicts direction on every bar.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.labels.triple_barrier import BarrierSpec


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

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        asked = (identity.config if identity is not None else overrides).get(TIMEFRAME_KEY, type(self).timeframe)
        base = {**self.default_config, **self.timeframe_defaults.get(asked, {})}
        unused = self.unused_config({**base, **(identity.config if identity is not None else overrides)})
        base = {k: v for k, v in base.items() if k not in unused}
        cfg = {k: v for k, v in {**base, **overrides}.items() if k not in unused}
        self.identity = identity or AgentIdentity(family=self.family, config=cfg)
        self.config = {**base, **self.identity.config}
        tf = self.config.get(TIMEFRAME_KEY, type(self).timeframe)
        if tf != type(self).timeframe and tf not in self.timeframes:
            raise ValueError(f"{self.family} does not run on {tf}; allowed: {(type(self).timeframe, *self.timeframes)}")
        self.timeframe = tf

    @classmethod
    def unused_config(cls, config: dict[str, Any]) -> set[str]:
        """Keys `config` makes inert (e.g. a schedule a higher-timeframe signal ignores): left out of the configuration
        and its agent id, so a parameter that changes nothing cannot make two ids for one rule. None by default."""
        return set()

    @property
    def agent_id(self) -> str:
        return self.identity.agent_id

    @abstractmethod
    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """Return frame with columns idx, side (and optional diagnostics) for signal bars."""

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
        """Candidates when the higher-timeframe bars (`context`, keyed h1/h4/d1, store schema with visible_at) are at
        hand, as in research (`research.pipeline.prepare`). Default: `candidates`, which sees only the decision frame."""
        return self.candidates(mid_bars, features)

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


SPECIALISTS: dict[str, type[Specialist]] = {}


def register(cls: type[Specialist]) -> type[Specialist]:
    SPECIALISTS[cls.family] = cls
    return cls
