"""Model registry: which trained model each agent (population member) trades with, and its challengers.

A recalibration (weekly job, only the probability map changes) is a minor version: the entry keeps its version and
status, gets a new checksummed artefact and a row in `recalibrations` (before/after ECE, sample size).

Statuses: `challenger` (shadow only) -> `champion` (traded) -> `previous` (the champion before the current one,
kept so it can be restored if the new champion trips its alarm in the first two weeks) -> `retired`. Artefacts are
pickles under models/<family>/<agent_id>-<timestamp>.pkl; the registry stores each file's SHA-256 and refuses to load a file that does not
match, so a corrupted or swapped artefact can never reach the engine.

ADR 0106 decision 7 ("any past model can be rebuilt exactly from its logged data version, settings, seed and code
sha") and goal "every deep model beats the gradient-boosted baseline out of sample, or it is not deployed": every
entry stores the id of its run manifest (research/run_manifest.py, kept in models/manifests/) and its model family.
`load_for_serving` is the engine's loader (`champion_models`, `shadow_models`): a fitted model created on or after
model_quality.MANIFEST_REQUIRED_FROM is refused without a manifest that passes model_quality.require_servable; a refused
model is left out (the engine then proposes nothing for that agent), never served. Rule models (ADR 0038) are not
fitted and need none; older fitted entries keep serving (ADR 0109). `rollback-model <agent>` (`rollback_main`) restores
the previous champion in one command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import Record, UtcTimestamp, load_record
from goldbot.features.registry import live_feature_check
from goldbot.research import run_manifest as rm
from goldbot.research.model import MetaLabelModel
from goldbot.research.model_quality import MANIFEST_REQUIRED_FROM, ModelFamily, ModelRefused, servable_reasons
from goldbot.research.rule_model import RuleModel

log = logging.getLogger("goldbot.model_registry")

Status = Literal["challenger", "champion", "previous", "retired"]


class RegistryLockTimeout(TimeoutError):
    """registry.lock was not acquired within the instance's `lock_timeout_s`; nothing was changed."""


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
    recalibrations: list[dict[str, Any]] = Field(default_factory=list)    # minor versions: only the calibrator changed
    manifest_id: str | None = None    # run manifest in models/manifests/ (None: a rule model or a pre-manifest fit)
    model_family: ModelFamily | None = None    # ADR 0106 decision 3, from the manifest


