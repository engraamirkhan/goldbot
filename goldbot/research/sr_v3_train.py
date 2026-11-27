"""The sr-v3 history trainer, fit path: Stage 0 -> walk-forward fit of every built learner -> v0 artifacts and sealed
OOF scores (card docs/research/prereg-2026-11-26-sr-v3.md rev 8, sections 2.3, 3.3-3.6, 5.1 and 5.4; SYNTH-ml items 3
and 10; ADR 0106 "Training"; ADR 0108).

Three modules, so the fit path stays guarded (quant LOW on #488: `objective.GUARDED_MODULES` holds this module, so
no AUC, accuracy or self-selecting estimator is on it or on anything it imports):
- this module: folds and the gap, cost(x) and the cost of record, Stage 0 counts, the fits, OOF scores, v0 artifacts
  and the fit-only report;
- `sr_v3_store`: the dl-v1 / fs-v1 stores and the read refusals (quant sequencing, read-once booking per unit, gate
  status);
- `sr_v3_report`: the read (refused unless gate_eligible at ub95, under a booked trial for a judged unit), the
  descriptive battery and the run record (diagnostics, model_quality, run_manifest).

Per judged unit (1m, 5m, 15m, 30m, 1h, 2h, 4h, and 1d+1w pooled), on learn 2010-17 only:
1. **Stage 0** (`stage0_counts`, `sr_v3.decide_unit`): label-blind. The ladder is computed from the outcome-blind
   skeleton (one T2 long event per bar, phase 0, tier-usable under P30) and the uniqueness weights, for the gold
   body and the pooled body, on fold 2010-11 (2010-13 under remedy (b)). No label value is read (card test 18 (iv)).
2. **Folds** (card 5.3 Stage L, R-B; ADR 0106 "a gap between train, validation and test"): expanding yearly folds,
   OOF years `first_oof`..2017. A training row of test year Y has its label horizon end before Y's start minus the
   unit's gap (`gap`: max(1 d, max H over the unit's tfs)). The AVG calibrator ("validation") uses only OOF rows of
   earlier years under the same gap. Inputs are imputed with the smallest fold's medians and standardised with each
   fold's own training rows.
3. **Fit**: L-cw and HB from additive moments over every row (streamed by year), G-cw and the D-40 dense prior on a
   deterministic Horvitz-Thompson thinned sample of at most `MAX_TREE_ROWS` rows (weights / π), each G-cw seed on
   its own 90 % resample, AVG on the earlier OOF years. cost(x) per fold = decision_cost.cost_x with `CostPriors`
   from that fold's training rows only, at the per-cell ub95 cost of record (`CostTable`).
4. **v0**: every learner refitted on all learn rows: the frozen artifacts, each with its sha (deterministic bytes).
5. **Sealed OOF scores** (`sealed_scores`): row keys, cost(x), each learner's value and keep, no label column; their
   sha is in the report and every manifest before any read statistic exists.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from goldbot.data.daily import session_date
from goldbot.research import decision_cost as DC
from goldbot.research import learners as L
from goldbot.research import objective as O
from goldbot.research import sr_v3 as V
from goldbot.research import windows as WN

ROOT = Path(__file__).resolve().parents[2]


OUT = Path.home() / "tmp" / "sr-v3"                 # market-derived outputs stay local (ADR 0048)


MAX_TREE_ROWS = 2_000_000


# ADR 0106 ">= 5 seeds", fixed before the first fit by the run's manifest key (quant Q1 on #494, ADR 0109): every
# unit and learner of the v0 run fits with these seeds and every manifest records the same key
MANIFEST_KEY = "sr-v3:v0"


# model_quality.seed_list's formula, pinned equal by a test: this guarded module may not import model_quality, whose
# imports reach research/metrics (objective.guard_violations)
SEEDS: tuple[int, ...] = tuple(int.from_bytes(hashlib.sha256(f"{MANIFEST_KEY}|{i}".encode()).digest(), "big") % 2**31
                               for i in range(5))


SEED_SUBSAMPLE = 0.9                                # each G-cw seed fits this seed-hash share of its training rows


def one_thread() -> Any:
    """One BLAS/OpenMP thread, as `run_manifest.build` rebuilds: a multi-threaded reduction can change the last bit of
    a fit and so the artifact sha (every fit whose bytes a manifest records runs under it)."""
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]
    return threadpool_limits(limits=1)


LAMBDA_RW = V.LAMBDA_ROWS                           # HB: τ = 0.05 R per sd per tf step, as λ


HB_COLUMNS: tuple[str, ...] = tuple(c for c in V.P30 if c != "dist_vwap48_atr")   # bid-only safe for every membership


CODE_FILES: tuple[str, ...] = ("goldbot/research/sr_v3.py", "goldbot/research/learners.py",
                               "goldbot/research/sr_v3_train.py", "goldbot/research/sr_v3_store.py",
                               "goldbot/research/sr_v3_report.py", "goldbot/research/sr_v3_serve.py",
                               "goldbot/research/objective.py", "goldbot/research/decision_cost.py",
                               "goldbot/research/srv3_cost_record.py")


ROW_COLS: tuple[str, ...] = ("instrument", "tf", "phase", "decision_utc", "horizon_end_utc", "side", "gross",
                             "t0_gross", "cost_a", "cost_b", "cost_c", "stop", "nights_held", "nights_full", "w",
                             "entry", "base_dist", "cost_rt_bp", "p30_ok", *(c for c in V.P30 if c != "side"))


BUILT = ("L-cw", "G-cw", "HB", "AVG")


W2_COST_VERSION = "w2-point"           # the rows' own W2 round trip: never a read (ADR 0085 A4, quant H1 on #488)


NOT_READ: dict[str, Any] = {"gate_eligible": False,
                            "gate_block": ["no label read through load_labels: no gate status"]}


class UnitData(Protocol):
    """A unit's rows, one (year, instrument) frame of `ROW_COLS` at a time (labels already read under the gate)."""
    def frame(self, instrument: str, tf: str, year: int, *, phases: Sequence[int]) -> pd.DataFrame: ...
    def manifest(self) -> dict[str, Any]: ...
    def version(self, members: Sequence[str], tfs: Sequence[str], phases: Sequence[int]) -> str: ...
    def gate_status(self) -> dict[str, Any]: ...
    def cost_version(self) -> str: ...


class FrameData:
    """In-memory frames (tests, and a caller who already holds rows). Keyed (instrument, tf, year). They carry no
    load_labels gate status and W2 point costs unless the caller says otherwise: fit-only by default."""

    def __init__(self, frames: Mapping[tuple[str, str, int], pd.DataFrame], version_name: str = "in-memory", *,
                 gate: Mapping[str, Any] | None = None, cost_version: str = W2_COST_VERSION) -> None:
        self.frames, self.version_name = frames, version_name
        self.gate, self.costs = dict(gate or NOT_READ), cost_version

    def gate_status(self) -> dict[str, Any]:
        return {"gate_eligible": self.gate.get("gate_eligible") is True,
                "gate_block": list(self.gate.get("gate_block") or [])}

    def cost_version(self) -> str:
        return self.costs

    def frame(self, instrument: str, tf: str, year: int, *, phases: Sequence[int] = (0,)) -> pd.DataFrame:
        f = self.frames.get((instrument, tf, year))
        if f is None:
            return pd.DataFrame(columns=list(ROW_COLS))
        return f[f["phase"].isin(list(phases))].reset_index(drop=True)

    def manifest(self) -> dict[str, Any]:
        h = hashlib.sha256()
        for k in sorted(self.frames):
            h.update(repr(k).encode())
            h.update(pd.util.hash_pandas_object(self.frames[k][list(ROW_COLS)], index=False).to_numpy().tobytes())
        return {"source": self.version_name, "frames_sha256": h.hexdigest()}

    def version(self, members: Sequence[str], tfs: Sequence[str], phases: Sequence[int]) -> str:
        return str(self.manifest()["frames_sha256"])


