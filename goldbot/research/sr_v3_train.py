"""The sr-v3 history trainer: Stage 0 -> walk-forward fit of every built learner -> training report and run log
(card docs/research/prereg-2026-11-26-sr-v3.md rev 6, sections 2.3, 3.3-3.6, 5.1 and 5.4; SYNTH-ml items 3 and 10;
ADR 0106 "Training" and "Model quality").

What it does per judged unit (1m, 5m, 15m, 30m, 1h, 2h, 4h, and 1d+1w pooled), on learn 2010-17 only:
1. **Stage 0** (`stage0_counts`, `sr_v3.decide_unit`): label-blind. The ladder is computed from the outcome-blind
   skeleton (one T2 long event per bar, phase 0, tier-usable under P30) and the uniqueness weights, for the gold
   body and the pooled body, on fold 2010-11 (2010-13 under remedy (b)). It records the D-12 file, tiers and
   partition keys by hash. No label value is read (card test 18 (iv)).
2. **Folds** (card 5.3 Stage L, R-B; ADR 0106 "a gap between train, validation and test"): expanding yearly folds,
   OOF years `first_oof`..2017. A training row of test year Y has its label horizon end before Y's start minus the
   gap `windows.label_embargo(H)` = max(1 d, H). The AVG calibrator ("validation") uses only OOF rows of earlier
   years under the same gap. Inputs are imputed with the smallest fold's medians and standardised with each fold's
   own training rows (normalised with past data only).
3. **Fit**: L-cw and HB from additive moments over every row (streamed by year), G-cw and the D-40 dense prior on a
   deterministic Horvitz-Thompson thinned sample of at most `MAX_TREE_ROWS` rows (weights / π), AVG on the earlier
   OOF years. cost(x) per fold = decision_cost.cost_x with `CostPriors` from that fold's training rows only (affine in
   p_stop and the night share, so it is exact per fold). Net R = clip(gross − cost(x)).
4. **v0**: every learner refitted on all learn rows: the frozen artifacts, each with its sha (deterministic bytes).
5. **Report** (descriptive, never a selector; Stage L gates are the evaluator's read): per learner, the keep share,
   kept mean, S with its split, S at 2x cost, the paired D per OOS session day against G-cw (ADR 0106: GBT is the
   baseline every model must beat out of sample after costs) and between every pair, B0/B1/B8, the matched random
   twin (expectation), buy-and-hold of the same market after one round-trip cost, weighted calibration slope, R²_OOS,
   within-instrument IC and AUC (diagnostic), score deciles, the N6 shuffle check and the oracle canaries, seed
   stability over `SEEDS` (5), the parameter-shift check, the learning curve, half-split sign agreement, the train-to-
   OOF gap, gold's shrunk and unshrunk levels and v0's keep share on 2015-17 (the H1 breaker reference).
6. **Run record** (ADR 0106 decision 7, ADR 0109): `record_unit` writes one research/run_manifest.py manifest per v0
   artifact (data version and file shas, settings, seeds, code sha, train end, results, the model_quality verdicts:
   seed_stability over 5 seeds, param_shift, beats_baseline against the G-cw manifest), with `build_artifact` as the
   builder, so `run_manifest.rebuild` / scripts/research/rebuild_model.py refit any model byte for byte.

Labels are read only through `dense_labels.load_labels` on the learn window (`StoreData`). Until ADR 0085 A4 records
the tiers sha (PR #482) it raises SealedStore and nothing here works around it. Never confirm, test_old or the holdout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from goldbot.data.daily import session_date
from goldbot.research import decision_cost as DC
from goldbot.research import diagnostics as G
from goldbot.research import learners as L
from goldbot.research import objective as O
from goldbot.research import sr_v3 as V
from goldbot.research import windows as WN

log = logging.getLogger("goldbot.sr_v3_train")

ROOT = Path(__file__).resolve().parents[2]
OUT = Path.home() / "tmp" / "sr-v3"                 # market-derived outputs stay local (ADR 0048)
MAX_TREE_ROWS = 2_000_000
SEEDS: tuple[int, ...] = tuple(V.SEED_CANARY + i for i in range(5))      # ADR 0106: >= 5 seeds
CANARY_DELTAS: tuple[float, ...] = (0.03, 0.05, 0.10)


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
SHIFT = (0.8, 1.25)                                 # the parameter-shift check: each named setting x 0.8 and x 1.25
CURVE_SIZES: tuple[int, ...] = (1_000, 3_000, 10_000, 30_000, 100_000)
LAMBDA_RW = V.LAMBDA_ROWS                           # HB: τ = 0.05 R per sd per tf step, as λ
HB_COLUMNS: tuple[str, ...] = tuple(c for c in V.P30 if c != "dist_vwap48_atr")   # bid-only safe for every membership
CODE_FILES: tuple[str, ...] = ("goldbot/research/sr_v3.py", "goldbot/research/learners.py",
                               "goldbot/research/sr_v3_train.py", "goldbot/research/sr_v3_serve.py",
                               "goldbot/research/objective.py", "goldbot/research/decision_cost.py")
ROW_COLS: tuple[str, ...] = ("instrument", "tf", "phase", "decision_utc", "horizon_end_utc", "side", "gross",
                             "t0_gross", "cost_a", "cost_b", "cost_c", "stop", "nights_held", "nights_full", "w",
                             "entry", "base_dist", "cost_rt_bp", *(c for c in V.P30 if c != "side"))
BUILT = ("L-cw", "G-cw", "HB", "AVG")


# ---------------------------------------------------------------------------------------------- data access
class UnitData(Protocol):
    """A unit's rows, one (year, instrument) frame of `ROW_COLS` at a time (labels already read under the gate)."""
    def frame(self, instrument: str, tf: str, year: int, *, phases: Sequence[int]) -> pd.DataFrame: ...
    def manifest(self) -> dict[str, Any]: ...
    def version(self, members: Sequence[str], tfs: Sequence[str], phases: Sequence[int]) -> str: ...


@dataclass
class FrameData:
    """In-memory frames (tests, and a caller who already holds rows). Keyed (instrument, tf, year)."""
    frames: Mapping[tuple[str, str, int], pd.DataFrame]
    version_name: str = "in-memory"

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


def cost_parts(rows: pd.DataFrame, swap: Any | None = None) -> pd.DataFrame:
    """cost(x) = a + b·p_stop[arm] + c·night_share[arm] per row, from decision-time columns only (decision_cost.cost_x
    and dense_labels.net_r's round-trip term, rearranged so one row set serves every fold's priors)."""
    sw = swap or DC.swap_spec()
    k = DC.row_k(rows, sw)
    rt = rows["cost_rt_bp"].to_numpy(float)
    rate = np.abs(DC._rate_signed(rows["side"].to_numpy(float), sw))
    full = DC.nights_full_hold(rows, sw)
    return pd.DataFrame({"cost_a": k * rt, "cost_b": k * DC.OVERSHOOT_BP, "cost_c": full * k * 1e4 * rate,
                         "nights_full": full})


