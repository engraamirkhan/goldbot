"""Run manifest of every training run (ADR 0106 decision 7: "Every change is reversible, and any past model can be
rebuilt exactly from its logged data version, settings, seed and code sha"; objectives "Log every run: data version,
settings, seed, results" and "Normalise features using past data only"; goal "Any past model can be rebuilt exactly
from logs").

One immutable `RunManifest` per fitted artifact: the data version (the dataset version id or tiers sha the fit read,
plus the sha256 of each data file when the trainer names them), the settings and their hash, every seed, the git
commit and whether the tracked tree was clean, the uv.lock hash, the runtime, the train end and the scaler fit end
(the last instant any fitted transform saw; it must not lie after the train end), the artifact's sha256, the
builder that makes it, the results and the model-quality verdicts (research/model_quality.py).

Stored content-addressed: `<root>/<manifest_id>.json`, where manifest_id is the sha256 of the manifest's canonical
JSON, so an edited file is refused on load. The model registry keeps its manifests in `models/manifests/` beside the
artefacts (backed up with them); a trainer that records elsewhere hands the manifest to `ModelRegistry.add_challenger`,
which copies it in.

The trainer hook is one call per fit:

    art, m = run_manifest.fit_and_record("pkg.mod:build", model=..., family="A", data_version=..., data_root=...,
                                         data_files=[...], settings=..., manifest_key=..., served="ensemble",
                                         unit=..., fold_keys=[...], train_end=..., scaler_fit_end=...,
                                         label_end_max=..., quality=[...], root=...)

where the seeds are `model_quality.seed_list(manifest_key)` (quant Q1: fixed before the first fit, every seed fitted
is recorded) and `label_end_max` is the latest label horizon end among the training rows (quant Q4).

which runs the builder exactly as `rebuild` will (one BLAS/OpenMP thread), or, when the trainer fits by itself,
`run_manifest.record(model=..., artifact=<bytes>, ...)`. A builder is a pure `Callable[[BuildSpec], bytes]`.

`rebuild(manifest)` refuses (RebuildRefused) a different uv.lock, a dirty tracked tree, a commit other than the
manifest's (check that commit out in a worktree and run there), a changed data file and a manifest without a builder;
it raises RebuildMismatch when the rebuilt bytes differ. scripts/research/rebuild_model.py is its command line.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import platform
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field, field_validator, model_validator

from goldbot.base import FrozenRecord, UtcTimestamp, write_atomic
from goldbot.research.model_quality import MIN_SEEDS, ModelFamily, QualityVerdict, Served, seed_list

__all__ = ["BuildSpec", "DEFAULT_ROOT", "ManifestError", "RebuildMismatch", "RebuildRefused", "RunManifest", "Schedule",
           "build", "code_state", "file_sha256", "fit_and_record", "load", "lock_hash", "record", "rebuild", "resolve",
           "settings_hash", "sha256_bytes", "store"]

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path("models") / "manifests"        # relative to the service's working directory, beside registry.json
Schedule = Literal["adhoc", "daily", "weekly", "monthly_walkforward"]
RUNTIME_PACKAGES = ("numpy", "pandas", "scikit-learn", "scipy", "lightgbm", "torch")
_HEX64 = r"^[0-9a-f]{64}$"
# A builder is `module:function` under goldbot. or the test builders, nothing else (security follow-up to #494): a
# manifest is data, and data must never choose an arbitrary module to import and call.
_BUILDER = r"^(goldbot(\.[A-Za-z_]\w*)+|tests\.rebuild_builders):[A-Za-z_]\w*$"


class ManifestError(ValueError):
    """A manifest that cannot be recorded or loaded as it stands."""


class RebuildRefused(RuntimeError):
    """A rebuild whose preconditions (lock, clean tree, commit, data, builder) do not hold."""


class RebuildMismatch(RuntimeError):
    """A rebuild that ran and produced different bytes."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical(obj: Any, what: str) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{what} must be plain JSON (str, int, finite float, bool, None, list, dict): {exc}") \
            from exc


def settings_hash(settings: Mapping[str, Any]) -> str:
    """sha256 of the settings' canonical JSON (sorted keys): the same settings in any key order hash alike."""
    return sha256_bytes(_canonical(dict(settings), "settings").encode())


