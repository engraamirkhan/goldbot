"""Model registry: which trained model each agent (population member) trades with, and its challengers.

Statuses: `challenger` (shadow only) -> `champion` (traded) -> `previous` (the champion before the current one,
kept so it can be restored if the new champion trips its alarm in the first two weeks) -> `retired`. Artefacts are
pickles under models/<family>/<agent_id>-<timestamp>.pkl; the registry stores each file's SHA-256 and refuses to load a file that does not
match, so a corrupted or swapped artefact can never reach the engine.
"""
from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import Record, UtcTimestamp
from goldbot.research.model import MetaLabelModel

Status = Literal["challenger", "champion", "previous", "retired"]


class ModelEntry(Record):
    version: str
    family: str
    agent_id: str
    status: Status
    created_utc: UtcTimestamp
    feature_version: str
    feature_names: list[str]
    artefact: str                     # path relative to the registry folder
    sha256: str
    backtest: dict[str, Any] = Field(default_factory=dict)
    shadow: dict[str, Any] | None = None
    promoted_utc: UtcTimestamp | None = None
    notes: list[str] = Field(default_factory=list)


class ModelRegistry:
    """Keyed by agent: every population member (a specialist family + config, see research/population.py) has its own
    champion, challengers and previous champion. The founder agent of a family is its default configuration."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "registry.json"
        raw = json.loads(self.path.read_text()) if self.path.exists() else []
        self.entries: list[ModelEntry] = [ModelEntry.model_validate(r) for r in raw]

    # ------------------------------------------------------------------ queries
    def by_agent(self, agent_id: str, status: Status | None = None) -> list[ModelEntry]:
        return [e for e in self.entries if e.agent_id == agent_id and (status is None or e.status == status)]

    def champion(self, agent_id: str) -> ModelEntry | None:
        ch = self.by_agent(agent_id, "champion")
        return ch[-1] if ch else None

    def agent_ids(self) -> list[str]:
        return sorted({e.agent_id for e in self.entries})

    def get(self, version: str) -> ModelEntry:
        for e in self.entries:
            if e.version == version:
                return e
        raise KeyError(version)

    def load(self, entry: ModelEntry) -> MetaLabelModel:
        data = (self.root / entry.artefact).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.sha256:
            raise ValueError(f"artefact {entry.artefact} does not match its registered checksum; refusing to load")
        model = pickle.loads(data)
        if not isinstance(model, MetaLabelModel):
            raise TypeError(f"{entry.artefact} is not a MetaLabelModel")
        return model

    def champion_models(self) -> dict[str, MetaLabelModel]:
        """agent_id -> champion model."""
        out = {}
        for aid in self.agent_ids():
            ch = self.champion(aid)
            if ch is not None:
                out[aid] = self.load(ch)
        return out

    def shadow_models(self) -> dict[str, tuple[str, MetaLabelModel]]:
        """version -> (agent_id, model) for every champion and challenger: what the shadow book paper-trades."""
        return {e.version: (e.agent_id, self.load(e)) for e in self.entries if e.status in ("champion", "challenger")}

    # ------------------------------------------------------------------ changes
    def add_challenger(self, model: MetaLabelModel, *, family: str, agent_id: str, backtest: dict[str, Any],
                       now: UtcTimestamp | None = None, notes: list[str] | None = None) -> ModelEntry:
        now = now or pd.Timestamp.now("UTC")
        version = f"{agent_id}-{now:%Y%m%dT%H%M%S}"
        n = 1
        while any(e.version == version for e in self.entries):     # two trainings in one second
            version, n = f"{agent_id}-{now:%Y%m%dT%H%M%S}-{n}", n + 1
        rel = Path(family) / f"{version}.pkl"
        (self.root / family).mkdir(parents=True, exist_ok=True)
        data = pickle.dumps(model)
        (self.root / rel).write_bytes(data)
        e = ModelEntry(version=version, family=family, agent_id=agent_id, status="challenger", created_utc=now,
                       feature_version=model.feature_version, feature_names=list(model.feature_names),
                       artefact=str(rel), sha256=hashlib.sha256(data).hexdigest(), backtest=backtest, notes=notes or [])
        self.entries.append(e)
        self._save()
        return e

    def promote(self, version: str, now: UtcTimestamp | None = None, note: str = "") -> ModelEntry:
        e = self.get(version)
        if e.status != "challenger":
            raise ValueError(f"{version} is {e.status}, only a challenger can be promoted")
        for old in self.by_agent(e.agent_id, "previous"):
            old.status = "retired"
        for old in self.by_agent(e.agent_id, "champion"):
            old.status = "previous"
        e.status, e.promoted_utc = "champion", now or pd.Timestamp.now("UTC")
        if note:
            e.notes.append(note)
        self._save()
        return e

    def restore_previous(self, agent_id: str, reason: str) -> ModelEntry:
        prev = self.by_agent(agent_id, "previous")
        if not prev:
            raise ValueError(f"no previous champion for {agent_id}")
        for cur in self.by_agent(agent_id, "champion"):
            cur.status = "retired"
            cur.notes.append(f"demoted: {reason}")
        prev[-1].status = "champion"
        prev[-1].notes.append(f"restored: {reason}")
        self._save()
        return prev[-1]

    def retire(self, version: str, reason: str) -> None:
        e = self.get(version)
        e.status = "retired"
        e.notes.append(reason)
        self._save()

    def retire_agent(self, agent_id: str, reason: str) -> None:
        """End of an agent's life: called when a retired member's six months of shadow trading are over (population
        retirement itself only stops live trading; its champion keeps shadow-trading so the record stays unbiased)."""
        for e in self.entries:
            if e.agent_id == agent_id and e.status in ("champion", "challenger", "previous"):
                e.status = "retired"
                e.notes.append(reason)
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.model_dump(mode="json") for e in self.entries], indent=1))
        tmp.replace(self.path)
