"""The frozen ub95 cost of record per class for history label reads (sr-v3 card D-3: W2-2, A9; ADR 0098 R-H).

Why: dense labels are GROSS. `dense_labels.label_rows` stamps every row `cost_version` "w2-point" (the W2 point
round trip, a floor), and `dense_labels.gate_status` refuses those rows as a gate read until D-3 freezes the cost of
record (quant A4 on #482). The label store is not rebuilt to change a cost: a reader charges each row the recorded
cost of record at read time, and `apply` re-stamps the rows it charged (version + per-row round trip in bp), then
re-runs the quant's own `gate_status` unchanged.

Frozen means (all four, checked here):
1. a record file `config/research/srv3_cost_record/<version>.yaml`: version = "srv3-cost-" + content sha256[:16], the
   sha256 of each table file (cost, swap, gold spread; the files stay local, market-derived), the ECR version and its
   sha256, `charge_rule` ub95, the W2 floor, the history months (none in the holdout) and every gold cell's value;
2. the record's version and content sha256 on an ADR 0098 R-H "Recorded" line (a test reads the ADR);
3. the reader's table files hash to the record (`verify`, after `sr_v3_train.CostTable.from_manifest` has hashed the
   bytes against the manifest);
4. per class: only instruments whose class basis is "measured" (gold: broker fills, ECR) are charged and lifted; a
   "prior" (FX10, silver: no fills yet) keeps the D-3 block, with the instrument named.

Label-blind: the record is built from broker fills and quoted spreads only; `apply` never reads a label value.
"""
from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml

from goldbot.base import FrozenRecord
from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D

ROOT = Path(__file__).resolve().parents[2]
RECORD_DIR = ROOT / "config" / "research" / "srv3_cost_record"
TABLE_FILES: tuple[str, ...] = ("cost_table", "swap_table", "gold_spread_table")
W2_POINT = "w2-point"
CELL_TOL_BP = 1e-6                     # the record keeps 6 decimals


class CostRecordRefused(PermissionError):
    """A cost version, table file or record that is not the frozen, recorded cost of record."""


class GoldCell(FrozenRecord):
    broker: str
    session: str
    tf: str
    rt_bp: float
    ecr_ub95_usd: float
    measured_n: int
    basis: str


class ClassEntry(FrozenRecord):
    instrument: str
    asset_class: str
    basis: Literal["measured", "prior"]


class CostRecord(FrozenRecord):
    version: str
    content_sha256: str
    frozen_utc: str
    frozen_by: str
    builder: str
    built_utc: str
    charge_rule: Literal["ub95"]
    w2_bp: float
    overshoot_bp: float
    rule: str
    files: dict[str, str]
    ecr_version: str
    ecr_sha256: str
    ecr_window_utc: tuple[str, str]
    ecr_min_fills: int
    history_months: tuple[str, str]
    commission_rt_usd_per_oz: dict[str, float]
    classes: tuple[ClassEntry, ...]
    gold_cells: tuple[GoldCell, ...]

    def measured(self) -> frozenset[str]:
        """Instruments charged at a measured ub95 cost of record (a gate read on cost)."""
        return frozenset(c.instrument for c in self.classes if c.basis == "measured")

    def class_of(self, instrument: str) -> ClassEntry | None:
        return next((c for c in self.classes if c.instrument == instrument), None)


@functools.cache
def _load(record_dir: Path) -> dict[str, CostRecord]:
    out: dict[str, CostRecord] = {}
    for p in sorted(record_dir.glob("*.yaml")):
        rec = CostRecord.model_validate(yaml.safe_load(p.read_text()))
        if p.stem != rec.version or rec.version != "srv3-cost-" + rec.content_sha256[:16]:
            raise ValueError(f"{p.name}: file name, version and content sha256 disagree")
        out[rec.version] = rec
    return out


def recorded() -> dict[str, CostRecord]:
    """Every frozen cost of record, by version."""
    return dict(_load(RECORD_DIR))


