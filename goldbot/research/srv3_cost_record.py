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

Measured FX (record schema 2, backlog 280; U2 card prereg-2026-11-28-u2-pooled-body.md section 3, P3): the same rule
per FX instrument, in bp of the entry (`fx_row_bp`): per broker, max(cell rt_bp, ECR ub95 bp, the era term = Dukascopy
bid+ask spread ub95 of the session's latest complete month / entry + the broker's commission bp + slip_x bp of the
charged ECR cell), the worse broker, floored at W2. A month whose quote basis is not bid+ask (HistData FX is bid-only)
has no measured spread and is refused, never imputed. An FX instrument is measured only when BOTH brokers have at least
`ecr_min_fills` fills in at least one session (`session_n`): "the measured broker is never charged alone"; fills sent
after `FILL_CUTOFF_UTC` are not counted (the builder, goldbot.research.fx_cost_cells, and `fill_cutoff_utc` here). FX
swap per side per rollover from the broker's symbol swap spec captured point in time, triple on its triple day
(`fx_swap_bp`); a credit is never banked. A dry-run record (`dry_run`) is never of record. Schema 1 (gold's
srv3-cost-2b3628372a9b3bfa) is byte-identical: the schema-2 fields are outside its self-hash and must be absent.

Staleness (backlog 280(b)): the as-of spread row of a charged row must be available no more than `STALE_DAYS` (40)
days before its decision: a live-era row against a table that stopped months earlier is refused, not charged an old
era. History rows always see the previous month (at most 31 days), so no history charge changes.

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
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.data import holdout_guard
from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D

ROOT = Path(__file__).resolve().parents[2]
RECORD_DIR = ROOT / "config" / "research" / "srv3_cost_record"
TABLE_FILES: tuple[str, ...] = ("cost_table", "swap_table", "gold_spread_table")
TABLE_FILES_V2: tuple[str, ...] = (*TABLE_FILES, "fx_spread_table", "fx_swap_spec")   # schema 2 (measured FX)
FILL_CUTOFF_UTC = "2026-12-12T00:00:00+00:00"  # U2 card P3: fills dated after 2026-12-11 23:59 UTC are not counted
STALE_DAYS = 40                                # 280(b): an as-of spread row older than this is refused
BID_ASK = "bid_ask"                            # the only quote basis that measures a spread
V2_FIELDS = frozenset({"schema_version", "dry_run", "fill_cutoff_utc", "fx_commission_rt_bp", "fx_cells"})
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


class FxCell(FrozenRecord):
    """One measured FX instrument x broker x session x tf cell, in bp of the fill price (scale-free across eras)."""
    instrument: str
    broker: str
    session: str
    tf: str
    rt_bp: float
    ecr_ub95_bp: float
    slip_x_bp: float
    measured_n: int              # fills in the cell it charges (tf, or the session `all` cell)
    session_n: int               # fills in the broker x session `all` cell (membership, card P3)
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
    # schema 2 (measured FX, backlog 280); a schema-1 record leaves them at their defaults (outside its self-hash)
    schema_version: Literal[1, 2] = 1
    dry_run: bool = False
    fill_cutoff_utc: str | None = None
    fx_commission_rt_bp: dict[str, dict[str, float]] = Field(default_factory=dict)
    fx_cells: tuple[FxCell, ...] = ()

    def measured(self) -> frozenset[str]:
        """Instruments charged at a measured ub95 cost of record (a gate read on cost)."""
        return frozenset(c.instrument for c in self.classes if c.basis == "measured")

    def class_of(self, instrument: str) -> ClassEntry | None:
        return next((c for c in self.classes if c.instrument == instrument), None)

    def cells(self) -> pd.DataFrame:
        return pd.DataFrame([c.model_dump() for c in self.gold_cells])

    def measured_fx(self) -> frozenset[str]:
        return frozenset(c.instrument for c in self.classes if c.basis == "measured" and c.asset_class == "fx")

    def fx_frame(self) -> pd.DataFrame:
        return pd.DataFrame([c.model_dump() for c in self.fx_cells], columns=list(FxCell.model_fields))

    def table_files(self) -> tuple[str, ...]:
        return TABLE_FILES_V2 if self.schema_version == 2 else TABLE_FILES