# ---------------------------------------------------------------------------------------------- folds and priors
def gap(tf: str) -> pd.Timedelta:
    return WN.label_embargo(pd.Timedelta(seconds=V.H_S[tf]))


def year_start(y: int) -> pd.Timestamp:
    return pd.Timestamp(f"{y}-01-01", tz="UTC")


def train_mask(f: pd.DataFrame, test_year: int) -> np.ndarray:
    """Rows that may train for test year `test_year`: labelled before its start minus the gap. The v0 fold (the year
    after learn) has no test block after it: every row labelled by the learn end trains (load_labels' own cut)."""
    if not len(f):
        return np.zeros(0, bool)
    if test_year > V.LEARN_YEARS[-1]:
        return _ns(f["horizon_end_utc"]) <= WN.SPANS[WN.Window.LEARN][1].value
    cut = np.array([(year_start(test_year) - gap(t)).value for t in f["tf"].astype(str)])
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


def cost_of(f: pd.DataFrame, pri: DC.CostPriors) -> np.ndarray:
    arm = arm_of(f)
    p = np.array([pri.p_stop.get(a, pri.p_stop_pooled) for a in arm])
    ns = np.array([pri.night_share.get(a, pri.night_share_pooled) for a in arm])
    return f["cost_a"].to_numpy(float) + f["cost_b"].to_numpy(float) * p + f["cost_c"].to_numpy(float) * ns


# ---------------------------------------------------------------------------------------------- Stage 0
def stage0_counts(events: pd.DataFrame, *, years: Sequence[int], kish_eff: float | None = None) -> list[V.InstCounts]:
    """Label-blind counts per instrument from outcome-blind events (instrument, phase, decision_utc, usable, u): phase 0,
    usable, decided in `years`. A frame carrying label columns gives the same counts (they are never touched)."""
    e = events[(events["phase"].to_numpy() == 0) & events["usable"].to_numpy(bool)]
    yr = pd.DatetimeIndex(pd.to_datetime(e["decision_utc"], utc=True)).year
    e = e[np.isin(yr, list(years))]
    out = []
    for inst, g in e.groupby("instrument", sort=True):
        day = np.asarray(session_date(g["decision_utc"]).astype(str))
        out.append(V.inst_counts(str(inst), g["u"].to_numpy(float), day, kish_eff=kish_eff))
    return out


def stage0(unit: str, tfs: Sequence[str], events: Mapping[str, pd.DataFrame], *, rho_file: Path,
           available: Mapping[str, Sequence[str]] | None = None) -> V.Stage0Unit:
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
        rho["pooled"] = V.rho_inputs(rho_file, body="pooled", members=members, tf=tf0)
    av = {"gold": tuple(available.get("gold", V.P30)) if available else V.P30,
          "pooled": tuple(available.get("pooled", HB_COLUMNS)) if available else HB_COLUMNS}

    def counts(years: Sequence[int]) -> dict[str, list[V.InstCounts]]:
        def cat(d: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
            return pd.concat(list(d.values()), ignore_index=True) if d else pd.DataFrame(
                columns=["instrument", "phase", "decision_utc", "usable", "u"])
        return {"gold": stage0_counts(cat(gold_ev), years=years), "pooled": stage0_counts(cat(pooled_ev), years=years)}
    return V.decide_unit(unit, tfs, counts_a=counts((2010, 2011)), counts_b=counts((2010, 2011, 2012, 2013)),
                         rho=rho, available=av)


# ---------------------------------------------------------------------------------------------- the unit fit
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
    train_end: pd.Timestamp | None = None    # the last label instant v0 trains on (run manifest train_end)


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
    sample. Folds: the OOF years plus 2018 (v0: every learn row)."""
    folds = (*plan.oof_years, V.LEARN_YEARS[-1] + 1)
    first = [f for f in _years(data, plan, (2010, 2011), phases)]
    base = pd.concat(first, ignore_index=True) if first else pd.DataFrame(columns=list(ROW_COLS))
    impute = L.medians(base[list(V.P30)].to_numpy(float)) if len(base) else np.zeros(len(V.P30))
    lc = list(plan.lcw_columns)
    impute_lcw = L.medians(base[lc].to_numpy(float)) if len(base) and lc else np.zeros(len(lc))
    n_total = n_hint if n_hint is not None else sum(len(f) for f in _years(data, plan, V.LEARN_YEARS, phases))
    pi = min(1.0, MAX_TREE_ROWS / max(1, n_total))
    lcw: dict[int, dict[str, L.Moments]] = {y: {} for y in folds}
    hb: dict[int, dict[tuple[str, str], L.Moments]] = {y: {} for y in folds}
    pri = {y: PriorAcc() for y in folds}
    hb_idx = [V.P30.index(c) for c in HB_COLUMNS]
    samples = []
    n_rows = 0
    last = 0
    for f in _years(data, plan, V.LEARN_YEARS, phases):
        n_rows += len(f)
        v0m = train_mask(f, V.LEARN_YEARS[-1] + 1)
        if v0m.any():
            last = max(last, int(_ns(f["horizon_end_utc"])[v0m].max()))
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
            hsub = sub.subset(np.ones(m.sum(), bool), X=sub.X[:, hb_idx], columns=HB_COLUMNS)
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
                     train_end=pd.Timestamp(last, tz="UTC") if last else None)


def hb_fit(moms: Mapping[tuple[str, str], L.Moments], impute: Mapping[tuple[str, str], np.ndarray],
           design_effect: Mapping[tuple[str, str], float], lam_a: Mapping[str, float], *,
           lam_rw: float = LAMBDA_RW, lam0: float = V.LAMBDA_ROWS) -> L.HbArtifact:
    """HB over the tf ladder present in `moms`; the class prior is the largest λ_a of the units' buckets (the tighter
    prior, pre-named) so one solve covers every rung."""
    tfs = tuple(t for t in V.H_S if any(k[0] == t for k in moms))
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
    m = train_mask(s, Y)
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
            sub = tr if col.pi >= 1.0 else tr[_hash_u(tr, sd) < 0.9].reset_index(drop=True)   # seed: the resample
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


# ---------------------------------------------------------------------------------------------- OOF scoring and report
def score_frame(f: pd.DataFrame, fm: FoldModels, seeds: Sequence[int]) -> pd.DataFrame:
    """Every learner's value and keep on rows of one test year (AVG is added by the caller)."""
    cost = cost_of(f, fm.priors)
    g = f["gross"].to_numpy(float)
    out = pd.DataFrame({"instrument": f["instrument"].astype(str).to_numpy(), "tf": f["tf"].astype(str).to_numpy(),
                        "decision_utc": f["decision_utc"].to_numpy(), "side": f["side"].to_numpy(int),
                        "horizon_end_utc": f["horizon_end_utc"].to_numpy(),
                        "day": np.asarray(session_date(f["decision_utc"]).astype(str)), "w": f["w"].to_numpy(float),
                        "gross": O.clip_r(g), "cost": cost, "net": O.clip_r(g - cost),
                        "net2": O.clip_r(g - 2 * cost), "entry": f["entry"].to_numpy(float),
                        "base_dist": f["base_dist"].to_numpy(float), "cost_rt_bp": f["cost_rt_bp"].to_numpy(float),
                        "hour": pd.DatetimeIndex(pd.to_datetime(f["decision_utc"], utc=True)).hour.to_numpy()})
    inst = out["instrument"].to_numpy()
    lc = [c for c in fm.lcw.columns]
    out["v_L-cw"] = fm.lcw.value(f[lc].to_numpy(float) if lc else np.empty((len(f), 0)), inst)
    out["v_B8"] = fm.b8.value(np.empty((len(f), 0)), inst)
    out["v_HB"] = fm.hb.value(f[list(HB_COLUMNS)].to_numpy(float), inst, out["tf"].to_numpy()) if fm.hb else np.nan
    if fm.gcw:
        X = _with_dense(f, fm.dense, cost)[list(gcw_columns())].to_numpy(float)
        for sd, a in fm.gcw.items():
            out[f"v_G-cw@{sd}"] = a.value(X)
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
    Needs every base present; the first OOF year has no calibration rows and stays NaN (reported)."""
    bases = [b for b in ("L-cw", "G-cw", "HB") if cur[f"v_{b}"].notna().all() and len(earlier)
             and earlier[f"v_{b}"].notna().all()]
    cur = cur.copy()
    if len(bases) < 2 or len(earlier) < 100:
        cur["v_AVG"], cur["k_AVG"] = np.nan, False
        return cur, None
    kinds = {b: L.LEARNERS[b].kind or "gross" for b in bases}
    av = L.fit_avg({b: earlier[f"v_{b}"].to_numpy(float) for b in bases}, kinds, {b: shas.get(b, "") for b in bases},
                   earlier["cost"].to_numpy(float), earlier["net"].to_numpy(float), earlier["w"].to_numpy(float))
    v = av.value({b: cur[f"v_{b}"].to_numpy(float) for b in bases}, cur["cost"].to_numpy(float))
    cur["v_AVG"], cur["k_AVG"] = v, L.keep("p", v, cur["cost"].to_numpy(float))
    return cur, av


def _day_boot(d_by_day: np.ndarray, *, seed: int = V.SEED_BOOT, boot: int = 2_000) -> tuple[float, float, float]:
    """(mean per day, 5 %, 95 %) of a per-day series under the stationary day-block bootstrap (MEAN_BLOCK_DAYS).
    Uses objective's draw generator so every day-block read in the repo draws the same way."""
    n = len(d_by_day)
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    draws = np.concatenate([(W @ d_by_day) / n for W in O._day_weights(n, boot=boot, seed=seed,
                                                                         mean_block=V.MEAN_BLOCK_DAYS)])
    return float(d_by_day.mean()), float(np.quantile(draws, 0.05)), float(np.quantile(draws, 0.95))


