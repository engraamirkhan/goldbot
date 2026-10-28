"""Search ledger: SQLite (WAL), append-only, hash-chained (design section 3.1; robust-storage rule).

Every spec is logged BEFORE its result: `record_result` refuses a hash with no spec row. K_search is the number of
distinct canonical spec hashes logged with is_null=False; null-run (G0) specs are counted separately as K_null and
never mixed in. Re-logging a hash adds nothing (returns the existing entry), so re-evaluation never moves K.

Signatures (the design leaves them open):
    SearchLedger(path)                                   creates the file/schema; one connection per call, so it is
                                                         safe across worker processes (appends take BEGIN IMMEDIATE)
    .log_spec(spec, generator, generator_version, parents=(), *, null=False) -> LedgerEntry
    .record_result(spec_hash, result, *, null=False) -> LedgerEntry | None
                                                         LedgerError if the spec is not ledgered; None if a result for
                                                         that hash is already recorded (first result stands)
    .is_logged(spec_hash, *, null=False) -> bool         (evaluators call this and refuse an unledgered hash)
    .require_logged(spec_hash, *, null=False) -> None    raises LedgerError
    .get_spec(spec_hash) -> StrategySpec | None
    .result(spec_hash, *, null=False) -> dict | None
    .k_search() -> int ; .k_null() -> int
    .entries() -> list[LedgerEntry]                      in chain order
    .head() -> tuple[int, str]                           (seq, digest) of the tip; (0, genesis) when empty. Keep it
                                                         elsewhere to detect tail truncation
    .verify() -> int                                     recomputes the whole chain; raises LedgerTamperError, returns
                                                         the entry count
Digest = sha256(prev_digest | seq | kind | spec_hash | is_null | payload | written_utc); payload is canonical JSON
(spec rows: generator, version, parents, canonical spec; result rows: the result mapping).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.research.autodisc.spec import StrategySpec, canonical_json, spec_hash

GENESIS = "0" * 64


class LedgerError(RuntimeError):
    """A ledger rule was broken (result before spec, bad input)."""


class LedgerTamperError(LedgerError):
    """The hash chain does not verify."""


class LedgerEntry(FrozenRecord):
    seq: int
    kind: Literal["spec", "result"]
    spec_hash: str
    is_null: bool
    payload: str
    written_utc: str
    prev_digest: str
    digest: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    seq INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('spec','result')),
    spec_hash TEXT NOT NULL,
    is_null INTEGER NOT NULL CHECK (is_null IN (0,1)),
    payload TEXT NOT NULL,
    written_utc TEXT NOT NULL,
    prev_digest TEXT NOT NULL,
    digest TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS entries_once ON entries (kind, spec_hash, is_null);
"""
_COLS = "seq, kind, spec_hash, is_null, payload, written_utc, prev_digest, digest"


def _digest(prev: str, seq: int, kind: str, h: str, is_null: bool, payload: str, written: str) -> str:
    return hashlib.sha256("|".join([prev, str(seq), kind, h, str(int(is_null)), payload, written]).encode()).hexdigest()


def _row_entry(r: Sequence[Any]) -> LedgerEntry:
    return LedgerEntry(seq=r[0], kind=r[1], spec_hash=r[2], is_null=bool(r[3]), payload=r[4], written_utc=r[5],
                       prev_digest=r[6], digest=r[7])


def _dumps(d: Any) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


