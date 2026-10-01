"""Specialist = rule-based candidate generator + label spec + exit policy, wrapped in an agent identity
so it can take part in the population tournament (parent, generation, config hash).

A specialist proposes `Candidate`s (side + time). The meta-labelling model then estimates
P(target hit before stop) for each candidate. Nothing here predicts direction on every bar.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

from goldbot.labels.triple_barrier import BarrierSpec


@dataclass(frozen=True)
class AgentIdentity:
    family: str                 # e.g. "session_open"
    config: dict[str, Any] = field(default_factory=dict)
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
        return AgentIdentity(self.family, cfg, parent_id=self.agent_id, generation=self.generation + 1)


@dataclass
class Candidate:
    idx: int
    ts_utc: pd.Timestamp
    side: int


class Specialist(ABC):
    family: str = "abstract"
    timeframe: str = "15m"
    default_config: dict[str, Any] = {}

    def __init__(self, identity: AgentIdentity | None = None, **overrides):
        cfg = {**self.default_config, **overrides}
        self.identity = identity or AgentIdentity(self.family, cfg)
        self.config = {**self.default_config, **self.identity.config}

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
                "config": self.config, "label_spec": asdict(self.label_spec), "exit": self.exit_policy(),
                "parent": self.identity.parent_id, "generation": self.identity.generation}


SPECIALISTS: dict[str, type[Specialist]] = {}


def register(cls: type[Specialist]) -> type[Specialist]:
    SPECIALISTS[cls.family] = cls
    return cls
