"""H-101 model zoo and fusion on the retired gold arms (owner idea 2026-11-25: "train several models on historical and
present data, then compare each model with combinations (fusions) of them"; pre-registration
docs/research/prereg-2026-11-25-model-zoo-fusion.md, spec `SPEC_VERSION` zoo-v1, quant-signed at 1943da01).

The card, as coded here:
- Population (2.1): the sr-v2 `PRENAMED` arms replayed on Dukascopy XAUUSD 1m (`research/history_replay.py`); every
  candidate the rule emits, taken or not. Label: net R under W2 bps costs, y = 1[net R > 0]; `r_mid` the same trade at
  mid, no cost. Inputs: `self_retrain.INPUTS` exactly (39 market columns + side = 40), designed by
  `self_retrain.design`.
- Candidates (2.2), "fixed spec: no grid, no inner tuning": B1 LOGIT (per arm), B2 GBT (LightGBM, per arm), B3 POOL
  (one logistic over all arms, no arm dummies), B4 REGIME (a logistic per `vol_tercile` cell, per arm), B5 KNN (per
  arm), F1 AVG, F2 STACK (logistic on the logits of the five base OOF scores), F3 GATE (per `vol_tercile` cell the base
  with the best OOF edge over earlier blocks). "All base learners: median imputation and standardisation fitted on the
  training fold only."
- Filter (C1 / C4): "tau is set per arm for every candidate", the arm's 1/3 quantile of the candidate's own scores on
  its fit rows (bases: their OOF scores of blocks 2011..t-1; fusions: their scores on the fusion fit rows).
- Walk-forward (2.3): yearly blocks 2011..2017, training 2010..t-1, "purged by the full-horizon rule and a 1-day
  embargo before the block's first decision"; "every F2 / F3 fit row, every F3 cell edge and every row used for a tau
  meets the same rule"; 2011 is a meta-training block only; all 8 judged on the same rows 2012-2017.
- Reading rule (4): pooled edge_nm = the candidate-weighted mean of the per-arm edge_nm, kept = the mean net R of all
  kept rows; p = max(p[edge_nm <= 0], p[kept <= 0]); "stationary bootstrap, mean block 5 session days, B = 10,000, seed
  20261125. Session days are drawn jointly across arms ... A draw with no kept row counts against" (`JointBootstrap`,
  new code: `self_retrain.oos_metrics` resamples arm and control days independently). Paired D(F, B*) uses identical
  index draws. "Only the pooled numbers decide; per-arm results are descriptive only."
- Stage 0 (4, 4a): outcome-blind counts and the ADR 0085 power check, written through `power_first.start_or_refuse`;
  test_old stays sealed (n_T = each arm's learn 2012-17 rate x 1.92 years). Stage L books K = 8 (`book_stage_l`,
  registry rows before any label is read, after the budget check). Stage C confirm 2018-01..2025-09 read once; Stage T
  test_old 2008-02..2009-12 read once, winner only.

Phase 1 is gold only (2.4): `gold_only` refuses any instrument that is not XAUUSD of asset class metal. Nothing here
reads aux.db labels, the engine, RiskGate or the arm config; no model is served (phase-1 coefficients never ship).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd
from scipy import stats

from goldbot.config import Settings
from goldbot.research import power_first as pf
from goldbot.research import self_retrain as sr
from goldbot.research.model import check_feature_cap
from goldbot.research.registry import TrialRegistry, config_hash, quarter_of
from goldbot.research.windows import HOLDOUT_FROM, HoldoutSealed

log = logging.getLogger("goldbot.model_zoo")

# ---------------------------------------------------------------------------------------------- the frozen spec
SPEC_VERSION = "zoo-v1"
PREREG = "docs/research/prereg-2026-11-25-model-zoo-fusion.md"
REGISTRY_FAMILY = "model_zoo_fusion"
SEED = 20261125
ARMS: tuple[str, ...] = sr.PRENAMED
# "RBR15 is replayed with RB15's rule (HISTORY_OF)": the arm id the history replay loads for a card arm
REPLAY_AS: dict[str, str] = {new: old for old, new in sr.HISTORY_OF.items()}
MIN_ARMS = 5                      # fewer replayable arms: the card does not run (0 K); fewer at Stage T: INCONCLUSIVE
INPUTS: tuple[str, ...] = sr.INPUTS
check_feature_cap(list(INPUTS))
BASES: tuple[str, ...] = ("B1", "B2", "B3", "B4", "B5")
FUSIONS: tuple[str, ...] = ("F1", "F2", "F3")
CANDIDATES: tuple[str, ...] = BASES + FUSIONS
NAMES = {"B1": "LOGIT", "B2": "GBT", "B3": "POOL", "B4": "REGIME", "B5": "KNN", "F1": "AVG", "F2": "STACK",
         "F3": "GATE"}
SIMPLICITY = {"B3": 1, "B1": 2, "B4": 3, "B5": 4, "B2": 5, "F1": 6, "F3": 7, "F2": 8}   # 1 = simplest
C_BASE = sr.C_REG                 # B1, B3, B4: L2 logistic, C = 0.1 (self_retrain.fit_logit)
C_STACK = 1.0                     # F2
STACK_CLIP = (0.01, 0.99)         # F2 inputs: logit of the base OOF scores clipped to this
# B2, LightGBM sklearn names: subsample_freq is bagging_freq; force_row_wise makes `deterministic` reproducible
LGBM_PARAMS: dict[str, Any] = {"num_leaves": 15, "max_depth": 4, "learning_rate": 0.03, "n_estimators": 300,
                               "min_child_samples": 200, "subsample": 0.8, "subsample_freq": 1,
                               "colsample_bytree": 0.8, "reg_lambda": 1.0, "deterministic": True,
                               "force_row_wise": True, "n_jobs": 1, "random_state": SEED, "verbose": -1}
KNN_MIN_K = 50                    # B5: k = max(50, round(sqrt(n_train))), uniform, Euclidean on the standardised inputs
REGIME_MIN_ROWS = 100             # B4: a cell with fewer training rows (or one class) uses B1's score
F3_MIN_CELL = 100                 # F3: a cell with fewer OOF fit rows uses B*'s rule
REGIME_COL = "vol_tercile"
KEEP_QUANTILE = sr.DROP_SHARE     # keep score >= the arm's 1/3 quantile: about 2/3 kept
EMBARGO = sr.EMBARGO              # 1 day
MIN_TRAIN = sr.MIN_TRAIN          # an arm's learner needs this many purged training rows and both classes
FIRST_TRAIN_YEAR = 2010
OOF_BLOCKS: tuple[int, ...] = tuple(range(2011, 2018))
JUDGED_BLOCKS: tuple[int, ...] = tuple(range(2012, 2018))
CONFIRM_FROM = pd.Timestamp("2018-01-01", tz="UTC")
CONFIRM_TO = HOLDOUT_FROM
TEST_OLD_FROM = pd.Timestamp("2008-02-01", tz="UTC")     # clean intraday years only (ADR 0067)
TEST_OLD_TO = pd.Timestamp("2010-01-01", tz="UTC")
TEST_OLD_YEARS = 1.92
CONFIRM_YEARS = 7.75
# reading rule (section 4)
MIN_EDGE_R = 0.05
ALPHA = 0.10
BOOT = 10_000
MEAN_BLOCK_DAYS = 5
MIN_BLOCK_ROWS = 60               # Stage 0: every 2012-17 block has at least this many judged rows
# the 8 K are allocated from this quarter (card header: "8 K from 2026Q4"); booking in any other is refused
ALLOCATED_QUARTER = "2026Q4"
# inputs the engine's 90k-minute memory can never compute (they need 100 daily bars; it holds about 80): always NaN, in
# sr-v2 production too, so 38 of the 40 inputs are informative. Named, never silently imputed; fixing them is a new spec
STRUCTURAL_NAN: tuple[str, ...] = ("d1_atr_ratio_14_100", "d1_rv_ratio")
MAX_NAN_SHARE = 0.05              # any other input missing on more rows than this: the read is refused
STAGE0_NOTES: tuple[str, ...] = (
    "joint power simulates the two IUT legs (edge_nm, kept) as independent draws per window; their true correlation "
    "is about 0.58 (k = 2/3), so the joint power is understated (conservative): a recorded deviation from a joint draw",
    "n per gate counts every in-window candidate; a thin arm's first blocks without a model or tau are later left "
    "out of the judged set, so the judged n is slightly lower",
)
SHUFFLED_AUC_BAND = (0.47, 0.53)  # leakage check 1: every base learner's OOF AUC on shuffled labels inside this
# power (section 4a; the ADR 0085 settings may only tighten these, tests hold them equal)
KEEP_SHARE = 2.0 / 3.0
DELTA_R = 0.10
SD_R = 1.2
RHO_DAY = 0.25
MIN_GATE_POWER = 0.5
MIN_JOINT_POWER = 0.25
N_EFF_FLOOR = 200.0
# the longest hold of any replayed arm (RBR15: 96 x 15m), in days: stage 0's own n_eff has no hold-overlap term, which
# is exact only while this is <= 1 day (ADR 0096: the overlap factor max(1, signal days a day x hold) is then 1)
HOLD_DAYS = 1.0
Z_L = pf.holm_first_z(ALPHA, len(CANDIDATES))     # 0.10 / 8 at Holm rank 1: 2.24
Z_C = pf.holm_first_z(ALPHA, len(CANDIDATES))     # worst case: all 8 continue
Z_T = pf.holm_first_z(ALPHA, 1)                   # the winner only: 1.28
GOLD = "XAUUSD"
ASSET_CLASS = "metal"


class GoldOnly(PermissionError):
    """A non-gold instrument was asked for while phase 1 is gold only (card 2.4: non-gold replay labels carry a unit
    bug being fixed on fix/label-r-quarantine)."""


class PowerRefused(PermissionError):
    """Stage L was asked for without a started Stage 0 power verdict, or after a NOT STARTED (power) row of this
    spec."""


class QuarterRefused(PermissionError):
    """Stage L was asked for outside the quarter its K is allocated from."""


def check_inputs(rows: pd.DataFrame) -> dict[str, Any]:
    """Before any read: every row has a snapshot time at or before its decision (a missing one fails: card section
    3.4), and no input outside STRUCTURAL_NAN is missing on more than MAX_NAN_SHARE of the rows (refused, never
    median-imputed in silence). Returns the NaN share per input and the informative input count."""
    avail = pd.to_datetime(rows["available_utc_max"], utc=True)
    dec = _ts(rows["decision_utc"])
    if avail.isna().any() or (pd.DatetimeIndex(avail) > dec).any():
        raise ValueError("point in time: a candidate without a feature snapshot time or with one after its decision")
    nan = {c: float(rows[c].isna().mean()) if c in rows else 1.0 for c in sr.FEATURES}
    bad = {c: v for c, v in nan.items() if v > MAX_NAN_SHARE and c not in STRUCTURAL_NAN}
    if bad:
        raise ValueError(f"inputs missing on more than {MAX_NAN_SHARE:.0%} of rows: {bad}")
    informative = sum(1 for c in INPUTS if c == "side" or nan.get(c, 0.0) < 1.0)
    return {"nan_share": nan, "informative_inputs": informative, "structural_nan": list(STRUCTURAL_NAN)}


def gold_only(instrument: str) -> None:
    """Refuse any instrument but XAUUSD of asset class `metal` (config/instruments.yaml)."""
    from goldbot.instruments import spec_of
    try:
        cls = str(spec_of(instrument).asset_class)
    except (KeyError, ValueError) as exc:
        raise GoldOnly(f"{instrument!r}: not in config/instruments.yaml; phase 1 is gold only") from exc
    if instrument != GOLD or cls != ASSET_CLASS:
        raise GoldOnly(f"{instrument!r} ({cls}): phase 1 of H-101 is gold only (metal, XAUUSD); nothing non-gold is read")


# ---------------------------------------------------------------------------------------------- rows
def _ts(col: pd.Series) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(col, utc=True))


def _refuse_holdout(rows: pd.DataFrame) -> None:
    if len(rows) and (_ts(rows["decision_utc"]) >= HOLDOUT_FROM).any():
        raise HoldoutSealed("model zoo: a candidate decided inside the sealed holdout: refused")


def horizon(rows: pd.DataFrame) -> pd.DatetimeIndex:
    """decision + MAX_SEND_DELAY + max_hold_s (sr-v2's full horizon)."""
    return sr.horizon_end(rows)


def purged(rows: pd.DataFrame, t0: pd.Timestamp) -> np.ndarray:
    """The full-horizon purge with the 1-day embargo: the row's horizon and its label exit end before t0 - 1 day."""
    cut = t0 - EMBARGO
    return np.asarray((horizon(rows) < cut) & (_ts(rows["label_exit_utc"]) < cut), dtype=bool)


def read_rows(rows: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    """(rows decided in [lo, hi) whose horizon and label exit end before hi, the count dropped). A row reaching hi
    is "dropped, not truncated" (2.3); a row decided in the holdout is refused."""
    _refuse_holdout(rows)
    dec = _ts(rows["decision_utc"])
    inside = rows[np.asarray((dec >= lo) & (dec < hi))].reset_index(drop=True)
    ok = np.asarray((horizon(inside) < hi) & (_ts(inside["label_exit_utc"]) < hi), dtype=bool) if len(inside) \
        else np.zeros(0, dtype=bool)
    return inside[ok].reset_index(drop=True), int((~ok).sum())


# ---------------------------------------------------------------------------------------------- base learners
class Learner(Protocol):
    def score(self, X: pd.DataFrame) -> np.ndarray: ...
    def fingerprint(self) -> str: ...


@dataclass(frozen=True)
class _Prep:
    """Median imputation and standardisation, fitted on the training fold only."""
    medians: np.ndarray
    means: np.ndarray
    scales: np.ndarray

    @classmethod
    def fit(cls, X: pd.DataFrame) -> _Prep:
        m = X.reindex(columns=list(INPUTS)).to_numpy(dtype=float)
        finite = np.where(np.isfinite(m), m, np.nan)
        med = np.array([np.nanmedian(c) if np.isfinite(c).any() else 0.0 for c in finite.T])
        m = np.where(np.isfinite(m), m, med)
        sd = m.std(axis=0)
        return cls(med, m.mean(axis=0), np.where(sd > 1e-12, sd, 1.0))

    def __call__(self, X: pd.DataFrame) -> np.ndarray:
        m = X.reindex(columns=list(INPUTS)).to_numpy(dtype=float)
        m = np.where(np.isfinite(m), m, self.medians)
        return (m - self.means) / self.scales

    def digest(self) -> bytes:
        return b"".join(np.ascontiguousarray(a, dtype=np.float64).tobytes()
                        for a in (self.medians, self.means, self.scales))


def _sha(*parts: bytes | str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode() if isinstance(p, str) else p)
    return h.hexdigest()


@dataclass(frozen=True)
class LogitLearner:
    """B1 / B3 (and B4's cells): `self_retrain.fit_logit`, C = 0.1."""
    model: sr.LogitModel

    @classmethod
    def fit(cls, X: pd.DataFrame, y: np.ndarray) -> LogitLearner:
        return cls(sr.fit_logit(X, y))

    def score(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.score(X)

    def fingerprint(self) -> str:
        return _sha(self.model.model_dump_json(exclude={"tau"}))


@dataclass(frozen=True)
class GbtLearner:
    """B2: LightGBM with the card's fixed hyperparameters, no early stopping."""
    prep: _Prep
    booster: Any

    @classmethod
    def fit(cls, X: pd.DataFrame, y: np.ndarray) -> GbtLearner:
        from lightgbm import LGBMClassifier
        prep = _Prep.fit(X)
        clf = LGBMClassifier(**LGBM_PARAMS)
        clf.fit(prep(X), y)
        return cls(prep, clf)

    def score(self, X: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.booster.predict_proba(self.prep(X)))[:, 1]

    def fingerprint(self) -> str:
        return _sha(self.prep.digest(), self.booster.booster_.model_to_string())


@dataclass(frozen=True)
class KnnLearner:
    """B5: k = max(50, round(sqrt(n_train))) nearest neighbours, Euclidean on the standardised inputs, uniform
    weights; score = the mean y of the neighbours (brute force: exact and deterministic)."""
    prep: _Prep
    Xs: np.ndarray
    y: np.ndarray
    k: int

    @classmethod
    def fit(cls, X: pd.DataFrame, y: np.ndarray) -> KnnLearner:
        prep = _Prep.fit(X)
        k = min(len(y), max(KNN_MIN_K, round(math.sqrt(len(y)))))
        return cls(prep, prep(X), np.asarray(y, dtype=float), int(k))

    def score(self, X: pd.DataFrame) -> np.ndarray:
        from sklearn.neighbors import NearestNeighbors
        nn = NearestNeighbors(n_neighbors=self.k, algorithm="brute", metric="euclidean").fit(self.Xs)
        out = np.empty(len(X))
        Q = self.prep(X)
        for lo in range(0, len(Q), 2048):           # bounded memory: 2048 x n_train distances at a time
            idx = nn.kneighbors(Q[lo:lo + 2048], return_distance=False)
            out[lo:lo + 2048] = self.y[idx].mean(axis=1)
        return out

    def fingerprint(self) -> str:
        return _sha(self.prep.digest(), np.ascontiguousarray(self.Xs).tobytes(), self.y.tobytes(), str(self.k))


@dataclass(frozen=True)
class RegimeLearner:
    """B4: one C = 0.1 logistic per `vol_tercile` value at decision; "a cell with < 100 training rows or one class
    uses B1's score"; a NaN tercile gets B1's score (C9)."""
    cells: dict[float, LogitLearner]
    fallback: LogitLearner

    @classmethod
    def fit(cls, X: pd.DataFrame, y: np.ndarray, fallback: LogitLearner) -> RegimeLearner:
        vt = X[REGIME_COL].to_numpy(dtype=float)
        cells: dict[float, LogitLearner] = {}
        for v in sorted(set(vt[np.isfinite(vt)].tolist())):
            m = vt == v
            if m.sum() >= REGIME_MIN_ROWS and len(set(y[m].tolist())) == 2:
                cells[float(v)] = LogitLearner.fit(X[m], y[m])
        return cls(cells, fallback)

    def score(self, X: pd.DataFrame) -> np.ndarray:
        out = self.fallback.score(X)
        vt = X[REGIME_COL].to_numpy(dtype=float)
        for v, model in self.cells.items():
            m = vt == v
            if m.any():
                out[m] = model.score(X[m])
        return out

    def fingerprint(self) -> str:
        return _sha(self.fallback.fingerprint(), *[f"{v}:{m.fingerprint()}" for v, m in sorted(self.cells.items())])


def _usable(y: np.ndarray) -> bool:
    return len(y) >= MIN_TRAIN and len(set(y.tolist())) == 2


def fit_bases(train: pd.DataFrame) -> dict[str, dict[str, Learner]]:
    """The five base learners on purged training rows: {"B3": {"*": pooled}, "B1"/"B2"/"B4"/"B5": {arm: model}}. An
    arm with fewer than MIN_TRAIN rows or one class gets no model (its rows are unscored, so dropped from every
    candidate alike)."""
    out: dict[str, dict[str, Learner]] = {b: {} for b in BASES}
    if len(train) == 0:
        return out
    X, y = sr.design(train), (train["r"].to_numpy(dtype=float) > 0).astype(int)
    if _usable(y):
        out["B3"]["*"] = LogitLearner.fit(X, y)
    arms = train["arm"].astype(str).to_numpy()
    for arm in sorted(set(arms)):
        m = arms == arm
        Xa, ya = X[m].reset_index(drop=True), y[m]
        if not _usable(ya):
            continue
        b1 = LogitLearner.fit(Xa, ya)
        out["B1"][arm] = b1
        out["B2"][arm] = GbtLearner.fit(Xa, ya)
        out["B4"][arm] = RegimeLearner.fit(Xa, ya, b1)
        out["B5"][arm] = KnnLearner.fit(Xa, ya)
    return out


def score_bases(models: Mapping[str, Mapping[str, Learner]], rows: pd.DataFrame) -> pd.DataFrame:
    """Each base learner's P(y = 1) on `rows` (NaN where the row's arm has no model)."""
    out = pd.DataFrame(np.nan, index=range(len(rows)), columns=list(BASES))
    if len(rows) == 0:
        return out
    X = sr.design(rows)
    arms = rows["arm"].astype(str).to_numpy()
    if "*" in models["B3"]:
        out["B3"] = models["B3"]["*"].score(X)
    for b in ("B1", "B2", "B4", "B5"):
        for arm, model in models[b].items():
            m = arms == arm
            if m.any():
                out.loc[m, b] = model.score(X[m].reset_index(drop=True))
    return out


# ---------------------------------------------------------------------------------------------- statistics helpers
def pooled_edge(r: np.ndarray, kept: np.ndarray, arm: np.ndarray) -> float:
    """Pooled edge_nm (C1): the candidate-weighted mean over arms of (kept mean - all mean). An arm with no kept row
    has no edge and is left out (weights renormalised); none kept anywhere: NaN."""
    num = den = 0.0
    for a in np.unique(arm):
        m = arm == a
        k = m & kept
        if not k.any():
            continue
        n = float(m.sum())
        num += n * (float(r[k].mean()) - float(r[m].mean()))
        den += n
    return num / den if den else float("nan")


def arm_taus(scores: np.ndarray, arm: np.ndarray) -> dict[str, float]:
    """tau per arm: the arm's KEEP_QUANTILE quantile of the finite scores given (C1 / C4)."""
    out: dict[str, float] = {}
    for a in np.unique(arm):
        s = scores[(arm == a) & np.isfinite(scores)]
        if len(s):
            out[str(a)] = float(np.quantile(s, KEEP_QUANTILE))
    return out


def keep_by_tau(scores: np.ndarray, arm: np.ndarray, taus: Mapping[str, float]) -> tuple[np.ndarray, np.ndarray]:
    """(keep flags, the tau each row was compared with): keep when score >= tau_arm; no tau or no score: not kept."""
    tau = np.array([taus.get(str(a), np.nan) for a in arm], dtype=float)
    with np.errstate(invalid="ignore"):
        return np.isfinite(scores) & np.isfinite(tau) & (scores >= tau), tau


# ---------------------------------------------------------------------------------------------- fusions
def _logit(p: np.ndarray) -> np.ndarray:
    q = np.clip(p, *STACK_CLIP)
    return np.log(q / (1 - q))


def _best(edges: Mapping[str, float]) -> str:
    """The base with the highest edge; ties (and NaN) to the lower simplicity rank."""
    return min(BASES, key=lambda b: (-(edges.get(b, float("nan")) if np.isfinite(edges.get(b, float("nan")))
                                       else -math.inf), SIMPLICITY[b]))


@dataclass(frozen=True)
class Fusions:
    """F2's stacker and F3's cell choices, fitted on purged base OOF rows of earlier blocks only."""
    stack_coef: tuple[float, ...]
    stack_intercept: float
    cells: dict[float, str]           # vol_tercile cell -> chosen base (cells with >= F3_MIN_CELL fit rows)
    best_base: str                    # B*: the best base by OOF edge_nm over all fit rows (small cells)

    def fingerprint(self) -> str:
        return _sha(json.dumps([self.stack_coef, self.stack_intercept, sorted(self.cells.items()), self.best_base]))


def _base_edges(fit: pd.DataFrame, mask: np.ndarray | None = None) -> dict[str, float]:
    """Each base's pooled OOF edge (kept minus all, net R) on the fit rows (restricted to `mask`), kept = score >=
    the arm's 1/3 quantile of that base's scores on the fit rows."""
    r, arm = fit["r"].to_numpy(dtype=float), fit["arm"].astype(str).to_numpy()
    m = np.ones(len(fit), dtype=bool) if mask is None else mask
    out = {}
    for b in BASES:
        s = fit[b].to_numpy(dtype=float)
        keep, _ = keep_by_tau(s, arm, arm_taus(s, arm))
        out[b] = pooled_edge(r[m], keep[m], arm[m])
    return out


def fit_fusions(fit: pd.DataFrame) -> Fusions:
    """F2: L2 logistic (C = 1.0) on the logits of the five base OOF scores (clipped to [0.01, 0.99]); F3: per cell the
    base with the highest OOF edge (ties to the lower simplicity rank); B* for cells under F3_MIN_CELL rows."""
    from sklearn.linear_model import LogisticRegression
    S = _logit(fit[list(BASES)].to_numpy(dtype=float))
    y = (fit["r"].to_numpy(dtype=float) > 0).astype(int)
    clf = LogisticRegression(C=C_STACK, max_iter=2000)
    clf.fit(S, y)
    best = _best(_base_edges(fit))
    vt = fit[REGIME_COL].to_numpy(dtype=float)
    cells = {}
    for v in sorted(set(vt[np.isfinite(vt)].tolist())):
        m = vt == v
        if m.sum() >= F3_MIN_CELL:
            cells[float(v)] = _best(_base_edges(fit, m))
    return Fusions(tuple(map(float, clf.coef_[0])), float(clf.intercept_[0]), cells, best)


def score_fusions(fus: Fusions, S: pd.DataFrame, vt: np.ndarray) -> pd.DataFrame:
    """F1 (equal-weight mean of B1-B5), F2 and F3 from the base scores `S` and the rows' `vol_tercile`. F3: the cell's
    base, B*'s score in a small cell, F1's score for a NaN tercile (C9)."""
    B = S[list(BASES)].to_numpy(dtype=float)
    f1 = B.mean(axis=1)
    f2 = 1.0 / (1.0 + np.exp(-(_logit(B) @ np.asarray(fus.stack_coef) + fus.stack_intercept)))
    col = {b: i for i, b in enumerate(BASES)}
    f3 = B[:, col[fus.best_base]].copy()
    for v, b in fus.cells.items():
        m = vt == v
        f3[m] = B[m, col[b]]
    f3[~np.isfinite(vt)] = f1[~np.isfinite(vt)]
    return pd.DataFrame({"F1": f1, "F2": f2, "F3": f3})


# ---------------------------------------------------------------------------------------------- freeze and score
@dataclass(frozen=True)
class Frozen:
    """Every model of one fit point t0: the bases on purged training rows, the fusions and every tau on purged base
    OOF rows of earlier blocks (absent for the first block: meta-training only)."""
    t0: pd.Timestamp
    bases: dict[str, dict[str, Learner]]
    fusions: Fusions | None = None
    taus: dict[str, dict[str, float]] = field(default_factory=dict)
    train_ids: tuple[str, ...] = ()
    fit_ids: tuple[str, ...] = ()


def freeze(train_rows: pd.DataFrame, fit_oof: pd.DataFrame | None, *, t0: pd.Timestamp) -> Frozen:
    """Fit every model for scoring rows decided from t0: bases on the training rows purged at t0 (2.3), the fusions
    and the per-arm taus of all 8 on the purged OOF rows of `fit_oof` (C5). The confirm models are
    `freeze(learn rows, learn OOF, t0=CONFIRM_FROM)`."""
    _refuse_holdout(train_rows)
    tr = train_rows[purged(train_rows, t0) & np.asarray(_ts(train_rows["decision_utc"]) < t0 - EMBARGO)]
    tr = tr.reset_index(drop=True)
    bases = fit_bases(tr)
    train_ids = tuple(tr["candidate_id"].astype(str))
    if fit_oof is None or len(fit_oof) == 0:
        return Frozen(t0, bases, train_ids=train_ids)
    fit = fit_oof[purged(fit_oof, t0)]
    fit = fit[np.isfinite(fit[list(BASES)].to_numpy(dtype=float)).all(axis=1)].reset_index(drop=True)
    if len(fit) == 0 or len(set((fit["r"] > 0).tolist())) < 2:
        return Frozen(t0, bases, train_ids=train_ids)
    fus = fit_fusions(fit)
    arm = fit["arm"].astype(str).to_numpy()
    F = score_fusions(fus, fit, fit[REGIME_COL].to_numpy(dtype=float))
    taus = {c: arm_taus((fit[c] if c in BASES else F[c]).to_numpy(dtype=float), arm) for c in CANDIDATES}
    return Frozen(t0, bases, fus, taus, train_ids, tuple(fit["candidate_id"].astype(str)))


def score(frozen: Frozen, rows: pd.DataFrame) -> pd.DataFrame:
    """All 8 scores, their taus and keep flags on `rows` (fusions and keep flags NaN / False before the first fusion
    fit)."""
    S = score_bases(frozen.bases, rows)
    if frozen.fusions is not None:
        S = pd.concat([S, score_fusions(frozen.fusions, S, rows[REGIME_COL].to_numpy(dtype=float))], axis=1)
    else:
        S = S.assign(**dict.fromkeys(FUSIONS, np.nan))
    arm = rows["arm"].astype(str).to_numpy()
    for c in CANDIDATES:
        keep, tau = keep_by_tau(S[c].to_numpy(dtype=float), arm, frozen.taus.get(c, {}))
        S[f"keep_{c}"] = keep
        S[f"tau_{c}"] = tau
    return S


def frozen_hash(frozen: Frozen) -> str:
    """sha256 of every model and tau of a fit point: the confirm models are hashed before confirm is opened and the
    confirm read refuses a refit whose hash differs."""
    parts = [str(frozen.t0.value), SPEC_VERSION]
    for b in BASES:
        parts += [f"{b}:{a}:{m.fingerprint()}" for a, m in sorted(frozen.bases[b].items())]
    parts.append(frozen.fusions.fingerprint() if frozen.fusions is not None else "-")
    parts.append(json.dumps({c: sorted(t.items()) for c, t in sorted(frozen.taus.items())}))
    return _sha(*parts)


# ---------------------------------------------------------------------------------------------- walk-forward
_META = ["candidate_id", "arm", "session_day", "decision_utc", "label_exit_utc", "max_hold_s", "r", "r_mid", REGIME_COL]


@dataclass(frozen=True)
class WalkForward:
    """The OOF table (one row per candidate of the walk's blocks) and, per block, its t0 and the ids of its training
    rows and of its fit rows (every F2 / F3 fit row, F3 cell-edge row and tau row)."""
    oof: pd.DataFrame
    audit: dict[int, dict[str, Any]]


def walk_forward(rows: pd.DataFrame, *, blocks: Sequence[int] = OOF_BLOCKS) -> WalkForward:
    """The expanding yearly walk (2.3): block t's bases train on 2010..t-1 purged at t0 = the block's first decision
    (all arms); from the second block the fusions and all 8 taus fit on the purged base OOF of the earlier blocks."""
    _refuse_holdout(rows)
    tab = rows.sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True)
    year = _ts(tab["decision_utc"]).year
    oof = pd.DataFrame()
    audit: dict[int, dict[str, Any]] = {}
    for t in blocks:
        test = tab[np.asarray(year == t)].reset_index(drop=True)
        if test.empty:
            continue
        t0 = _ts(test["decision_utc"]).min()
        earlier = tab[np.asarray(year < t)]
        fz = freeze(earlier, oof if len(oof) else None, t0=t0)
        sc = score(fz, test)
        part = pd.concat([test.reindex(columns=_META), sc], axis=1)
        part = part.assign(block=t, block_t0=t0, base_train_end=t0 - EMBARGO)
        oof = pd.concat([oof, part], ignore_index=True) if len(oof) else part
        audit[t] = {"t0": t0, "train": list(fz.train_ids), "fit": list(fz.fit_ids)}
    return WalkForward(oof.reset_index(drop=True), audit)


def judged(oof: pd.DataFrame, *, blocks: Sequence[int] = JUDGED_BLOCKS) -> pd.DataFrame:
    """The judged rows: every candidate of `blocks` scored by all 8 and with a tau for all 8 (every paired comparison
    uses exactly these). A row of an arm with no model or no earlier OOF rows (a thin arm's first blocks) is left out
    of every candidate alike and counted by `unjudged`."""
    j = oof[oof["block"].isin(list(blocks))]
    cols = [*CANDIDATES, *[f"tau_{c}" for c in CANDIDATES]]
    ok = np.isfinite(j[cols].to_numpy(dtype=float)).all(axis=1)
    return j[ok].reset_index(drop=True)


def unjudged(oof: pd.DataFrame, *, blocks: Sequence[int] = JUDGED_BLOCKS) -> dict[str, int]:
    """Rows of `blocks` left out of the judged set, per arm (reported with the read)."""
    j = oof[oof["block"].isin(list(blocks))]
    cols = [*CANDIDATES, *[f"tau_{c}" for c in CANDIDATES]]
    bad = ~np.isfinite(j[cols].to_numpy(dtype=float)).all(axis=1)
    return {str(a): int(n) for a, n in j.loc[bad, "arm"].value_counts().items()}


def keep_shares(j: pd.DataFrame) -> dict[str, dict[tuple[int, str], float]]:
    """The realised OOS keep share for every candidate, block and arm (reported, 2.2)."""
    out: dict[str, dict[tuple[int, str], float]] = {}
    for c in CANDIDATES:
        m = j.groupby(["block", "arm"])[f"keep_{c}"].mean()
        out[c] = {(int(b), str(a)): float(v) for b, a, v in zip(m.index.get_level_values(0), m.index.get_level_values(1),
                                                                 m.to_numpy(), strict=True)}
    return out


# ---------------------------------------------------------------------------------------------- bootstrap
class JointBootstrap:
    """Stationary bootstrap (Politis-Romano) over the union of session days: mean block MEAN_BLOCK_DAYS, circular, one
    day index per draw serving every arm and every candidate (C7). `index` is (boot x n_days) day positions,
    `weights` the day counts per draw."""

    def __init__(self, days: np.ndarray, *, boot: int = BOOT, seed: int = SEED, mean_block: int = MEAN_BLOCK_DAYS):
        self.days, self.inv = np.unique(np.asarray(days).astype(str), return_inverse=True)
        n = len(self.days)
        rng = np.random.default_rng(seed)
        idx = np.empty((boot, n), dtype=np.int64)
        idx[:, 0] = rng.integers(n, size=boot)
        p = 1.0 / mean_block
        for k in range(1, n):
            jump = rng.random(boot) < p
            idx[:, k] = np.where(jump, rng.integers(n, size=boot), (idx[:, k - 1] + 1) % n)
        self.index = idx
        flat = (np.arange(boot)[:, None] * n + idx).ravel()
        self.weights = np.bincount(flat, minlength=boot * n).reshape(boot, n).astype(np.float64)

    def day_sums(self, arm: np.ndarray, *cols: np.ndarray) -> tuple[np.ndarray, list[np.ndarray]]:
        """(arm labels, per column a (n_days x n_arms) sum table)."""
        arms, ainv = np.unique(arm.astype(str), return_inverse=True)
        key = self.inv * len(arms) + ainv
        size = len(self.days) * len(arms)
        return arms, [np.bincount(key, weights=c, minlength=size).reshape(len(self.days), len(arms)) for c in cols]


def _stats(W: np.ndarray, n: np.ndarray, s: np.ndarray, kn: np.ndarray, ks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(pooled edge_nm, pooled kept mean) per weight row, as `pooled_edge`: an arm with no kept row in a draw is left
    out of that draw's edge (weights renormalised); a draw with no kept row at all is NaN (counts against)."""
    N, S, KN, KS = W @ n, W @ s, W @ kn, W @ ks
    with np.errstate(invalid="ignore", divide="ignore"):
        has = KN > 0
        e_arm = np.where(has, KS / np.where(has, KN, 1.0) - S / np.where(N > 0, N, 1.0), 0.0)
        wsum = np.where(has, N, 0.0).sum(axis=1)
        edge = (np.where(has, N, 0.0) * e_arm).sum(axis=1) / wsum
        kept = KS.sum(axis=1) / KN.sum(axis=1)
    return np.where(wsum > 0, edge, np.nan), kept


def _p(draws: np.ndarray) -> float:
    """(1 + #draws <= 0) / (1 + B); a NaN draw counts against."""
    return float((1 + int((~(draws > 0)).sum())) / (1 + len(draws)))


def _tables(j: pd.DataFrame, c: str, bs: JointBootstrap, col: str = "r") -> list[np.ndarray]:
    r = j[col].to_numpy(dtype=float)
    k = j[f"keep_{c}"].to_numpy(dtype=bool).astype(float)
    _, t = bs.day_sums(j["arm"].to_numpy(), np.ones(len(j)), r, k, r * k)
    return t


def metrics(j: pd.DataFrame, c: str, *, boot: int = BOOT, seed: int = SEED, bs: JointBootstrap | None = None,
            keep_draws: bool = False) -> dict[str, Any]:
    """Candidate `c` on the judged rows `j` (section 4): edge_nm, kept, gross (edge_nm on r_mid), n, n_kept, and the
    intersection-union p = max(p_edge, p_kept) of the joint stationary bootstrap."""
    bs = bs or JointBootstrap(j["session_day"].to_numpy(), boot=boot, seed=seed)
    t = _tables(j, c, bs)
    one = np.ones((1, len(bs.days)))
    e0, k0 = _stats(one, *t)
    g0, _ = _stats(one, *_tables(j, c, bs, "r_mid"))
    e, k = _stats(bs.weights, *t)
    keep = j[f"keep_{c}"].to_numpy(dtype=bool)
    out: dict[str, Any] = {"n": int(len(j)), "n_kept": int(keep.sum()), "n_days": int(len(bs.days)),
                           "edge_nm": float(e0[0]), "kept": float(k0[0]), "gross": float(g0[0]),
                           "p_edge": _p(e), "p_kept": _p(k)}
    out["p"] = max(out["p_edge"], out["p_kept"])
    if keep_draws:
        out["kept_draws"] = k
    return out


def paired(j: pd.DataFrame, f: str, b: str, *, boot: int = BOOT, seed: int = SEED, bs: JointBootstrap | None = None,
           keep_draws: bool = False) -> dict[str, Any]:
    """D(F, B*) = kept(F) - kept(B*) on the same rows with identical index draws; p_D = (1 + #D <= 0) / (1 + B)."""
    bs = bs or JointBootstrap(j["session_day"].to_numpy(), boot=boot, seed=seed)
    one = np.ones((1, len(bs.days)))
    tf, tb = _tables(j, f, bs), _tables(j, b, bs)
    d0 = float(_stats(one, *tf)[1][0] - _stats(one, *tb)[1][0])
    d = _stats(bs.weights, *tf)[1] - _stats(bs.weights, *tb)[1]
    out: dict[str, Any] = {"d": d0, "p_d": _p(d)}
    if keep_draws:
        out["d_draws"] = d
    return out


# ---------------------------------------------------------------------------------------------- reading rule
def _passes(m: Mapping[str, float], p_holm: float) -> bool:
    return (m["edge_nm"] >= MIN_EDGE_R and m["kept"] > 0 and m["gross"] >= 0 and p_holm <= ALPHA)


def decide_l(m: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """Stage L: c CONTINUES on edge_nm >= +0.05 R, kept > 0, gross >= 0 and Holm p <= 0.10 across the 8. B* = the
    continuing base with the highest learn edge_nm (no base continuing: among all bases), ties to the simpler.
    Nothing continuing: NULL (the family is retired)."""
    adj = sr.holm({c: float(m[c]["p"]) for c in CANDIDATES})
    cont = [c for c in CANDIDATES if _passes(m[c], adj[c])]
    pool = [b for b in BASES if b in cont] or list(BASES)
    best = min(pool, key=lambda b: (-float(m[b]["edge_nm"]), SIMPLICITY[b]))
    return {"holm": adj, "continuing": cont, "best_base": best, "verdict": "CONTINUE" if cont else "NULL"}


def decide_c(m: Mapping[str, Mapping[str, float]], *, continuing: Sequence[str], best_base: str,
             pairs: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    """Stage C: a continuing candidate passes as at Stage L (Holm across the continuing set). A fusion beats B* only if
    it passes, its paired D(F, B*) has Holm p_D <= 0.10 (family: the fusions that continued), D > 0 and kept > 0.
    Winner: the largest D among those, else the simplest passing BASE (a fusion that passes but does not beat B*
    through the paired test never wins by the fallback), else NULL."""
    cont = list(continuing)
    adj = sr.holm({c: float(m[c]["p"]) for c in cont}) if cont else {}
    passing = [c for c in cont if _passes(m[c], adj[c])]
    fus = [f for f in cont if f in FUSIONS]
    adj_d = sr.holm({f: float(pairs[f]["p_d"]) for f in fus}) if fus else {}
    beats = [f for f in fus if f in passing and adj_d[f] <= ALPHA and pairs[f]["d"] > 0 and m[f]["kept"] > 0]
    if beats:
        winner: str | None = max(beats, key=lambda f: (pairs[f]["d"], -SIMPLICITY[f]))
    else:      # quant 1: the fallback is the simplest passing BASE; a fusion wins only through the paired test
        bases = [c for c in passing if c in BASES]
        winner = min(bases, key=lambda c: SIMPLICITY[c]) if bases else None
    return {"holm": adj, "holm_d": adj_d, "passing": passing, "beats_b_star": beats, "best_base": best_base,
            "winner": winner, "verdict": "PASS" if winner else "NULL",
            "note": "the winner's confirm D is max-selected, not an effect estimate (C10)"}


def decide_t(m: Mapping[str, float], *, arms_left: int) -> dict[str, Any]:
    """Stage T: fewer than MIN_ARMS arms left after the ledger exclusion: INCONCLUSIVE; the winner qualifies on
    edge_nm > 0, kept > 0 and p <= 0.10. Qualifying is not promotion (C10)."""
    if arms_left < MIN_ARMS:
        return {"verdict": "INCONCLUSIVE", "reason": f"{arms_left} arms left < {MIN_ARMS} (C6)"}
    ok = m["edge_nm"] > 0 and m["kept"] > 0 and m["p"] <= ALPHA
    return {"verdict": "QUALIFIED" if ok else "NULL"}


def stage_l(j: pd.DataFrame, *, boot: int = BOOT, seed: int = SEED) -> dict[str, Any]:
    """Stage L on the judged learn OOF rows 2012-17: all 8 read with one set of joint draws."""
    bs = JointBootstrap(j["session_day"].to_numpy(), boot=boot, seed=seed)
    m = {c: metrics(j, c, bs=bs) for c in CANDIDATES}
    return {"metrics": m, **decide_l(m), "n_judged": int(len(j)), "keep_shares": {c: {f"{b}:{a}": v for (b, a), v in s.items()}
                                                         for c, s in keep_shares(j).items()}}


def stage_c(j: pd.DataFrame, *, continuing: Sequence[str], best_base: str, boot: int = BOOT,
            seed: int = SEED) -> dict[str, Any]:
    """Stage C on the confirm rows scored by the frozen models (read once)."""
    bs = JointBootstrap(j["session_day"].to_numpy(), boot=boot, seed=seed)
    m = {c: metrics(j, c, bs=bs) for c in sorted({*continuing, best_base}, key=CANDIDATES.index)}
    pairs = {f: paired(j, f, best_base, bs=bs) for f in continuing if f in FUSIONS}
    return {"metrics": m, "pairs": pairs, **decide_c(m, continuing=continuing, best_base=best_base, pairs=pairs)}


def stage_t(j: pd.DataFrame, *, winner: str, arms_left: int, boot: int = BOOT, seed: int = SEED) -> dict[str, Any]:
    """Stage T: the winner on test_old (read once), the same bootstrap."""
    m = metrics(j, winner, boot=boot, seed=seed)
    return {"winner": winner, "metrics": m, **decide_t(m, arms_left=arms_left)}


def shuffled_label_check(rows: pd.DataFrame, *, blocks: Sequence[int] = OOF_BLOCKS,
                         judged_blocks: Sequence[int] = JUDGED_BLOCKS, seed: int = SEED) -> dict[str, Any]:
    """Leakage check 1 (card section 3), before the real labels are read: the learn labels permuted (r and r_mid
    together), `feature_snapshots.shuffled_label_auc` on the 40 inputs, and every base learner's judged OOF AUC through
    this module's own walk-forward; all inside SHUFFLED_AUC_BAND or the card does not read (0 K)."""
    from sklearn.metrics import roc_auc_score

    from goldbot.research.feature_snapshots import shuffled_label_auc
    perm = np.random.default_rng(seed).permutation(len(rows))
    bent = rows.copy()
    bent["r"] = rows["r"].to_numpy()[perm]
    bent["r_mid"] = rows["r_mid"].to_numpy()[perm]
    y = (bent["r"].to_numpy(dtype=float) > 0).astype(int)
    auc = {"lightgbm_snapshot": shuffled_label_auc(sr.design(bent), y, seed=seed, columns=list(INPUTS))}
    j = judged(walk_forward(bent, blocks=blocks).oof, blocks=judged_blocks)
    yj = (j["r"].to_numpy(dtype=float) > 0).astype(int)
    for b in BASES:
        auc[b] = float(roc_auc_score(yj, j[b].to_numpy(dtype=float)))
    lo, hi = SHUFFLED_AUC_BAND
    return {"auc": auc, "ok": all(lo <= v <= hi for v in auc.values())}


def test_old_excluded(arm_rule: Mapping[str, str], read_families: Collection[str]) -> list[str]:
    """Arms excluded label-blind before test_old (C6): the arm id or its rule (its family) already read on test_old."""
    fam = set(read_families)
    return sorted(a for a, rule in arm_rule.items() if a in fam or rule in fam)


# ---------------------------------------------------------------------------------------------- Stage 0
def count_table(cands: pd.DataFrame) -> pd.DataFrame:
    """Outcome-blind counts (no label column is read): per arm and block, the judged rows of learn 2012-17 and of
    confirm (rows whose horizon or exit reaches the holdout dropped), with their session days."""
    _refuse_holdout(cands)
    dec = _ts(cands["decision_utc"])
    learn = cands[np.asarray(dec.year.isin(list(JUDGED_BLOCKS)))].assign(gate="L")
    learn = learn.assign(block=_ts(learn["decision_utc"]).year)
    conf, _ = read_rows(cands.reindex(columns=["arm", "decision_utc", "label_exit_utc", "max_hold_s", "session_day"]),
                        CONFIRM_FROM, CONFIRM_TO)
    conf = conf.assign(gate="C", block=_ts(conf["decision_utc"]).year)
    both = pd.concat([learn, conf], ignore_index=True)
    return both[["gate", "block", "arm", "session_day"]].reset_index(drop=True)


def _gate(n: float, m_day: float, rho_day: float) -> tuple[float, float]:
    """(n_eff, the SE of kept minus all) for `n` judged rows (section 4a)."""
    n_eff = n / (1.0 + (max(m_day, 1.0) - 1.0) * rho_day)
    se = SD_R * math.sqrt((1 - KEEP_SHARE) / (KEEP_SHARE * n_eff)) if n_eff > 0 else math.inf
    return n_eff, se


def stage0(counts: pd.DataFrame, *, excluded: Collection[str], settings: Settings
           ) -> tuple[pf.PowerVerdict, pf.PowerCard]:
    """Stage 0 (section 4a) from `count_table`: per gate n, m_day (rows per session day, floored at 1), n_eff, the SE of
    kept minus all (k = 2/3, sd = 1.2 R), power_g = Phi(delta / SE - z_g), and the joint power of both IUT legs at L, C
    and T (`power_first.joint_power`, one draw per window and leg). Start: every power_g >= 0.5, joint >= 0.25, n_eff
    >= 200 at C and T, every 2012-17 block >= 60 rows, and at least MIN_ARMS arms."""
    p = settings.research.power_first
    delta, rho = max(DELTA_R, p.delta_r), max(RHO_DAY, p.rho_day)
    reasons: list[str] = []
    L, C = counts[counts["gate"] == "L"], counts[counts["gate"] == "C"]
    arms = sorted(set(L["arm"].astype(str)))
    if len(arms) < MIN_ARMS:
        reasons.append(f"{len(arms)} replayable arms < {MIN_ARMS}: the card does not run")
    per_block = L.groupby("block").size().reindex(list(JUDGED_BLOCKS), fill_value=0)
    reasons += [f"block {b}: {int(n)} judged rows < {MIN_BLOCK_ROWS}" for b, n in per_block.items() if n < MIN_BLOCK_ROWS]
    years = float(len(JUDGED_BLOCKS))
    n_l, d_l = float(len(L)), float(L["session_day"].nunique())
    n_c, d_c = float(len(C)), float(C["session_day"].nunique())
    left = [a for a in arms if a not in set(excluded)]
    n_t = float(L["arm"].astype(str).isin(left).sum()) / years * TEST_OLD_YEARS
    m_l = n_l / d_l if d_l else 1.0
    m_c = n_c / d_c if d_c else 1.0
    gates_in = (("L", n_l, m_l, Z_L, False), ("C", n_c, m_c, Z_C, True), ("T", n_t, m_l, Z_T, True))
    gates, n_eff_leg, legs = [], {}, []
    for name, n, m_day, z, fresh in gates_in:
        n_eff, se = _gate(n, m_day, rho)
        power = float(stats.norm.cdf(delta / se - z)) if n_eff > 0 else 0.0
        gates.append(pf.GatePower(name=name, n_expected=n, n_eff=n_eff, z=z, fresh=fresh, power=power))
        # joint_power reads SE = sd / sqrt(x): the edge leg's x = n_eff k / (1 - k), the kept leg's x = k n_eff
        n_eff_leg[f"{name}_edge"] = n_eff * KEEP_SHARE / (1 - KEEP_SHARE)
        n_eff_leg[f"{name}_kept"] = n_eff * KEEP_SHARE
        legs += [pf.Gate(name=f"{name}_{leg}", segments=(f"{name}_{leg}",), z=z, fresh=fresh) for leg in ("edge", "kept")]
    joint = pf.joint_power(n_eff_leg, legs, delta, SD_R, sims=p.sims, seed=p.seed) if n_l > 0 else 0.0
    reasons += [f"gate {g.name}: power {g.power:.3f} < {p.min_gate_power} (n {g.n_expected:.0f}, n_eff {g.n_eff:.0f}, "
                f"z {g.z:.3f})" for g in gates if g.power < max(MIN_GATE_POWER, p.min_gate_power)]
    reasons += [f"gate {g.name}: n_eff {g.n_eff:.0f} < {p.n_eff_floor:.0f}" for g in gates
                if g.fresh and g.n_eff < max(N_EFF_FLOOR, p.n_eff_floor)]
    if joint < max(MIN_JOINT_POWER, p.min_joint_power):
        reasons.append(f"joint power {joint:.3f} < {p.min_joint_power} (both IUT legs at L, C, T)")
    card = pf.PowerCard(card_id=f"H-101 {SPEC_VERSION}", family=REGISTRY_FAMILY, timeframe="1m-15m",
                        learn_signals=int(n_l), learn_signal_clusters=int(min(d_l, n_l)), learn_years=years,
                        hold_days=HOLD_DAYS, instruments=[GOLD],
                        segments=[pf.Segment(name="L", years=years), pf.Segment(name="C", years=CONFIRM_YEARS),
                                  pf.Segment(name="T", years=TEST_OLD_YEARS)],
                        gates=[pf.Gate(name=n, segments=(n,), z=z, fresh=f) for n, _, _, z, f in gates_in])
    risk_gbp = settings.research.lot_fit_equity * settings.risk.risk_per_trade
    goal = pf.goal_edge_r(n_l / years / p.trading_days_per_year, settings.research.north_star_daily_gbp, risk_gbp)
    verdict = pf.PowerVerdict(card_id=card.card_id, family=card.family, timeframe=card.timeframe, started=not reasons,
                              delta_r=delta, goal_edge_r=goal, sd_r=SD_R,
                              rho_instrument=pf.rho_instrument([GOLD], p.rho_instrument, p.rho_instrument_metals),
                              design_effect=1.0 + (max(m_l, 1.0) - 1.0) * rho, gates=gates, joint_power=joint,
                              min_gate_power=p.min_gate_power, min_joint_power=p.min_joint_power,
                              n_eff_floor=p.n_eff_floor, reasons=reasons)
    return verdict, card


def stage0_record(registry: TrialRegistry, counts: pd.DataFrame, *, excluded: Collection[str], settings: Settings,
                  agent_id: str) -> pf.PowerVerdict:
    """Stage 0 through `power_first.start_or_refuse`: a refusal is a NOT STARTED (power) row (0 K, nothing read);
    a start writes nothing here."""
    verdict, card = stage0(counts, excluded=excluded, settings=settings)
    return pf.start_or_refuse(registry, card, settings, agent_id=agent_id, verdict=verdict)


def candidate_config(c: str) -> dict[str, Any]:
    return {"spec": SPEC_VERSION, "candidate": c, "name": NAMES[c], "arms": list(ARMS), "instrument": GOLD}


def _not_started(registry: TrialRegistry) -> bool:
    from goldbot.research.registry import NOT_STARTED_POWER
    card_id = f"H-101 {SPEC_VERSION}"
    return any(r.get("status") == NOT_STARTED_POWER and (r.get("config") or {}).get("card_id") == card_id
               for r in registry.by_family(REGISTRY_FAMILY))


def book_stage_l(registry: TrialRegistry, settings: Settings, *, stage0: pf.PowerVerdict, agent_id: str,
                 now: Any = None, provenance: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """K = 8 at Stage L, before any label is read: one registry row per candidate (family `model_zoo_fusion`), after
    the budget check against `research.trial_budget_quarter` (refused over it). Only with a started Stage 0 and no
    NOT STARTED (power) row of this spec, and only in ALLOCATED_QUARTER. Each row carries `provenance` (git SHA,
    learn-rows sha256) and the Stage 0 verdict. A candidate already booked is not booked again."""
    if not stage0.started:
        raise PowerRefused("H-101 Stage 0 did not start (power): Stage L is not booked and nothing is read")
    with registry.locked():
        if _not_started(registry):
            raise PowerRefused(f"H-101 {SPEC_VERSION} has a NOT STARTED (power) row: Stage L is never booked")
        q = quarter_of(now)
        if q != ALLOCATED_QUARTER:
            raise QuarterRefused(f"H-101's 8 K are allocated from {ALLOCATED_QUARTER}, not {q}: a new allocation first")
        have = {r.get("config_hash") for r in registry.by_family(REGISTRY_FAMILY)}
        todo = [c for c in CANDIDATES if config_hash(candidate_config(c)) not in have]
        if not todo:
            return []
        registry.check_budget(len(todo), settings.research.budget_for(q), now)
        results = {"stage": "L", "booked_before_read": True, **dict(provenance or {}),
                   "stage0": stage0.model_dump(mode="json")}
        return [registry.record(agent_id=agent_id, family=REGISTRY_FAMILY, config=candidate_config(c),
                                feature_version=SPEC_VERSION, rule_ref=PREREG, budget_quarter=q,
                                rationale=f"H-101 Stage L candidate {c} ({NAMES[c]}), booked before any label is read",
                                results=results)
                for c in todo]


def stage_l_written_back(registry: TrialRegistry) -> bool:
    """Whether a Stage L outcome of this spec was already attached to its K rows (the read happened)."""
    mine = {config_hash(candidate_config(c)) for c in CANDIDATES}
    return any(e.get("family") == REGISTRY_FAMILY and e.get("config_hash") in mine
               for e in registry.evidence(kind="stage_l_outcome"))


def write_back_stage_l(registry: TrialRegistry, outcome: Mapping[str, Any], *, frozen_hash: str) -> int:
    """The Stage L outcome and the frozen confirm-model hash, attached to each of this spec's K rows as evidence
    (kind `stage_l_outcome`: the registry is append-only, the trial count is unchanged). Returns rows written."""
    if stage_l_written_back(registry):
        raise ValueError(f"a Stage L outcome of {SPEC_VERSION} was already written back: Stage L reads once")
    keep = ("verdict", "continuing", "best_base", "holm", "n_judged", "metrics")
    payload = {"frozen_hash": frozen_hash, **{k: outcome[k] for k in keep if k in outcome}}
    n = 0
    for r in registry.by_family(REGISTRY_FAMILY):
        if r.get("trial") and (r.get("config") or {}).get("spec") == SPEC_VERSION:
            registry.attach_evidence(int(r["trial"]), "stage_l_outcome", payload)
            n += 1
    return n


__all__ = ["ARMS", "BASES", "CANDIDATES", "FUSIONS", "Frozen", "GoldOnly", "JointBootstrap", "PowerRefused",
           "WalkForward", "book_stage_l", "count_table", "decide_c", "decide_l", "decide_t", "freeze", "frozen_hash",
           "check_inputs", "gold_only", "judged", "keep_shares", "metrics", "paired", "pooled_edge", "read_rows", "score", "stage0",
           "stage0_record", "stage_c", "stage_l_written_back", "write_back_stage_l", "stage_l", "stage_t", "test_old_excluded", "walk_forward"]
