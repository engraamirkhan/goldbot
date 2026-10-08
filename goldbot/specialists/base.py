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

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        cfg = {**self.default_config, **overrides}
        self.identity = identity or AgentIdentity(family=self.family, config=cfg)
        self.config = {**self.default_config, **self.identity.config}
        tf = self.config.get(TIMEFRAME_KEY, type(self).timeframe)
        if tf != type(self).timeframe and tf not in self.timeframes:
            raise ValueError(f"{self.family} does not run on {tf}; allowed: {(type(self).timeframe, *self.timeframes)}")
        self.timeframe = tf

    @property
    def agent_id(self) -> str:
        return self.identity.agent_id

    @abstractmethod
    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        """Return frame with columns idx, side (and optional diagnostics) for signal bars."""

    @property
    @abstractmethod
    def label_spec(self) -> BarrierSpec: ...

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