def _finite(obj: Any) -> Any:
    """Results as JSON: a non-finite float becomes None; anything else not JSON-native is refused by _canonical."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, Mapping):
        return {str(k): _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):     # numpy scalars
        return _finite(obj.item())
    return obj


def _git(repo: Path, *args: str) -> str:
    # safe.directory: the brain's checkout is owned by the deploy user, not by the service user that trains
    r = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ManifestError(f"git {' '.join(args)} failed in {repo}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def code_state(repo: Path | str = ROOT) -> tuple[str, bool]:
    """(HEAD commit, tracked tree dirty?). Untracked files (state, outputs) do not count."""
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ManifestError(f"no git checkout at {repo}")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    dirty = bool(_git(repo, "status", "--porcelain", "--untracked-files=no").strip())
    return sha, dirty


def lock_hash(repo: Path | str = ROOT) -> str:
    p = Path(repo) / "uv.lock"
    if not p.is_file():
        raise ManifestError(f"no uv.lock in {repo}: the dependency set of the run cannot be recorded")
    return file_sha256(p)


def _runtime() -> dict[str, str]:
    out = {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()}
    for pkg in RUNTIME_PACKAGES:
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            continue
    return out


class RunManifest(FrozenRecord):
    manifest_id: str = Field(pattern=_HEX64)
    created_utc: UtcTimestamp
    model: str = Field(min_length=1)                 # learner name, e.g. "sr-v3:GBT-slow", "seq1:tcn"
    family: ModelFamily                              # ADR 0106 decision 3
    manifest_key: str = Field(min_length=1)          # fixes the seeds: seeds == model_quality.seed_list(manifest_key)
    served: Served                                   # declared before fitting: the seeds' ensemble or seed index 0
    unit: str = ""                                   # the trained unit, e.g. "XAUUSD:15m" (the baseline must match)
    fold_keys: list[str] = Field(default_factory=list)   # walk-forward folds (the baseline must match)
    spec_id: str = ""                                # the card / pre-registration the fit belongs to
    schedule: Schedule = "adhoc"
    data_version: str = Field(min_length=1)          # dataset version id or tiers sha (data/dataset_version)
    data_files: dict[str, str] = Field(default_factory=dict)   # path relative to the data root -> sha256
    settings: dict[str, Any]
    settings_hash: str = Field(pattern=_HEX64)
    seeds: list[int]
    code_sha: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    code_dirty: bool
    uv_lock_hash: str = Field(pattern=_HEX64)
    runtime: dict[str, str]
    train_end: UtcTimestamp                          # the last decision instant the fit used
    label_end_max: UtcTimestamp                      # the latest label horizon end among the training rows
    scaler_fit_end: UtcTimestamp                     # the last instant any fitted transform saw (<= train_end)
    artifact_sha256: str = Field(pattern=_HEX64)
    builder: str | None = Field(None, pattern=_BUILDER)
    results: dict[str, Any] = Field(default_factory=dict)
    quality: list[QualityVerdict] = Field(default_factory=list)

    @field_validator("seeds")
    @classmethod
    def _seeds(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("a run records at least one seed")
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate seeds {v}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> RunManifest:
        if self.scaler_fit_end > self.train_end:
            raise ValueError(f"scaler_fit_end {self.scaler_fit_end} is after train_end {self.train_end}: a transform "
                             "saw data the model may not (normalise with past data only)")
        if self.seeds != seed_list(self.manifest_key, len(self.seeds)):
            raise ValueError("seeds are not model_quality.seed_list(manifest_key): the seeds are fixed by the key "
                             "before the first fit, every seed fitted is recorded, none is chosen after reading")
        if settings_hash(self.settings) != self.settings_hash:
            raise ValueError("settings_hash does not match the settings")
        if _id_of(self) != self.manifest_id:
            raise ValueError("manifest_id does not match the manifest's content")
        return self

    def body(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_id"})


def _id_of(m: RunManifest | Mapping[str, Any]) -> str:
    body = m.body() if isinstance(m, RunManifest) else {k: v for k, v in m.items() if k != "manifest_id"}
    return sha256_bytes(_canonical(body, "manifest").encode())


def store(m: RunManifest, root: Path | str = DEFAULT_ROOT) -> Path:
    """Write `m` to `<root>/<manifest_id>.json` (atomic; an existing identical file is left as is)."""
    p = Path(root) / f"{m.manifest_id}.json"
    text = json.dumps(m.model_dump(mode="json"), sort_keys=True, indent=1)
    if not p.exists() or p.read_text() != text:
        write_atomic(p, text)
    return p


def load(manifest_id: str, root: Path | str = DEFAULT_ROOT) -> RunManifest:
    """The manifest `manifest_id`; ManifestError when it is missing, unreadable or edited since it was recorded."""
    p = Path(root) / f"{manifest_id}.json"
    try:
        doc = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        raise ManifestError(f"manifest {manifest_id[:12]} cannot be read from {p}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ManifestError(f"{p} is not a JSON object (trading-safety H1 on #494): refused")
    if doc.get("manifest_id") != manifest_id:
        raise ManifestError(f"{p} names manifest {str(doc.get('manifest_id'))[:12]}, not {manifest_id[:12]}")
    try:
        return RunManifest.model_validate(doc)
    except ValueError as exc:
        raise ManifestError(f"manifest {manifest_id[:12]} does not match its recorded content: {exc}") from exc


def resolve(ref: str, *, models_dir: Path | str = "models") -> RunManifest:
    """A manifest by its id, or by a model registry version (the entry's manifest_id), in `<models_dir>/manifests`."""
    root = Path(models_dir) / "manifests"
    if (root / f"{ref}.json").exists():
        return load(ref, root)
    from goldbot.research.model_registry import ModelRegistry
    try:
        entry = ModelRegistry(models_dir).get(ref)
    except KeyError:
        raise ManifestError(f"{ref} is neither a manifest id in {root} nor a registry version") from None
    if entry.manifest_id is None:
        raise ManifestError(f"registry version {ref} has no run manifest (trained before manifests existed)")
    return load(entry.manifest_id, root)


def _utc(t: pd.Timestamp) -> str:
    """The stored form of a UtcTimestamp, so the id is computed over exactly what is written."""
    t = pd.Timestamp(t)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).isoformat()