def learner_read(o: pd.DataFrame, name: str) -> dict[str, Any]:
    """The descriptive obj-v1 read of one learner on OOF rows (no bootstrap beyond the per-day D)."""
    k = o[f"k_{name}"].to_numpy(bool)
    if not len(o):
        return {"n": 0}
    rd = O.read(o["net"].to_numpy(float), o["gross"].to_numpy(float), k, o["day"].to_numpy(),
                cost_x=o["cost"].to_numpy(float), boot=0, stress_keep=k)
    net2 = o["net2"].to_numpy(float)
    kf = np.zeros(len(k)) if rd.abstain else k.astype(float)
    per_day = pd.Series(o["net"].to_numpy(float) * kf).groupby(o["day"].to_numpy()).sum()
    return {"n": rd.n, "days": rd.n_days, "kept": rd.n_kept, "kept_days": rd.n_kept_days, "abstain": rd.abstain,
            "keep_share": rd.keep_share, "kept_mean_net": rd.kept_mean, "mean_net": rd.mean_net, "s": rd.s,
            "s_2x_cost": float((net2 * kf).mean() - max(0.0, float(net2.mean()))),
            "split": rd.decomposition.model_dump(), "net_r_per_day": float(per_day.mean()),
            "n_eff": O.kish_n_eff(o["w"].to_numpy(float))}


def paired_d(o: pd.DataFrame, a: str, b: str) -> dict[str, float]:
    """SYNTH R3: Δ net R per OOS session day, −(Σ dropped net)/days, as Σ net·(keep_a − keep_b) / days, with a
    day-block 90 % band. Reported, never judged."""
    ok = o[f"v_{a}"].notna() & o[f"v_{b}"].notna()
    x = o[ok]
    if not len(x):
        return {"n": 0}
    d = x["net"].to_numpy(float) * (x[f"k_{a}"].to_numpy(float) - x[f"k_{b}"].to_numpy(float))
    by_day = pd.Series(d).groupby(x["day"].to_numpy()).sum().sort_index().to_numpy()
    m, lo, hi = _day_boot(by_day)
    return {"n": int(len(x)), "days": int(len(by_day)), "d_per_day": m, "lo90": lo, "hi90": hi}


def twin_expectation(o: pd.DataFrame, name: str) -> float:
    """B7, the side- and hour-matched random twin at the learner's keep share per (side, hour) cell, in expectation
    (no draw): Σ_cells share · mean net · n / N − max(0, mean net)."""
    k = o[f"k_{name}"].to_numpy(float)
    g = pd.DataFrame({"c": o["side"].astype(str) + "|" + o["hour"].astype(str), "k": k, "net": o["net"]})
    agg = g.groupby("c").agg(share=("k", "mean"), mu=("net", "mean"), n=("net", "size"))
    mean = float(o["net"].mean())
    return float((agg["share"] * agg["mu"] * agg["n"]).sum() / len(o) - max(0.0, mean))


