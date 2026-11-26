"""Run log of record for history-trained models (ADR 0106 7: "any past model can be rebuilt exactly from its logged
data version, settings, seed and code sha"; objective "Log every run: data version, settings, seed, results").

One `RunLog` per fit: the model and spec ids, `data_version` (sha256 of the exact design arrays and keys the fit
read), `settings` (the frozen spec as JSON), the seed, `code_sha` (git blob shas of the code files that define the
fit), the artifact's sha and the results. `append` writes it to a JSONL ledger (state/, never committed: it names no
market value, but the ledgers live beside the research registry). A model's `rebuild(log, data)` refits from the log
and must reproduce `artifact_sha` exactly, or raises `RebuildMismatch`; a different data version or code sha is
refused before any fit.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord

ROOT = Path(__file__).resolve().parents[2]


class RebuildMismatch(RuntimeError):
    """A rebuild from the run log did not reproduce the logged data version, code or artifact sha."""


class RunLog(FrozenRecord):
    model: str
    spec_id: str
    data_version: str
    settings: dict[str, Any]
    seed: int
    code_sha: str
    artifact_sha: str
    results: dict[str, Any]
    created_utc: str


def data_version(arrays: Sequence[np.ndarray], frames: Sequence[pd.DataFrame] = ()) -> str:
    """sha256 of the arrays (dtype, shape and bytes) and the frames (columns and values as CSV, UTC ISO times)."""
    h = hashlib.sha256()
    for a in arrays:
        x = np.ascontiguousarray(a)
        h.update(f"{x.dtype.str}{x.shape}".encode())
        h.update(x.tobytes())
    for f in frames:
        h.update(f.to_csv(index=False, date_format="%Y-%m-%dT%H:%M:%S.%fZ").encode())
    return h.hexdigest()


def _blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def code_sha(files: Iterable[str]) -> str:
    """sha256 over the git blob shas of `files` (repo-relative), in the order given."""
    return hashlib.sha256("|".join(f"{f}:{_blob_sha(ROOT / f)}" for f in files).encode()).hexdigest()


def new(*, model: str, spec_id: str, data: str, settings: Mapping[str, Any], seed: int, code: str,
        artifact_sha: str, results: Mapping[str, Any]) -> RunLog:
    return RunLog(model=model, spec_id=spec_id, data_version=data, settings=dict(settings), seed=seed, code_sha=code,
                  artifact_sha=artifact_sha, results=json.loads(json.dumps(dict(results), default=str)),
                  created_utc=pd.Timestamp.now(tz="UTC").isoformat())


def check_inputs(log: RunLog, *, data: str, code: str) -> None:
    """Refuse a rebuild on other data or other code than the log names."""
    if data != log.data_version:
        raise RebuildMismatch(f"{log.model}: data version {data[:12]} is not the logged {log.data_version[:12]}")
    if code != log.code_sha:
        raise RebuildMismatch(f"{log.model}: code sha {code[:12]} is not the logged {log.code_sha[:12]}")


def check_artifact(log: RunLog, sha: str) -> None:
    if sha != log.artifact_sha:
        raise RebuildMismatch(f"{log.model}: rebuilt artifact {sha[:12]} is not the logged {log.artifact_sha[:12]}")


def append(ledger: Path | str, log: RunLog) -> None:
    p = Path(ledger)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(log.model_dump_json() + "\n")


def read(ledger: Path | str) -> list[RunLog]:
    p = Path(ledger)
    if not p.exists():
        return []
    return [RunLog.model_validate_json(x) for x in p.read_text().splitlines() if x.strip()]