class CostCellMissing(KeyError):
    """A row has no cell in the cost of record (instrument x session x tf) or no swap for its instrument and month."""


@dataclass(frozen=True)
class CostTable:
    """The per-cell cost of record (quant H1 on #488; ADR 0098 R-H, ADR 0102 3): round trip rt_bp = max(W2, ecr ub95)
    per instrument x broker x session x tf, and the swap of record per instrument and month (fraction of price per
    night, broker sign: negative = paid). Delivered by the data team (`costs` in ~/tmp/sr-v3-trainsets/manifest.json,
    market-derived, never in the repo); `from_manifest` checks each file's sha256.

    A cell charges the WORSE of its brokers: a history row has no account, so the conservative broker is charged
    (W2 stays the floor whatever the table says).

    Gold rows (data team N2, cost version srv3-cost-bb199294 on): per row and broker, max(the cell's rt_bp, ECR ub95
    USD / entry x 1e4, (Dukascopy gold spread ub95 of the session's latest COMPLETE month before the decision + the
    broker's commission per oz round trip) / entry x 1e4), the worse broker taken; the spread is joined as-of on its
    `available_utc` (the instant after its month). Identical to scripts/research/sr_v3_cost_table.py:row_rt_bp
    (pinned by a test). The swap table's metal rows are the USD rate rule, never a credit."""
    version: str
    rt: Mapping[tuple[str, str, str], float]             # (instrument, session, tf) -> rt bp, max over brokers
    swap: Mapping[tuple[str, str], tuple[float, float]]  # (instrument, "YYYY-MM") -> (long, short) per night
    files: Mapping[str, str] = field(default_factory=dict)
    gold_cells: pd.DataFrame | None = None               # gold: broker, session, tf, rt_bp, ecr_ub95_usd
    gold_spread: pd.DataFrame | None = None              # session, available_utc, ub95_usd (sorted)
    commission_rt: Mapping[str, float] = field(default_factory=dict)   # broker -> USD per oz round trip
    paths: Mapping[str, Path] = field(default_factory=dict)   # the table files, for srv3_cost_record.apply (F6 on #515)
    # the D-3 per-row gold charge of record (srv3_cost_record.gold_row_bp), injected by sr_v3_store.cost_table: this
    # guarded module may not import srv3_cost_record (it reaches dense_labels -> self_retrain -> sklearn.metrics)
    gold_charge: Callable[[pd.DataFrame], np.ndarray] | None = None

    @classmethod
    def from_manifest(cls, path: Path | str) -> CostTable:
        m = json.loads(Path(path).read_text())
        c = m.get("costs", m)
        f = c["files"]
        version = str(c["cost_version"])
        shas = {}
        for name in ("cost_table", "swap_table", *(("gold_spread_table",) if "gold_spread_table" in f else ())):
            p = Path(f[name])
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            if got != f[f"{name}_sha256"]:
                raise ValueError(f"{p}: sha256 {got[:12]} is not the manifest's {f[f'{name}_sha256'][:12]}: refused")
            shas[name] = got
        ct = pd.read_parquet(f["cost_table"])
        rt = ct.groupby(["instrument", "session", "tf"], as_index=False)["rt_bp"].max()
        sw = pd.read_parquet(f["swap_table"])
        gold_cells = gold_spread = None
        comm: dict[str, float] = {}
        if "gold_spread_table" in f:
            meta = json.loads(Path(c["path"]).read_text())
            comm = {str(k): float(v) for k, v in meta["commission_rt_usd_per_oz"].items()}
            gold_cells = ct[ct["instrument"] == V.GOLD][["broker", "session", "tf", "rt_bp", "ecr_ub95_usd"]]
            gold_spread = pd.read_parquet(f["gold_spread_table"]).sort_values("available_utc").reset_index(drop=True)
        return cls(version=version, gold_cells=gold_cells, gold_spread=gold_spread, commission_rt=comm,
                   paths={k: Path(f[k]) for k in shas},
                   rt={(str(r.instrument), str(r.session), str(r.tf)): float(r.rt_bp)
                       for r in rt.itertuples()},
                   swap={(str(i), str(mo)): (float(lp), float(sp)) for i, mo, lp, sp in zip(
                       sw["instrument"], sw["month"], sw["long_per_night"].to_numpy(float),
                       sw["short_per_night"].to_numpy(float), strict=True)}, files=shas)

    def rt_bp(self, rows: pd.DataFrame) -> np.ndarray:
        """max(W2, the cell's charge) per row; `CostCellMissing` names every row cell the table lacks."""
        from goldbot.data.calendar import DEFAULT_SESSIONS
        dec = pd.DatetimeIndex(pd.to_datetime(rows["decision_utc"], utc=True))
        sess = np.asarray(DEFAULT_SESSIONS.session_label(dec)).astype(str) if len(dec) else np.array([], str)
        keys = list(zip(rows["instrument"].astype(str), sess, rows["tf"].astype(str), strict=True))
        missing = sorted({":".join(k) for k in keys if k not in self.rt})
        if missing:
            raise CostCellMissing(f"cost {self.version}: no cell for {missing[:5]}{' ...' if len(missing) > 5 else ''}"
                                  " (instrument:session:tf); refused, never charged at W2")
        rt = np.array([self.rt[k] for k in keys], dtype=float)
        gold = rows["instrument"].astype(str).to_numpy() == V.GOLD
        if self.gold_cells is not None and gold.any():
            g = rows[gold]
            if self.gold_charge is not None:       # the D-3 cost of record: its own charge, slip_x included (R1)
                rt[gold] = self.gold_charge(g)
            else:                                   # an unrecorded (fit-only) table: the builder's legacy terms
                rt[gold] = np.maximum.reduce([self._gold_row_bp(g, sess[gold], b) for b in sorted(self.commission_rt)])
        return np.maximum(DC.RT_BP, rt)

    def _gold_row_bp(self, rows: pd.DataFrame, sess: np.ndarray, broker: str) -> np.ndarray:
        """sr_v3_cost_table.row_rt_bp for one broker (quant N2): max(cell rt_bp, ECR ub95 / entry, (spread ub95 of
        the latest complete month + commission) / entry), in bp, NaN terms ignored."""
        assert self.gold_cells is not None and self.gold_spread is not None
        cells = self.gold_cells[self.gold_cells["broker"] == broker]
        c = pd.DataFrame({"session": sess, "tf": rows["tf"].astype(str).to_numpy()}).merge(
            cells[["session", "tf", "rt_bp", "ecr_ub95_usd"]], on=["session", "tf"], how="left")
        entry = rows["entry"].to_numpy(float)
        ecr = c["ecr_ub95_usd"].to_numpy(float) / entry * 1e4
        sp = np.full(len(rows), np.nan)
        dec = pd.to_datetime(rows["decision_utc"], utc=True).to_numpy()
        for s_name, g in self.gold_spread.groupby("session"):
            m = sess == s_name
            if not m.any():
                continue
            av = pd.to_datetime(g["available_utc"], utc=True).to_numpy()
            i = np.searchsorted(av, dec[m], side="right") - 1
            v = g["ub95_usd"].to_numpy(float)
            sp[m] = np.where(i >= 0, v[np.clip(i, 0, None)], np.nan)
        spread_bp = (sp + self.commission_rt[broker]) / entry * 1e4
        return np.asarray(np.nanmax(np.vstack([c["rt_bp"].to_numpy(float), ecr, spread_bp]), axis=0))

    def swap_rate(self, rows: pd.DataFrame) -> np.ndarray:
        """|swap per night| as a fraction of price for the row's instrument, decision month and side (a received swap
        is never credited, as decision_cost)."""
        dec = pd.DatetimeIndex(pd.to_datetime(rows["decision_utc"], utc=True))
        month = np.asarray(dec.strftime("%Y-%m")).astype(str) if len(dec) else np.array([], str)
        keys = list(zip(rows["instrument"].astype(str), month, strict=True))
        missing = sorted({":".join(k) for k in keys if k not in self.swap})
        if missing:
            raise CostCellMissing(f"cost {self.version}: no swap for {missing[:5]} (instrument:month); refused")
        side = rows["side"].to_numpy(float)
        return np.abs(np.array([self.swap[k][0 if sd > 0 else 1] for k, sd in zip(keys, side, strict=True)],
                               dtype=float))