def content_sha256(cost: pd.DataFrame, swap: pd.DataFrame, spread: pd.DataFrame,
                   commission_rt_usd_per_oz: Mapping[str, float]) -> str:
    """The builder's content sha256 (scripts/research/sr_v3_cost_table.py, #512): the three tables' row hashes and the
    round-trip commission per broker. Recomputed from the parsed file bytes by `verify`."""
    h = hashlib.sha256()
    for f in (cost, swap, spread):
        h.update(pd.util.hash_pandas_object(f, index=False).to_numpy().tobytes())
    h.update(json.dumps(dict(commission_rt_usd_per_oz), sort_keys=True).encode())
    return h.hexdigest()


def content_sha256_v2(frames: Mapping[str, pd.DataFrame], commission_rt_usd_per_oz: Mapping[str, float],
                      fx_commission_rt_bp: Mapping[str, Mapping[str, float]]) -> str:
    """Schema 2's content sha256: the five tables' row hashes (TABLE_FILES_V2 order) and both commissions."""
    h = hashlib.sha256()
    for k in TABLE_FILES_V2:
        h.update(pd.util.hash_pandas_object(frames[k], index=False).to_numpy().tobytes())
    h.update(json.dumps({"gold": dict(commission_rt_usd_per_oz),
                         "fx": {i: dict(v) for i, v in fx_commission_rt_bp.items()}}, sort_keys=True).encode())
    return h.hexdigest()


def record_sha256(rec: CostRecord) -> str:
    """sha256 of the record's canonical JSON without version, content_sha256 and record_sha256 (code review 1 on
    #515): the version names the tables; this pins every other field of the record file. Schema 1 hashes without the
    schema-2 fields (gold's record is unchanged); `_load` refuses a schema-1 record that sets any of them."""
    drop = {"version", "content_sha256", "record_sha256"} | (set(V2_FIELDS) if rec.schema_version == 1 else set())
    body = rec.model_dump(mode="json", exclude=drop)
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@functools.cache
def _load(record_dir: Path, stamp: tuple[tuple[str, str], ...]) -> dict[str, CostRecord]:
    out: dict[str, CostRecord] = {}
    for name, _ in stamp:
        p = record_dir / name
        rec = CostRecord.model_validate(yaml.safe_load(p.read_text()))
        if p.stem != rec.version or rec.version != "srv3-cost-" + rec.content_sha256[:16]:
            raise CostRecordRefused(f"{p.name}: file name, version and content sha256 disagree")
        if record_sha256(rec) != rec.record_sha256:
            raise CostRecordRefused(f"{p.name}: record_sha256 {rec.record_sha256[:12]} is not the record's content "
                                    f"({record_sha256(rec)[:12]}): edited in place, refused")
        if rec.w2_bp != DC.RT_BP:
            raise CostRecordRefused(f"{p.name}: w2_bp {rec.w2_bp} is not the W2 floor {DC.RT_BP}")
        _check_schema(rec, p.name)
        out[rec.version] = rec
    return out


def _check_schema(rec: CostRecord, name: str) -> None:
    """Schema 1: no schema-2 field. Schema 2: not a dry run, the card's fill cut-off, and every measured FX
    instrument measured at BOTH brokers (each with >= ecr_min_fills fills in at least one session) with a complete
    cell grid and a commission per broker; a prior FX instrument has no cell (card P3)."""
    if rec.schema_version == 1:
        default = CostRecord.model_fields
        set_ = [k for k in sorted(V2_FIELDS - {"schema_version"})
                if getattr(rec, k) != (default[k].get_default(call_default_factory=True))]
        if set_:
            raise CostRecordRefused(f"{name}: a schema 1 record carries schema-2 fields {set_}: refused")
        return
    if rec.dry_run:
        raise CostRecordRefused(f"{name}: a dry run is never of record (built before the fill cut-off)")
    if rec.fill_cutoff_utc is None or pd.Timestamp(rec.fill_cutoff_utc) > pd.Timestamp(FILL_CUTOFF_UTC):
        raise CostRecordRefused(f"{name}: fill_cutoff_utc {rec.fill_cutoff_utc} is not at or before the card's "
                                f"{FILL_CUTOFF_UTC}: refused")
    brokers = set(rec.commission_rt_usd_per_oz)
    grid = {(b, c.session, c.tf) for b in brokers for c in rec.gold_cells}
    fx = rec.fx_frame()
    stray = sorted({str(x) for x in fx["instrument"]} - rec.measured_fx())
    if stray:
        raise CostRecordRefused(f"{name}: {stray} have FX cells but are not measured fx classes: refused")
    for i in sorted(rec.measured_fx()):
        f = fx[fx["instrument"] == i]
        have = set(zip(f["broker"], f["session"], f["tf"], strict=True))
        if set(f["broker"]) != brokers or set(rec.fx_commission_rt_bp.get(i, {})) != brokers or have != grid:
            raise CostRecordRefused(f"{name}: {i} is not measured at both brokers {sorted(brokers)} on the full "
                                    "session x tf grid with a commission each (the measured broker is never "
                                    "charged alone): refused")
        thin = [b for b in sorted(brokers) if int(f.loc[f["broker"] == b, "session_n"].max()) < rec.ecr_min_fills]
        if thin:
            raise CostRecordRefused(f"{name}: {i} has fewer than {rec.ecr_min_fills} fills in every session at "
                                    f"{thin}: it stays prior (card P3)")


