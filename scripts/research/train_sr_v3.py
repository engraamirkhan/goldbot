"""Train sr-v3 on history: Stage 0 -> walk-forward fit of every built learner -> training report, for every unit
(goldbot/research/sr_v3_train.py; card docs/research/prereg-2026-11-26-sr-v3.md).

    python scripts/research/train_sr_v3.py --stage0-only            # label-blind ladder and body choice per unit
    python scripts/research/train_sr_v3.py --units 1m,5m,15m --workers 4       # fit-only (the default)
    python scripts/research/train_sr_v3.py --units 4h --read                    # + read statistics (refused unless
                                                                                #   the read is gate_eligible at ub95)

Phases: (1) per unit, in parallel: Stage 0 from the outcome-blind skeleton and weights, then one streamed pass over
learn 2010-17 (moments, priors, the thinned tree sample); (2) HB, one solve per fold over every unit's (tf, class)
cells; (3) per unit, in parallel: the fold fits, OOF scoring, v0 artifacts, the report and one run manifest per
artifact (research/run_manifest.py; rebuild with scripts/research/rebuild_model.py). Outputs go to
--out (default ~/tmp/sr-v3, outside the repo: market-derived, never committed, ADR 0048): <unit>/stage0.json,
report.md, report.json, oof_scores.parquet (sealed: scores only), manifests/ and one JSON artifact per learner;
oof.parquet only under --read; index.json.

Fit-only is the default (quant H2 on #488): artifacts and hash-sealed OOF scores, no S, kept mean, S at 2x cost, D or
other read statistic, in the files or on stdout. `--read` raises unless every label load was gate_eligible and the
rows are costed at the ub95 cost of record (--cost-manifest: rt_bp = max(W2, ecr ub95) per instrument x session x
tf at the worse broker, the instrument's own swap; a row with no cell is refused).

Refusals, by design:
- labels are read only through dense_labels.load_labels on the learn window: a sealed store (ADR 0085 A4 not recorded,
  PR #482) raises SealedStore and the run stops;
- quant sequencing 2026-11-26 (binding): no sr-v3 label value or Stage 0 statistic until #482 (A4), #484 (X6), the
  rev 8 rv_ratio amendment and price_action_x6.X6_MERGED_UTC are all on main; `refuse_before_x6` checks each and the
  script refuses to run (Stage 0 included) while any is missing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data import holdout_guard
from goldbot.research import learners as L
from goldbot.research import sr_v3 as V
from goldbot.research import sr_v3_train as T

ROOT = Path(__file__).resolve().parents[2]
RHO = ROOT / "docs" / "research" / "rho-measurement-2026-11.json"
COSTS = Path.home() / "tmp" / "sr-v3-trainsets" / "manifest.json"     # the data team's cost of record (H1), local


def refuse_before_x6() -> None:
    """Quant sequencing (binding, 2026-11-26): no sr-v3 label value or Stage 0 statistic before #482 (A4), #484 (X6),
    the rev 8 rv_ratio amendment and price_action_x6.X6_MERGED_UTC are all on main. The store refuses each read on its
    own (`sr_v3_train.refuse_unsequenced`); this stops the run before any phase starts."""
    if gaps := T.sequencing_gaps():
        raise SystemExit("refused: quant sequencing 2026-11-26 - no sr-v3 label value or Stage 0 statistic until all "
                         "are on main: " + "; ".join(gaps))


def _store(a: argparse.Namespace) -> T.StoreData:
    return T.StoreData(labels=Path(a.labels), features=Path(a.features), tiers_path=Path(a.tiers),
                       ledger=Path(a.ledger), rho=Path(a.rho),
                       costs=T.CostTable.from_manifest(a.cost_manifest) if a.cost_manifest else None)


def phase1(unit: str, a: argparse.Namespace) -> dict[str, Any]:
    tfs = V.UNITS[unit]
    store = _store(a)
    members = [m for m in a.members.split(",") if m]
    events = {}
    for inst in members:
        ev = [store.events(inst, tf, (2010, 2011, 2012, 2013)) for tf in tfs]
        ev = [e for e in ev if len(e)]
        if ev:
            events[inst] = pd.concat(ev, ignore_index=True)
    out = Path(a.out) / unit
    out.mkdir(parents=True, exist_ok=True)
    s0 = T.stage0(unit, tfs, events, rho_file=Path(a.rho))
    (out / "stage0.json").write_text(json.dumps({"stage0": s0.model_dump(mode="json"),
                                                 "inputs": {"rho_sha256": V.file_sha256(a.rho),
                                                            "tiers_sha256": V.file_sha256(a.tiers),
                                                            "store": store.used}}, indent=1, default=str))
    if a.stage0_only or s0.status != "fit":
        return {"unit": unit, "status": s0.status, "level": s0.level, "body": s0.body}
    plan = T.plan_from_stage0(s0)
    col = T.collect(store, plan, seed=T.SEEDS[0])
    save_collected(col, out)
    (out / "plan.json").write_text(json.dumps(plan.__dict__, indent=1, default=str))
    return {"unit": unit, "status": "collected", "level": s0.level, "body": s0.body, "rows": col.n_rows}


def save_collected(col: T.Collected, out: Path) -> None:
    col.sample.to_parquet(out / "sample.parquet", index=False)
    blob = {"impute": col.impute.tolist(), "impute_lcw": col.impute_lcw.tolist(), "pi": col.pi, "n_rows": col.n_rows,
            "lcw": {str(y): {k: m.model_dump() for k, m in d.items()} for y, d in col.lcw.items()},
            "hb": {str(y): {f"{k[0]}|{k[1]}": m.model_dump() for k, m in d.items()} for y, d in col.hb.items()},
            "priors": {str(y): p.__dict__ for y, p in col.priors.items()},
            # quant H5 on #488: phase 3 records these in every manifest (it recorded the learn end without them)
            "train_end": col.train_end.isoformat() if col.train_end is not None else None,
            "label_end_max": col.label_end_max.isoformat() if col.label_end_max is not None else None}
    (out / "collected.json").write_text(json.dumps(blob))


def load_collected(out: Path) -> T.Collected:
    b = json.loads((out / "collected.json").read_text())
    sample = pd.read_parquet(out / "sample.parquet")
    holdout_guard.refuse(sample, what=f"sr-v3 tree sample {out}", col="decision_utc")
    holdout_guard.refuse(sample, what=f"sr-v3 tree sample {out} (label horizon)", col="horizon_end_utc")
    pri = {}
    for y, d in b["priors"].items():
        acc = T.PriorAcc()
        acc.stop, acc.n, acc.held, acc.full = d["stop"], d["n"], d["held"], d["full"]
        pri[int(y)] = acc
    return T.Collected(impute=np.asarray(b["impute"]), impute_lcw=np.asarray(b["impute_lcw"]),
                       lcw={int(y): {k: L.Moments(**m) for k, m in d.items()} for y, d in b["lcw"].items()},
                       hb={int(y): {(k.split("|")[0], k.split("|")[1]): L.Moments(**m) for k, m in d.items()}
                           for y, d in b["hb"].items()},
                       priors=pri, sample=sample, pi=float(b["pi"]), n_rows=int(b["n_rows"]),
                       train_end=pd.Timestamp(b["train_end"]) if b.get("train_end") else None,
                       label_end_max=pd.Timestamp(b["label_end_max"]) if b.get("label_end_max") else None)


def _plan(out: Path) -> T.UnitPlan:
    p = json.loads((out / "plan.json").read_text())
    return T.UnitPlan(unit=p["unit"], tfs=tuple(p["tfs"]), members=tuple(p["members"]),
                      lcw_columns=tuple(p["lcw_columns"]), first_oof=int(p["first_oof"]), lam_a=float(p["lam_a"]),
                      design_effect=float(p["design_effect"]), stage0=json.loads(json.dumps(p["stage0"])))


def phase2(units: list[str], a: argparse.Namespace) -> dict[int, L.HbArtifact]:
    """HB per fold over every collected unit's cells (each cell's imputation and DEFF from its own unit)."""
    moms: dict[int, dict[tuple[str, str], L.Moments]] = {}
    imp: dict[tuple[str, str], np.ndarray] = {}
    deff: dict[tuple[str, str], float] = {}
    lam_a: dict[str, float] = {}
    hb_idx = [V.P30.index(c) for c in T.HB_COLUMNS]
    for u in units:
        out = Path(a.out) / u
        if not (out / "collected.json").exists():
            continue
        col, plan = load_collected(out), _plan(out)
        lam_a[u] = plan.lam_a
        for y, cells in col.hb.items():
            for k, m in cells.items():
                moms.setdefault(y, {})[k] = m
                imp[k], deff[k] = col.impute[hb_idx], plan.design_effect
    hb = {}
    for y, cells in moms.items():
        hb[y] = T.hb_fit(cells, {k: imp[k] for k in cells}, {k: deff[k] for k in cells}, lam_a)
        (Path(a.out) / "_hb").mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "_hb" / f"fold_{y}.json").write_text(L.artifact_json(hb[y]))
    return hb


def phase3(unit: str, a: argparse.Namespace) -> dict[str, Any]:
    out = Path(a.out) / unit
    if not (out / "collected.json").exists():
        return {"unit": unit, "status": "skipped"}
    hb = {int(p.stem.split("_")[1]): L.load_artifact(p.read_text()) for p in sorted((Path(a.out) / "_hb").glob("*.json"))}
    store, plan = _store(a), _plan(out)
    res = T.fit_unit(store, plan, hb_by_fold=hb, col=load_collected(out), read=a.read)   # type: ignore[arg-type]
    hb_plans = [_plan(Path(a.out) / u) for u in a.units.split(",") if (Path(a.out) / u / "plan.json").exists()]
    tiers = Path(a.tiers)
    mans = T.record_unit(res, plan, seeds=T.SEEDS, data_version=store.version(plan.members, plan.tfs, (0,)),
                         source={"kind": "store", "labels": str(a.labels), "features": str(a.features),
                                 "tiers_file": tiers.name},
                         data_root=tiers.parent, data_files=[tiers.name], hb_plans=hb_plans,
                         root=Path(a.out) / "manifests")
    # quant H5 on #488: the manifests are recorded BEFORE the unit's outputs are written, and the report names them,
    # so no artifact on disk lacks its run record
    res.report["manifests"] = {k: m.manifest_id for k, m in mans.items()}
    T.write_unit(res, Path(a.out))
    return {"unit": unit, "status": "fit", "mode": res.report["mode"], "gate_eligible": res.report["gate_eligible"],
            "artifacts": res.report.get("artifacts")}           # never a read statistic on stdout (quant H3)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--units", default=",".join(V.UNITS))
    ap.add_argument("--members", default=",".join(V.POOLED_MEMBERS))
    ap.add_argument("--labels", default=str(Path.home() / "tmp" / "dense-labels"))
    ap.add_argument("--features", default=str(Path.home() / "tmp" / "feature-store"))
    ap.add_argument("--tiers", default=str(ROOT / "raw" / "tiers" / "tiers_served_r11-v2.1.csv"))
    ap.add_argument("--rho", default=str(RHO))
    ap.add_argument("--ledger", default=str(T.default_ledger()),
                    help="the read-once / consumption ledger load_labels appends to (#482); default beside the "
                         "research registry, the file the X6 guard reads (quant H4 on #488)")
    ap.add_argument("--cost-manifest", default=str(COSTS) if COSTS.exists() else None,
                    help="manifest whose `costs` names the per-cell cost table and swap table (sha-checked); without "
                         "it rows are costed at the W2 point and --read is refused (quant H1 on #488)")
    ap.add_argument("--out", default=str(T.OUT))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--stage0-only", action="store_true")
    ap.add_argument("--read", action="store_true",
                    help="compute the read statistics (refused unless the label read is gate_eligible at ub95)")
    return ap


def main() -> int:
    a = parser().parse_args()
    refuse_before_x6()
    units = [u for u in a.units.split(",") if u]
    bad = [u for u in units if u not in V.UNITS]
    if bad:
        raise SystemExit(f"unknown units {bad}; one of {list(V.UNITS)}")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    with ProcessPoolExecutor(max_workers=max(1, a.workers)) as ex:
        p1 = list(ex.map(phase1, units, [a] * len(units)))
    print(json.dumps(p1, indent=1, default=str), flush=True)
    if a.stage0_only:
        return 0
    phase2(units, a)
    with ProcessPoolExecutor(max_workers=max(1, a.workers)) as ex:
        p3 = list(ex.map(phase3, units, [a] * len(units)))
    idx = {"units": p1, "fits": p3, "settings": T.settings(), "code": T.code_version()}
    (Path(a.out) / "index.json").write_text(json.dumps(idx, indent=1, default=str))
    print(json.dumps(p3, indent=1, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