def cost_parts(rows: pd.DataFrame, costs: CostTable | None = None, swap: Any | None = None) -> pd.DataFrame:
    """cost(x) = a + b·p_stop[arm] + c·night_share[arm] per row, from decision-time columns only (decision_cost.cost_x
    and dense_labels.net_r's round-trip term, rearranged so one row set serves every fold's priors).

    With `costs` (quant H1 on #488): the round trip is the cell's max(W2, ecr ub95) and the swap is the instrument's
    own of record (rows need instrument and tf). Without it: the row's own `cost_rt_bp` (W2, the w2-point version a
    read refuses) and decision_cost's swap. Rollover nights are counted on decision_cost's swap calendar either way."""
    sw = swap or DC.swap_spec()
    k = DC.row_k(rows, sw)
    if costs is None:
        rt = rows["cost_rt_bp"].to_numpy(float)
        rate = np.abs(DC._rate_signed(rows["side"].to_numpy(float), sw))
    else:
        rt, rate = costs.rt_bp(rows), costs.swap_rate(rows)
    full = DC.nights_full_hold(rows, sw)
    return pd.DataFrame({"cost_a": k * rt, "cost_b": k * DC.OVERSHOOT_BP, "cost_c": full * k * 1e4 * rate,
                         "nights_full": full, "cost_rt_bp": rt})


def unit_of(tf: str) -> tuple[str, ...]:
    """The tfs judged together with `tf` (1d and 1w are one unit)."""
    return next(tfs for tfs in V.UNITS.values() if tf in tfs)


def gap(tf: str) -> pd.Timedelta:
    """The purge/embargo before a test year, per unit: max(1 d, max H over the unit's tfs) (`windows.label_embargo`).
    Quant ruling (1) on #488, a label-blind clarification of card R-B (K 0): no input-lookback term, because the
    folds expand forward and purge every label whose horizon reaches the cut, so a training row carries no future
    information (the M1 lookback term was withdrawn)."""
    return WN.label_embargo(pd.Timedelta(seconds=max(V.H_S[t] for t in unit_of(tf))))


def year_start(y: int) -> pd.Timestamp:
    return pd.Timestamp(f"{y}-01-01", tz="UTC")


def train_mask(f: pd.DataFrame, test_year: int) -> np.ndarray:
    """Rows that may train for test year `test_year`: labelled before its start minus the gap. The v0 fold (the year
    after learn) has no test block after it: every row labelled by the learn end trains (load_labels' own cut)."""
    if not len(f):
        return np.zeros(0, bool)
    if test_year > V.LEARN_YEARS[-1]:
        return _ns(f["horizon_end_utc"]) <= WN.SPANS[WN.Window.LEARN][1].value
    cuts = {t: (year_start(test_year) - gap(t)).value for t in f["tf"].astype(str).unique()}
    cut = np.array([cuts[t] for t in f["tf"].astype(str)])
    return _ns(f["horizon_end_utc"]) < cut


def _ns(s: pd.Series) -> np.ndarray:
    from goldbot.data.timeutil import epoch_ns
    return np.asarray(epoch_ns(pd.DatetimeIndex(pd.to_datetime(s, utc=True))))


def arm_of(f: pd.DataFrame) -> np.ndarray:
    return (f["instrument"].astype(str) + ":" + f["tf"].astype(str) + ":" + V.JUDGED_HEAD).to_numpy()


@dataclass
class PriorAcc:
    """Per arm: stop count, rows, nights held, full-hold nights (the label statistics of decision_cost.priors)."""
    stop: dict[str, float] = field(default_factory=dict)
    n: dict[str, float] = field(default_factory=dict)
    held: dict[str, float] = field(default_factory=dict)
    full: dict[str, float] = field(default_factory=dict)

    def add(self, f: pd.DataFrame) -> None:
        if not len(f):
            return
        g = pd.DataFrame({"arm": arm_of(f), "stop": f["stop"].astype(float), "one": 1.0,
                          "held": f["nights_held"].astype(float), "full": f["nights_full"].astype(float)})
        s = g.groupby("arm")[["stop", "one", "held", "full"]].sum()
        for arm, r in s.iterrows():
            for d, v in ((self.stop, r["stop"]), (self.n, r["one"]), (self.held, r["held"]), (self.full, r["full"])):
                d[str(arm)] = d.get(str(arm), 0.0) + float(v)

    def priors(self, before: pd.Timestamp) -> DC.CostPriors:
        n = sum(self.n.values())
        if not n:
            raise ValueError("no training rows to fit the cost priors")
        full = sum(self.full.values())
        return DC.CostPriors(p_stop={a: self.stop[a] / self.n[a] for a in sorted(self.n)},
                             night_share={a: (self.held[a] / self.full[a] if self.full[a] > 0 else 1.0)
                                          for a in sorted(self.n)},
                             p_stop_pooled=sum(self.stop.values()) / n,
                             night_share_pooled=sum(self.held.values()) / full if full > 0 else 1.0,
                             n_rows=int(n), labelled_before=before.isoformat())


def realised_cost(f: pd.DataFrame) -> np.ndarray:
    """The realised cost leg of a read, in R (quant M2 on #488; card 3.4 "Realised net R in every read charges swap
    per rollover actually held"): k·(RT_ub95 + OV·1[stop]) + nights held × the per-night swap. An outcome: reads
    only, never a keep decision (cost(x), `cost_of`, keeps)."""
    full = f["nights_full"].to_numpy(float)
    per_night = np.divide(f["cost_c"].to_numpy(float), full, out=np.zeros(len(f)), where=full > 0)
    return (f["cost_a"].to_numpy(float) + f["cost_b"].to_numpy(float) * f["stop"].to_numpy(float)
            + per_night * f["nights_held"].to_numpy(float))


def net_read(f: pd.DataFrame, *, gross: np.ndarray | None = None, mult: float = 1.0) -> np.ndarray:
    """net_read = clip(gross − mult·realised cost): the net R every read statistic uses (M2)."""
    g = f["gross"].to_numpy(float) if gross is None else gross
    return np.asarray(O.clip_r(g - mult * realised_cost(f)))


def cost_of(f: pd.DataFrame, pri: DC.CostPriors) -> np.ndarray:
    arm = arm_of(f)
    p = np.array([pri.p_stop.get(a, pri.p_stop_pooled) for a in arm])
    ns = np.array([pri.night_share.get(a, pri.night_share_pooled) for a in arm])
    return f["cost_a"].to_numpy(float) + f["cost_b"].to_numpy(float) * p + f["cost_c"].to_numpy(float) * ns