def buy_and_hold(o: pd.DataFrame) -> dict[str, Any]:
    """ADR 0106 benchmark: buy the instrument at its first OOS decision, hold to the last, pay one round trip. In % of
    price and in R per OOS session day (R = the median T2 base distance)."""
    out = {}
    for inst, g in o.sort_values("decision_utc").groupby("instrument"):
        if len(g) < 2:
            continue
        p0, p1 = float(g["entry"].iloc[0]), float(g["entry"].iloc[-1])
        rt = float(g["cost_rt_bp"].median()) * 1e-4 * p0
        r = float(g["base_dist"].median())
        days = int(g["day"].nunique())
        out[str(inst)] = {"pct": (p1 - p0 - rt) / p0, "r_per_day": (p1 - p0 - rt) / r / days if r > 0 else None,
                          "from": str(g["decision_utc"].iloc[0]), "to": str(g["decision_utc"].iloc[-1])}
    return out


def calibration(o: pd.DataFrame, name: str, kind: str) -> dict[str, Any]:
    v = o[f"v_{name}"].to_numpy(float)
    ok = np.isfinite(v)
    if ok.sum() < 10:
        return {}
    w = o["w"].to_numpy(float)[ok]
    if kind == "gross":
        cal = G.weighted_calibration(v[ok], o["gross"].to_numpy(float)[ok], w)
        return {"slope": cal.slope, "intercept": cal.intercept,
                "r2_oos": G.mse_skill(v[ok], o["gross"].to_numpy(float)[ok], w)}
    y = (o["net"].to_numpy(float)[ok] > 0).astype(float)
    cw = O.net_logit_weights(o["net"].to_numpy(float)[ok], w)
    cal = G.weighted_calibration(v[ok], y, cw)
    pl = L.fit_platt(v[ok], y, cw)
    return {"slope": cal.slope, "intercept": cal.intercept, "brier_skill_w": G.brier_skill(v[ok], y, cw),
            "platt": pl.model_dump()}


def rank_diagnostics(o: pd.DataFrame, name: str, kind: str) -> dict[str, Any]:
    v = o[f"v_{name}"].to_numpy(float)
    ok = np.isfinite(v)
    if ok.sum() < 50:
        return {}
    s = v[ok] - o["cost"].to_numpy(float)[ok] if kind == "gross" else v[ok]
    net = o["net"].to_numpy(float)[ok]
    inst = o["instrument"].to_numpy()[ok]
    ic = {str(i): float(pd.Series(s[inst == i]).corr(pd.Series(net[inst == i]), method="spearman"))
          for i in np.unique(inst) if (inst == i).sum() > 10}
    dec = G.score_deciles(s, net) if len(s) >= 10 else pd.DataFrame()
    return {"ic_within_instrument": ic, "auc_within_instrument": G.within_arm_auc(s, net > 0, inst),
            "deciles": dec.reset_index().to_dict(orient="records")}


# ---------------------------------------------------------------------------------------------- canaries, shift, curve
def _fit_score(name: str, tr: pd.DataFrame, te: pd.DataFrame, plan: UnitPlan, pri: DC.CostPriors, impute: np.ndarray,
               *, seed: int, extra: Sequence[str] = (), shift: Mapping[str, float] | None = None,
               lam_mult: float = 1.0, lam_a_mult: float = 1.0) -> np.ndarray:
    """Fit one learner on `tr`, return its keep on `te` (the canary, shuffle and shift harness; HB on its own rung)."""
    cost_te = cost_of(te, pri)
    if name in ("L-cw", "HB"):
        cols = (*plan.lcw_columns, *extra) if name == "L-cw" else (*HB_COLUMNS, *extra)
        imp = L.medians(tr[list(cols)].to_numpy(float)) if cols else np.zeros(0)
        td = _sample_td(tr, cols, pri, plan)
        a = L.fit_lcw(td, lam=V.LAMBDA_ROWS * lam_mult, lam_a=plan.lam_a * lam_a_mult, impute=imp)
        v = a.value(te[list(cols)].to_numpy(float) if cols else np.empty((len(te), 0)),
                    te["instrument"].astype(str).to_numpy())
        return L.keep("gross", v, cost_te)
    cols = (*V.P30, L.RANGE_TO_COST, *extra)
    trd = _with_dense(tr, None, cost_of(tr, pri))
    ted = _with_dense(te, None, cost_te)
    g = L.fit_gcw(_td(trd, cols, cost_of(tr, pri), design_effect=plan.design_effect), seed=seed, shift=shift)
    return L.keep("p", g.value(ted[list(cols)].to_numpy(float)), cost_te)


def _s(te: pd.DataFrame, k: np.ndarray, pri: DC.CostPriors, gross: np.ndarray | None = None) -> float:
    g = te["gross"].to_numpy(float) if gross is None else gross
    net = O.clip_r(g - cost_of(te, pri))
    return O.s_statistic(net, k.astype(float))


