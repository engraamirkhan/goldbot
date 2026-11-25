"""The obj-v1 baseline ladder (ML lifecycle plan 2026-11-25, N2 as extended by A23 / ruling R-S): "judged on the same
OOF rows ... A model must beat max(B0, B3, B4, B5)", and after R-S "serve only if the model beats max(B0, B3, B4, B5,
B6, B8) on paired D". Every baseline is frozen by rule (0 K) and read through `objective.read`, so the same clip,
the same ABSTAIN-from-counts rule and the same S apply to the model and to every rung.

Rows (`eval_rows` = the judged OOF rows, `learn_rows` = rows the baselines may fit on, all decided and labelled before
the judged block) carry: `net`, `gross` (R per candidate, unclipped), `cost` (the ub95 cost of record known at
decision, R), `arm`, `day` (session day), `t0` / `t1` (decision and label exit, UTC), and any cell or rule column
named by the caller. `twin_net` (the side- and hour-matched control twin's net R) is optional.

- B0 ABSTAIN: keep nothing.
- B1 take-all.
- B2 random keep at the model's keep count in every cell (seeded).
- B3 per-arm intercept, the offset form with no features: keep iff learn mean gross of the arm − cost(x) > 0.
- B4 the best one-column rule chosen on learn only: keep iff column <= or >= one of its learn deciles, the rule with
  the highest learn S (a cost or range-to-cost column; "tf × session" tables are B5).
- B5 the per-cell mean-net-R table: keep a cell iff its learn mean net R > 0 (a cell unseen on learn is not kept).
- B6 per-arm trailing-year switch: an arm trades in year y iff its mean net R over year y−1 > 0, using only rows whose
  label had closed by 1 January of y.
- B7 matched twin (descriptive, not on the bar): the model's keep applied to the twin's net R. Prices out "the model
  picks good times, not good trades".
- B8 arm × year intercept, IN SAMPLE on the judged rows (keep an arm-year iff its own mean net R > 0). Not tradeable;
  it prices out regime switching ("keep_shares swing 0.42-0.99 by arm × year"), so S(B8) >= 0 always. `best_bar`
  reports the best rung with and without it.

Learn rows must all have closed before the first judged decision (`ladder_keeps` refuses otherwise), so a learn row
is never also a judged row and B6 never counts a row twice.

Every arm judged here is a retired, learn-selected arm, so any edge over this ladder is conditional on that selection
(S9S gap); a card states it.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.research import objective as O

NAMES: tuple[str, ...] = ("B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8")
BAR: tuple[str, ...] = ("B0", "B3", "B4", "B5", "B6", "B8")     # the max a model must beat (R-S)
RULE_QUANTILES: tuple[float, ...] = tuple(q / 10 for q in range(1, 10))
SEED = 20261127


class BaselineRead(FrozenRecord):
    """One rung, read by `objective.read` (boot = 0: the paired bootstrap against the model is eval-v1's)."""
    name: str
    available: bool
    in_sample: bool
    s: float | None
    keep_share: float
    n_kept: int
    n_kept_days: int
    abstain: bool
    note: str = ""


class Rule(FrozenRecord):
    """B4's rule: keep iff `col` `op` `threshold`, chosen on learn by obj-v1 S."""
    col: str
    op: Literal["le", "ge"]
    threshold: float
    learn_s: float


# ---------------------------------------------------------------------------------------------- the rungs
def keep_random_matched(rows: pd.DataFrame, model_keep: np.ndarray, *, cells: Sequence[str],
                        seed: int = SEED) -> np.ndarray:
    """B2: in every cell keep as many rows as the model kept there, drawn at random (seeded)."""
    mk = np.asarray(model_keep, dtype=bool)
    rng = np.random.default_rng(seed)
    out = np.zeros(len(rows), dtype=bool)
    for _, idx in rows.reset_index(drop=True).groupby(list(cells), sort=True, dropna=False).indices.items():
        k = int(mk[idx].sum())
        if k:
            out[rng.choice(idx, size=k, replace=False)] = True
    return out


def keep_arm_intercept(rows: pd.DataFrame, learn: pd.DataFrame) -> np.ndarray:
    """B3: keep iff mean clipped gross R of the arm on learn − cost(x) > 0 (an arm unseen on learn: not kept)."""
    mu = pd.Series(O.clip_r(learn["gross"]), index=learn.index).groupby(learn["arm"]).mean()
    pred = rows["arm"].map(mu).to_numpy(dtype=float)
    return np.isfinite(pred) & O.keep_rule(np.nan_to_num(pred, nan=-np.inf), rows["cost"].to_numpy(dtype=float))


def _s(rows: pd.DataFrame, keep: np.ndarray) -> float:
    return O.read(rows["net"].to_numpy(dtype=float), rows["gross"].to_numpy(dtype=float), keep,
                  rows["day"].to_numpy(), boot=0).s


def choose_rule(learn: pd.DataFrame, *, rule_cols: Sequence[str]) -> Rule:
    """B4: over every named column, its learn deciles and both directions, the rule with the highest learn S (ties:
    the first in column, decile, le-before-ge order). Chosen on learn only; never sees the judged rows."""
    best: Rule | None = None
    for col in rule_cols:
        x = learn[col].to_numpy(dtype=float)
        fin = np.isfinite(x)
        if not fin.any():
            continue
        for thr in np.unique(np.quantile(x[fin], RULE_QUANTILES)):
            for op in ("le", "ge"):
                s = _s(learn, fin & ((x <= thr) if op == "le" else (x >= thr)))
                if best is None or s > best.learn_s:
                    best = Rule(col=col, op=op, threshold=float(thr), learn_s=s)
    if best is None:
        raise ValueError("choose_rule: no finite learn value in any rule column")
    return best


def apply_rule(rows: pd.DataFrame, rule: Rule) -> np.ndarray:
    x = rows[rule.col].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        return np.isfinite(x) & ((x <= rule.threshold) if rule.op == "le" else (x >= rule.threshold))


def keep_cell_means(rows: pd.DataFrame, learn: pd.DataFrame, *, cells: Sequence[str]) -> np.ndarray:
    """B5: keep a cell iff its learn mean clipped net R > 0."""
    cols = list(cells)
    mu = pd.Series(O.clip_r(learn["net"]), index=learn.index).groupby([learn[c] for c in cols]).mean()
    good = set(mu.index[mu.to_numpy() > 0])
    keys = rows[cols].itertuples(index=False, name=None) if len(cols) > 1 else rows[cols[0]]
    return np.array([k in good for k in keys], dtype=bool)


def _year(t: pd.Series) -> np.ndarray:
    return pd.DatetimeIndex(t).year.to_numpy()


def keep_trailing_year(rows: pd.DataFrame, history: pd.DataFrame) -> np.ndarray:
    """B6: an arm is on in year y iff its mean clipped net R over rows decided in year y−1 whose label closed before
    1 January y is > 0 (`history` may include the judged rows; only closed labels of the previous year are read)."""
    t0 = pd.DatetimeIndex(history["t0"])
    t1 = pd.DatetimeIndex(history["t1"])
    net = O.clip_r(history["net"])
    arm = history["arm"].astype(str).to_numpy()
    on: dict[tuple[str, int], bool] = {}
    yrs = _year(rows["t0"])
    for y in np.unique(yrs):
        start = pd.Timestamp(f"{int(y)}-01-01", tz="UTC")
        m = (t0.year == y - 1) & (t1 < start)
        for a in np.unique(arm[m]):
            on[(str(a), int(y))] = float(net[m & (arm == a)].mean()) > 0
    return np.array([on.get((str(a), int(y)), False) for a, y in zip(rows["arm"], yrs, strict=True)], dtype=bool)


def keep_arm_year_in_sample(rows: pd.DataFrame) -> np.ndarray:
    """B8: in sample, keep an (arm, year) iff its own mean clipped net R > 0."""
    yrs = _year(rows["t0"])
    mu = pd.Series(O.clip_r(rows["net"])).groupby([rows["arm"].astype(str).to_numpy(), yrs]).transform("mean")
    return mu.to_numpy() > 0


# ---------------------------------------------------------------------------------------------- the ladder
def _rung(name: str, rows: pd.DataFrame, keep: np.ndarray, *, in_sample: bool = False, net_col: str = "net",
          gross_col: str = "gross", note: str = "") -> BaselineRead:
    cx = rows["cost"].to_numpy(dtype=float) if net_col == "net" else None
    rd = O.read(rows[net_col].to_numpy(dtype=float), rows[gross_col].to_numpy(dtype=float), keep,
                rows["day"].to_numpy(), cost_x=cx, boot=0)
    return BaselineRead(name=name, available=True, in_sample=in_sample, s=rd.s, keep_share=rd.keep_share,
                        n_kept=rd.n_kept, n_kept_days=rd.n_kept_days, abstain=rd.abstain, note=note)


def ladder_keeps(eval_rows: pd.DataFrame, learn_rows: pd.DataFrame, *, model_keep: np.ndarray,
                 cells: Sequence[str] = ("arm",), rule_cols: Sequence[str] = ("cost",),
                 seed: int = SEED) -> tuple[dict[str, np.ndarray], dict[str, str]]:
    """(keep vector per rung, note per rung) for B0..B6 and B8 on `eval_rows`, so a walk-forward caller can pool the
    keeps of several blocks and read the pooled unit once (the max(0, ·) is per judged unit, R-M)."""
    ev = eval_rows.reset_index(drop=True)
    lr = learn_rows.reset_index(drop=True)
    if len(ev) and len(lr) and (pd.DatetimeIndex(lr["t1"]) >= pd.DatetimeIndex(ev["t0"]).min()).any():
        raise ValueError("ladder_keeps: a learn label closes at or after the first judged decision")
    n = len(ev)
    rule = choose_rule(lr, rule_cols=rule_cols)
    keeps = {
        "B0": np.zeros(n, dtype=bool),
        "B1": np.ones(n, dtype=bool),
        "B2": keep_random_matched(ev, model_keep, cells=cells, seed=seed),
        "B3": keep_arm_intercept(ev, lr),
        "B4": apply_rule(ev, rule),
        "B5": keep_cell_means(ev, lr, cells=cells),
        "B6": keep_trailing_year(ev, pd.concat([lr, ev], ignore_index=True)),
        "B8": keep_arm_year_in_sample(ev),
    }
    notes = {"B4": f"{rule.col} {rule.op} {rule.threshold:.6g}", "B5": "cells " + " x ".join(cells),
             "B8": "in sample: not tradeable"}
    return keeps, notes


def read_ladder(rows: pd.DataFrame, keeps: Mapping[str, np.ndarray], *, model_keep: np.ndarray,
                notes: Mapping[str, str] | None = None) -> dict[str, BaselineRead]:
    """Every rung through the obj-v1 read on the same rows; B7 from `twin_net` when the rows carry it."""
    ev = rows.reset_index(drop=True)
    notes = notes or {}
    out = {b: _rung(b, ev, keeps[b], in_sample=b == "B8", note=notes.get(b, "")) for b in keeps}
    if "twin_net" in ev.columns:
        out["B7"] = _rung("B7", ev, np.asarray(model_keep, dtype=bool), net_col="twin_net", gross_col="twin_net",
                          note="descriptive: the model's keep on the matched twin")
    else:
        out["B7"] = BaselineRead(name="B7", available=False, in_sample=False, s=None, keep_share=0.0, n_kept=0,
                                 n_kept_days=0, abstain=True, note="no matched twin rows")
    return {k: out[k] for k in NAMES if k in out}


def ladder(eval_rows: pd.DataFrame, learn_rows: pd.DataFrame, *, model_keep: np.ndarray,
           cells: Sequence[str] = ("arm",), rule_cols: Sequence[str] = ("cost",),
           seed: int = SEED) -> dict[str, BaselineRead]:
    """B0..B8 on `eval_rows` (the model's judged rows, one block), each through the obj-v1 read."""
    keeps, notes = ladder_keeps(eval_rows, learn_rows, model_keep=model_keep, cells=cells, rule_cols=rule_cols,
                                seed=seed)
    return read_ladder(eval_rows, keeps, model_keep=model_keep, notes=notes)


def best_bar(lad: Mapping[str, BaselineRead], *, include_b8: bool = True) -> tuple[str, float]:
    """(name, S) of the best available rung on the bar; ties go to the earlier rung. `include_b8=False` gives the best
    tradeable rung (B8 is in sample); a card reports both (quant ruling 4 on PR #461), and the serve rule uses B8."""
    bar = [b for b in BAR if include_b8 or b != "B8"]
    avail = [(b, float(s)) for b in bar if b in lad and lad[b].available and (s := lad[b].s) is not None]
    return max(avail, key=lambda t: t[1])


def beats_ladder(model: O.ObjectiveRead, lad: Mapping[str, BaselineRead]) -> bool:
    """A model that abstained, or whose S does not strictly exceed the best rung on the bar, is not served. The paired
    day-block D against that rung (`objective.paired_delta`) is read on top of this, in eval-v1."""
    return not model.abstain and model.s > best_bar(lad)[1]