def usable_under(events: pd.DataFrame, columns: Sequence[str]) -> np.ndarray:
    """Rev 9 D1: a row is usable under a column set iff its label window is usable (`usable`) and every column's own
    span is (`ok:<column>`, from `sr_v3_store.prefix_usability`: attach_tiers per column, whose conjunction is ADR
    0104 4's "a row's usability is the conjunction over its requested columns")."""
    m = events["usable"].to_numpy(bool).copy()
    for c in columns:
        if c == "side":
            continue
        k = f"ok:{c}"
        if k not in events:
            raise KeyError(f"events carry no usability for {c!r} ({k}): build them with prefix_usability")
        m &= events[k].to_numpy(bool)
    return m


def stage0_counts(events: pd.DataFrame, *, years: Sequence[int], kish_eff: float | None = None,
                  columns: Sequence[str] = ()) -> list[V.InstCounts]:
    """Label-blind counts per instrument from outcome-blind events (instrument, phase, decision_utc, usable, u, and
    `ok:<column>` per column): phase 0, usable under `columns` (rev 9 D1; () = the label window only), decided in
    `years`. A frame carrying label columns gives the same counts (they are never touched)."""
    e = events[(events["phase"].to_numpy() == 0) & usable_under(events, columns)]
    yr = pd.DatetimeIndex(pd.to_datetime(e["decision_utc"], utc=True)).year
    e = e[np.isin(yr, list(years))]
    out = []
    for inst, g in e.groupby("instrument", sort=True):
        day = np.asarray(session_date(g["decision_utc"]).astype(str))
        out.append(V.inst_counts(str(inst), g["u"].to_numpy(float), day, kish_eff=kish_eff))
    return out