class ModelRegistry:
    """Keyed by agent: every population member (a specialist family + config, see research/population.py) has its own
    champion, challengers and previous champion. The founder agent of a family is its default configuration.

    Several processes write registry.json (the scheduler, the engine's rule challengers, `rollback-model`) and the
    first two hold one instance for their life. Every query re-reads the file when it changed on disk, and every
    change is a load-modify-save under the exclusive lock on <root>/registry.lock (code review MEDIUM on #494: a
    long-lived instance's save wrote its stale list back and silently undid a rollback)."""

    def __init__(self, root: str | Path, *, lock_timeout_s: float | None = None):
        """`lock_timeout_s`: how long a change waits for registry.lock before RegistryLockTimeout; None waits as long
        as it takes (jobs, the CLI). The engine passes a bound: its loop also runs exits (PR #494 round 3)."""
        self.root = Path(root)
        self.lock_timeout_s = lock_timeout_s
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "registry.json"
        self.manifests = self.root / "manifests"
        self._held = False                # this process holds registry.lock (inside `locked` or `_write`)
        self._entries: list[ModelEntry] = []      # read through `entries`, which re-reads a changed file
        self._sig: tuple[int, int, int] | None = None
        self._load()

    @property
    def entries(self) -> list[ModelEntry]:
        """Every entry, as registry.json holds it now (re-read when another writer changed it). Inside a change
        (`_write`) the list being changed. PR #494 round 3: jobs read this list directly."""
        self._fresh()
        return self._entries

    def _stat(self) -> tuple[int, int, int] | None:
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size, st.st_ino      # an atomic replace changes the inode

    def _load(self) -> None:
        sig = self._stat()
        raw = json.loads(self.path.read_text()) if sig is not None else []
        self._entries = [load_record(ModelEntry, r, source="models/registry.json") for r in raw]
        self._sig = sig

    def _fresh(self) -> None:
        """Re-read registry.json when another writer changed it since this instance last read or wrote it."""
        if self._stat() != self._sig:
            self._load()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        from goldbot.research.windows import _lock_fd, _try_lock_fd, _unlock_fd
        if self._held:                    # re-entrant: a change made inside `locked`
            yield
            return
        fd = os.open(self.root / "registry.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if self.lock_timeout_s is None:
                _lock_fd(fd)
            else:
                deadline = time.monotonic() + self.lock_timeout_s
                while not _try_lock_fd(fd):
                    if time.monotonic() >= deadline:
                        raise RegistryLockTimeout(f"{self.root / 'registry.lock'} held by another writer for more "
                                                  f"than {self.lock_timeout_s:g} s; nothing changed")
                    time.sleep(0.01)
            self._held = True
            try:
                yield
            finally:
                self._held = False
                _unlock_fd(fd)
        finally:
            os.close(fd)

    @contextmanager
    def _write(self) -> Iterator[None]:
        """One change: under the lock, from the file's current content, saved on success."""
        with self._lock():
            self._fresh()
            try:
                yield
            except BaseException:
                self._load()              # a change that failed half-way leaves nothing behind in memory
                raise
            self._save()

    @classmethod
    @contextmanager
    def locked(cls, root: str | Path) -> Iterator[ModelRegistry]:
        """The registry loaded under an exclusive lock on <root>/registry.lock, for a read-check-change sequence that
        must not interleave with another writer (code review LOW-1 on #494). flock on POSIX, msvcrt on Windows."""
        reg = cls(root)
        with reg._lock():
            reg._load()
            yield reg

    # ------------------------------------------------------------------ queries
    def by_agent(self, agent_id: str, status: Status | None = None) -> list[ModelEntry]:
        self._fresh()
        return [e for e in self._entries if e.agent_id == agent_id and (status is None or e.status == status)]

    def champion(self, agent_id: str) -> ModelEntry | None:
        ch = self.by_agent(agent_id, "champion")
        return ch[-1] if ch else None

    def agent_ids(self) -> list[str]:
        self._fresh()
        return sorted({e.agent_id for e in self._entries})

    def get(self, version: str) -> ModelEntry:
        self._fresh()
        for e in self._entries:
            if e.version == version:
                return e
        raise KeyError(version)

    def load(self, entry: ModelEntry) -> Any:
        """The checked artefact: a MetaLabelModel, or a RuleModel (ADR 0038)."""
        data = (self.root / entry.artefact).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.sha256:
            raise ValueError(f"artefact {entry.artefact} does not match its registered checksum; refusing to load")
        model = pickle.loads(data)
        if not isinstance(model, (MetaLabelModel, RuleModel)):
            raise TypeError(f"{entry.artefact} is not a MetaLabelModel or a RuleModel")
        return model

    def serving_refusal(self, entry: ModelEntry) -> str | None:
        """Why `entry` may not serve, or None. Reads the artefact's type (a rule model needs no manifest). Never
        raises (trading-safety H1 on #494): any failure to read the artefact, the manifest or the baseline is a
        refusal of this one entry, so one bad file can never stop the engine from loading the others."""
        try:
            return self._serving_refusal(entry)
        except Exception as exc:   # noqa: BLE001 - a refusal, never a crash of the loader
            return f"{entry.version}: serving check failed: {type(exc).__name__}: {exc}"

    def _serving_refusal(self, entry: ModelEntry) -> str | None:
        if entry.manifest_id is None:
            if entry.created_utc < MANIFEST_REQUIRED_FROM or isinstance(self.load(entry), RuleModel):
                return None
            return (f"{entry.version}: a fitted model with no run manifest (trained on or after "
                    f"{MANIFEST_REQUIRED_FROM:%Y-%m-%d}; ADR 0106 decision 7)")
        try:
            m = rm.load(entry.manifest_id, self.manifests)
        except rm.ManifestError as exc:
            return f"{entry.version}: {exc}"
        fitted = entry.recalibrations[0].get("base_sha256") if entry.recalibrations else entry.sha256
        if m.artifact_sha256 != fitted:          # a recalibration keeps the fit's sha in its first record
            return f"{entry.version}: its manifest describes another artefact"
        reasons = servable_reasons(m, lookup=lambda i: rm.load(i, self.manifests))
        return f"{entry.version}: " + "; ".join(reasons) if reasons else None

    def load_for_serving(self, entry: ModelEntry) -> Any:
        """The checked artefact, or ModelRefused when the serving rule refuses it."""
        why = self.serving_refusal(entry)
        if why is not None:
            raise ModelRefused(why)
        return self.load(entry)

    def _served(self, entries: list[ModelEntry]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for e in entries:
            try:
                out[e.version] = self.load_for_serving(e)
            except Exception as exc:   # noqa: BLE001 - ModelRefused, or an artefact that fails its checksum or unpickling
                log.warning("model refused for serving: %s: %s", e.version, exc)
        return out

    def champion_models(self) -> dict[str, Any]:
        """agent_id -> champion model, for the champions the serving rule admits."""
        champs = [ch for aid in self.agent_ids() if (ch := self.champion(aid)) is not None]
        served = self._served(champs)
        return {e.agent_id: served[e.version] for e in champs if e.version in served}

    def shadow_models(self) -> dict[str, tuple[str, Any]]:
        """version -> (agent_id, model) for every champion and challenger the serving rule admits: what the shadow
        book paper-trades."""
        self._fresh()
        live = [e for e in self._entries if e.status in ("champion", "challenger")]
        served = self._served(live)
        return {e.version: (e.agent_id, served[e.version]) for e in live if e.version in served}

    # ------------------------------------------------------------------ changes
    def ensure_rule_challenger(self, *, family: str, agent_id: str, now: UtcTimestamp | None = None) -> ModelEntry | None:
        """ADR 0038: a `RuleModel` challenger for a rule agent with no registry entry yet (any status), so it
        shadow-trades; None (nothing written) when the agent has one. No backtest, so the weekly gate never promotes it."""
        if self.by_agent(agent_id):       # the usual case on every engine reload: no lock taken
            return None
        with self._lock():                # RegistryLockTimeout under the engine's bound
            if self.by_agent(agent_id):   # another writer added it meanwhile
                return None
            return self.add_challenger(RuleModel(rule=family), family=family, agent_id=agent_id, backtest={}, now=now,
                                       notes=["rule model (ADR 0038): p = rule fired, uncalibrated; shadow only"])

    def add_challenger(self, model: MetaLabelModel | RuleModel, *, family: str, agent_id: str, backtest: dict[str, Any],
                       now: UtcTimestamp | None = None, notes: list[str] | None = None,
                       manifest: rm.RunManifest | None = None) -> ModelEntry:
        """Store `model` as a challenger. `manifest`: the run that fitted it, recorded over these exact pickled bytes
        (run_manifest.record(artifact=pickle.dumps(model), ...)); it is copied into models/manifests/."""
        live_feature_check(list(model.feature_names))      # a challenger shadow-trades on live bars
        data = pickle.dumps(model)
        if manifest is not None:
            if manifest.artifact_sha256 != hashlib.sha256(data).hexdigest():
                raise ValueError(f"manifest {manifest.manifest_id[:12]} was recorded for another artefact "
                                 f"({manifest.artifact_sha256[:12]}), not this model's pickle")
            rm.store(manifest, self.manifests)
        with self._write():
            now = now or pd.Timestamp.now("UTC")
            version = f"{agent_id}-{now:%Y%m%dT%H%M%S}"
            n = 1
            while any(e.version == version for e in self._entries):     # two trainings in one second
                version, n = f"{agent_id}-{now:%Y%m%dT%H%M%S}-{n}", n + 1
            rel = Path(family) / f"{version}.pkl"
            (self.root / family).mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(data)
            e = ModelEntry(version=version, family=family, agent_id=agent_id, status="challenger", created_utc=now,
                           feature_version=model.feature_version, feature_names=list(model.feature_names),
                           artefact=str(rel), sha256=hashlib.sha256(data).hexdigest(), backtest=backtest, notes=notes or [],
                           manifest_id=manifest.manifest_id if manifest else None,
                           model_family=manifest.family if manifest else None)
            self._entries.append(e)
            return e

    def promote(self, version: str, now: UtcTimestamp | None = None, note: str = "") -> ModelEntry:
        """The challenger becomes champion; ModelRefused (nothing changed) when the serving rule refuses it (PR #494
        safety MEDIUM-3: a refused champion is never loaded, so promoting it would leave the agent, and its open
        trades, with no model while a serving champion is demoted)."""
        with self._write():
            e = self.get(version)
            if e.status != "challenger":
                raise ValueError(f"{version} is {e.status}, only a challenger can be promoted")
            why = self.serving_refusal(e)
            if why is not None:
                raise ModelRefused(f"{version} not promoted: {why}")
            live_feature_check(list(e.feature_names))
            for old in self.by_agent(e.agent_id, "previous"):
                old.status = "retired"
            for old in self.by_agent(e.agent_id, "champion"):
                old.status = "previous"
            e.status, e.promoted_utc = "champion", now or pd.Timestamp.now("UTC")
            if note:
                e.notes.append(note)
            return e

    def restore_previous(self, agent_id: str, reason: str, *, demote_to: Status = "retired") -> ModelEntry:
        """The previous champion becomes champion again. The current one is retired (the CUSUM watch's alarm), or kept
        as `previous` (`demote_to="previous"`, rollback-model) so the rollback itself can be undone the same way."""
        if demote_to not in ("retired", "previous"):
            raise ValueError("a demoted champion becomes retired or previous")
        with self._write():
            prev = self.by_agent(agent_id, "previous")
            if not prev:
                raise ValueError(f"no previous champion for {agent_id}")
            back = prev[-1]
            for cur in self.by_agent(agent_id, "champion"):
                cur.status = demote_to
                cur.notes.append(f"demoted: {reason}")
            back.status = "champion"
            back.notes.append(f"restored: {reason}")
            return back

    def recalibrate(self, version: str, model: MetaLabelModel, record: dict[str, Any]) -> ModelEntry:
        """Store `model` (same trees, new calibrator) as the next minor version of `version`: a new artefact with its
        checksum, the record appended to `recalibrations`. Status, version and the shadow record are unchanged, so
        nothing is promoted or retired by a recalibration."""
        with self._write():
            e = self.get(version)
            if e.status not in ("champion", "challenger"):
                raise ValueError(f"{version} is {e.status}; only a champion or challenger is recalibrated")
            if list(model.feature_names) != e.feature_names or model.feature_version != e.feature_version:
                raise ValueError("a recalibration may change only the calibrator, not the model's inputs")
            minor = len(e.recalibrations) + 1
            base = {"base_sha256": e.sha256} if minor == 1 else {}      # the fitted artefact the run manifest describes
            rel = Path(e.family) / f"{e.version}-r{minor}.pkl"
            data = pickle.dumps(model)
            (self.root / rel).write_bytes(data)
            e.artefact, e.sha256 = str(rel), hashlib.sha256(data).hexdigest()
            e.recalibrations.append({"minor": minor, **base, **record})
            e.notes.append(f"recalibration r{minor}: ECE {record.get('ece_before', float('nan')):.4f} -> "
                           f"{record.get('ece_after', float('nan')):.4f} on {record.get('n', 0)} shadow outcomes")
            return e

    def retire(self, version: str, reason: str) -> None:
        with self._write():
            e = self.get(version)
            e.status = "retired"
            e.notes.append(reason)

    def retire_agent(self, agent_id: str, reason: str) -> None:
        """End of an agent's life: called when a retired member's six months of shadow trading are over (population
        retirement itself only stops live trading; its champion keeps shadow-trading so the record stays unbiased)."""
        with self._write():
            for e in self._entries:
                if e.agent_id == agent_id and e.status in ("champion", "challenger", "previous"):
                    e.status = "retired"
                    e.notes.append(reason)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([e.model_dump(mode="json") for e in self._entries], indent=1))
        tmp.replace(self.path)
        self._sig = self._stat()


def rollback_main(argv: list[str] | None = None) -> int:
    """`python -m goldbot.ops.run rollback-model <agent_id> [--reason TEXT] [--models DIR]`: restore the agent's
    previous champion in one command (ADR 0106 decision 7, "every change is reversible"); the demoted champion is kept
    as previous, so running it again rolls forward. Under the registry lock. Refused (exit 2, nothing
    changed) when there is no previous champion or when the serving rule would refuse it. The engine picks the
    restored champion up at its next model reload or restart."""
    ap = argparse.ArgumentParser(prog="rollback-model", description=rollback_main.__doc__)
    ap.add_argument("agent_id")
    ap.add_argument("--reason", default="manual rollback")
    ap.add_argument("--models", default=None, help="model registry folder (default: research.models_dir)")
    a = ap.parse_args(argv)
    if a.models is None:
        from goldbot.config import load_settings
        a.models = load_settings().research.models_dir
    with ModelRegistry.locked(a.models) as reg:
        prev = reg.by_agent(a.agent_id, "previous")
        if not prev:
            print(f"rollback-model: no previous champion for {a.agent_id}; nothing changed", file=sys.stderr)
            return 2
        why = reg.serving_refusal(prev[-1])
        if why is not None:
            print(f"rollback-model: the previous champion would not serve ({why}); nothing changed", file=sys.stderr)
            return 2
        cur = reg.champion(a.agent_id)
        restored = reg.restore_previous(a.agent_id, f"rollback-model: {a.reason}", demote_to="previous")
    print(f"rollback-model: {a.agent_id} champion {cur.version if cur else '-'} -> {restored.version} "
          f"(the demoted one is kept as previous: run rollback-model again to roll forward)")
    return 0