def recorded() -> dict[str, CostRecord]:
    """Every frozen cost of record, by version."""
    stamp = tuple((p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(RECORD_DIR.glob("*.yaml")))
    return dict(_load(RECORD_DIR, stamp))              # re-read whenever a record file's bytes change


def _verified(version: str, paths: Mapping[str, Path | str]) -> tuple[CostRecord, dict[str, pd.DataFrame]]:
    rec = recorded().get(version)
    if rec is None:
        raise CostRecordRefused(f"cost version {version} is not recorded in {RECORD_DIR} (D-3)")
    want = rec.table_files()
    blobs = {k: Path(paths[k]).read_bytes() for k in want if k in paths and Path(paths[k]).is_file()}
    bad = [k for k in want if k not in blobs or hashlib.sha256(blobs[k]).hexdigest() != rec.files.get(k)]
    if bad:
        raise CostRecordRefused(f"cost {version}: {', '.join(bad)} sha256 is not the record's: refused")
    frames = {k: pd.read_parquet(io.BytesIO(b)) for k, b in blobs.items()}     # parsed from the hashed bytes
    got = (content_sha256_v2(frames, rec.commission_rt_usd_per_oz, rec.fx_commission_rt_bp)
           if rec.schema_version == 2 else
           content_sha256(frames["cost_table"], frames["swap_table"], frames["gold_spread_table"],
                          rec.commission_rt_usd_per_oz))
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
    if rec.schema_version == 2:
        fdrift = check_fx_cells(frames["cost_table"], rec)
        if fdrift:
            raise CostRecordRefused(f"cost {version}: the record's fx cells are not the cost table's: {fdrift[:5]}")
        fs = frames["fx_spread_table"]
        holdout_guard.refuse(pd.DataFrame({"ts_utc": pd.to_datetime(fs["month"].astype(str) + "-01", utc=True)}),
                             what=f"cost {version} fx spread months")
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


def check_fx_cells(cost_table: pd.DataFrame, rec: CostRecord) -> list[str]:
    """The FX cells of a built cost table against a schema-2 record: [] when equal, else one entry per drift."""
    if rec.fx_cells and "ecr_ub95_bp" not in cost_table:
        return ["cost table has no ecr_ub95_bp column: missing every fx cell"]
    have = {(str(r.instrument), str(r.broker), str(r.session), str(r.tf)): r
            for r in cost_table[cost_table["instrument"].astype(str).isin(sorted(rec.measured_fx()))].itertuples()}
    out = []
    for c in rec.fx_cells:
        r = have.get((c.instrument, c.broker, c.session, c.tf))
        if r is None:
            out.append(f"{c.instrument}:{c.broker}:{c.session}:{c.tf}: missing")
            continue
        out += [f"{c.instrument}:{c.broker}:{c.session}:{c.tf}: {k}" for k in ("rt_bp", "ecr_ub95_bp", "slip_x_bp")
                if not np.isclose(float(getattr(r, k)), getattr(c, k), rtol=0, atol=CELL_TOL)]
    return out


def _asof(keys: np.ndarray, dec: np.ndarray, table: pd.DataFrame, by: list[str], cols: tuple[str, ...]
          ) -> dict[str, np.ndarray]:
    """As-of join on `available_utc` per group `by` (a row never sees its own month or later): each col, plus
    `available_utc` (NaT where no prior row). `keys`: the rows' group keys joined with "|"; `dec`: naive UTC ns."""
    out: dict[str, np.ndarray] = {c: np.full(len(dec), None if c == "quote_basis" else np.nan,
                                             dtype=object if c == "quote_basis" else float) for c in cols}
    out["available_utc"] = np.full(len(dec), np.datetime64("NaT"), dtype="datetime64[ns]")
    for k, g in table.sort_values("available_utc").groupby(by):
        m = keys == "|".join(map(str, k if isinstance(k, tuple) else (k,)))
        if not m.any():
            continue
        av = pd.to_datetime(g["available_utc"], utc=True).dt.tz_localize(None).to_numpy("datetime64[ns]")
        i = np.searchsorted(av, dec[m], side="right") - 1
        ok = i >= 0
        j = np.clip(i, 0, None)
        for c in cols:
            v = g[c].to_numpy()
            out[c][m] = np.where(ok, v[j], out[c][m])
        out["available_utc"][m] = np.where(ok, av[j], np.datetime64("NaT"))
    return out


def _refuse_stale(rec: CostRecord, what: str, dec: np.ndarray, av: np.ndarray) -> None:
    """280(b): the as-of row must be available within STALE_DAYS of the decision."""
    old = ~np.isnat(av) & (dec - av > np.timedelta64(STALE_DAYS, "D"))
    if old.any():
        k = int(np.argmax(old))
        raise CostRecordRefused(f"cost {rec.version}: {int(old.sum())} {what} rows' as-of row was available more than "
                                f"{STALE_DAYS} days before the decision (first decision_utc {pd.Timestamp(dec[k])} "
                                f"UTC, as-of row available {pd.Timestamp(av[k])} UTC): a stale era is refused "
                                "(280(b))")


def _naive_utc(s: pd.Series) -> np.ndarray:
    return np.asarray(pd.DatetimeIndex(pd.to_datetime(s, utc=True)).tz_localize(None).to_numpy("datetime64[ns]"))


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
    dec = _naive_utc(rows["decision_utc"])
    j = _asof(sess, dec, spread, ["session"], ("ub95_usd",))
    sp = j["ub95_usd"].astype(float)
    gap = ~np.isfinite(sp)                     # code review 2 on #515: never let nanmax drop the era term
    if gap.any():
        raise CostRecordRefused(f"cost {rec.version}: {int(gap.sum())} gold rows have no finite era spread ub95 "
                                f"(no complete prior month, or a NaN month), first decision_utc "
                                f"{pd.DatetimeIndex(pd.to_datetime(rows['decision_utc'], utc=True))[gap][0]}: "
                                "refused")
    _refuse_stale(rec, "gold spread", dec, j["available_utc"])
    per = []
    for b, comm in sorted(rec.commission_rt_usd_per_oz.items()):
        c = _cell_terms(rows, rec, b)
        era = (sp + comm + c["slip_x_usd"].to_numpy(float)) / entry * 1e4
        per.append(np.nanmax(np.vstack([c["rt_bp"].to_numpy(float),
                                        c["ecr_ub95_usd"].to_numpy(float) / entry * 1e4, era]), axis=0))
    return np.asarray(np.maximum(rec.w2_bp, np.maximum.reduce(per)))


def fx_row_bp(rows: pd.DataFrame, rec: CostRecord, fx_spread: pd.DataFrame) -> np.ndarray:
    """The per-row FX charge of record (module docstring), bp. `fx_spread`: instrument, session, available_utc,
    ub95_px (price units), quote_basis; joined as-of on `available_utc` per instrument x session (point in time)."""
    inst = rows["instrument"].astype(str).to_numpy()
    bad = sorted(set(inst) - rec.measured_fx())
    if bad:
        raise CostRecordRefused(f"cost {rec.version}: {bad[:5]} not a measured FX class in this record: no charge")
    entry = rows["entry"].to_numpy(float)
    sess = _sessions(rows)
    dec = _naive_utc(rows["decision_utc"])
    keys = np.array([f"{i}|{s}" for i, s in zip(inst, sess, strict=True)], dtype=object)
    j = _asof(keys, dec, fx_spread, ["instrument", "session"], ("ub95_px", "quote_basis"))
    qb = j["quote_basis"]
    nb = np.array([q is not None and q != BID_ASK for q in qb], dtype=bool)
    if nb.any():
        raise CostRecordRefused(f"cost {rec.version}: {int(nb.sum())} FX rows' as-of spread month has quote basis "
                                f"{sorted({str(q) for q in qb[nb]})} (not {BID_ASK}): no measured spread, refused")
    sp = j["ub95_px"].astype(float)
    gap = ~np.isfinite(sp)
    if gap.any():
        raise CostRecordRefused(f"cost {rec.version}: {int(gap.sum())} FX rows have no finite era spread ub95 "
                                f"(no complete prior bid+ask month), first decision_utc {pd.Timestamp(dec[gap][0])} "
                                "UTC: refused")
    _refuse_stale(rec, "FX spread", dec, j["available_utc"])
    cells = rec.fx_frame()
    key = pd.DataFrame({"instrument": inst, "session": sess, "tf": rows["tf"].astype(str).to_numpy()})
    per = []
    for b in sorted(rec.commission_rt_usd_per_oz):
        c = key.merge(cells[cells["broker"] == b][["instrument", "session", "tf", "rt_bp", "ecr_ub95_bp",
                                                   "slip_x_bp"]], on=["instrument", "session", "tf"], how="left")
        if c["rt_bp"].isna().any():
            raise CostRecordRefused(f"cost {rec.version}: no {b} FX cell for some rows (instrument:session:tf)")
        comm = np.array([rec.fx_commission_rt_bp[i][b] for i in inst], dtype=float)
        era = sp / entry * 1e4 + comm + c["slip_x_bp"].to_numpy(float)
        per.append(np.nanmax(np.vstack([c["rt_bp"].to_numpy(float), c["ecr_ub95_bp"].to_numpy(float), era]),
                             axis=0))
    return np.asarray(np.maximum(rec.w2_bp, np.maximum.reduce(per)))


_MT5_TO_PY = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}         # MT5 weekday (0 = Sunday) -> Python (0 = Monday)


