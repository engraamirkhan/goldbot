"""Novelty check (ADR 0058 section 3.4), explore window only. A spec is NOVEL only if (a) the Jaccard overlap of its
entry bars with every reference (existing family, arm, earlier survivor) is < 0.30 and (b) the |correlation| of its
daily net-R series with each reference is < 0.50. Otherwise it is a VARIANT of the nearest reference: still counted
in K, labelled with the parent, and it advances only if its returns carry positive alpha over the parent's with
t >= 2 (`incremental_alpha_t`). Novelty is a label and a hurdle, never a filter that hides a failure.

References come from the explore-window masks of the existing families (`autodisc.primitives`, via the evaluator);
`existing_names()` lists the families and arms by name from `config/exploration_arms.yaml` and
`arm_rules.RULE_FUNCS`. A spec whose family has no reference series is labelled a variant by NAME (its parent is
that family) rather than passed as novel: absence of a reference is never evidence of novelty."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from goldbot.research.autodisc.contracts import BoolArr, FloatArr, IntArr

JACCARD_MAX = 0.30
CORR_MAX = 0.50
ALPHA_T_MIN = 2.0
_ARMS_YAML = Path(__file__).resolve().parents[3] / "config" / "exploration_arms.yaml"


@dataclass(frozen=True)
class Reference:
    name: str                 # family rule, arm id or survivor hash
    entry_mask: BoolArr       # entry bars over the explore tensor
    daily_r: FloatArr         # daily net R, one value per explore session day (0 on no-trade days)


@dataclass(frozen=True)
class NoveltyResult:
    novel: bool
    nearest: str | None       # parent when a variant
    jaccard: float | None
    corr: float | None
    reason: str               # "novel", "overlap", "correlated" or "named"

    @property
    def label(self) -> str:
        return "novel" if self.novel else f"variant_of:{self.nearest}"


def existing_names() -> frozenset[str]:
    """Every existing family (rule) and arm id by name."""
    from goldbot.research.arm_rules import RULE_FUNCS
    names = set(RULE_FUNCS)
    for row in yaml.safe_load(_ARMS_YAML.read_text())["arms"]:
        names.add(row["id"])
        names.add(row["rule"])
    names.discard("blocked")
    return frozenset(names)


def jaccard(a: BoolArr, b: BoolArr) -> float:
    union = int(np.count_nonzero(a | b))
    return float(np.count_nonzero(a & b)) / union if union else 0.0


def daily_series(r: FloatArr, bar_idx: IntArr, day_id: IntArr, n_days: int) -> FloatArr:
    """Daily net-R series from per-trade R at `bar_idx`."""
    return np.bincount(day_id[bar_idx], weights=r, minlength=n_days).astype(float)


def _corr(a: FloatArr, b: FloatArr) -> float:
    if a.std() == 0.0 or b.std() == 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def check_novelty(family: str, mask: BoolArr, daily_r: FloatArr, refs: list[Reference],
                  known_names: frozenset[str] | None = None) -> NoveltyResult:
    """Label one spec. `family` is the spec's entry family (used only when no reference carries that name)."""
    if not refs or family not in {r.name for r in refs}:
        names = known_names if known_names is not None else existing_names()
        if family in names:
            return NoveltyResult(False, family, None, None, "named")
    if not refs:
        return NoveltyResult(True, None, None, None, "novel")
    pairs = [(jaccard(mask, r.entry_mask), abs(_corr(daily_r, r.daily_r)), r.name) for r in refs]
    # variant when ANY reference breaches either bound; the parent is the nearest by overlap
    breach = [p for p in pairs if p[0] >= JACCARD_MAX or p[1] >= CORR_MAX]
    if not breach:
        return NoveltyResult(True, None, max(p[0] for p in pairs), max(p[1] for p in pairs), "novel")
    j, c, name = max(breach, key=lambda x: (x[0], x[1]))
    return NoveltyResult(False, name, j, c, "overlap" if j >= JACCARD_MAX else "correlated")


def incremental_alpha_t(r: FloatArr, parent_r: FloatArr) -> float:
    """t of the intercept (alpha) in r = alpha + beta * parent_r + e over the daily series (OLS, homoskedastic)."""
    n = r.size
    if n < 4 or parent_r.std() == 0.0:
        return 0.0
    x = np.column_stack([np.ones(n), parent_r])
    coef, *_ = np.linalg.lstsq(x, r, rcond=None)
    resid = r - x @ coef
    s2 = float(resid @ resid) / (n - 2)
    cov = s2 * np.linalg.inv(x.T @ x)
    se = float(np.sqrt(cov[0, 0]))
    return float(coef[0] / se) if se > 0 else 0.0


def variant_advances(r: FloatArr, parent_r: FloatArr) -> bool:
    return incremental_alpha_t(r, parent_r) >= ALPHA_T_MIN
