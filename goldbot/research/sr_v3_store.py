"""The sr-v3 trainer's data access and read refusals (split from sr_v3_train so its fit path stays guarded; ADR 0108):
the dl-v1 label store, the fs-v1 feature store and the uniqueness weights, read ONLY through
`dense_labels.load_labels` on the learn window, and every refusal in front of a label read:
- quant sequencing on #482 (`sequencing_gaps`, `SequencingRefused`);
- the read-once booking of each unit's learn read, by a read only (quant H3/N1 on #488: `book_learn_read`,
  `require_booked`, family `sr-v3:<unit>` in windows.READ_ONCE_FAMILIES), in the ledger beside the research registry
  (H4, `default_ledger`); fit-only label loads are ledgered by load_labels' consumption rows only;
- load_labels' gate status per load, kept for the report and every manifest (H2).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.research import decision_cost as DC
from goldbot.research import sr_v3 as V
from goldbot.research import windows as WN
from goldbot.research.sr_v3_train import (
    MANIFEST_KEY,
    NOT_READ,
    ROW_COLS,
    W2_COST_VERSION,
    CostTable,
    cost_parts,
)


class ReadNotBooked(PermissionError):
    """A label read of a unit whose learn read this run has not booked in the read-once ledger (quant H3 on #488)."""


class SequencingRefused(PermissionError):
    """An sr-v3 label value or Stage 0 statistic was asked for before the quant sequencing holds."""


def sequencing_gaps() -> list[str]:
    """Quant sequencing on #482 (binding, 2026-11-26): "no sr-v3 label-value read until all three are on main: (1) #484
    merged; (2) its one-line follow-up sets `X6_MERGED_UTC`; (3) the rev 8 card amendment (rv_ratio replacing
    atr_ratio_14_100 in core order) merged", with #482 (ADR 0085 A4) before any read. Returns what is still missing;
    empty means the store may be read. The trainer's core order must also equal dense_labels' (one card 3.1 order)."""
    import importlib

    from goldbot.research import dense_labels as D
    gaps = []
    if not D.RECORDED_TIERS_SHA256:
        gaps.append("ADR 0085 A4 tiers sha (PR #482)")
    try:
        x6 = importlib.import_module("goldbot.features.price_action_x6")
    except ImportError:
        gaps.append("block X6 (PR #484): goldbot/features/price_action_x6.py")
    else:
        if getattr(x6, "X6_MERGED_UTC", None) is None:
            gaps.append("price_action_x6.X6_MERGED_UTC is unset (the one-line follow-up after rev 8 merges)")
    if D.CORE12[3:4] != ("rv_ratio",):
        gaps.append("the rev 8 amendment: dense_labels.CORE12 position 4 is not rv_ratio")
    if tuple(D.CORE12) != V.CORE_12:
        gaps.append("sr_v3.CORE_12 differs from dense_labels.CORE12 (card 3.1 has one core order)")
    return gaps


def refuse_unsequenced(what: str) -> None:
    """Raise `SequencingRefused` naming every gap while `sequencing_gaps` is not empty."""
    if gaps := sequencing_gaps():
        raise SequencingRefused(f"refused {what}: quant sequencing 2026-11-26, no sr-v3 label value or Stage 0 "
                                "statistic until all are on main: " + "; ".join(gaps))


def learn_family(unit: str) -> str:
    """The read-once family of one sr-v3 unit's learn-label read (windows.READ_ONCE_FAMILIES)."""
    return f"sr-v3:{unit}"


def book_learn_read(ledger: Path | str, unit: str, key: str, *, freeze: Mapping[str, Any] | None = None) -> None:
    """Quant N1 on #488: a READ books the unit's learn read (`windows.record_read_once`, family `sr-v3:<unit>`) at the
    start of phase 3, before its first label read, keyed on the frozen fit (`read_key`). Once per unit: a second read,
    under any key, raises `AlreadyRead`. A fit-only run never books (its label loads are ledgered by load_labels'
    consumption rows only), so refits after a spec or code change never burn the read."""
    from goldbot.research import dense_labels as D
    WN.record_read_once(ledger, learn_family(unit), WN.Window.LEARN,
                        ref={"procedure": V.SPEC_VERSION, "unit": unit, "manifest_key": MANIFEST_KEY, "read_key": key,
                             "label_spec_hash": D.spec_hash(), **dict(freeze or {})})   # rev 9 D1 "Freeze"


def read_key(frozen: Mapping[str, Any]) -> str:
    """The key of a read: the frozen fit-only run's artifact shas and sealed OOF scores sha, and the label spec hash
    (quant N1 on #488)."""
    from goldbot.research import dense_labels as D
    blob = {"artifacts": dict(sorted((frozen.get("artifacts") or {}).items())),
            "oof_scores_sha256": frozen.get("oof_scores_sha256"), "label_spec_hash": D.spec_hash()}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


def require_booked(ledger: Path | str, unit: str | None, key: str | None) -> None:
    """A read's label load refuses unless this read (`key`) booked its unit's learn read in `ledger`."""
    fam = learn_family(unit) if unit else None
    if fam is None or key is None or not any(
            r.get("family") == fam and r.get("window") == WN.Window.LEARN.value
            and (r.get("ref") or {}).get("read_key") == key for r in WN.read_ledger(ledger)):
        raise ReadNotBooked(f"refused the read's label load: unit {unit!r} read {key!r} has no learn booking in "
                            f"{ledger} (book_learn_read at the start of phase 3; quant N1 on #488)")


def _tier_free(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.drop(columns=[c for c in ("tier", "usable") if c in rows.columns])


def prefix_usability(rows: pd.DataFrame, tiers: Any, columns: Sequence[str] = V.P30) -> pd.DataFrame:
    """Rev 9 D1 (label-blind): `usable` = the label window alone (`attach_tiers(columns=["side"])`, side reads no
    history) and `ok:<column>` = that column's own span, per column of `columns`. A row is usable under a column set
    iff `usable` and every `ok:` of the set (`sr_v3_train.usable_under`): attach_tiers' conjunction over columns
    (ADR 0104 4), so every ladder prefix is masked without a label value."""
    from goldbot.research import dense_labels as D
    base = _tier_free(rows)
    out = base.assign(usable=D.attach_tiers(base, tiers, columns=["side"])["usable"].to_numpy(bool))
    for c in columns:
        if c != "side":
            out[f"ok:{c}"] = D.attach_tiers(base, tiers, columns=[c])["usable"].to_numpy(bool)
    return out


def p30_mask(rows: pd.DataFrame, tiers: Any) -> np.ndarray:
    """Rev 9 quant C3: the P30 mask of HB, G-cw and AVG, by `attach_tiers(columns=P30)` on rows already loaded under
    the served columns, never by a second label load."""
    from goldbot.research import dense_labels as D
    return D.attach_tiers(_tier_free(rows), tiers, columns=V.P30)["usable"].to_numpy(bool)


def default_ledger() -> Path:
    """`windows.READS_LEDGER` beside the research registry (`catalogue.run.default_registry`, GOLDBOT_REGISTRY or the
    main checkout's state/): the one ledger every sr-v3 label read and booking is written to."""
    from goldbot.research.catalogue.run import default_registry
    return default_registry().with_name(WN.READS_LEDGER)



def cost_table(manifest: Path | str) -> CostTable:
    """The trainer's cost table from a cost manifest. A version recorded under srv3_cost_record (D-3) charges gold rows
    the record's own `gold_row_bp` (files verified from their bytes); it needs the manifest's gold_spread_table, else
    refused (F6 on #515). An unrecorded version keeps the builder's legacy charge and is never a gate read."""
    import dataclasses
    import functools

    from goldbot.research import srv3_cost_record as CR
    table = CostTable.from_manifest(manifest)
    if table.version not in CR.recorded():
        return table
    if "gold_spread_table" not in table.paths or table.gold_spread is None:
        raise CR.CostRecordRefused(f"cost {table.version} is recorded but the manifest has no gold_spread_table: the "
                                   "per-row gold charge of record cannot be made (F6 on #515): refused")
    rec = CR.verify(table.version, table.paths)
    # a partial of a module-level function, not a lambda: the table stays picklable (code review)
    return dataclasses.replace(table, gold_charge=functools.partial(CR.gold_row_bp, rec=rec,
                                                                    spread=table.gold_spread))

def charge_of_record(lab: pd.DataFrame, meta: dict[str, Any], costs: CostTable | None, instrument: str, tf: str
                     ) -> tuple[pd.DataFrame, dict[str, Any]]:
    """`lab` (a `load_labels` frame of one instrument x tf) charged at the D-3 cost of record and its gate status
    re-derived (`srv3_cost_record.apply`, F6 on #515), when `costs` is a recorded version; otherwise unchanged
    (w2-point rows keep the D-3 block). The one charging route of every history label read (the sr-v3 trainer, and
    learn_labels for SEQ-1 / RL-X1): a second copy could drift from the record."""
    from goldbot.research import dense_labels as D
    from goldbot.research import srv3_cost_record as CR
    if costs is None or costs.version not in CR.recorded():
        return lab, meta
    rows = lab.assign(instrument=instrument, tf=tf)
    return CR.apply(rows, meta, costs.version, costs.paths, rt_bp=costs.rt_bp(rows), spec=D.SPEC)


@dataclass
class StoreData:
    """The local dl-v1 label store, the fs-v1 feature store and the uniqueness weights (scripts/research/
    build_dense_labels.py). Labels are read ONLY by `dense_labels.load_labels` on the learn window, purpose "train",
    columns P30 (one row set for every learner, so paired reads are on the same rows); a sealed store raises
    `dense_labels.SealedStore` and the run stops (card ruling 5, ADR 0085 A4). Stage 0 reads the outcome-blind
    skeleton only. Partition keys come from the manifests under the current spec and code hashes; two candidates for
    one (year, phase) refuse (prune the stale one)."""
    labels: Path
    features: Path
    tiers_path: Path
    weights: Path | None = None
    rho: Path | None = None                              # the D-12 rho file Stage 0 read (in the data version, H5)
    costs: CostTable | None = None                       # the per-cell cost of record (H1); None = w2-point
    unit: str | None = None                              # the unit and read whose learn read is booked (N1)
    read_key: str | None = None
    read: bool = False                                   # a read's store: every label load needs the booking
    # rev 9 quant C3: the unit's narrowest served column set, the ONE column set load_labels is called with
    # (() = intercept-only: the label window alone); set from the Stage 0 plan before any label load
    columns: tuple[str, ...] | None = None
    # the read-once / consumption ledger load_labels appends to (#482), beside the research registry: the file the X6
    # legitimacy guard reads, also from a worktree (which has no state/ of its own; quant H4 on #488)
    ledger: Path = field(default_factory=lambda: default_ledger())
    used: dict[str, Any] = field(default_factory=dict)
    _cache: dict[tuple[str, str, tuple[int, ...]], pd.DataFrame] = field(default_factory=dict, repr=False)

    def _tiers(self, instrument: str) -> Any:
        from goldbot.research import feature_store as F
        return F.Tiers.from_csv(self.tiers_path, source="dukascopy" if instrument == V.GOLD else "histdata")

    def keys(self, instrument: str, tf: str, year: int, phase: int) -> tuple[str, str] | None:
        from goldbot.research import dense_labels as D
        d = Path(self.labels) / D.SPEC.spec_id / instrument / tf / f"p{phase}" / str(year)
        want_s, want_c = D.spec_hash(), D.code_hash()
        mans = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
        cands = [m for m in mans if m.get("spec_hash") == want_s and m.get("code_hash") == want_c]
        if not cands:
            return None
        if len(cands) > 1:
            raise ValueError(f"dl-v1 {instrument} {tf} p{phase} {year}: {len(cands)} partitions under this spec and "
                             "code; prune the stale one so the read is unambiguous")
        return str(cands[0]["key"]), str(cands[0]["fs_key"])

    def _charge_of_record(self, lab: pd.DataFrame, meta: dict[str, Any], instrument: str, tf: str
                          ) -> tuple[pd.DataFrame, dict[str, Any]]:
        return charge_of_record(lab, meta, self.costs, instrument, tf)

    def _weights_path(self, instrument: str, tf: str) -> Path:
        return Path(self.weights or Path(self.labels) / "_weights") / instrument / f"{tf}.parquet"

    def _weights(self, instrument: str, tf: str) -> pd.DataFrame:
        from goldbot.data import holdout_guard
        from goldbot.research import dense_labels as D
        p = self._weights_path(instrument, tf)
        self.used.setdefault("weights", {})[f"{instrument}:{tf}"] = V.file_sha256(p)
        w = D._typed(pd.read_parquet(p))[["phase", "decision_utc", "u_tf", "w"]]
        return holdout_guard.cut(w, what=f"sr-v3 weights {instrument} {tf}", col="decision_utc")

    def events(self, instrument: str, tf: str, years: Sequence[int]) -> pd.DataFrame:
        """Stage 0's label-blind events: the skeleton's T2 long rows (one per bar), with u, the label window's
        usability and each P30 column's (`prefix_usability`, rev 9 D1).
        Refused while `sequencing_gaps` is not empty (no Stage 0 statistic before the quant sequencing holds)."""
        from goldbot.research import dense_labels as D
        refuse_unsequenced(f"Stage 0 events {instrument} {tf}")
        ks = {(y, 0): k[0] for y in years if (k := self.keys(instrument, tf, y, 0)) is not None}
        if not ks:
            return pd.DataFrame(columns=["instrument", "tf", "phase", "decision_utc", "usable", "u"])
        sk = D.skeleton(self.labels, instrument=instrument, tf=tf, years=sorted(y for y, _ in ks), keys=ks)
        sk = sk[(sk["template"] == V.JUDGED_HEAD) & (sk["side"] == 1)].reset_index(drop=True)
        sk = prefix_usability(sk, self._tiers(instrument))
        w = self._weights(instrument, tf)
        ev = sk.merge(w, on=["phase", "decision_utc"], how="left")
        self.used.setdefault("stage0_keys", {})[f"{instrument}:{tf}"] = {f"{y}:{p}": k for (y, p), k in ks.items()}
        ok = [c for c in ev.columns if str(c).startswith("ok:")]
        return ev.assign(u=ev["u_tf"].fillna(0.0), usable=ev["usable"] & ev["label_ok"])[
            ["instrument", "tf", "phase", "decision_utc", "usable", "u", *ok]]

    def frame(self, instrument: str, tf: str, year: int, *, phases: Sequence[int] = (0,)) -> pd.DataFrame:
        """One year of the instrument x tf's learn rows. The labels are loaded ONCE per (instrument, tf, phases) for
        every learn year (#482: a consumption id procedure/instrument/tf/purpose is fixed by its first load's columns
        and keys), then sliced; one (instrument, tf) is held at a time."""
        k = (instrument, tf, tuple(sorted(int(p) for p in phases)))
        if k not in self._cache:
            self._cache.clear()
            self._cache[k] = self._learn_frame(instrument, tf, k[2])
        f = self._cache[k]
        if not len(f):
            return f
        yr = pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True)).year.to_numpy()
        return f[yr == year].reset_index(drop=True)

    def _learn_frame(self, instrument: str, tf: str, phases: Sequence[int]) -> pd.DataFrame:
        from goldbot.research import dense_labels as D
        refuse_unsequenced(f"the label read {instrument} {tf}")
        if self.read:
            require_booked(self.ledger, self.unit, self.read_key)
        from goldbot.research import feature_store as F
        from goldbot.research.self_retrain import design
        ks = {(y, int(p)): k for y in V.LEARN_YEARS for p in phases
              if (k := self.keys(instrument, tf, y, int(p))) is not None}
        if not ks:
            return pd.DataFrame(columns=list(ROW_COLS))
        if self.columns is None:
            raise ValueError("StoreData.columns is unset: the label load names the unit's served columns (rev 9 C3)")
        tiers = self._tiers(instrument)
        years = sorted({y for y, _ in ks})
        # #482 (quant L1): every load is a ledgered consumption row with procedure "sr-v3", which the X6 guard reads
        lab, meta = D.load_labels(self.labels, procedure=V.SPEC_VERSION, ledger=self.ledger, instrument=instrument,
                                  tf=tf, years=years, tiers=tiers, columns=list(self.columns) or ["side"],
                                  keys={k: v[0] for k, v in ks.items()},
                                  window=WN.SPANS[WN.Window.LEARN], purpose="train", phases=list(phases),
                                  templates=("T0", V.JUDGED_HEAD))
        lab, meta = self._charge_of_record(lab, meta, instrument, tf)
        feats, fmeta = F.load(self.features, instrument=instrument, tf=tf, years=years, tiers=tiers,
                              phases=list(phases), purpose="train", expect_keys={k: v[1] for k, v in ks.items()})
        self.used.setdefault("labels", {})[f"{instrument}:{tf}"] = {
            "keys": meta["keys"], "tiers_sha256": meta["tiers_sha256"], "rows": meta["rows"],
            "dropped_tier": meta.get("dropped_tier"), "fs_keys": fmeta["keys"],
            # quant A4 on #482: load_labels' gate status of this read, kept for the report and every manifest
            "gate_eligible": meta.get("gate_eligible") is True, "gate_block": list(meta.get("gate_block") or [])}
        t2 = lab[lab["template"] == V.JUDGED_HEAD]
        t2 = t2.assign(p30_ok=p30_mask(t2, tiers) if len(t2) else np.zeros(0, bool))
        t0 = lab[lab["template"] == "T0"][["phase", "decision_utc", "side", "gross_r_raw"]].rename(
            columns={"gross_r_raw": "t0_gross"})
        rows = t2.merge(t0, on=["phase", "decision_utc", "side"], how="inner")
        rows = rows.merge(feats.drop(columns=[c for c in ("instrument", "tf", "source", "bid_only", "group_id",
                                                           "available_utc", "window_from_utc", "tier")
                                              if c in feats.columns]), on=["phase", "decision_utc"], how="inner")
        if not len(rows):
            return pd.DataFrame(columns=list(ROW_COLS))
        rows = rows.merge(self._weights(instrument, tf)[["phase", "decision_utc", "w"]], on=["phase", "decision_utc"],
                          how="inner")
        if not len(rows):
            return pd.DataFrame(columns=list(ROW_COLS))
        X = design(rows)
        parts = cost_parts(rows.assign(instrument=instrument, tf=tf), self.costs)
        out = pd.DataFrame({"instrument": instrument, "tf": tf, "phase": rows["phase"].to_numpy(),
                            "decision_utc": rows["decision_utc"].to_numpy(),
                            "horizon_end_utc": rows["horizon_end_utc"].to_numpy(), "side": rows["side"].to_numpy(),
                            "gross": rows["gross_r_raw"].to_numpy(float), "t0_gross": rows["t0_gross"].to_numpy(float),
                            "stop": DC.is_stop(rows), "nights_held": DC.nights_held(rows),
                            "w": rows["w"].to_numpy(float), "entry": rows["entry"].to_numpy(float),
                            "base_dist": rows["base_dist"].to_numpy(float),
                            "p30_ok": rows["p30_ok"].to_numpy(bool)})
        out = pd.concat([out, parts.reset_index(drop=True), X[[c for c in V.P30 if c != "side"]].reset_index(drop=True)], axis=1)
        return out[list(ROW_COLS)]

    def gate_status(self) -> dict[str, Any]:
        """Eligible only when every label load so far was gate_eligible (quant A4 on #482); before any load, not."""
        loads = self.used.get("labels") or {}
        if not loads:
            return {"gate_eligible": False, "gate_block": list(NOT_READ["gate_block"])}
        block = sorted({f"{k}: {b}" for k, v in loads.items() for b in v.get("gate_block") or []}
                       | {f"{k}: no gate status" for k, v in loads.items() if v.get("gate_eligible") is not True
                          and not v.get("gate_block")})
        return {"gate_eligible": not block, "gate_block": block}

    def cost_version(self) -> str:
        return self.costs.version if self.costs is not None else W2_COST_VERSION

    def version(self, members: Sequence[str], tfs: Sequence[str], phases: Sequence[int]) -> str:
        """The data identity a rebuild needs: label spec and code hashes, tiers sha, every partition key used, and (quant
        H5 on #488) the uniqueness-weights file sha per member x tf and the rho file sha."""
        from goldbot.research import dense_labels as D
        keys = {f"{i}:{t}:{y}:{p}": self.keys(i, t, y, p) for i in members for t in tfs for y in V.LEARN_YEARS
                for p in phases}
        wp = {f"{i}:{t}": self._weights_path(i, t) for i in members for t in tfs}
        weights = {k: V.file_sha256(p) if p.exists() else None for k, p in sorted(wp.items())}
        blob = {"spec": D.spec_hash(), "code": D.code_hash(), "tiers": V.file_sha256(self.tiers_path), "keys": keys,
                "weights": weights, "rho": V.file_sha256(self.rho) if self.rho else None,
                "costs": {"version": self.cost_version(), **(dict(self.costs.files) if self.costs else {})}}
        return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()

    def manifest(self) -> dict[str, Any]:
        from goldbot.research import dense_labels as D
        return {"source": "dl-v1 + fs-v1 stores", "label_spec_hash": D.spec_hash(), "label_code_hash": D.code_hash(),
                "cost_version": self.cost_version(),
                "tiers_sha256": V.file_sha256(self.tiers_path), "used": self.used}


__all__ = ["p30_mask", "prefix_usability", "ReadNotBooked", "SequencingRefused", "StoreData", "book_learn_read", "charge_of_record", "default_ledger", "learn_family",
           "read_key", "refuse_unsequenced", "require_booked", "sequencing_gaps"]