def record(*, model: str, family: ModelFamily, artifact: bytes, data_version: str, settings: Mapping[str, Any],
           manifest_key: str, served: Served, train_end: pd.Timestamp, scaler_fit_end: pd.Timestamp,
           label_end_max: pd.Timestamp, seeds: Sequence[int] | None = None, unit: str = "",
           fold_keys: Sequence[str] = (), data_files: Mapping[str, str] | None = None, builder: str | None = None,
           results: Mapping[str, Any] | None = None, quality: Sequence[QualityVerdict] = (),
           schedule: Schedule = "adhoc", spec_id: str = "", root: Path | str = DEFAULT_ROOT, repo: Path | str = ROOT,
           now: pd.Timestamp | None = None) -> RunManifest:
    """Record the run that produced `artifact` (its exact bytes) and store the manifest under `root`. `seeds`
    defaults to `seed_list(manifest_key)` (MIN_SEEDS seeds); any other list is refused. Fails loudly (ManifestError,
    ValueError) rather than record a manifest that could not rebuild the model."""
    seeds = seed_list(manifest_key, MIN_SEEDS) if seeds is None else list(seeds)
    sha, dirty = code_state(repo)
    plain = json.loads(_canonical(dict(settings), "settings"))
    body: dict[str, Any] = {
        "created_utc": _utc(now or pd.Timestamp.now(tz="UTC")), "model": model, "family": family,
        "manifest_key": manifest_key, "served": served, "unit": unit, "fold_keys": [str(k) for k in fold_keys],
        "label_end_max": _utc(label_end_max),
        "spec_id": spec_id, "schedule": schedule, "data_version": data_version, "data_files": dict(data_files or {}),
        "settings": plain, "settings_hash": settings_hash(plain), "seeds": [int(s) for s in seeds], "code_sha": sha,
        "code_dirty": dirty, "uv_lock_hash": lock_hash(repo), "runtime": _runtime(),
        "train_end": _utc(train_end), "scaler_fit_end": _utc(scaler_fit_end),
        "artifact_sha256": sha256_bytes(artifact), "builder": builder,
        "results": json.loads(_canonical(_finite(dict(results or {})), "results")),
        "quality": [q.model_dump(mode="json") for q in quality],
    }
    m = RunManifest.model_validate({**body, "manifest_id": _id_of(body)})
    store(m, root)
    return m


# ---------------------------------------------------------------------------------------------- build and rebuild
class BuildSpec(FrozenRecord):
    """What a builder may read: the data files (name -> absolute path), the settings and the seeds."""
    data_files: dict[str, str]
    settings: dict[str, Any]
    seeds: list[int]
    train_end: UtcTimestamp
    scaler_fit_end: UtcTimestamp


def _builder(ref: str) -> Callable[[BuildSpec], bytes]:
    """Import the builder only after `ref` matches _BUILDER, so a manifest can never name os:system or the like."""
    if not re.fullmatch(_BUILDER, ref):
        raise RebuildRefused(f"{ref!r} is not an allowed builder: only goldbot.<module>:<function> or "
                             "tests.rebuild_builders:<function> are imported")
    mod, _, fn = ref.partition(":")
    try:
        f = getattr(importlib.import_module(mod), fn)
    except (ImportError, AttributeError) as exc:
        raise RebuildRefused(f"builder {ref} cannot be imported: {exc}") from exc
    if not callable(f):
        raise RebuildRefused(f"builder {ref} is not callable")
    return f


def build(builder: str, spec: BuildSpec) -> bytes:
    """Run `builder` on `spec` with one BLAS/OpenMP thread (thread count changes float summation order)."""
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]
    fn = _builder(builder)
    with threadpool_limits(limits=1):
        out = fn(spec)
    if not isinstance(out, bytes):
        raise TypeError(f"builder {builder} returned {type(out).__name__}, not bytes")
    return out