def harness(col: Collected, plan: UnitPlan, *, seeds: Sequence[int], learners: Sequence[str] = ("L-cw", "HB", "G-cw"),
            max_rows: int = 200_000) -> dict[str, Any]:
    """On the last OOF fold of the thinned sample (capped at `max_rows`, deterministic): oracle canaries at +0.03 /
    +0.05 / +0.10 R (a planted fair-coin column), the N6 shuffle (gross R permuted, OOF AUC within 0.5 ± 4·SE and the
    keep near the intercept-only keep), the parameter shift (each named setting x 0.8 / x 1.25, report only), seed
    stability and the learning curve (L-cw). Descriptive: nothing here selects."""
    Y = plan.oof_years[-1]
    s = col.sample
    if len(s) > max_rows:
        s = s[_hash_u(s, seeds[0]) < max_rows / len(s)].reset_index(drop=True)
    yr = pd.DatetimeIndex(pd.to_datetime(s["decision_utc"], utc=True)).year.to_numpy()
    tr, te = s[train_mask(s, Y)].reset_index(drop=True), s[(yr == Y) & (s["phase"] == 0)].reset_index(drop=True)
    out: dict[str, Any] = {"fold": Y, "train_rows": len(tr), "test_rows": len(te)}
    if len(tr) < 1_000 or len(te) < 200:
        out["skipped"] = "too few sample rows on the last fold"
        return out
    pri = col.priors[Y].priors(year_start(Y))
    rng = np.random.default_rng(V.SEED_CANARY)
    z_tr, z_te = rng.integers(0, 2, len(tr)).astype(float), rng.integers(0, 2, len(te)).astype(float)
    can: dict[str, list[dict[str, Any]]] = {}
    for name in learners:
        rows = []
        for delta in CANARY_DELTAS:
            g_tr = tr["gross"].to_numpy(float) + delta * (2 * z_tr - 1)
            g_te = te["gross"].to_numpy(float) + delta * (2 * z_te - 1)
            k = _fit_score(name, tr.assign(gross=g_tr, canary=z_tr), te.assign(gross=g_te, canary=z_te), plan, pri,
                           col.impute, seed=seeds[0], extra=("canary",))
            oracle = L.keep("gross", float(np.average(O.clip_r(tr["gross"]), weights=tr["w"])) + delta * (2 * z_te - 1),
                            cost_of(te, pri))
            null = L.keep("gross", np.full(len(te), float(np.average(O.clip_r(g_tr), weights=tr["w"]))),
                          cost_of(te, pri))
            sm, so, sn = (_s(te, kk, pri, g_te) for kk in (k, oracle, null))
            rows.append({"delta": delta, "s_model": sm, "s_oracle": so, "s_null": sn,
                         "recovered": bool(so > sn and (sm - sn) >= 0.5 * (so - sn))})
        can[name] = rows
    out["canaries"] = can
    perm = np.random.default_rng(V.SEED_B2).permutation(len(tr))
    sh: dict[str, Any] = {}
    for name in learners:
        k = _fit_score(name, tr.assign(gross=tr["gross"].to_numpy(float)[perm]), te, plan, pri, col.impute,
                       seed=seeds[0])
        k0 = L.keep("gross", np.full(len(te), float(np.average(O.clip_r(tr["gross"]), weights=tr["w"]))),
                    cost_of(te, pri))
        y = te["gross"].to_numpy(float) - cost_of(te, pri) > 0
        npos, nneg = int(y.sum()), int((~y).sum())
        auc = G.within_arm_auc(k.astype(float), y, te["instrument"].astype(str).to_numpy())
        se = float(np.sqrt((npos + nneg + 1) / (12.0 * max(1, npos) * max(1, nneg))))
        sh[name] = {"false_keep_share": float((k & ~k0).mean()), "auc": auc, "band": [0.5 - 4 * se, 0.5 + 4 * se],
                    "auc_in_band": bool(abs(auc - 0.5) <= 4 * se) if np.isfinite(auc) else True,
                    "passed": bool(float((k & ~k0).mean()) <= G.MAX_FALSE_KEEP or int(k.sum()) < V.MIN_KEPT)}
    out["shuffle"] = sh
    from goldbot.research import model_quality as MQ

    def net_mean(k: np.ndarray) -> float:
        return float((O.clip_r(te["gross"].to_numpy(float) - cost_of(te, pri)) * k).mean())
    lin = {"lambda": V.LAMBDA_ROWS, "lambda_a": plan.lam_a}

    def ev_lin(name: str) -> Any:
        def ev(pt: dict[str, Any]) -> float:
            return net_mean(_fit_score(name, tr, te, plan, pri, col.impute, seed=seeds[0],
                                       lam_mult=pt["lambda"] / lin["lambda"],
                                       lam_a_mult=pt["lambda_a"] / lin["lambda_a"]))
        return ev
    gb = {"learning_rate": L.GCW_PARAMS["learning_rate"], "num_iterations": L.GCW_PARAMS["num_iterations"],
          "min_child_share": L.GCW_PARAMS["min_child_share"]}

    def ev_g(pt: dict[str, Any]) -> float:
        return net_mean(_fit_score("G-cw", tr, te, plan, pri, col.impute, seed=seeds[0],
                                   shift={k: float(pt[k]) / float(gb[k]) for k in gb}))
    # ADR 0106 "stays profitable when parameters are shifted slightly": model_quality's +-10 % / +-20 % grid, one
    # parameter at a time, net mean after cost on the last fold (HB here is its own rung: λ0 and λ_c play λ, λ_a)
    out["parameter_shift"] = {"L-cw": MQ.param_shift(lin, ev_lin("L-cw")).model_dump(mode="json"),
                              "HB": MQ.param_shift(lin, ev_lin("HB")).model_dump(mode="json"),
                              "G-cw": MQ.param_shift(gb, ev_g).model_dump(mode="json")}
    out["seed_s"] = {"G-cw": [_s(te, _fit_score("G-cw", tr[_hash_u(tr, sd) < 0.9].reset_index(drop=True), te, plan,
                                                pri, col.impute, seed=sd), pri) for sd in seeds]}
    curve = []
    order = np.argsort(_ns(tr["decision_utc"]), kind="stable")
    for n in (*[c for c in CURVE_SIZES if c < len(tr)], len(tr)):
        sub = tr.iloc[order[-n:]].reset_index(drop=True)     # the most recent n rows
        curve.append({"n": int(n), "s": _s(te, _fit_score("L-cw", sub, te, plan, pri, col.impute, seed=seeds[0]),
                                           pri)})
    out["learning_curve_lcw"] = curve
    if plan.lcw_columns:
        yrs = pd.DatetimeIndex(pd.to_datetime(tr["decision_utc"], utc=True)).year.to_numpy()
        halves = [tr[yrs % 2 == r].reset_index(drop=True) for r in (0, 1)]
        if all(len(h) > 100 for h in halves):
            co = [L.fit_lcw(_sample_td(h, plan.lcw_columns, pri, plan), lam=V.LAMBDA_ROWS, lam_a=plan.lam_a).coef
                  for h in halves]
            out["half_split_sign_agreement"] = float(np.mean(np.sign(co[0]) == np.sign(co[1])))
    return out


# ---------------------------------------------------------------------------------------------- the run
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
            "hb_columns": list(HB_COLUMNS), "p30": list(V.P30), "gap": "max(1 d, H)"}


@dataclass
class UnitResult:
    report: dict[str, Any]
    artifacts: dict[str, str]                      # name -> JSON
    oof: pd.DataFrame


def fit_unit(data: UnitData, plan: UnitPlan, *, hb_by_fold: Mapping[int, L.HbArtifact] | None,
             seeds: Sequence[int] = SEEDS, col: Collected | None = None, harness_rows: int = 200_000,
             with_harness: bool = True) -> UnitResult:
    """Walk-forward fit, OOF scoring, v0 artifacts and the report for one unit (see the module docstring). The run is
    recorded by `record_unit` (research/run_manifest.py, ADR 0109), not here."""
    col = col or collect(data, plan, seed=seeds[0])
    dense_by_year: dict[int, L.DenseArtifact] = {}
    oof_parts: list[pd.DataFrame] = []
    avg_by_year: dict[int, str] = {}
    for Y in plan.oof_years:
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
        _, av0 = add_avg(oof.iloc[:1], oof, {"L-cw": L.artifact_sha(v0.lcw), "G-cw": L.artifact_sha(v0.gcw[seeds[0]])
                                             if v0.gcw else "", "HB": L.artifact_sha(v0.hb) if v0.hb else ""})
        if av0 is not None:
            arts["AVG"] = L.artifact_json(av0)
    report = unit_report(oof, plan, col, v0, seeds=seeds, harness_rows=harness_rows, with_harness=with_harness)
    report["artifacts"] = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in arts.items()}
    report["avg_by_year"] = avg_by_year
    report["train_end"] = str(col.train_end)
    return UnitResult(report=report, artifacts=arts, oof=oof)