def stage0(unit: str, tfs: Sequence[str], events: Mapping[str, pd.DataFrame], *, rho_file: Path,
           available: Mapping[str, Sequence[str]] | None = None, freeze_sha: str | None = None) -> V.Stage0Unit:
    """Both bodies on fold 2010-11 and, if needed, 2010-13. `events[instrument]`: the instrument's outcome-blind events
    over every tf of the unit (stage0_counts' columns). 1d+1w pools its two tfs' events (one unit)."""
    if any(len(f) and (f["tf"].astype(str) == "").any() for f in events.values()):
        raise ValueError("events need a tf")
    gold_ev = {k: v for k, v in events.items() if k == V.GOLD}
    pooled_ev = {k: v for k, v in events.items() if k in V.POOLED_MEMBERS}
    members = sorted(pooled_ev)
    tf0 = tfs[0]
    rho: dict[str, V.RhoInputs] = {"gold": V.rho_inputs(rho_file, body="gold", members=[V.GOLD], tf=tf0)}
    if len(members) > 1:
        rho["pooled"] = V.rho_inputs(rho_file, body="pooled", members=members, tf=tf0, freeze_sha=freeze_sha)
    av = {"gold": tuple(available.get("gold", V.P30)) if available else V.P30,
          "pooled": tuple(available.get("pooled", HB_COLUMNS)) if available else HB_COLUMNS}

    def cat(d: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
        return pd.concat(list(d.values()), ignore_index=True) if d else pd.DataFrame(
            columns=["instrument", "phase", "decision_utc", "usable", "u"])

    def counts(years: Sequence[int]) -> dict[str, V.CountsOf]:
        """Per body, counts as a function of the column set (rev 9 D1), cached per column set."""
        out: dict[str, V.CountsOf] = {}
        for body, ev in (("gold", cat(gold_ev)), ("pooled", cat(pooled_ev))):
            yr = pd.DatetimeIndex(pd.to_datetime(ev["decision_utc"], utc=True)).year
            fold = ev[np.isin(yr, list(years))].reset_index(drop=True)
            memo: dict[tuple[str, ...], list[V.InstCounts]] = {}

            def of(cols: tuple[str, ...], fold: pd.DataFrame = fold,
                   memo: dict[tuple[str, ...], list[V.InstCounts]] = memo) -> list[V.InstCounts]:
                if cols not in memo:
                    memo[cols] = stage0_counts(fold, years=years, columns=cols)
                return memo[cols]
            out[body] = of
        return out
    return V.decide_unit(unit, tfs, counts_a=counts(V.FOLD_A), counts_b=counts(V.FOLD_B),
                         rho=rho, available=av)


@dataclass
class UnitPlan:
    unit: str
    tfs: tuple[str, ...]
    members: tuple[str, ...]
    lcw_columns: tuple[str, ...]
    first_oof: int
    lam_a: float
    design_effect: float
    stage0: dict[str, Any]

    @property
    def oof_years(self) -> tuple[int, ...]:
        return tuple(range(self.first_oof, V.LEARN_YEARS[-1] + 1))


def plan_from_stage0(s0: V.Stage0Unit) -> UnitPlan:
    """The fit plan Stage 0 fixes: body members, served L-cw columns (() = intercept-only B8), first OOF year, λ_a and
    the overlap design effect (the chosen body's day DEFF at its measured ρ_u,day)."""
    assert s0.body is not None
    rho = s0.rho[s0.body]
    cs = s0.counts[s0.body]
    tot = sum(c.sum_u for c in cs)
    m_bar = sum(c.m_bar * c.sum_u for c in cs) / tot if tot else 1.0
    return UnitPlan(unit=s0.unit, tfs=s0.tfs, members=s0.members, lcw_columns=s0.served, first_oof=s0.first_oof,
                    lam_a=rho.lambda_a if s0.body == "pooled" else V.LAMBDA_ROWS,
                    design_effect=max(1.0, 1 + (m_bar - 1) * rho.rho_u_day), stage0=s0.model_dump(mode="json"))


def _hash_u(f: pd.DataFrame, seed: int) -> np.ndarray:
    key = (f["instrument"].astype(str) + "|" + f["tf"].astype(str) + "|" + f["phase"].astype(str) + "|"
           + pd.Series(_ns(f["decision_utc"])).astype(str).to_numpy() + "|" + f["side"].astype(str) + f"|{seed}")
    h = pd.util.hash_pandas_object(pd.Series(key.to_numpy()), index=False).to_numpy()
    return (h >> np.uint64(11)).astype(np.float64) / float(2 ** 53)


def _td(f: pd.DataFrame, cols: Sequence[str], cost: np.ndarray, *, design_effect: float,
        w: np.ndarray | None = None) -> L.TrainData:
    X = f[list(cols)].to_numpy(float) if cols else np.empty((len(f), 0))
    g = f["gross"].to_numpy(float)
    return L.TrainData(X=X, columns=tuple(cols), gross=g, net=O.clip_r(g - cost), cost=cost,
                       w=f["w"].to_numpy(float) if w is None else w, instrument=f["instrument"].astype(str).to_numpy(),
                       day=np.asarray(session_date(f["decision_utc"]).astype(str)), tf=f["tf"].astype(str).to_numpy(),
                       design_effect=design_effect)


def gcw_columns() -> tuple[str, ...]:
    return (*V.P30, *L.DENSE_COLUMNS, L.RANGE_TO_COST)


@dataclass
class Collected:
    """What a pass over the unit's years gathers: moments per fold, priors per fold, the thinned sample, the medians."""
    impute: np.ndarray                   # P30 medians of the smallest fold
    impute_lcw: np.ndarray               # the served L-cw columns' medians (P30 and any admitted block column)
    lcw: dict[int, dict[str, L.Moments]]
    hb: dict[int, dict[tuple[str, str], L.Moments]]
    priors: dict[int, PriorAcc]
    sample: pd.DataFrame
    pi: float
    n_rows: int
    train_end: pd.Timestamp | None = None    # the last decision instant v0 trains on (run manifest train_end)
    label_end_max: pd.Timestamp | None = None    # the latest label horizon end among v0's rows (manifest, quant Q4)
    # rev 9 D1: rows per learn year usable under the served columns (the judged population) and under P30
    rows_per_year: dict[int, int] = field(default_factory=dict)
    p30_rows_per_year: dict[int, int] = field(default_factory=dict)


def p30_ok(f: pd.DataFrame) -> np.ndarray:
    """Rows usable under P30 (rev 9 D1: HB, D-40 / G-cw and AVG train and score only on these; a frame without the
    column was loaded under P30 already)."""
    return f["p30_ok"].to_numpy(bool) if "p30_ok" in f else np.ones(len(f), bool)


def _years(data: UnitData, plan: UnitPlan, years: Sequence[int], phases: Sequence[int]) -> Iterator[pd.DataFrame]:
    """Frames in a fixed order: tf, then instrument, then year (a store holds one instrument x tf at a time)."""
    for tf in plan.tfs:
        for inst in plan.members:
            for y in years:
                f = data.frame(inst, tf, y, phases=phases)
                if len(f):
                    yield f


def collect(data: UnitData, plan: UnitPlan, *, seed: int, phases: Sequence[int] = (0,),
            n_hint: int | None = None) -> Collected:
    """One streamed pass: medians from the smallest fold's rows (2010-11, in every fold's training set), then per year
    the L-cw moments (members) and HB moments (tf, class) of every fold it trains, the fold priors, and the thinned
    sample. Folds: the OOF years plus 2018 (v0: every learn row). On one thread (`one_thread`): the moments are sums
    a rebuild must reproduce to the last bit."""
    with one_thread():
        return _collect(data, plan, seed=seed, phases=phases, n_hint=n_hint)


def _collect(data: UnitData, plan: UnitPlan, *, seed: int, phases: Sequence[int], n_hint: int | None) -> Collected:
    folds = (*plan.oof_years, V.LEARN_YEARS[-1] + 1)
    # rev 9 D1: L-cw imputes with the medians of the smallest training fold's rows usable under the served columns
    # (2010-11, or 2010-13 under remedy (b)); the P30-wide learners with those of their own smallest fold with
    # P30-usable rows (the fold's years extended one at a time until some exist; earlier folds have none and skip)
    smallest = tuple(range(V.LEARN_YEARS[0], plan.first_oof))
    first = [f for f in _years(data, plan, smallest, phases)]
    base = pd.concat(first, ignore_index=True) if first else pd.DataFrame(columns=list(ROW_COLS))
    lc = list(plan.lcw_columns)
    impute_lcw = L.medians(base[lc].to_numpy(float)) if len(base) and lc else np.zeros(len(lc))
    p30_years = list(smallest)
    base_p = base[p30_ok(base)] if len(base) else base
    while not len(base_p) and p30_years[-1] < V.LEARN_YEARS[-1]:
        p30_years.append(p30_years[-1] + 1)
        more = [f[p30_ok(f)] for f in _years(data, plan, (p30_years[-1],), phases)]
        base_p = pd.concat([base_p, *more], ignore_index=True) if more else base_p
    impute = L.medians(base_p[list(V.P30)].to_numpy(float)) if len(base_p) else np.zeros(len(V.P30))
    n_total = n_hint if n_hint is not None else sum(len(f) for f in _years(data, plan, V.LEARN_YEARS, phases))
    pi = min(1.0, MAX_TREE_ROWS / max(1, n_total))
    lcw: dict[int, dict[str, L.Moments]] = {y: {} for y in folds}
    hb: dict[int, dict[tuple[str, str], L.Moments]] = {y: {} for y in folds}
    pri = {y: PriorAcc() for y in folds}
    hb_idx = [V.P30.index(c) for c in HB_COLUMNS]
    samples = []
    n_rows = 0
    last = last_dec = 0
    per_year: dict[int, int] = {}
    per_year_p30: dict[int, int] = {}
    for f in _years(data, plan, V.LEARN_YEARS, phases):
        n_rows += len(f)
        ok30 = p30_ok(f)
        yrs = pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True)).year.to_numpy()
        for y in np.unique(yrs):
            per_year[int(y)] = per_year.get(int(y), 0) + int((yrs == y).sum())
            per_year_p30[int(y)] = per_year_p30.get(int(y), 0) + int(((yrs == y) & ok30).sum())
        v0m = train_mask(f, V.LEARN_YEARS[-1] + 1)
        if v0m.any():
            last = max(last, int(_ns(f["horizon_end_utc"])[v0m].max()))
            last_dec = max(last_dec, int(_ns(f["decision_utc"])[v0m].max()))
        zero = np.zeros(len(f))
        full = _td(f, V.P30, zero, design_effect=plan.design_effect)
        for Y in folds:
            m = train_mask(f, Y)
            if not m.any():
                continue
            pri[Y].add(f[m])
            sub = full.subset(m)
            xl = f.loc[m, lc].to_numpy(float) if lc else np.empty((int(m.sum()), 0))
            for inst, mo in L.moments(sub.subset(np.ones(m.sum(), bool), X=xl, columns=plan.lcw_columns),
                                      impute_lcw).items():
                lcw[Y][inst] = lcw[Y][inst] + mo if inst in lcw[Y] else mo
            mp = ok30[m]                     # HB on its P30-usable training rows only (rev 9 D1)
            if not mp.any():
                continue
            hsub = sub.subset(mp, X=sub.X[mp][:, hb_idx], columns=HB_COLUMNS)
            cls = np.array([V.instrument_class(i) for i in hsub.instrument])
            for c in np.unique(cls):
                for t in np.unique(hsub.tf[cls == c]):
                    mm = (cls == c) & (hsub.tf == t)
                    mo = L.moments(hsub.subset(mm, instrument=np.full(mm.sum(), c)), impute[hb_idx])[str(c)]
                    key = (str(t), str(c))
                    hb[Y][key] = hb[Y][key] + mo if key in hb[Y] else mo
        keep = _hash_u(f, seed) < pi
        samples.append(f[keep].assign(w=f["w"].to_numpy(float)[keep] / pi))
    sample = pd.concat(samples, ignore_index=True) if samples else pd.DataFrame(columns=list(ROW_COLS))
    return Collected(impute=impute, impute_lcw=impute_lcw, lcw=lcw, hb=hb, priors=pri, sample=sample, pi=pi, n_rows=n_rows,
                     train_end=pd.Timestamp(last_dec, tz="UTC") if last_dec else None,
                     label_end_max=pd.Timestamp(last, tz="UTC") if last else None,
                     rows_per_year=dict(sorted(per_year.items())),
                     p30_rows_per_year=dict(sorted(per_year_p30.items())))


def hb_fit(moms: Mapping[tuple[str, str], L.Moments], impute: Mapping[tuple[str, str], np.ndarray],
           design_effect: Mapping[tuple[str, str], float], lam_a: Mapping[str, float], *,
           lam_rw: float = LAMBDA_RW, lam0: float = V.LAMBDA_ROWS) -> L.HbArtifact:
    """HB over the tf ladder present in `moms`; the class prior is the largest λ_a of the units' buckets (the tighter
    prior, pre-named) so one solve covers every rung. On one thread (`one_thread`), as the rebuild runs it."""
    tfs = tuple(t for t in V.H_S if any(k[0] == t for k in moms))
    with one_thread():
        return L.fit_hb(moms, impute, HB_COLUMNS, tf_order=tfs, lam0=lam0, lam_rw=lam_rw,
                        lam_c=max(lam_a.values()) if lam_a else V.LAMBDA_ROWS, design_effect=design_effect)


