"""The gate-read cost check (quant 1-2 on PR #489): a learn read that computes outcome statistics (`seq1.read`,
`rl_exits.run`) is charged at ONE frozen D-3 cost of record (`srv3_cost_record`, version "srv3-cost-..."), never at
the w2-point floor of an unpriced label and never at a simulator constant.

A module of its own, importing nothing from goldbot: decision_cost is in dense_labels' CODE_FILES (a change there
re-keys every built label partition), and seq1 is on the obj-v1 guarded list (no path to dense_labels or
srv3_cost_record, which reach sklearn.metrics)."""
from __future__ import annotations

from typing import Any

import numpy as np

COST_RECORD_PREFIX = "srv3-cost-"   # every frozen D-3 cost of record (srv3_cost_record); w2-point is a floor, never one


class CostNotOfRecord(PermissionError):
    """A gate read whose rows are not all charged at one frozen D-3 cost of record."""


def is_of_record(versions: Any) -> np.ndarray:
    """Per row: charged at a cost of record."""
    return np.array([str(x).startswith(COST_RECORD_PREFIX) for x in np.asarray(versions, dtype=object).ravel()],
                    dtype=bool)


def require_cost_of_record(versions: Any, what: str) -> str:
    """The one cost version every row of a gate read was charged at; refuses None (no costs), an empty read, any row
    whose `cost_version` is not a recorded cost of record (w2-point is the floor of an unpriced label, never a gate
    read) and a read mixing versions."""
    if versions is None:
        raise CostNotOfRecord(f"{what}: no costs: a gate read is charged at the D-3 cost of record")
    v = [str(x) for x in np.asarray(versions, dtype=object).ravel()]
    if not v:
        raise CostNotOfRecord(f"{what}: no rows")
    bad = sorted({x for x in v if not x.startswith(COST_RECORD_PREFIX)})
    if bad:
        raise CostNotOfRecord(f"{what}: rows at {bad[:3]} are not charged at a cost of record ({COST_RECORD_PREFIX}*)")
    if len(set(v)) > 1:
        raise CostNotOfRecord(f"{what}: one read, one cost: {sorted(set(v))[:3]}")
    return v[0]


__all__ = ["COST_RECORD_PREFIX", "CostNotOfRecord", "is_of_record", "require_cost_of_record"]
