"""The stage journal of autonomous discovery (ADR 0058 phase 3): every confirm and holdout read of a discovered spec,
append-only and hash-chained, in the search ledger's database (table `stage_reads`, beside the ledger's own chain,
whose schema is unchanged).

Design 3.3: "A failed spec's descendants may be searched again in explore, but the failed hash can never be
re-confirmed." Design 3.5: the holdout is read "once per configuration". So a read is journalled BEFORE it looks at the
data (`<stage>_open`) and its outcome after (`<stage>_result`); each kind is unique per spec hash, so a second open of
the same stage for the same hash is refused by the database itself, whatever happened to the first (a crash between
open and result leaves the read void, never re-runnable).

    StageJournal(path)
      .open(stage, spec_hash, payload)        -> StageEntry   (StageRefused if that stage was opened already)
      .close(stage, spec_hash, payload)       -> StageEntry   (payload["passed"]: bool, or payload["void"])
      .state(spec_hash)                       -> StageState   confirm / holdout: None, "open", "void", "passed", "failed"
      .entries() / .verify()                  the chain, verified like the ledger's (LedgerTamperError)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.research.autodisc.ledger import GENESIS, LedgerTamperError

Stage = Literal["confirm", "holdout"]
Outcome = Literal["open", "void", "passed", "failed"]
_SCHEMA = """
CREATE TABLE IF NOT EXISTS stage_reads (
    seq INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('confirm_open','confirm_result','holdout_open','holdout_result')),
    spec_hash TEXT NOT NULL,
    written_utc TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_digest TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE (kind, spec_hash)
);
"""
_COLS = "seq, kind, spec_hash, written_utc, payload, prev_digest, digest"


class StageRefused(ValueError):
    """A confirm or holdout step was refused; the message says why. Nothing was read."""


class StageEntry(FrozenRecord):
    seq: int
    kind: str
    spec_hash: str
    written_utc: str
    payload: str
    prev_digest: str
    digest: str


class StageState(FrozenRecord):
    confirm: Outcome | None = None
    holdout: Outcome | None = None


def _digest(prev: str, seq: int, kind: str, h: str, written: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{seq}|{kind}|{h}|{written}|{payload}".encode()).hexdigest()


class StageJournal:
    def __init__(self, path: str | Path, *, clock: Callable[[], pd.Timestamp] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: pd.Timestamp.now("UTC"))
        con = self._connect()
        con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.executescript(_SCHEMA)
        return con

    def _append(self, kind: str, h: str, payload: Mapping[str, Any], *, need: str | None) -> StageEntry:
        body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                if con.execute("SELECT 1 FROM stage_reads WHERE kind=? AND spec_hash=?", (kind, h)).fetchone():
                    stage = kind.split("_")[0]
                    raise StageRefused(f"spec {h[:12]} was read on the {stage} window already ({kind} journalled): a "
                                       "stage read happens once, whatever its outcome")
                if need and not con.execute("SELECT 1 FROM stage_reads WHERE kind=? AND spec_hash=?",
                                            (need, h)).fetchone():
                    raise StageRefused(f"spec {h[:12]}: no {need} row to close")
                tip = con.execute("SELECT seq, digest FROM stage_reads ORDER BY seq DESC LIMIT 1").fetchone()
                seq, prev = (tip[0] + 1, tip[1]) if tip else (1, GENESIS)
                written = self._clock().isoformat()
                d = _digest(prev, seq, kind, h, written, body)
                con.execute(f"INSERT INTO stage_reads ({_COLS}) VALUES (?,?,?,?,?,?,?)",
                            (seq, kind, h, written, body, prev, d))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()
        return StageEntry(seq=seq, kind=kind, spec_hash=h, written_utc=written, payload=body, prev_digest=prev, digest=d)

    def open(self, stage: Stage, spec_hash: str, payload: Mapping[str, Any]) -> StageEntry:
        """Journal a read BEFORE it looks at the data; refused when that stage was opened for the hash before."""
        return self._append(f"{stage}_open", spec_hash, payload, need=None)

    def close(self, stage: Stage, spec_hash: str, payload: Mapping[str, Any]) -> StageEntry:
        """Journal the read's outcome (`passed`, or `void` when the read did not complete)."""
        return self._append(f"{stage}_result", spec_hash, payload, need=f"{stage}_open")

    def entries(self) -> list[StageEntry]:
        con = self._connect()
        try:
            rows = con.execute(f"SELECT {_COLS} FROM stage_reads ORDER BY seq").fetchall()
        finally:
            con.close()
        return [StageEntry(seq=r[0], kind=r[1], spec_hash=r[2], written_utc=r[3], payload=r[4], prev_digest=r[5],
                           digest=r[6]) for r in rows]

    def result(self, stage: Stage, spec_hash: str) -> dict[str, Any] | None:
        e = next((e for e in self.entries() if e.kind == f"{stage}_result" and e.spec_hash == spec_hash), None)
        return None if e is None else dict(json.loads(e.payload))

    def state(self, spec_hash: str) -> StageState:
        out: dict[str, Outcome | None] = {"confirm": None, "holdout": None}
        for e in self.entries():
            if e.spec_hash != spec_hash:
                continue
            stage, part = e.kind.split("_")
            if part == "open":
                out[stage] = "open"
            else:
                p = json.loads(e.payload)
                out[stage] = "void" if p.get("void") else ("passed" if p.get("passed") else "failed")
        return StageState(confirm=out["confirm"], holdout=out["holdout"])

    def verify(self) -> int:
        prev, expect = GENESIS, 1
        entries = self.entries()
        for e in entries:
            if e.seq != expect or e.prev_digest != prev or \
                    _digest(prev, e.seq, e.kind, e.spec_hash, e.written_utc, e.payload) != e.digest:
                raise LedgerTamperError(f"stage_reads row {e.seq} does not verify: the stage journal was altered")
            prev, expect = e.digest, expect + 1
        return len(entries)