def _sample_td(s: pd.DataFrame, cols: Sequence[str], pri: DC.CostPriors, plan: UnitPlan) -> L.TrainData:
    return _td(s, cols, cost_of(s, pri), design_effect=plan.design_effect)


def _with_dense(f: pd.DataFrame, dense: L.DenseArtifact | None, cost: np.ndarray) -> pd.DataFrame:
    out = f.copy()
    if dense is None:
        for c in L.DENSE_COLUMNS:
            out[c] = np.nan
    else:
        for c, v in dense.columns_for(f[list(V.P30)].to_numpy(float), cost).items():
            out[c] = v
    out[L.RANGE_TO_COST] = np.where(cost > 0, 1.0 / np.where(cost > 0, cost, 1.0), np.nan)
    return out


@dataclass
class FoldModels:
    year: int
    priors: DC.CostPriors
    lcw: L.LcwArtifact
    b8: L.LcwArtifact
    hb: L.HbArtifact | None
    dense: L.DenseArtifact | None
    gcw: dict[int, L.GcwArtifact]


def fit_fold(Y: int, col: Collected, plan: UnitPlan, *, hb: L.HbArtifact | None, seeds: Sequence[int],
             dense_by_year: dict[int, L.DenseArtifact]) -> FoldModels:
    pri = col.priors[Y].priors(year_start(Y) if Y <= V.LEARN_YEARS[-1] else WN.SPANS[WN.Window.LEARN][1])
    lcw = L.lcw_from_moments(col.lcw[Y], col.impute_lcw, plan.lcw_columns, lam=V.LAMBDA_ROWS, lam_a=plan.lam_a,
                             design_effect=plan.design_effect)
    b8 = L.lcw_from_moments({k: _drop_columns(v) for k, v in col.lcw[Y].items()}, np.zeros(0), (), lam=V.LAMBDA_ROWS,
                            lam_a=plan.lam_a, design_effect=plan.design_effect)
    s = col.sample
    m = train_mask(s, Y) & p30_ok(s)            # D-40 and G-cw train on P30-usable rows only (rev 9 D1)
    dense = None
    gcw: dict[int, L.GcwArtifact] = {}
    if m.sum() >= 2 * L.DENSE_PARAMS["min_data_in_leaf"]:
        tr = s[m].reset_index(drop=True)
        td = _sample_td(tr, V.P30, pri, plan)
        dense = L.fit_dense(td, t0_gross=tr["t0_gross"].to_numpy(float), seed=seeds[0])
        dense_by_year[Y] = dense
    if m.sum() >= 400:
        tr = s[m].reset_index(drop=True)
        tr = _forward_dense(tr, dense_by_year, pri)
        for sd in seeds:
            # the seed is the resample: always its own 90 % seed-hash subsample (quant M4 on #488; below the tree cap
            # every seed used to fit the same rows with no random component, so "5 seeds" were one fit)
            sub = tr[_hash_u(tr, sd) < SEED_SUBSAMPLE].reset_index(drop=True)
            gcw[sd] = L.fit_gcw(_sample_td(sub, gcw_columns(), pri, plan), seed=sd)
    return FoldModels(year=Y, priors=pri, lcw=lcw, b8=b8, hb=hb, dense=dense, gcw=gcw)


def _drop_columns(m: L.Moments) -> L.Moments:
    return L.Moments(n=m.n, s0=m.s0, sww=m.sww, s1=(), s2=(), sy=m.sy, sxy=())


def _forward_dense(f: pd.DataFrame, dense_by_year: Mapping[int, L.DenseArtifact], pri: DC.CostPriors) -> pd.DataFrame:
    """Cross-fitted dense columns: a row of year y takes the dense fit of fold y (it never saw the row's label); years
    with no fold (before the first OOF year) are NaN, imputed natively by the trees."""
    cost = cost_of(f, pri)
    out = _with_dense(f, None, cost)
    yr = pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True)).year.to_numpy()
    for y, d in dense_by_year.items():
        mm = yr == y
        if mm.any():
            cols = d.columns_for(f.loc[mm, list(V.P30)].to_numpy(float), cost[mm])
            for c, v in cols.items():
                out.loc[mm, c] = v
    return out


def score_frame(f: pd.DataFrame, fm: FoldModels, seeds: Sequence[int]) -> pd.DataFrame:
    """Every learner's value and keep on rows of one test year (AVG is added by the caller)."""
    cost = cost_of(f, fm.priors)
    g = f["gross"].to_numpy(float)
    out = pd.DataFrame({"instrument": f["instrument"].astype(str).to_numpy(), "tf": f["tf"].astype(str).to_numpy(),
                        "decision_utc": f["decision_utc"].to_numpy(), "side": f["side"].to_numpy(int),
                        "horizon_end_utc": f["horizon_end_utc"].to_numpy(),
                        "day": np.asarray(session_date(f["decision_utc"]).astype(str)), "w": f["w"].to_numpy(float),
                        # net: cost(x)-net, the AVG calibrator's fit target; net_read / net2: the realised read (M2)
                        "gross": O.clip_r(g), "cost": cost, "net": O.clip_r(g - cost), "net_read": net_read(f),
                        "net2": net_read(f, mult=2.0), "entry": f["entry"].to_numpy(float),
                        "base_dist": f["base_dist"].to_numpy(float), "cost_rt_bp": f["cost_rt_bp"].to_numpy(float),
                        "hour": pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True)).hour.to_numpy()})
    inst = out["instrument"].to_numpy()
    lc = [c for c in fm.lcw.columns]
    out["v_L-cw"] = fm.lcw.value(f[lc].to_numpy(float) if lc else np.empty((len(f), 0)), inst)
    out["v_B8"] = fm.b8.value(np.empty((len(f), 0)), inst)
    ok30 = p30_ok(f)                             # HB and G-cw score P30-usable rows only (rev 9 D1)
    out["p30_ok"] = ok30
    out["v_HB"] = np.where(ok30, fm.hb.value(f[list(HB_COLUMNS)].to_numpy(float), inst, out["tf"].to_numpy()),
                           np.nan) if fm.hb else np.nan
    if fm.gcw:
        X = _with_dense(f, fm.dense, cost)[list(gcw_columns())].to_numpy(float)
        for sd, a in fm.gcw.items():
            out[f"v_G-cw@{sd}"] = np.where(ok30, a.value(X), np.nan)
        out["v_G-cw"] = out[f"v_G-cw@{seeds[0]}"]
    else:
        out["v_G-cw"] = np.nan
    for name, kind in (("L-cw", "gross"), ("B8", "gross"), ("HB", "gross"), ("G-cw", "p")):
        v = out[f"v_{name}"].to_numpy(float)
        out[f"k_{name}"] = np.isfinite(v) & L.keep(kind, np.nan_to_num(v, nan=-np.inf), cost)   # type: ignore[arg-type]
    return out