def unit_report(oof: pd.DataFrame, plan: UnitPlan, col: Collected, v0: FoldModels, *, seeds: Sequence[int],
                harness_rows: int, with_harness: bool = True) -> dict[str, Any]:
    rep: dict[str, Any] = {"unit": plan.unit, "tfs": list(plan.tfs), "members": list(plan.members),
                           "level": plan.stage0.get("level"), "judged": plan.stage0.get("judged"),
                           "served_lcw": list(plan.lcw_columns), "first_oof": plan.first_oof,
                           "rows_learn": col.n_rows, "tree_sample_pi": col.pi, "learners": {},
                           "note": "descriptive, never a selector; Stage L gates are the evaluator's read"}
    if not len(oof):
        rep["oof"] = "no OOF rows"
        return rep
    gate10 = {int(str(k)): int(v) for k, v in oof.groupby("year").size().items()}
    rep["candidates"] = {"oof_rows": int(len(oof)), "per_year": {int(k): int(v) for k, v in gate10.items()},
                         "min_learn_1500": bool(col.n_rows >= V.MIN_LEARN_CANDIDATES),
                         "min_per_fold_60": bool(min(gate10.values()) >= V.MIN_PER_FOLD)}
    mean_net = float(oof["net"].mean())
    rep["baselines"] = {"B0_abstain": -max(0.0, mean_net), "B1_take_all": mean_net - max(0.0, mean_net),
                        "B8_intercept_only": learner_read(oof, "B8"), "buy_and_hold": buy_and_hold(oof)}
    for name in BUILT:
        if f"k_{name}" not in oof:
            continue
        kind = L.LEARNERS[name].kind or "gross"
        o = oof[oof[f"v_{name}"].notna()]
        entry = {"read": learner_read(o, name), "twin_s_expectation": twin_expectation(o, name) if len(o) else None,
                 "calibration": calibration(o, name, kind), "rank": rank_diagnostics(o, name, kind)}
        if name != "G-cw":
            entry["d_vs_gcw"] = paired_d(oof, name, "G-cw")
        rep["learners"][name] = entry
    rep["paired_d"] = {f"{a} vs {b}": paired_d(oof, a, b) for i, a in enumerate(BUILT) for b in BUILT[i + 1:]
                       if f"k_{a}" in oof and f"k_{b}" in oof}
    rep["seed_stability"] = {k: v.model_dump(mode="json") for k, v in seed_verdicts(oof, seeds).items()}
    gold = {i: {"shrunk": v0.lcw.levels.get(i), "unshrunk": v0.lcw.levels_unshrunk.get(i)} for i in v0.lcw.levels}
    rep["levels"] = gold or "gold body: no instrument level (unit intercept only)"
    yr = pd.DatetimeIndex(pd.to_datetime(oof["decision_utc"], utc=True)).year
    late = oof[np.isin(yr, [2015, 2016, 2017])]
    rep["v0_keep_share_2015_17"] = float(late["k_L-cw"].mean()) if len(late) else None   # H1 breaker reference (OOF)
    rep["harness"] = harness(col, plan, seeds=seeds, max_rows=harness_rows) if with_harness else {"skipped": "builder"}
    rep["not_in_this_report"] = ["Stage L gates (evaluator)", "B2-B6 baselines (5.2, evaluator)",
                                 "arm-fire subset S (no arm candidates in dense rows)", "block dS table (no X block "
                                 "built)", "offset bias, staleness (live)", "£/day at 0.01 lot (needs the lot map)"]
    return rep


def write_unit(res: UnitResult, out: Path) -> Path:
    d = out / res.report["unit"]
    d.mkdir(parents=True, exist_ok=True)
    for name, text in res.artifacts.items():
        (d / f"{name}.json").write_text(text)
    (d / "report.json").write_text(json.dumps(res.report, indent=1, sort_keys=True, default=_json))
    (d / "report.md").write_text(report_md(res.report))
    if len(res.oof):
        res.oof.to_parquet(d / "oof.parquet", index=False)
    return d


def _json(x: Any) -> Any:
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (pd.Timestamp,)):
        return x.isoformat()
    return str(x)


def _f(x: Any) -> str:
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:+.4f}" if isinstance(
        x, float) else str(x))


def report_md(r: Mapping[str, Any]) -> str:
    served = ", ".join(r.get("served_lcw") or []) or ("none (intercept-only B8: a per-instrument level minus cost(x), "
                                                      "a cost-and-level filter)")
    lines = [f"# sr-v3 training report: unit {r['unit']}", "",
             f"Level {r.get('level')} (judged: {r.get('judged')}); members {', '.join(r.get('members', []))}; "
             f"first OOF {r.get('first_oof')}; L-cw served columns: {served}.", "",
             "Descriptive, never a selector. Stage L gates are the evaluator's read. G-cw is the gradient-boosted "
             "baseline every learner must beat out of sample after costs (ADR 0106).", "",
             "| learner | rows | keep share | kept mean net | S | S at 2x cost | net R / day | D/day vs G-cw (90 %) |",
             "|---|---|---|---|---|---|---|---|"]
    for name, e in r.get("learners", {}).items():
        rd, d = e.get("read", {}), e.get("d_vs_gcw", {})
        band = f"{_f(d.get('d_per_day'))} [{_f(d.get('lo90'))}, {_f(d.get('hi90'))}]" if d.get("n") else "baseline"
        lines.append(f"| {name} | {rd.get('n')} | {_f(rd.get('keep_share'))} | {_f(rd.get('kept_mean_net'))} | "
                     f"{_f(rd.get('s'))} | {_f(rd.get('s_2x_cost'))} | {_f(rd.get('net_r_per_day'))} | {band} |")
    lines += ["", "## Benchmarks", "", f"- B0 abstain S: {_f(r.get('baselines', {}).get('B0_abstain'))}",
              f"- B1 take-all S: {_f(r.get('baselines', {}).get('B1_take_all'))}"]
    for i, b in (r.get("baselines", {}).get("buy_and_hold") or {}).items():
        lines.append(f"- buy-and-hold {i}: {_f(b.get('pct'))} of price, {_f(b.get('r_per_day'))} R per OOS day")
    lines += ["", "## Paired D per OOS session day (reported, not judged)", ""]
    for k, d in (r.get("paired_d") or {}).items():
        lines.append(f"- {k}: {_f(d.get('d_per_day'))} [{_f(d.get('lo90'))}, {_f(d.get('hi90'))}] over "
                     f"{d.get('days')} days")
    lines += ["", "## Diagnostics", "", "```json",
              json.dumps({k: r.get(k) for k in ("seed_stability", "levels", "v0_keep_share_2015_17", "candidates",
                                                 "harness")}, indent=1, default=_json), "```",
              "", "Not in this report: " + "; ".join(r.get("not_in_this_report", [])) + ".", ""]
    return "\n".join(lines)


