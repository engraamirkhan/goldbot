"""The discovery hand-off (ADR 0090): what the nightly search leaves for registration, in the search ledger's own
database beside the chain (whose schema is unchanged), as `record_survivors` does.

    candidate_arms   one row per explore survivor that can run live (`arm_rules_spec.check` with the prereg still to
                     come): the frozen arm row without an id, account or prereg. Insert-only, the first night stands.
    nightly_runs     one row per discovery_nightly run: the run record (counts, K spent, survivors, power verdicts),
                     pinned to the ledger head it was written at. Insert-only.

    write_candidates(path, rows) -> int     rows added (a spec already offered adds nothing)
    pending(path) -> list[CandidateArm]     offered and not yet in `arm_registrations` (ops/autodisc_register.py)
    registered(path) -> {arm_id: spec_hash} the registration journal's arms (empty before the first registration)
    record_run(path, night, payload) / runs(path)

The seam: `ops/autodisc_register.py` registers a spec by hash (`--stage explore` for an explore survivor: design 4.1
"the arm runs only on demo accounts", its forward read the first out-of-sample evidence) and journals it in
`arm_registrations`; a hot-load path reads `pending` instead of a hash typed by hand. Nothing here writes a config file,
an account or an arm: an offered row is a proposal, never a registration."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from goldbot.base import FrozenRecord, load_record_json
from goldbot.db.connection import create_private
from goldbot.research.autodisc.ledger import GENESIS

SPEC_RULE = "spec_rule"                  # research.arms.SPEC_RULE (not imported: the arms module loads arm configs)
STYLE_BY_TF = {"1h": "intraday", "4h": "swing", "1d": "swing"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_arms (
    spec_hash TEXT PRIMARY KEY,
    night TEXT NOT NULL,
    written_utc TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nightly_runs (
    seq INTEGER PRIMARY KEY,
    night TEXT NOT NULL,
    written_utc TEXT NOT NULL,
    head_seq INTEGER NOT NULL,
    head_digest TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


class CandidateArm(FrozenRecord):
    """A survivor offered for registration: the frozen arm row as `autodisc_register.plan` builds it, minus the id,
    the account and the prereg it adds."""
    spec_hash: str
    night: str
    timeframe: str
    style: str
    spec: str                                        # canonical JSON (`spec.canonical_json`)
    dsr: float | None
    meets_confirm_dsr: bool
    k_search: int
    power: dict[str, Any]                            # its ADR 0085 verdict (power_first.PowerVerdict, JSON)
    neighbours_admitted: int = 0                     # plateau neighbours the power admission let through, of
    neighbours_total: int = 0                        #   its grid neighbours (a refused one reads as not positive)
    # the ADR 0085 verdict of the forward window over the learning kill rule, reading "reported, not evidence" (quant
    # ruling (a), 2026-11-18): over that horizon no spec reaches the n_eff floor, so it never gates
    power_forward: dict[str, Any] = {}
    rule: Literal["spec_rule"] = "spec_rule"
    max_open: int = 1
    forward_only: bool = True
    stage: Literal["explore_survivor"] = "explore_survivor"

    def arm_params(self, prereg: str) -> dict[str, str]:
        """The arm's params once its forward reading rule exists (`arm_rules_spec.PARAM_KEYS`)."""
        return {"spec": self.spec, "spec_hash": self.spec_hash, "prereg": prereg}


def _connect(path: Path) -> sqlite3.Connection:
    create_private(path)
    con = sqlite3.connect(path, timeout=60, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return pd.Timestamp.now("UTC").isoformat()


def write_candidates(path: str | Path, rows: Iterable[CandidateArm]) -> int:
    con = _connect(Path(path))
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            added = sum(con.execute("INSERT OR IGNORE INTO candidate_arms (spec_hash, night, written_utc, payload) "
                                    "VALUES (?,?,?,?)", (r.spec_hash, r.night, _now(), r.model_dump_json())).rowcount
                        for r in rows)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        return added
    finally:
        con.close()


def _has(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def registered(path: str | Path) -> dict[str, str]:
    """arm id -> spec hash from the registration journal; {} when the database or the journal does not exist."""
    p = Path(path)
    if not p.exists():
        return {}
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        if not _has(con, "arm_registrations"):
            return {}
        return {str(a): str(h) for a, h in con.execute("SELECT arm_id, spec_hash FROM arm_registrations")}
    finally:
        con.close()


def pending(path: str | Path) -> list[CandidateArm]:
    p = Path(path)
    if not p.exists():
        return []
    done = set(registered(p).values())
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        if not _has(con, "candidate_arms"):
            return []
        rows = con.execute("SELECT payload FROM candidate_arms ORDER BY night, spec_hash").fetchall()
    finally:
        con.close()
    out = [load_record_json(CandidateArm, r[0], source="candidate_arms") for r in rows]
    return [c for c in out if c.spec_hash not in done]


def record_run(path: str | Path, night: str, payload: Mapping[str, Any]) -> None:
    """The run record in the discovery ledger's database, pinned to the chain head it was written at."""
    con = _connect(Path(path))
    try:
        tip = con.execute("SELECT seq, digest FROM entries ORDER BY seq DESC LIMIT 1").fetchone() \
            if _has(con, "entries") else None
        seq, digest = (int(tip[0]), str(tip[1])) if tip else (0, GENESIS)
        con.execute("INSERT INTO nightly_runs (night, written_utc, head_seq, head_digest, payload) VALUES (?,?,?,?,?)",
                    (night, _now(), seq, digest, json.dumps(dict(payload), sort_keys=True, default=str)))
    finally:
        con.close()


def runs(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        if not _has(con, "nightly_runs"):
            return []
        return [dict(json.loads(r[0])) for r in con.execute("SELECT payload FROM nightly_runs ORDER BY seq")]
    finally:
        con.close()