def add_avg(cur: pd.DataFrame, earlier: pd.DataFrame, shas: Mapping[str, str]) -> tuple[pd.DataFrame, L.AvgArtifact
                                                                                         | None]:
    """AVG on the test rows `cur`, calibrated on `earlier` (OOF rows of earlier years already gap-cut by the caller).
    Rev 9 quant C1: its inputs are taken only on the intersection of the bases' usable rows (L-cw on the served
    columns, HB and G-cw on P30), in calibration and in scoring; other rows stay NaN. A base with no value at all is
    left out; fewer than two bases, or under 100 calibration rows: NaN (reported)."""
    cur = cur.copy()
    cur["v_AVG"], cur["k_AVG"] = np.nan, False
    if not len(earlier):
        return cur, None
    bases = [b for b in ("L-cw", "G-cw", "HB") if cur[f"v_{b}"].notna().any() and earlier[f"v_{b}"].notna().any()]
    if len(bases) < 2:
        return cur, None
    both_e = np.logical_and.reduce([earlier[f"v_{b}"].notna().to_numpy() for b in bases])
    both_c = np.logical_and.reduce([cur[f"v_{b}"].notna().to_numpy() for b in bases])
    e = earlier[both_e]
    if len(e) < 100 or not both_c.any():
        return cur, None
    kinds = {b: L.LEARNERS[b].kind or "gross" for b in bases}
    av = L.fit_avg({b: e[f"v_{b}"].to_numpy(float) for b in bases}, kinds, {b: shas.get(b, "") for b in bases},
                   e["cost"].to_numpy(float), e["net"].to_numpy(float), e["w"].to_numpy(float))
    c = cur[both_c]
    v = av.value({b: c[f"v_{b}"].to_numpy(float) for b in bases}, c["cost"].to_numpy(float))
    cur.loc[both_c, "v_AVG"] = v
    cur.loc[both_c, "k_AVG"] = L.keep("p", v, c["cost"].to_numpy(float))
    return cur, av


def code_version(root: Path = ROOT) -> dict[str, Any]:
    def git(*a: str) -> str:
        try:
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""
    return {"git_head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", *CODE_FILES)),
            "files": {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in CODE_FILES
                      if (root / f).exists()}}


def settings() -> dict[str, Any]:
    return {"spec": V.SPEC_VERSION, "lambda_rows": V.LAMBDA_ROWS, "lambda_rw": LAMBDA_RW, "riley": V.RILEY_PER_PARAM,
            "clip_r": V.CLIP_R, "first_oof": [V.FIRST_OOF, V.FIRST_OOF_B], "max_tree_rows": MAX_TREE_ROWS,
            "gcw": L.GCW_PARAMS, "dense": L.DENSE_PARAMS, "gbt_threads": L.GBT_THREADS, "seeds": list(SEEDS),
            "hb_columns": list(HB_COLUMNS), "p30": list(V.P30),
            "gap": "per unit: max(1 d, max H over the unit's tfs)"}


@dataclass
class UnitResult:
    report: dict[str, Any]
    artifacts: dict[str, str]                      # name -> JSON
    oof: pd.DataFrame                              # fit-only: `sealed_scores` (no label column); read: every column
    # the fit's labelled OOF rows, v0 models and collected pass: in memory only, for `sr_v3_report.read_unit` (never
    # written by a fit-only run)
    full: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)
    v0: FoldModels | None = field(default=None, repr=False)
    col: Collected | None = field(default=None, repr=False)


# the OOF columns a fit-only run keeps: row keys, the decision-time cost(x), each learner's value and keep
SCORE_KEYS: tuple[str, ...] = ("instrument", "tf", "decision_utc", "side", "horizon_end_utc", "day", "year", "cost",
                                "p30_ok")


def sealed_scores(oof: pd.DataFrame) -> pd.DataFrame:
    """Quant H2 on #488: the OOF scores without any label column (gross, net, prices), so nothing read from them is
    a read statistic. Their sha (`scores_sha`) goes into the report and every manifest before any read exists."""
    if not len(oof):
        return pd.DataFrame(columns=list(SCORE_KEYS))
    cols = [c for c in SCORE_KEYS if c in oof] + sorted(str(c) for c in oof.columns if str(c).startswith(("v_", "k_")))
    return oof[cols].reset_index(drop=True)


def scores_sha(scores: pd.DataFrame) -> str:
    """sha256 of the sealed scores' content (column names and values in order; independent of the parquet writer)."""
    s = sealed_scores(scores)
    h = hashlib.sha256(json.dumps(list(s.columns)).encode())
    if len(s):
        h.update(pd.util.hash_pandas_object(s, index=False).to_numpy().tobytes())
    return h.hexdigest()


def fit_unit(data: UnitData, plan: UnitPlan, *, hb_by_fold: Mapping[int, L.HbArtifact] | None,
             seeds: Sequence[int] = SEEDS, col: Collected | None = None) -> UnitResult:
    """Walk-forward fit, OOF scoring and v0 artifacts for one unit (see the module docstring): fit-only (quant H2 on
    #488): artifacts, sealed OOF scores and their sha, and the fit report; NO S, kept mean, S at 2x cost, D or other
    read statistic. The read is `sr_v3_report.read_unit`; the run record `sr_v3_report.record_unit`. Fits on one
    BLAS/OpenMP thread, as `run_manifest.build` rebuilds: a multi-threaded reduction can change the last bit and so
    the artifact sha."""
    with one_thread():
        return _fit_unit(data, plan, hb_by_fold=hb_by_fold, seeds=seeds, col=col)


def _fit_unit(data: UnitData, plan: UnitPlan, *, hb_by_fold: Mapping[int, L.HbArtifact] | None,
              seeds: Sequence[int], col: Collected | None) -> UnitResult:
    col = col or collect(data, plan, seed=seeds[0])
    dense_by_year: dict[int, L.DenseArtifact] = {}
    oof_parts: list[pd.DataFrame] = []
    avg_by_year: dict[int, str] = {}
    avg_rows: dict[int, int] = {}
    no_train: list[int] = []
    for Y in plan.oof_years:
        if not col.priors[Y].n:          # the gap leaves this fold no training row (M1): skipped, reported
            no_train.append(Y)
            continue
        fm = fit_fold(Y, col, plan, hb=(hb_by_fold or {}).get(Y), seeds=seeds, dense_by_year=dense_by_year)
        test = [f for f in _years(data, plan, (Y,), (0,))]
        if not test:
            continue
        sc = score_frame(pd.concat(test, ignore_index=True), fm, seeds)
        sc["year"] = Y
        earlier = pd.concat(oof_parts, ignore_index=True) if oof_parts else sc.iloc[:0]
        if len(earlier):        # validation rows: labelled before the test year's start minus the gap
            earlier = earlier[train_mask(earlier, Y)]
        sc, av = add_avg(sc, earlier, {"L-cw": L.artifact_sha(fm.lcw), "G-cw": L.artifact_sha(fm.gcw[seeds[0]])
                                        if fm.gcw else "", "HB": L.artifact_sha(fm.hb) if fm.hb else ""})
        if av is not None:
            avg_by_year[Y] = L.artifact_sha(av)
            for sd in seeds[1:]:              # AVG over each G-cw seed (seed stability of the fusion)
                if f"v_G-cw@{sd}" in sc:
                    alt, _ = add_avg(sc.assign(**{"v_G-cw": sc[f"v_G-cw@{sd}"]}), earlier, {})
                    sc[f"v_AVG@{sd}"] = alt["v_AVG"]
            sc[f"v_AVG@{seeds[0]}"] = sc["v_AVG"]
        avg_rows[Y] = int(sc["v_AVG"].notna().sum())   # rev 9 C1: the intersection AVG is scored on
        oof_parts.append(sc)
    oof = pd.concat(oof_parts, ignore_index=True) if oof_parts else pd.DataFrame()
    v0 = fit_fold(V.LEARN_YEARS[-1] + 1, col, plan, hb=(hb_by_fold or {}).get(V.LEARN_YEARS[-1] + 1), seeds=seeds[:1],
                  dense_by_year=dict(dense_by_year))
    arts: dict[str, str] = {"L-cw": L.artifact_json(v0.lcw), "B8": L.artifact_json(v0.b8)}
    if v0.gcw:
        arts["G-cw"] = L.artifact_json(v0.gcw[seeds[0]])
    if v0.dense is not None:
        arts["D-40"] = L.artifact_json(v0.dense)
    if v0.hb is not None:
        arts["HB"] = L.artifact_json(v0.hb)
    if len(oof) and oof["v_L-cw"].notna().any():
        _, av0 = add_avg(oof, oof, {"L-cw": L.artifact_sha(v0.lcw), "G-cw": L.artifact_sha(v0.gcw[seeds[0]])
                                             if v0.gcw else "", "HB": L.artifact_sha(v0.hb) if v0.hb else ""})
        if av0 is not None:
            arts["AVG"] = L.artifact_json(av0)
    gate = data.gate_status()
    report = fit_report(oof, plan, col)
    report["mode"] = "fit-only"
    report["gate_eligible"], report["gate_block"] = gate["gate_eligible"], gate["gate_block"]
    report.update(cost_status(data, plan))
    report["oof_scores_sha256"] = scores_sha(oof)
    report["artifacts"] = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in arts.items()}
    report["avg_by_year"] = avg_by_year
    report["avg_intersection_rows_by_year"] = avg_rows
    report["folds_without_training_rows"] = no_train
    report["gap_days"] = {t: gap(t).total_seconds() / 86_400 for t in plan.tfs}
    report["train_end"] = col.train_end.isoformat() if col.train_end is not None else None
    report["label_end_max"] = col.label_end_max.isoformat() if col.label_end_max is not None else None
    return UnitResult(report=report, artifacts=arts, oof=sealed_scores(oof), full=oof, v0=v0, col=col)