def fx_swap_bp(rows: pd.DataFrame, rec: CostRecord, swap_spec: pd.DataFrame) -> np.ndarray:
    """FX swap per row, bp of entry (card P3, quant verbatim): per side per rollover from the broker's symbol swap spec
    captured point in time (as-of `captured_utc` <= decision, within STALE_DAYS), triple on the broker's triple day,
    on every row whose horizon (decision_utc, horizon_end_utc] crosses a rollover; the worse broker; a credit is never
    banked. A rollover is the broker-server midnight (`rollover_tz`) after a Monday..Friday server date; the triple day
    (`swap_triple_weekday`, MT5 numbering, 0 = Sunday) is charged three nights. Swap mode "points" only.
    rows: instrument, side (+1/-1), entry, decision_utc, horizon_end_utc. swap_spec: broker, instrument, captured_utc,
    swap_mode, swap_long_points, swap_short_points, point, swap_triple_weekday, rollover_tz."""
    if not (swap_spec["swap_mode"].astype(str) == "points").all():
        raise CostRecordRefused(f"cost {rec.version}: swap_mode {sorted(set(swap_spec['swap_mode'].astype(str)))}: "
                                "only 'points' is converted: refused")
    inst = rows["instrument"].astype(str).to_numpy()
    side = rows["side"].to_numpy(int)
    entry = rows["entry"].to_numpy(float)
    dec_ts = pd.DatetimeIndex(pd.to_datetime(rows["decision_utc"], utc=True))
    end_ts = pd.DatetimeIndex(pd.to_datetime(rows["horizon_end_utc"], utc=True))
    dec = _naive_utc(rows["decision_utc"])
    spec = swap_spec.assign(available_utc=pd.to_datetime(swap_spec["captured_utc"], utc=True))
    per = []
    for b in sorted(rec.commission_rt_usd_per_oz):
        sb = spec[spec["broker"].astype(str) == b]
        j = _asof(inst.astype(object), dec, sb, ["instrument"],
                  ("swap_long_points", "swap_short_points", "point", "swap_triple_weekday"))
        miss = np.isnat(j["available_utc"])
        if miss.any():
            raise CostRecordRefused(f"cost {rec.version}: {int(miss.sum())} FX rows have no broker swap spec captured "
                                    f"at {b} before their decision (first {pd.Timestamp(dec[miss][0])} UTC): refused")
        _refuse_stale(rec, f"FX swap ({b})", dec, j["available_utc"])
        tzs = sb.sort_values("available_utc").groupby("instrument")["rollover_tz"].last().astype(str).to_dict()
        pts = np.where(side > 0, j["swap_long_points"].astype(float), j["swap_short_points"].astype(float))
        rate = np.maximum(0.0, -pts * j["point"].astype(float)) / entry * 1e4
        nights = np.zeros(len(rows))
        for k in range(len(rows)):
            tz = tzs[inst[k]]
            a = dec_ts[k].tz_convert(tz).tz_localize(None).normalize()
            z = end_ts[k].tz_convert(tz).tz_localize(None).normalize()
            n = max(0, (z - a).days)                     # server dates whose closing midnight is crossed
            wd = (a.weekday() + np.arange(n)) % 7
            triple = _MT5_TO_PY[int(j["swap_triple_weekday"][k])]
            nights[k] = float(np.sum(np.where(wd == triple, 3, np.where(wd < 5, 1, 0))))
        per.append(nights * rate)
    return np.asarray(np.maximum.reduce(per)) if per else np.zeros(len(rows))