class SearchLedger:
    def __init__(self, path: str | Path, *, clock: Callable[[], pd.Timestamp] | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or (lambda: pd.Timestamp.now("UTC"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(_SCHEMA)
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _append(self, con: sqlite3.Connection, kind: str, h: str, is_null: bool, payload: str) -> LedgerEntry:
        """Caller holds BEGIN IMMEDIATE."""
        tip = con.execute("SELECT seq, digest FROM entries ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (tip[0] + 1, tip[1]) if tip else (1, GENESIS)
        written = self._clock().isoformat()
        d = _digest(prev, seq, kind, h, is_null, payload, written)
        con.execute(f"INSERT INTO entries ({_COLS}) VALUES (?,?,?,?,?,?,?,?)",
                    (seq, kind, h, int(is_null), payload, written, prev, d))
        return LedgerEntry(seq=seq, kind=kind, spec_hash=h, is_null=is_null,  # type: ignore[arg-type]
                           payload=payload, written_utc=written, prev_digest=prev, digest=d)

    def _find(self, con: sqlite3.Connection, kind: str, h: str, is_null: bool) -> LedgerEntry | None:
        r = con.execute(f"SELECT {_COLS} FROM entries WHERE kind=? AND spec_hash=? AND is_null=?",
                        (kind, h, int(is_null))).fetchone()
        return _row_entry(r) if r else None

    def log_spec(self, spec: StrategySpec, generator: str, generator_version: str,
                 parents: Sequence[str] = (), *, null: bool = False) -> LedgerEntry:
        if not generator:
            raise LedgerError("generator is required")
        h = spec_hash(spec)
        payload = _dumps({"generator": generator, "generator_version": generator_version,
                          "parents": list(parents), "spec": json.loads(canonical_json(spec))})
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                existing = self._find(con, "spec", h, null)
                entry = existing if existing is not None else self._append(con, "spec", h, null, payload)
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            return entry
        finally:
            con.close()

    def record_result(self, spec_hash: str, result: Mapping[str, Any], *, null: bool = False) -> LedgerEntry | None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                if self._find(con, "spec", spec_hash, null) is None:
                    raise LedgerError(f"spec {spec_hash[:12]} is not in the ledger: log it before its result")
                entry = None if self._find(con, "result", spec_hash, null) else \
                    self._append(con, "result", spec_hash, null, _dumps(dict(result)))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            return entry
        finally:
            con.close()

    def _query(self, sql: str, args: tuple[Any, ...] = ()) -> list[Any]:
        con = self._connect()
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()

    def is_logged(self, spec_hash: str, *, null: bool = False) -> bool:
        return bool(self._query("SELECT 1 FROM entries WHERE kind='spec' AND spec_hash=? AND is_null=?",
                                (spec_hash, int(null))))

    def require_logged(self, spec_hash: str, *, null: bool = False) -> None:
        if not self.is_logged(spec_hash, null=null):
            raise LedgerError(f"spec {spec_hash[:12]} is not in the ledger: log it before evaluating")

    def get_spec(self, spec_hash: str) -> StrategySpec | None:
        rows = self._query("SELECT payload FROM entries WHERE kind='spec' AND spec_hash=? ORDER BY seq LIMIT 1",
                           (spec_hash,))
        return StrategySpec.model_validate(json.loads(rows[0][0])["spec"]) if rows else None

    def result(self, spec_hash: str, *, null: bool = False) -> dict[str, Any] | None:
        rows = self._query("SELECT payload FROM entries WHERE kind='result' AND spec_hash=? AND is_null=?",
                           (spec_hash, int(null)))
        return dict(json.loads(rows[0][0])) if rows else None

    def _k(self, null: bool) -> int:
        return int(self._query("SELECT COUNT(DISTINCT spec_hash) FROM entries WHERE kind='spec' AND is_null=?",
                               (int(null),))[0][0])

    def k_search(self) -> int:
        return self._k(False)

    def k_null(self) -> int:
        return self._k(True)

    def entries(self) -> list[LedgerEntry]:
        return [_row_entry(r) for r in self._query(f"SELECT {_COLS} FROM entries ORDER BY seq")]

    def head(self) -> tuple[int, str]:
        rows = self._query("SELECT seq, digest FROM entries ORDER BY seq DESC LIMIT 1")
        return (int(rows[0][0]), str(rows[0][1])) if rows else (0, GENESIS)

    def verify(self) -> int:
        prev, expect, seen_spec = GENESIS, 1, set[tuple[str, bool]]()
        entries = self.entries()
        for e in entries:
            if e.seq != expect:
                raise LedgerTamperError(f"sequence gap: expected {expect}, found {e.seq}")
            if e.prev_digest != prev:
                raise LedgerTamperError(f"entry {e.seq}: prev_digest does not match the chain")
            if _digest(prev, e.seq, e.kind, e.spec_hash, e.is_null, e.payload, e.written_utc) != e.digest:
                raise LedgerTamperError(f"entry {e.seq}: digest mismatch (row altered)")
            if e.kind == "spec":
                seen_spec.add((e.spec_hash, e.is_null))
            elif (e.spec_hash, e.is_null) not in seen_spec:
                raise LedgerTamperError(f"entry {e.seq}: result without an earlier spec row")
            prev, expect = e.digest, expect + 1
        return len(entries)