def row_edge(oof: pd.DataFrame, kind: str, col: str) -> np.ndarray:
    """Net R after cost per OOF decision row, 0 where the learner does not keep (model_quality's read)."""
    v = oof[col].to_numpy(float)
    k = np.isfinite(v) & L.keep(kind, np.nan_to_num(v, nan=-np.inf), oof["cost"].to_numpy(float))  # type: ignore[arg-type]
    return oof["net"].to_numpy(float) * k


def seed_verdicts(oof: pd.DataFrame, seeds: Sequence[int]) -> dict[str, Any]:
    """ADR 0106 "results stable across at least 5 random seeds" (model_quality.seed_stability): per learner, the mean
    net R per OOF row for each seed. L-cw and HB are closed forms: every seed gives the same edge, and the verdict
    records that (detail.seed_invariant)."""
    from goldbot.research import model_quality as MQ
    out: dict[str, Any] = {}
    if not len(oof):
        return out
    for name in BUILT:
        kind = L.LEARNERS[name].kind or "gross"
        cols = {sd: f"v_{name}@{sd}" for sd in seeds if f"v_{name}@{sd}" in oof}
        if name in ("L-cw", "HB"):
            cols = dict.fromkeys(seeds, f"v_{name}")
        if len(cols) != len(seeds):
            continue
        edges = {sd: float(row_edge(oof, kind, c).mean()) for sd, c in cols.items()}
        v = MQ.seed_stability(edges)
        out[name] = v.model_copy(update={"detail": {**v.detail, "seed_invariant": bool(np.ptp(list(edges.values()))
                                                                                       == 0)}})
    return out


FAMILY: dict[str, str] = {"L-cw": "F", "B8": "F", "HB": "F", "G-cw": "C", "D-40": "C", "AVG": "G"}
BUILDER = "goldbot.research.sr_v3_train:build_artifact"


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


def run_settings(plan: UnitPlan, learner: str, source: Mapping[str, Any], hb_plans: Sequence[UnitPlan]) -> dict[str, Any]:
    """Everything `build_artifact` reads besides the data files and seeds (the manifest's settings)."""
    return {"sr_v3": {"learner": learner, "plan": plan_dict(plan), "hb_plans": [plan_dict(p) for p in hb_plans],
                      "source": dict(source)}, "frozen": settings()}


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


def build_artifact(spec: Any) -> bytes:
    """run_manifest builder (`BuildSpec` -> artifact bytes): refits one learner's v0 exactly as `fit_unit` did.
    Data: `frames.parquet` (in-memory data written by `frames_file`) or the stores named in settings, with the tiers
    file named by `source.tiers_file`. Labels only through load_labels, as in the original run."""
    cfg = spec.settings["sr_v3"]
    if cfg["source"]["kind"] == "frames":
        data: UnitData = frames_from(spec.data_files["frames.parquet"])
    else:
        st = cfg["source"]
        data = StoreData(labels=Path(st["labels"]), features=Path(st["features"]),
                         tiers_path=Path(spec.data_files[st["tiers_file"]]))
    plan = plan_of(cfg["plan"])
    seeds = tuple(int(s) for s in spec.seeds)
    hb_plans = [plan_of(p) for p in cfg["hb_plans"]]
    hb = hb_from_plans(data, hb_plans, seed=seeds[0]) if hb_plans else None
    res = fit_unit(data, plan, hb_by_fold=hb, seeds=seeds, with_harness=False)
    return res.artifacts[cfg["learner"]].encode()


def record_unit(res: UnitResult, plan: UnitPlan, *, seeds: Sequence[int], source: Mapping[str, Any],
                data_version: str, data_root: Path, data_files: Sequence[str], hb_plans: Sequence[UnitPlan],
                root: Path, repo: Path = ROOT) -> dict[str, Any]:
    """Record every v0 artifact with research/run_manifest.py (ADR 0106 decision 7, ADR 0109): data version and file
    shas, settings, seeds, code sha, train end, the builder, results and the quality verdicts. G-cw first: every other
    learner's `beats_baseline` names the GBT manifest (ADR 0106 decision 3, reported for families F and G too)."""
    from goldbot.research import model_quality as MQ
    from goldbot.research import run_manifest as RM
    oof = res.oof
    verdicts = seed_verdicts(oof, seeds)
    shift = (res.report.get("harness") or {}).get("parameter_shift") or {}
    shas = {f: RM.file_sha256(Path(data_root) / f) for f in data_files}
    end = pd.Timestamp(res.report.get("train_end") or WN.SPANS[WN.Window.LEARN][1])
    order = ["G-cw", *[k for k in res.artifacts if k != "G-cw"]]
    out: dict[str, Any] = {}
    gbt_id: str | None = None
    for name in order:
        if name not in res.artifacts:
            continue
        q: list[Any] = []
        if name in verdicts:
            q.append(verdicts[name])
        if name in shift:
            q.append(MQ.QualityVerdict.model_validate(shift[name]))
        if gbt_id is not None and name in BUILT and f"v_{name}" in oof:
            ok = oof[f"v_{name}"].notna() & oof["v_G-cw"].notna()
            o = oof[ok].reset_index(drop=True)
            kind = L.LEARNERS[name].kind or "gross"
            q.append(MQ.beats_baseline(pd.Series(row_edge(o, kind, f"v_{name}")), pd.Series(row_edge(o, "p", "v_G-cw")),
                                       o["day"].astype(str), baseline_manifest_id=gbt_id))
        m = RM.record(model=f"sr-v3:{plan.unit}:{name}", family=FAMILY[name],  # type: ignore[arg-type]
                      artifact=res.artifacts[name].encode(), data_version=data_version,
                      settings=run_settings(plan, name, source, hb_plans), seeds=seeds, train_end=end,
                      scaler_fit_end=end, data_files=shas, builder=BUILDER,
                      results=(res.report.get("learners", {}).get(name) or {}).get("read", {}), quality=q,
                      spec_id=V.SPEC_VERSION, root=root, repo=repo)
        out[name] = m
        if name == "G-cw":
            gbt_id = m.manifest_id
    return out

