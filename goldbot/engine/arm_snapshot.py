"""The last good exploration arm rows (BACKLOG 117; trading-safety reviews of f838b7d3 and 43a00d17, 2026-11-13).

Rule: "a bad exploration arm row must not stop the engine (exits keep running)", and a config defect never closes a
healthy position. So every arm row the engine loads cleanly (the ARM-SPEC and the discovered arms, a hash mismatch
aside: an unfrozen row is never good) is remembered in aux.db `arm_spec_last_good` (migration 29), upserted by arm id
in one transaction and never deleted; every account's engine on the host shares the table, and SQLite's write lock
keeps concurrent saves from losing a row. When a row later fails validation, shares its id, names a bad twin, goes
missing, or the whole file is unreadable, `exploration.config_for` keeps that arm from here with its entries refused
(`INVALID_RULE`), as an arm with a hash mismatch is kept: it stays configured, so its open positions keep their
stop, target, hold and every exit. A registered arm with a bad row and no last good row, or an unreadable ARM-SPEC
with none remembered, refuses the start (fail loud); the broker's own stops and targets still stand.

Persisted rows are read tolerantly (ADR 0010, `load_record_json`): a row that no longer validates is left out and
logged. Never raises."""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

from goldbot.base import load_record_json
from goldbot.db import Database
from goldbot.research.arms import Arm

log = logging.getLogger(__name__)

INVALID_RULE = "arm row invalid"       # an arm kept from its last good row: entries refused, every exit managed
_UPSERT = ("INSERT INTO arm_spec_last_good (arm_id, updated_ns, body) VALUES (?, ?, ?) "
           "ON CONFLICT(arm_id) DO UPDATE SET updated_ns = excluded.updated_ns, body = excluded.body "
           "WHERE arm_spec_last_good.body != excluded.body")


def remember(state_dir: str | Path, arms: Iterable[Arm]) -> None:
    """Upserts the rows of `arms` (each loaded cleanly) by arm id in one transaction of `state_dir`'s aux.db; a row
    already there is replaced by its newer one, none is ever removed. Logged, never raised."""
    now = time.time_ns()
    rows = [(a.id, now, a.model_dump_json(exclude={"entry_refused"}, exclude_defaults=True)) for a in arms]
    try:
        with Database.open(state_dir, "aux").write() as conn:
            conn.executemany(_UPSERT, rows)
    except (sqlite3.Error, OSError) as exc:
        log.error("last good arm rows not saved in %s (%s): the previous ones stay", state_dir, exc)


def recall(state_dir: str | Path) -> dict[str, Arm] | None:
    """The remembered arms by id; None when none is remembered or the table cannot be read. A row that no longer
    validates is left out and logged."""
    try:
        with Database.open(state_dir, "aux").read() as conn:
            rows = conn.execute("SELECT arm_id, body FROM arm_spec_last_good").fetchall()
    except (sqlite3.Error, OSError) as exc:
        log.error("last good arm rows unreadable in %s (%s)", state_dir, exc)
        return None
    out: dict[str, Arm] = {}
    for arm_id, body in rows:
        try:
            out[arm_id] = load_record_json(Arm, body, source="arm_spec_last_good")
        except ValueError as exc:
            log.error("last good row of arm %s left out: %s", arm_id, exc)
    return out or None


def refused(arm: Arm, why: str) -> Arm:
    """`arm` (its last good row) with its entries refused because its current row is bad (`why`)."""
    return arm.model_copy(update={"entry_refused": f"{INVALID_RULE}: {why}; the last good row is kept, entries "
                                                   f"refused, every exit managed"})