def verify(version: str, files: Mapping[str, str]) -> CostRecord:
    """The recorded cost of record `version`, if every table file sha256 the reader hashed is the record's; refuses
    otherwise (an unrecorded version is never a gate read, whatever its numbers)."""
    rec = recorded().get(version)
    if rec is None:
        raise CostRecordRefused(f"cost version {version} is not recorded in {RECORD_DIR.relative_to(ROOT)} (D-3)")
    bad = [k for k in TABLE_FILES if files.get(k) != rec.files[k]]
    if bad:
        raise CostRecordRefused(f"cost {version}: {', '.join(bad)} sha256 is not the record's: refused")
    return rec


def check_cells(cost_table: pd.DataFrame, rec: CostRecord) -> list[str]:
    """The gold cells of a built cost table against the record: '' list when equal, else one entry per drift."""
    g = cost_table[cost_table["instrument"].astype(str) == "XAUUSD"]
    have = {(str(r.broker), str(r.session), str(r.tf)): r for r in g.itertuples()}
    out = []
    for c in rec.gold_cells:
        r = have.get((c.broker, c.session, c.tf))
        if r is None:
            out.append(f"{c.broker}:{c.session}:{c.tf}: missing")
            continue
        out += [f"{c.broker}:{c.session}:{c.tf}: {k}" for k in ("rt_bp", "ecr_ub95_usd")
                if not np.isclose(float(getattr(r, k)), getattr(c, k), rtol=0, atol=CELL_TOL_BP)]
    return out


def apply(labels: pd.DataFrame, meta: Mapping[str, Any], rec: CostRecord, *, rt_bp: np.ndarray,
          spec: D.LabelSpec = D.SPEC) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Charge `labels` (a `load_labels` frame) the recorded cost of record and re-derive the gate status.

    `rt_bp` is the per-row round trip the reader charges under `rec` (sr_v3_train.CostTable.rt_bp on these rows):
    finite and at least the W2 floor on every measured-class row. Rows of a measured class take
    cost_version = rec.version and cost_rt_bp = rt_bp; prior-class rows keep "w2-point" and their D-3 block, which
    names the instrument. The h_ref (D-4) block is untouched. Returns new frames; the inputs are not mutated."""
    if recorded().get(rec.version) != rec:
        raise CostRecordRefused(f"cost {rec.version}: not the recorded record (D-3)")
    rt = np.asarray(rt_bp, dtype=float)
    if rt.shape != (len(labels),):
        raise ValueError(f"rt_bp has {rt.shape} values for {len(labels)} label rows")
    inst = labels["instrument"].astype(str).to_numpy()
    meas = np.isin(inst, sorted(rec.measured()))
    if meas.any() and not (np.isfinite(rt[meas]).all() and (rt[meas] >= DC.RT_BP - 1e-12).all()):
        raise ValueError(f"cost {rec.version}: a measured row's rt_bp is not finite or is below the W2 floor "
                         f"({DC.RT_BP} bp): not a cost of record")
    out = labels.copy()
    out.loc[meas, "cost_version"] = rec.version
    out.loc[meas, "cost_rt_bp"] = rt[meas]
    status = D.gate_status(out, spec)
    prior = sorted(set(inst[~meas & (out["cost_version"].astype(str).to_numpy() == W2_POINT)]))
    block = list(status["gate_block"]) + [
        f"{i}: class {c.asset_class if (c := rec.class_of(i)) else 'unrecorded'} has a "
        f"{c.basis if c else 'no'} cost (prior), no measured cost of record in {rec.version} (D-3)" for i in prior]
    m = {**meta, "gate_eligible": not block, "gate_block": block,
         "cost_of_record": {"version": rec.version, "content_sha256": rec.content_sha256,
                            "measured": sorted(rec.measured() & set(inst)), "rows_charged": int(meas.sum()),
                            "rows_prior": int((~meas).sum())}}
    return out, m


__all__ = ["RECORD_DIR", "TABLE_FILES", "ClassEntry", "CostRecord", "CostRecordRefused", "GoldCell", "apply",
           "check_cells", "recorded", "verify"]