# ---------------------------------------------------------------------------------------------- the stores
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
    ledger: Path = ROOT / "state" / WN.READS_LEDGER       # the read-once / consumption ledger (#482: load_labels' ledger)
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

    def _weights(self, instrument: str, tf: str) -> pd.DataFrame:
        from goldbot.research import dense_labels as D
        p = Path(self.weights or Path(self.labels) / "_weights") / instrument / f"{tf}.parquet"
        self.used.setdefault("weights", {})[f"{instrument}:{tf}"] = V.file_sha256(p)
        return D._typed(pd.read_parquet(p))[["phase", "decision_utc", "u_tf", "w"]]

    def events(self, instrument: str, tf: str, years: Sequence[int]) -> pd.DataFrame:
        """Stage 0's label-blind events: the skeleton's T2 long rows (one per bar), tier-usable under P30, with u.
        Refused while `sequencing_gaps` is not empty (no Stage 0 statistic before the quant sequencing holds)."""
        from goldbot.research import dense_labels as D
        refuse_unsequenced(f"Stage 0 events {instrument} {tf}")
        ks = {(y, 0): k[0] for y in years if (k := self.keys(instrument, tf, y, 0)) is not None}
        if not ks:
            return pd.DataFrame(columns=["instrument", "tf", "phase", "decision_utc", "usable", "u"])
        sk = D.skeleton(self.labels, instrument=instrument, tf=tf, years=sorted(y for y, _ in ks), keys=ks)
        sk = sk[(sk["template"] == V.JUDGED_HEAD) & (sk["side"] == 1)].reset_index(drop=True)
        sk = D.attach_tiers(sk.drop(columns=["tier", "usable"]), self._tiers(instrument), columns=V.P30)
        w = self._weights(instrument, tf)
        ev = sk.merge(w, on=["phase", "decision_utc"], how="left")
        self.used.setdefault("stage0_keys", {})[f"{instrument}:{tf}"] = {f"{y}:{p}": k for (y, p), k in ks.items()}
        return ev.assign(u=ev["u_tf"].fillna(0.0), usable=ev["usable"] & ev["label_ok"])[
            ["instrument", "tf", "phase", "decision_utc", "usable", "u"]]

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
        from goldbot.research import feature_store as F
        from goldbot.research.self_retrain import design
        ks = {(y, int(p)): k for y in V.LEARN_YEARS for p in phases
              if (k := self.keys(instrument, tf, y, int(p))) is not None}
        if not ks:
            return pd.DataFrame(columns=list(ROW_COLS))
        tiers = self._tiers(instrument)
        years = sorted({y for y, _ in ks})
        # #482 (quant L1): every load is a ledgered consumption row with procedure "sr-v3", which the X6 guard reads
        lab, meta = D.load_labels(self.labels, procedure=V.SPEC_VERSION, ledger=self.ledger, instrument=instrument,
                                  tf=tf, years=years, tiers=tiers, columns=V.P30, keys={k: v[0] for k, v in ks.items()},
                                  window=WN.SPANS[WN.Window.LEARN], purpose="train", phases=list(phases),
                                  templates=("T0", V.JUDGED_HEAD))
        feats, fmeta = F.load(self.features, instrument=instrument, tf=tf, years=years, tiers=tiers,
                              phases=list(phases), purpose="train", expect_keys={k: v[1] for k, v in ks.items()})
        self.used.setdefault("labels", {})[f"{instrument}:{tf}"] = {
            "keys": meta["keys"], "tiers_sha256": meta["tiers_sha256"], "rows": meta["rows"],
            "dropped_tier": meta.get("dropped_tier"), "fs_keys": fmeta["keys"]}
        t2 = lab[lab["template"] == V.JUDGED_HEAD]
        t0 = lab[lab["template"] == "T0"][["phase", "decision_utc", "side", "gross_r_raw"]].rename(
            columns={"gross_r_raw": "t0_gross"})
        rows = t2.merge(t0, on=["phase", "decision_utc", "side"], how="inner")
        rows = rows.merge(feats.drop(columns=[c for c in ("instrument", "tf", "source", "bid_only", "group_id",
                                                           "available_utc", "window_from_utc", "tier")
                                              if c in feats.columns]), on=["phase", "decision_utc"], how="inner")
        rows = rows.merge(self._weights(instrument, tf)[["phase", "decision_utc", "w"]], on=["phase", "decision_utc"],
                          how="inner")
        if not len(rows):
            return pd.DataFrame(columns=list(ROW_COLS))
        X = design(rows)
        parts = cost_parts(rows)
        out = pd.DataFrame({"instrument": instrument, "tf": tf, "phase": rows["phase"].to_numpy(),
                            "decision_utc": rows["decision_utc"].to_numpy(),
                            "horizon_end_utc": rows["horizon_end_utc"].to_numpy(), "side": rows["side"].to_numpy(),
                            "gross": rows["gross_r_raw"].to_numpy(float), "t0_gross": rows["t0_gross"].to_numpy(float),
                            "stop": DC.is_stop(rows), "nights_held": DC.nights_held(rows),
                            "w": rows["w"].to_numpy(float), "entry": rows["entry"].to_numpy(float),
                            "base_dist": rows["base_dist"].to_numpy(float),
                            "cost_rt_bp": rows["cost_rt_bp"].to_numpy(float)})
        out = pd.concat([out, parts.reset_index(drop=True), X[[c for c in V.P30 if c != "side"]].reset_index(drop=True)], axis=1)
        return out[list(ROW_COLS)]

    def version(self, members: Sequence[str], tfs: Sequence[str], phases: Sequence[int]) -> str:
        """The data identity a rebuild needs: label spec and code hashes, tiers sha, every partition key used."""
        from goldbot.research import dense_labels as D
        keys = {f"{i}:{t}:{y}:{p}": self.keys(i, t, y, p) for i in members for t in tfs for y in V.LEARN_YEARS
                for p in phases}
        blob = {"spec": D.spec_hash(), "code": D.code_hash(), "tiers": V.file_sha256(self.tiers_path), "keys": keys}
        return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()

    def manifest(self) -> dict[str, Any]:
        from goldbot.research import dense_labels as D
        return {"source": "dl-v1 + fs-v1 stores", "label_spec_hash": D.spec_hash(), "label_code_hash": D.code_hash(),
                "tiers_sha256": V.file_sha256(self.tiers_path), "used": self.used}


__all__ = ["BUILT", "CODE_FILES", "MAX_TREE_ROWS", "OUT", "ROW_COLS", "SEEDS", "Collected", "FrameData",
           "StoreData", "UnitData", "UnitPlan", "UnitResult", "buy_and_hold", "code_version", "collect", "cost_of", "cost_parts",
           "fit_unit", "gap", "hb_fit", "harness", "paired_d", "plan_from_stage0", "build_artifact", "record_unit",
           "hb_from_plans", "frames_file", "frames_from", "seed_verdicts", "report_md", "settings",
           "stage0", "stage0_counts", "train_mask", "twin_expectation", "write_unit"]