def row_bp(rows: pd.DataFrame, rec: CostRecord, frames: Mapping[str, pd.DataFrame]) -> np.ndarray:
    """The record's per-row round trip for measured rows (gold: `gold_row_bp`, measured FX: `fx_row_bp`); NaN for a
    prior row."""
    inst = rows["instrument"].astype(str).to_numpy()
    out = np.full(len(rows), np.nan)
    g = (inst == GOLD) & (GOLD in rec.measured())
    f = np.isin(inst, sorted(rec.measured_fx()))
    if g.any():
        out[g] = gold_row_bp(rows[g], rec, frames["gold_spread_table"])
    if f.any():
        out[f] = fx_row_bp(rows[f], rec, frames["fx_spread_table"])
    return out


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
    fx_ok = rec.measured_fx() if rec.schema_version == 2 else frozenset()
    if not rec.measured() <= {GOLD} | fx_ok:
        raise CostRecordRefused(f"cost {version}: measures {sorted(rec.measured() - {GOLD} - fx_ok)} with no per-row "
                                "charge of record (only gold_row_bp for gold and, in schema 2, fx_row_bp for measured "
                                "FX): refused")
    rt = np.asarray(rt_bp, dtype=float)
    if rt.shape != (len(labels),):
        raise ValueError(f"rt_bp has {rt.shape} values for {len(labels)} label rows")
    inst = labels["instrument"].astype(str).to_numpy()
    meas = np.isin(inst, sorted(rec.measured()))
    if meas.any():
        r = rt[meas]
        if not np.isfinite(r).all():
            raise ValueError(f"cost {version}: a measured row's rt_bp is not finite")
        want = row_bp(labels[meas], rec, frames)
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
    block += [f"{i} {tf}: decision-to-fill wait cost not in {version} (D-15)" for i in sorted(set(inst[meas]))
              for tf in WAIT_TFS if tf not in rec.wait_charged_tfs and (meas & (inst == i) & (tfs == tf)).any()]
    m = {**meta, "gate_eligible": not block, "gate_block": block,
         "cost_of_record": {"version": version, "content_sha256": rec.content_sha256,
                            "measured": sorted(rec.measured() & set(inst)), "rows_charged": int(meas.sum()),
                            "rows_prior": int((~meas).sum())}}
    return out, m


__all__ = ["BID_ASK", "D3_BLOCK", "FILL_CUTOFF_UTC", "RECORD_DIR", "STALE_DAYS", "TABLE_FILES", "TABLE_FILES_V2",
           "WAIT_TFS", "ClassEntry", "CostRecord", "CostRecordRefused", "FxCell", "GoldCell", "apply", "charge_floor",
           "check_cells", "check_fx_cells", "content_sha256", "content_sha256_v2", "fx_row_bp", "fx_swap_bp",
           "gold_row_bp", "record_sha256", "recorded", "row_bp", "verify"]