def _spec(data_root: Path, files: Sequence[str] | Mapping[str, str], settings: Mapping[str, Any], seeds: Sequence[int],
          train_end: pd.Timestamp, scaler_fit_end: pd.Timestamp) -> BuildSpec:
    return BuildSpec(data_files={f: str((data_root / f).resolve()) for f in files},
                     settings=json.loads(_canonical(dict(settings), "settings")), seeds=[int(s) for s in seeds],
                     train_end=train_end, scaler_fit_end=scaler_fit_end)


def fit_and_record(builder: str, *, model: str, family: ModelFamily, data_version: str, data_root: Path | str,
                   data_files: Sequence[str], settings: Mapping[str, Any], manifest_key: str, served: Served,
                   train_end: pd.Timestamp, scaler_fit_end: pd.Timestamp, label_end_max: pd.Timestamp,
                   seeds: Sequence[int] | None = None, unit: str = "", fold_keys: Sequence[str] = (),
                   results: Mapping[str, Any] | None = None,
                   quality: Sequence[QualityVerdict] = (), schedule: Schedule = "adhoc", spec_id: str = "",
                   root: Path | str = DEFAULT_ROOT, repo: Path | str = ROOT,
                   now: pd.Timestamp | None = None) -> tuple[bytes, RunManifest]:
    """Fit through `builder` exactly as `rebuild` will, then `record` the run with each data file's sha256."""
    data_root = Path(data_root)
    seeds = seed_list(manifest_key, MIN_SEEDS) if seeds is None else list(seeds)
    if seeds != seed_list(manifest_key, len(seeds)):
        raise ManifestError("seeds are not model_quality.seed_list(manifest_key)")
    shas = {f: file_sha256(data_root / f) for f in data_files}
    art = build(builder, _spec(data_root, data_files, settings, seeds, train_end, scaler_fit_end))
    m = record(model=model, family=family, artifact=art, data_version=data_version, settings=settings,
               manifest_key=manifest_key, served=served, seeds=seeds, train_end=train_end,
               scaler_fit_end=scaler_fit_end, label_end_max=label_end_max, unit=unit, fold_keys=fold_keys,
               data_files=shas, builder=builder, results=results, quality=quality, schedule=schedule,
               spec_id=spec_id, root=root, repo=repo, now=now)
    return art, m


def rebuild(m: RunManifest, *, repo: Path | str = ROOT, data_root: Path | str) -> str:
    """Refit `m` from its log and return the artifact sha256 (equal to m.artifact_sha256), or raise RebuildRefused
    before any fit / RebuildMismatch after it."""
    if m.builder is None:
        raise RebuildRefused(f"manifest {m.manifest_id[:12]} names no builder: the trainer recorded bytes it fitted "
                             "itself, so only the inputs can be checked")
    try:
        sha, dirty = code_state(repo)
        lock = lock_hash(repo)
    except ManifestError as exc:
        raise RebuildRefused(str(exc)) from exc
    if lock != m.uv_lock_hash:
        raise RebuildRefused(f"uv.lock differs from the run's ({lock[:12]} vs {m.uv_lock_hash[:12]}): the "
                             "dependency set is not the one the model was built with")
    if dirty:
        raise RebuildRefused(f"the tracked tree in {repo} is dirty: commit or stash before rebuilding")
    if m.code_dirty:
        raise RebuildRefused(f"manifest {m.manifest_id[:12]} was recorded from a dirty tree: its code is not in git")
    if sha != m.code_sha:
        raise RebuildRefused(f"HEAD is {sha[:12]}, the run was built at {m.code_sha[:12]}: "
                             f"`git worktree add <dir> {m.code_sha}` and rebuild there")
    data_root = Path(data_root)
    top = data_root.resolve()
    for f in m.data_files:
        if not (data_root / f).resolve().is_relative_to(top):
            raise RebuildRefused(f"data file {f} resolves outside the data root {data_root}: refused")
    for f, want in m.data_files.items():
        p = data_root / f
        got = file_sha256(p) if p.is_file() else "missing"
        if got != want:
            raise RebuildRefused(f"data file {f} is {got[:12]}, the run read {want[:12]}")
    art = build(m.builder, _spec(data_root, list(m.data_files), m.settings, m.seeds, m.train_end, m.scaler_fit_end))
    got_sha = sha256_bytes(art)
    if got_sha != m.artifact_sha256:
        here = _runtime()
        diff = {k: (m.runtime.get(k), here.get(k)) for k in set(m.runtime) | set(here) if m.runtime.get(k) != here.get(k)}
        raise RebuildMismatch(f"rebuilt artifact {got_sha[:12]} does not match the recorded {m.artifact_sha256[:12]}"
                              + (f" (runtime differs: {diff})" if diff else ""))
    return got_sha
