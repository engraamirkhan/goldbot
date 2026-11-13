"""The last good exploration arm rows (BACKLOG 117; trading-safety review of f838b7d3, 2026-11-13).

Rule: "a bad exploration arm row must not stop the engine (exits keep running)", and a config defect never closes a
healthy position. An arm is closed as an orphan only when it has never been in any good spec. So every arm row that
loads cleanly (the ARM-SPEC and the discovered arms) is remembered here, under the engine's state dir, merged by id
and never removed, with an atomic write (`goldbot.base.write_atomic`). When a row later fails validation, shares its
id, names a bad twin, goes missing, or the whole file is unreadable, `exploration.config_for` keeps that arm from
this file with its entries refused (`INVALID_RULE`), as an arm with a hash mismatch is kept: it stays configured,
so its open positions keep their stop, target, hold and every exit. An unreadable ARM-SPEC with no snapshot refuses
the start (fail loud); the broker's own stops and targets still stand.

A copy of config rows, read tolerantly (ADR 0010, `load_record`): a row that no longer validates is left out and
logged. Never raises."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from goldbot.base import load_record, write_atomic
from goldbot.research.arms import Arm

log = logging.getLogger(__name__)

NAME = "arm_spec_last_good.yaml"       # under the engine's state dir (services/engine.py)
INVALID_RULE = "arm row invalid"       # an arm kept from the snapshot: entries refused, every exit managed


def path_for(state_dir: str | Path) -> Path:
    return Path(state_dir) / NAME


def _rows(path: Path) -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text()) or {}
    return {str(r["id"]): r for r in raw.get("arms") or [] if isinstance(r, dict) and r.get("id")}


def remember(path: Path, arms: Iterable[Arm]) -> None:
    """Merges the rows of `arms` (each loaded cleanly) into the snapshot by id; an arm already there is replaced by
    its newer row, none is ever removed. Written only on a change. Logged, never raised."""
    try:
        rows = _rows(path) if path.exists() else {}
    except (yaml.YAMLError, OSError, AttributeError, TypeError) as exc:
        log.error("arm snapshot %s unreadable (%s): rewritten from this load", path, exc)
        rows = {}
    changed = not path.exists()
    for a in arms:
        row = a.model_dump(mode="json", exclude={"entry_refused"}, exclude_defaults=True)
        if rows.get(a.id) != row:
            rows[a.id], changed = row, True
    if not changed:
        return
    try:
        write_atomic(path, yaml.safe_dump({"arms": [rows[k] for k in sorted(rows)]}, sort_keys=False))
    except OSError as exc:
        log.error("arm snapshot %s not written (%s): the previous one stays", path, exc)


def recall(path: Path) -> dict[str, Arm] | None:
    """The remembered arms by id; None when there is no snapshot or it cannot be read. A row that no longer
    validates is left out and logged."""
    if not path.exists():
        return None
    try:
        rows = _rows(path)
    except (yaml.YAMLError, OSError, AttributeError, TypeError) as exc:
        log.error("arm snapshot %s unreadable (%s)", path, exc)
        return None
    out: dict[str, Arm] = {}
    for arm_id, row in rows.items():
        try:
            out[arm_id] = load_record(Arm, row, source="arm_snapshot")
        except ValueError as exc:
            log.error("arm snapshot row %s left out: %s", arm_id, exc)
    return out


def refused(arm: Arm, why: str) -> Arm:
    """`arm` (its last good row) with its entries refused because its current row is bad (`why`)."""
    return arm.model_copy(update={"entry_refused": f"{INVALID_RULE}: {why}; the last good row is kept, entries "
                                                   f"refused, every exit managed"})
