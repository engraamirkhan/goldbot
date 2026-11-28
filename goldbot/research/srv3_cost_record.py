"""The frozen ub95 cost of record per class for history label reads (sr-v3 card D-3: W2-2, A9; ADR 0098 R-H).

Why: dense labels are GROSS. `dense_labels.label_rows` stamps every row `cost_version` "w2-point" (the W2 point
round trip, a floor), and `dense_labels.gate_status` refuses those rows as a gate read until D-3 freezes the cost of
record (quant A4 on #482). The label store is not rebuilt to change a cost: a reader charges each row the recorded
cost of record at read time, and `apply` re-stamps the rows it charged (version + per-row round trip in bp), then
re-derives the gate status.

Frozen means (all checked here):
1. a record file `config/research/srv3_cost_record/<version>.yaml`: version = "srv3-cost-" + content sha256[:16], the
   sha256 of each table file (cost, swap, gold spread; the files stay local, market-derived), the ECR version and its
   sha256, `charge_rule` ub95, the W2 floor, the history months (none in the holdout) and every gold cell's value
   (rt_bp, ECR ub95 USD, slip_x USD);
2. the record's version and content sha256 on an ADR 0098 R-H "Recorded" line (a test reads the ADR);
3. `apply` itself hashes the table files' bytes against the record (`verify`, from paths: never a caller's hash or
   frame), reads the gold spread from the verified file, and stamps a measured row only when its charge equals the
   record's own `gold_row_bp` (quant F1, R1 on #515; a stress charge is run unstamped, never as the record);
4. per class: only instruments whose class basis is "measured" (gold: broker fills, ECR) are lifted; every
   other row keeps the D-3 block, whatever its cost_version (quant F2);
5. gold 1m/5m rows stay blocked until the D-15 decision-to-fill wait cost is pinned (`wait_charged_tfs`, quant F3).

The per-row gold charge of record (`gold_row_bp`; sr_v3_train.CostTable._gold_row_bp and sr_v3_cost_table.row_rt_bp
must equal it): per broker, max(cell rt_bp, ECR ub95 / entry, (the era spread ub95 of the session's latest complete
month + commission + slip_x) / entry) in bp, the worse broker, floored at W2. slip_x = max(0, ECR charged - spread -
commission) of the charged ECR cell (quant F4): the era term carries slippage, not spread and commission alone.

Label-blind: the record is built from broker fills and quoted spreads only; `apply` never reads a label value.
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml

from goldbot.base import FrozenRecord
from goldbot.data import holdout_guard
from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D

ROOT = Path(__file__).resolve().parents[2]
RECORD_DIR = ROOT / "config" / "research" / "srv3_cost_record"
TABLE_FILES: tuple[str, ...] = ("cost_table", "swap_table", "gold_spread_table")
W2_POINT = "w2-point"
D3_BLOCK = "cost_version w2-point: the ub95 cost of record per cell is not frozen (D-3)"   # dense_labels.gate_status
WAIT_TFS: tuple[str, ...] = ("1m", "5m")       # card 4 F3: gated 1m/5m charge the decision-to-fill wait (D-15)
CELL_TOL = 1e-6                                # the record keeps 6 decimals
GOLD = "XAUUSD"


class CostRecordRefused(PermissionError):
    """A cost version, table file, record or charge that is not the frozen, recorded cost of record."""


class GoldCell(FrozenRecord):
    broker: str
    session: str
    tf: str
    rt_bp: float
    ecr_ub95_usd: float
    slip_x_usd: float
    measured_n: int
    basis: str


class ClassEntry(FrozenRecord):
    instrument: str
    asset_class: str
    basis: Literal["measured", "prior"]


class CostRecord(FrozenRecord):
    version: str
    content_sha256: str          # the builder's sha over the three tables + commission (`content_sha256`)
    record_sha256: str           # this record's own canonical content (`record_sha256`): an in-place edit is refused
    frozen_utc: str
    frozen_by: str
    builder: str
    wait_charged_tfs: tuple[str, ...]
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

    def cells(self) -> pd.DataFrame:
        return pd.DataFrame([c.model_dump() for c in self.gold_cells])


def content_sha256(cost: pd.DataFrame, swap: pd.DataFrame, spread: pd.DataFrame,
                   commission_rt_usd_per_oz: Mapping[str, float]) -> str:
    """The builder's content sha256 (scripts/research/sr_v3_cost_table.py, #512): the three tables' row hashes and the
    round-trip commission per broker. Recomputed from the parsed file bytes by `verify`."""
    h = hashlib.sha256()
    for f in (cost, swap, spread):
        h.update(pd.util.hash_pandas_object(f, index=False).to_numpy().tobytes())
    h.update(json.dumps(dict(commission_rt_usd_per_oz), sort_keys=True).encode())
    return h.hexdigest()


def record_sha256(rec: CostRecord) -> str:
    """sha256 of the record's canonical JSON without version, content_sha256 and record_sha256 (code review 1 on
    #515): the version names the tables; this pins every other field of the record file."""
    body = rec.model_dump(mode="json", exclude={"version", "content_sha256", "record_sha256"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@functools.cache
def _load(record_dir: Path, stamp: tuple[tuple[str, str], ...]) -> dict[str, CostRecord]:
    out: dict[str, CostRecord] = {}
    for name, _ in stamp:
        p = record_dir / name
        rec = CostRecord.model_validate(yaml.safe_load(p.read_text()))
        if p.stem != rec.version or rec.version != DC.COST_RECORD_PREFIX + rec.content_sha256[:16]:
            raise CostRecordRefused(f"{p.name}: file name, version and content sha256 disagree")
        if record_sha256(rec) != rec.record_sha256:
            raise CostRecordRefused(f"{p.name}: record_sha256 {rec.record_sha256[:12]} is not the record's content "
                                    f"({record_sha256(rec)[:12]}): edited in place, refused")
        if rec.w2_bp != DC.RT_BP:
            raise CostRecordRefused(f"{p.name}: w2_bp {rec.w2_bp} is not the W2 floor {DC.RT_BP}")
        out[rec.version] = rec
    return out


def recorded() -> dict[str, CostRecord]:
    """Every frozen cost of record, by version."""
    stamp = tuple((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(RECORD_DIR.glob("*.yaml")))
    return dict(_load(RECORD_DIR, stamp))              # re-read whenever a record file's bytes change


def _verified(version: str, paths: Mapping[str, Path | str]) -> tuple[CostRecord, dict[str, pd.DataFrame]]:
    rec = recorded().get(version)
    if rec is None:
        raise CostRecordRefused(f"cost version {version} is not recorded in {RECORD_DIR} (D-3)")
    blobs = {k: Path(paths[k]).read_bytes() for k in TABLE_FILES if k in paths and Path(paths[k]).is_file()}
    bad = [k for k in TABLE_FILES if k not in blobs or hashlib.sha256(blobs[k]).hexdigest() != rec.files[k]]
    if bad:
        raise CostRecordRefused(f"cost {version}: {', '.join(bad)} sha256 is not the record's: refused")
    frames = {k: pd.read_parquet(io.BytesIO(b)) for k, b in blobs.items()}     # parsed from the hashed bytes
    got = content_sha256(frames["cost_table"], frames["swap_table"], frames["gold_spread_table"],
                         rec.commission_rt_usd_per_oz)
    if got != rec.content_sha256:
        raise CostRecordRefused(f"cost {version}: the files' content sha256 {got[:12]} is not the record's: refused")
    drift = check_cells(frames["cost_table"], rec)
    if drift:
        raise CostRecordRefused(f"cost {version}: the record's gold cells are not the cost table's: {drift[:5]}")
    _check_spread_sessions(frames["gold_spread_table"], rec)
    # the spread months are history only: a month that starts inside the sealed holdout is refused (holdout seal)
    sp = frames["gold_spread_table"]
    holdout_guard.refuse(pd.DataFrame({"ts_utc": pd.to_datetime(sp["month"].astype(str) + "-01", utc=True)}),
                         what=f"cost {version} gold spread months")
    return rec, frames


def verify(version: str, paths: Mapping[str, Path | str]) -> CostRecord:
    """The recorded cost of record `version`, if every table file at `paths` hashes (its bytes, read once here) to
    the record's sha256, the parsed tables reproduce its content sha256 and gold cells, and the spread table's sessions
    are the cells'; refuses otherwise (an unrecorded version is never a gate read, whatever its numbers)."""
    return _verified(version, paths)[0]


def _check_spread_sessions(spread: pd.DataFrame, rec: CostRecord) -> None:
    want = {c.session for c in rec.gold_cells}
    have = set(spread["session"].astype(str))
    if have != want:
        raise CostRecordRefused(f"cost {rec.version}: spread table sessions {sorted(have)} are not the cells' "
                                f"{sorted(want)}: refused")


def check_cells(cost_table: pd.DataFrame, rec: CostRecord) -> list[str]:
    """The gold cells of a built cost table against the record: [] when equal, else one entry per drift."""
    g = cost_table[cost_table["instrument"].astype(str) == GOLD]
    have = {(str(r.broker), str(r.session), str(r.tf)): r for r in g.itertuples()}
    out = []
    for c in rec.gold_cells:
        r = have.get((c.broker, c.session, c.tf))
        if r is None:
            out.append(f"{c.broker}:{c.session}:{c.tf}: missing")
            continue
        out += [f"{c.broker}:{c.session}:{c.tf}: {k}" for k in ("rt_bp", "ecr_ub95_usd", "slip_x_usd")
                if not np.isclose(float(getattr(r, k)), getattr(c, k), rtol=0, atol=CELL_TOL)]
    return out


def _sessions(rows: pd.DataFrame) -> np.ndarray:
    from goldbot.data.calendar import DEFAULT_SESSIONS
    dec = pd.DatetimeIndex(pd.to_datetime(rows["decision_utc"], utc=True))
    nat = np.asarray(dec.isna(), dtype=bool)
    if nat.any():
        raise CostRecordRefused(f"{int(nat.sum())} rows have a NaT decision_utc: no session, no charge")
    return np.asarray(DEFAULT_SESSIONS.session_label(dec)).astype(str) if len(dec) else np.array([], str)


def _cell_terms(rows: pd.DataFrame, rec: CostRecord, broker: str) -> pd.DataFrame:
    cells = rec.cells()
    key = pd.DataFrame({"session": _sessions(rows), "tf": rows["tf"].astype(str).to_numpy()})
    c = key.merge(cells[cells["broker"] == broker][["session", "tf", "rt_bp", "ecr_ub95_usd", "slip_x_usd"]],
                  on=["session", "tf"], how="left")
    if c["rt_bp"].isna().any():
        miss = sorted({f"{s}:{t}" for s, t in zip(c["session"], c["tf"], strict=True)} - {
            f"{x.session}:{x.tf}" for x in rec.gold_cells})
        raise CostRecordRefused(f"cost {rec.version}: no gold cell for {miss[:5]} (session:tf)")
    return c


def charge_floor(rows: pd.DataFrame, rec: CostRecord) -> np.ndarray:
    """DIAGNOSTIC only (never the gate; R1): the record's cell and ECR terms without the era term: max over brokers of max(cell rt_bp,
    ECR ub95 / entry x 1e4) (a NaN entry leaves the cell rt_bp), floored at W2."""
    entry = rows["entry"].to_numpy(float)
    per = []
    for b in sorted(rec.commission_rt_usd_per_oz):
        c = _cell_terms(rows, rec, b)
        per.append(np.fmax(c["rt_bp"].to_numpy(float), c["ecr_ub95_usd"].to_numpy(float) / entry * 1e4))
    return np.maximum(rec.w2_bp, np.maximum.reduce(per)) if per else np.full(len(rows), rec.w2_bp)


def gold_row_bp(rows: pd.DataFrame, rec: CostRecord, spread: pd.DataFrame) -> np.ndarray:
    """The per-row gold charge of record (module docstring). `spread`: session, available_utc, ub95_usd (the gold
    spread table); joined as-of on `available_utc`, so a row never sees its own month or later (point in time)."""
    entry = rows["entry"].to_numpy(float)
    sess = _sessions(rows)
    _check_spread_sessions(spread, rec)
    dec = pd.to_datetime(rows["decision_utc"], utc=True).to_numpy()
    sp = np.full(len(rows), np.nan)
    for s_name, g in spread.sort_values("available_utc").groupby("session"):
        m = sess == s_name
        if not m.any():
            continue
        av = pd.to_datetime(g["available_utc"], utc=True).to_numpy()
        i = np.searchsorted(av, dec[m], side="right") - 1
        v = g["ub95_usd"].to_numpy(float)
        sp[m] = np.where(i >= 0, v[np.clip(i, 0, None)], np.nan)
    gap = ~np.isfinite(sp)                     # code review 2 on #515: never let nanmax drop the era term
    if gap.any():
        raise CostRecordRefused(f"cost {rec.version}: {int(gap.sum())} gold rows have no finite era spread ub95 "
                                f"(no complete prior month, or a NaN month), first decision_utc "
                                f"{pd.DatetimeIndex(pd.to_datetime(rows['decision_utc'], utc=True))[gap][0]}: "
                                "refused")
    per = []
    for b, comm in sorted(rec.commission_rt_usd_per_oz.items()):
        c = _cell_terms(rows, rec, b)
        era = (sp + comm + c["slip_x_usd"].to_numpy(float)) / entry * 1e4
        per.append(np.nanmax(np.vstack([c["rt_bp"].to_numpy(float),
                                        c["ecr_ub95_usd"].to_numpy(float) / entry * 1e4, era]), axis=0))
    return np.asarray(np.maximum(rec.w2_bp, np.maximum.reduce(per)))


def apply(labels: pd.DataFrame, meta: Mapping[str, Any], version: str, paths: Mapping[str, Path | str], *,
          rt_bp: np.ndarray, spec: D.LabelSpec = D.SPEC) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Charge `labels` (a `load_labels` frame) under the recorded cost of record `version` and re-derive the gate.

    `paths` are the table files (cost_table, swap_table, gold_spread_table) the reader charged from; `apply` hashes
    them itself and reads the spread from the verified file. `rt_bp` is the reader's per-row round trip
    (CostTable.rt_bp on these rows). Refused: an unrecorded version or other bytes (`verify`), a malformed charge, and
    any measured row whose charge is not the record's `gold_row_bp` within 1e-6 bp (higher too). Measured-class rows take cost_version = version and cost_rt_bp =
    rt_bp; every other row keeps its own and is blocked (D-3, named). Gold 1m/5m rows are blocked until their wait cost
    is pinned (D-15). Every existing block but the w2-point D-3 one is kept. The inputs are not mutated."""
    rec, frames = _verified(version, paths)
    if not rec.measured() <= {GOLD}:
        raise CostRecordRefused(f"cost {version}: measures {sorted(rec.measured())}; only gold has a per-row charge "
                                "of record (gold_row_bp): refused")
    rt = np.asarray(rt_bp, dtype=float)
    if rt.shape != (len(labels),):
        raise ValueError(f"rt_bp has {rt.shape} values for {len(labels)} label rows")
    inst = labels["instrument"].astype(str).to_numpy()
    meas = np.isin(inst, sorted(rec.measured()))
    if meas.any():
        r = rt[meas]
        if not np.isfinite(r).all():
            raise ValueError(f"cost {version}: a measured row's rt_bp is not finite")
        want = gold_row_bp(labels[meas], rec, frames["gold_spread_table"])
        off = ~np.isclose(r, want, rtol=0, atol=CELL_TOL)
        if off.any():
            raise CostRecordRefused(f"cost {version}: {int(off.sum())} measured rows differ from the record's charge, "
                                    f"largest gap {float(np.max(np.abs(r - want)[off])):.6f} bp: refused (a stress "
                                    "charge runs unstamped)")
    out = labels.copy()
    out.loc[meas, "cost_version"] = version
    out.loc[meas, "cost_rt_bp"] = rt[meas]
    block = [b for b in meta.get("gate_block") or [] if b != D3_BLOCK]
    block += [b for b in D.gate_status(out, spec)["gate_block"] if b not in block]
    block += [f"{i}: class {c.asset_class if (c := rec.class_of(i)) else 'unrecorded'} has a "
              f"{c.basis if c else 'no'} cost (prior), no measured cost of record in {version} (D-3)"
              for i in sorted(set(inst[~meas]))]
    tfs = labels["tf"].astype(str).to_numpy()
    block += [f"{GOLD} {tf}: decision-to-fill wait cost not in {version} (D-15)" for tf in WAIT_TFS
              if tf not in rec.wait_charged_tfs and (meas & (inst == GOLD) & (tfs == tf)).any()]
    m = {**meta, "gate_eligible": not block, "gate_block": block,
         "cost_of_record": {"version": version, "content_sha256": rec.content_sha256,
                            "measured": sorted(rec.measured() & set(inst)), "rows_charged": int(meas.sum()),
                            "rows_prior": int((~meas).sum())}}
    return out, m


__all__ = ["content_sha256", "record_sha256", "D3_BLOCK", "RECORD_DIR", "TABLE_FILES", "WAIT_TFS", "ClassEntry", "CostRecord", "CostRecordRefused",
           "GoldCell", "apply", "charge_floor", "check_cells", "gold_row_bp", "recorded", "verify"]