PRIOR_COST_VERSION = "prior"
POOLED_PRIOR_BLOCK = ("pooled body: FX and other non-gold costs are priors (spread + commission, W2 floor) with no "
                      "measured anchor; a descriptive read only, never a gate read (quant re-verify on #488)")


def cost_status(data: UnitData, plan: UnitPlan) -> dict[str, Any]:
    """The run's cost version and whether its read may be a gate read. Any body with a non-gold member is costed at
    priors: cost_version "prior", never a gate read, until FX has a measured cost anchor (quant re-verify on #488)."""
    table = data.cost_version()
    pooled = any(m != V.GOLD for m in plan.members)
    return {"cost_version": PRIOR_COST_VERSION if pooled else table, "cost_table_version": table,
            "gate_read": not pooled, "gate_read_block": POOLED_PRIOR_BLOCK if pooled else None}


def fit_report(oof: pd.DataFrame, plan: UnitPlan, col: Collected) -> dict[str, Any]:
    """The fit-only report: what was fitted on which rows, never how well (no label value enters it)."""
    rep: dict[str, Any] = {"unit": plan.unit, "tfs": list(plan.tfs), "members": list(plan.members),
                           "level": plan.stage0.get("level"), "judged": plan.stage0.get("judged"),
                           "served_lcw": list(plan.lcw_columns), "first_oof": plan.first_oof,
                           "rows_learn": col.n_rows, "tree_sample_pi": col.pi,
                           # rev 9 D1: the judged population (rows usable under the served columns) and the P30 rows
                           # per learn year; the mask's drops are the difference
                           "rows_per_year": dict(col.rows_per_year), "p30_rows_per_year": dict(col.p30_rows_per_year),
                           "oof_rows": int(len(oof)), "oof_rows_per_year": {int(str(k)): int(v) for k, v in
                                                                            oof.groupby("year").size().items()}
                           if len(oof) else {},
                           "note": "fit-only (quant H2 on #488): artifacts and sealed OOF scores; no read statistic "
                                   "until the label read is gate_eligible and costed at ub95"}
    return rep


def plan_dict(plan: UnitPlan) -> dict[str, Any]:
    return {"unit": plan.unit, "tfs": list(plan.tfs), "members": list(plan.members),
            "lcw_columns": list(plan.lcw_columns), "first_oof": plan.first_oof, "lam_a": plan.lam_a,
            "design_effect": plan.design_effect, "stage0": plan.stage0}


def plan_of(d: Mapping[str, Any]) -> UnitPlan:
    return UnitPlan(unit=str(d["unit"]), tfs=tuple(d["tfs"]), members=tuple(d["members"]),
                    lcw_columns=tuple(d["lcw_columns"]), first_oof=int(d["first_oof"]), lam_a=float(d["lam_a"]),
                    design_effect=float(d["design_effect"]), stage0=dict(d.get("stage0") or {}))


def hb_from_plans(data: UnitData, plans: Sequence[UnitPlan], *, seed: int) -> dict[int, L.HbArtifact]:
    """HB per fold over every listed unit's (tf, class) cells: each cell imputed with its unit's smallest-fold medians,
    with its unit's design effect; the class prior is the largest of the units' λ_a."""
    moms: dict[int, dict[tuple[str, str], L.Moments]] = {}
    imp: dict[tuple[str, str], np.ndarray] = {}
    deff: dict[tuple[str, str], float] = {}
    lam_a: dict[str, float] = {}
    idx = [V.P30.index(c) for c in HB_COLUMNS]
    for plan in plans:
        col = collect(data, plan, seed=seed)
        lam_a[plan.unit] = plan.lam_a
        for y, cells in col.hb.items():
            for k, m in cells.items():
                moms.setdefault(y, {})[k] = m
                imp[k], deff[k] = col.impute[idx], plan.design_effect
    return {y: hb_fit(c, {k: imp[k] for k in c}, {k: deff[k] for k in c}, lam_a) for y, c in moms.items()}


def frames_file(data: FrameData, path: Path) -> Path:
    """One parquet of every frame (the rebuild's data file for in-memory data)."""
    pd.concat([f for _, f in sorted(data.frames.items(), key=lambda kv: kv[0])], ignore_index=True).to_parquet(
        path, index=False)
    return path


def frames_from(path: Path | str) -> FrameData:
    f = pd.read_parquet(path)
    from goldbot.data import holdout_guard
    holdout_guard.refuse(f, what=f"sr-v3 frames {path}", col="decision_utc")
    yr = pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True)).year.to_numpy()
    out = {}
    for key, g in f.assign(_y=yr).groupby(["instrument", "tf", "_y"], sort=True):
        i, t, y = (str(x) for x in key)
        out[(i, t, int(y))] = g.drop(columns="_y").reset_index(drop=True)
    return FrameData(out)


__all__ = ["BUILT", "CODE_FILES", "MANIFEST_KEY", "MAX_TREE_ROWS", "OUT", "ROW_COLS", "SEEDS", "W2_COST_VERSION",
           "Collected", "CostCellMissing", "CostTable", "FrameData", "UnitData", "UnitPlan", "UnitResult", "code_version",
           "collect", "cost_of", "cost_parts", "fit_unit", "frames_file", "frames_from", "gap", "hb_fit",
           "hb_from_plans", "net_read", "plan_from_stage0", "realised_cost", "scores_sha", "sealed_scores", "settings",
           "stage0", "stage0_counts", "train_mask", "usable_under"]
