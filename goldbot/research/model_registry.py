"""Model registry: which trained model each specialist family trades with, and its challengers.

Statuses: `challenger` (shadow only) -> `champion` (traded) -> `previous` (the champion before the current one,
kept so it can be restored if the new champion trips its alarm in the first two weeks) -> `retired`. Artefacts are
pickles under models/<family>/; the registry stores each file's SHA-256 and refuses to load a file that does not
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
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "registry.json"
        raw = json.loads(self.path.read_text()) if self.path.exists() else []
        self.entries: list[ModelEntry] = [ModelEntry.model_validate(r) for r in raw]

    # ------------------------------------------------------------------ queries
    def by_family(self, family: str, status: Status | None = None) -> list[ModelEntry]:
        return [e for e in self.entries if e.family == family and (status is None or e.status == status)]

    def champion(self, family: str) -> ModelEntry | None:
        ch = self.by_family(family, "champion")
        return ch[-1] if ch else None

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
        out = {}
        for fam in sorted({e.family for e in self.entries}):
            ch = self.champion(fam)
            if ch is not None:
                out[fam] = self.load(ch)
        return out

    def shadow_models(self) -> dict[str, tuple[str, MetaLabelModel]]:
        """version -> (family, model) for every champion and challenger: what the shadow book paper-trades."""
        return {e.version: (e.family, self.load(e)) for e in self.entries if e.status in ("champion", "challenger")}

    # ------------------------------------------------------------------ changes
    def add_challenger(self, model: MetaLabelModel, *, family: str, agent_id: str, backtest: dict[str, Any],
                       now: UtcTimestamp | None = None, notes: list[str] | None = None) -> ModelEntry:
        now = now or pd.Timestamp.now("UTC")
        version = f"{family}-{now:%Y%m%dT%H%M%S}"
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
        for old in self.by_family(e.family, "previous"):
            old.status = "retired"
        for old in self.by_family(e.family, "champion"):
            old.status = "previous"
        e.status, e.promoted_utc = "champion", now or pd.Timestamp.now("UTC")
        if note:
            e.notes.append(note)
        self._save()
        return e

    def restore_previous(self, family: str, reason: str) -> ModelEntry:
        prev = self.by_family(family, "previous")
        if not prev:
            raise ValueError(f"no previous champion for {family}")
        for cur in self.by_family(family, "champion"):
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

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.model_dump(mode="json") for e in self.entries], indent=1))
        tmp.replace(self.path)
