"""Data-sufficiency contract (owner 2026-11-25: "the data team should be responsible for providing the necessary and
sufficient data for models"). For every model cell (instrument class x timeframe x objective) the ML side states the
requirement (config/data/sufficiency_requirements.yaml); the data team measures what the graded data supplies and
certifies the cell SUFFICIENT or SHORT, a SHORT cell always with the gap, the plan that closes it and its date.
"Too little data" is never a conclusion here: it is a gap with an owner and a date.

Requirement (stated by the ML side; the sr-v3 card, docs/research/prereg-2026-11-26-sr-v3.md, is the source):
- training: Riley et al. 2019 (continuous outcome, shrinkage >= 0.9, design R2 0.0207): RILEY_PER_PARAM = 430 n_eff
  per input, so a model of p inputs needs 430 p (a 40-input model 17,200; one 10-input block 4,300);
- judging: the A22 power table (card 5.1) at the label-blind keep share (default 0.33: 1,109 n_eff).
Supply: n_eff per window by the card's method (ADR 0085 Q1/Q3, card 2.2; outcome-blind counts), until the dense label
factory (D-5) measures it per cell. One method for both sides: this module never recounts n_eff another way.
A cell is SUFFICIENT when its learn n_eff carries the planned inputs and its judge n_eff meets the power row; else
SHORT with the gaps, plans and dates. `max_inputs` = learn n_eff / 430 is reported either way.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import NormalDist
from typing import Any, Literal

from goldbot.base import FrozenRecord

RILEY_PER_PARAM = 430
# card 5.1 (A22): n_eff for 80 % power, one-sided 0.05, at S = +0.05 R, by label-blind keep share
POWER_N_EFF: dict[float, int] = {0.10: 333, 0.20: 666, 0.33: 1109, 0.50: 1664, 1.00: 3328}
Verdict = Literal["SUFFICIENT", "SHORT"]


def r2_from_auc(auc: float, prevalence: float = 0.5) -> float:
    """Explained variance of a binary outcome by a score with this AUC (binormal, equal variances): d = sqrt(2)
    Phi^-1(AUC), r^2 = d^2 / (d^2 + 1 / (p (1 - p))). A cross-check of the card's design R2, not a requirement."""
    d = math.sqrt(2.0) * NormalDist().inv_cdf(auc)
    return d * d / (d * d + 1.0 / (prevalence * (1.0 - prevalence)))


def power_n(delta: float, sd: float = 1.0, alpha: float = 0.05, power: float = 0.8) -> int:
    """n_eff for a one-sided test of mean >= delta (sd per row) at alpha with the given power (the k = 1 row of A22
    with sd = 1 R is 2,474; the card's 3,328 uses its own sd)."""
    z = NormalDist().inv_cdf(1 - alpha) + NormalDist().inv_cdf(power)
    return math.ceil((z * sd / delta) ** 2)


class Requirement(FrozenRecord):
    """One cell's requirement as the ML side states it (defaults: the sr-v3 card)."""
    cell: str
    unit: str                                  # the card's judged unit (instrument or pooled class)
    tf: str
    objective: str = "obj-v1"
    inputs: int = 10                           # one information block (R-D); the core may not carry 40
    keep_share: float = 0.33
    tiers: str = "r11-v2.1"
    label_templates: str = "T0-T7 (D-4)"
    source: str = "sr-v3 card rev 2 (PR #460) 2.2 / 5.1"

    def n_train(self) -> int:
        return RILEY_PER_PARAM * self.inputs

    def n_judge(self) -> int:
        return POWER_N_EFF[self.keep_share]


class Gap(FrozenRecord):
    gap: str
    plan: str
    owner: str
    due: str
    blocks: bool = True          # the cell cannot be certified until it closes (a projection is not a measurement)
    item: str = ""               # the data item id (sr-v3 card 9a: D-1..D-11)


class Certificate(FrozenRecord):
    cell: str
    verdict: Verdict
    # on the numbers alone if every open item delivers: SUFFICIENT / SHORT, INFEASIBLE (no pooling at rho_inst
    # reaches the need), "UNMEASURED (D-1)" (supply not countable before grading), "n/a: B is daily-only"
    projected: str
    n_eff_learn: int | None
    n_eff_judge: int | None
    need_train: int
    need_judge: int
    max_inputs: int | None
    gaps: tuple[Gap, ...] = ()
    requirement: dict[str, Any]
    method: str = ""             # how learn and judge were counted (one method per cell: quant A3 on PR #482)

    def health_line(self) -> str:
        def f(x: int | None) -> str:
            return "unmeasured" if x is None else f"{x:,}"
        head = (f"{self.verdict} {self.cell}: n_eff learn {f(self.n_eff_learn)}/{self.need_train:,} "
                f"(max inputs {f(self.max_inputs)}), judge {f(self.n_eff_judge)}/{self.need_judge:,}")
        if self.verdict == "SUFFICIENT":
            return head
        head += f", projected {self.projected}"
        g = self.gaps[0]
        more = f" (+{len(self.gaps) - 1} more)" if len(self.gaps) > 1 else ""
        return f"{head}; gap: {g.gap}; plan: {g.plan} ({g.owner}, due {g.due}){more}"


def certify(req: Requirement, n_eff_learn: int | None, n_eff_judge: int | None, gaps: Sequence[Gap],
            projected: str | None = None, method: str = "") -> Certificate:
    """SUFFICIENT when the learn n_eff carries the planned inputs, the judge n_eff meets the power row and no open
    item blocks; an unmeasured window is SHORT (its gap: measure it). `gaps`: the cell's open data items, most urgent
    first; non-blocking ones are listed but do not hold the certificate."""
    need_t, need_j = req.n_train(), req.n_judge()
    enough = (n_eff_learn is not None and n_eff_judge is not None and n_eff_learn >= need_t
              and n_eff_judge >= need_j)
    ok = enough and not any(g.blocks for g in gaps)
    if not ok and not gaps:
        raise ValueError(f"{req.cell}: a SHORT cell needs its gap, plan, owner and date")
    return Certificate(cell=req.cell, verdict="SUFFICIENT" if ok else "SHORT",
                       projected=projected or ("SUFFICIENT" if enough else "SHORT"), n_eff_learn=n_eff_learn,
                       n_eff_judge=n_eff_judge, need_train=need_t, need_judge=need_j,
                       max_inputs=None if n_eff_learn is None else n_eff_learn // RILEY_PER_PARAM,
                       gaps=tuple(gaps), requirement=req.model_dump(mode="json"), method=method)


METHODS = ("dense-skeleton", "card-2.2")
NOT_STARTED_DATA = "NOT STARTED (data)"
RHO_INST = 0.5            # card 2.2: pooled classes divide by 1 + (k - 1) rho, so pooling adds at most 1 / rho


def _gap(item: str, d: dict[str, Any], blocks: bool = True) -> Gap:
    return Gap(item=item, gap=d["gap"], plan=d["plan"], owner=str(d["owner"]), due=str(d["due"]), blocks=blocks)


def unit_method(unit: str, u: dict[str, Any]) -> str:
    """The unit's counting method (quant A3 on PR #482, verbatim: "Every unit's yaml row states its method (`method:
    dense-skeleton | card-2.2`), and the renderer refuses a cell whose learn and judge methods differ."). `method` is
    one name, or {learn: ..., judge: ...} naming the same one."""
    m = u.get("method")
    if isinstance(m, dict):
        if m.get("learn") != m.get("judge"):
            raise ValueError(f"{unit}: learn is counted by {m.get('learn')!r} and judge by {m.get('judge')!r}: one "
                             "method per cell (quant A3)")
        m = m.get("learn")
    if m not in METHODS:
        raise ValueError(f"{unit}: method {m!r} is not one of {METHODS} (quant A3)")
    return str(m)


def _at(values: list[Any] | None, i: int) -> int | None:
    """One tf's supply: None (the whole row, or this tf: not measurable, e.g. 2h with no bar rule) or an int."""
    if values is None or values[i] is None:
        return None
    return int(values[i])


def certificates(cfg: dict[str, Any]) -> list[Certificate]:
    """Every unit x tf cell of a sufficiency config (config/data/sufficiency.yaml)."""
    from goldbot.config import TF_SECONDS
    req0 = cfg["requirement"]
    out = []
    for unit, u in cfg["units"].items():
        method = unit_method(unit, u)
        daily_only = u.get("tiers_kept") == "B"
        for i, tf in enumerate(cfg["tfs"]):
            req = Requirement(cell=f"{unit}/{tf}", unit=unit, tf=tf, inputs=int(req0["inputs"]),
                              keep_share=float(req0["keep_share"]), objective=req0["objective"],
                              label_templates=req0["label_templates"])
            learn = _at(u["learn"], i)
            judge = _at(u["judge"], i)
            gaps = [_gap(k, cfg["items"][k]) for k in [*cfg.get("tf_items", {}).get(tf, []), *u["items"]]]
            projected: str | None = None
            if daily_only and TF_SECONDS.get(tf, 0) < TF_SECONDS["1d"]:
                # quant C2: supply is what the loader keeps; B months load at 1d/1w only
                learn = judge = 0
                projected = "n/a: B is daily-only"
                gaps.insert(0, Gap(item="B-DAILY", gap="B months carry close-to-close labels and daily features "
                                   "only: no intraday rows load", plan="intraday rows need A months: D-14 second "
                                   "source to lift these months, D-13 graded instruments; this unit serves 1d/1w",
                                   owner="data-engineer", due="2026-12-09"))
            need = req.n_train()
            if learn == 0 and projected is None:
                # quant A1 on PR #482 (verbatim): "`learn == 0` (measured, no usable graded rows under ADR 0104 §4):
                # projected `NOT STARTED (data)`, gap item `NO-ROWS` 'no usable graded rows under ADR 0104 §4 for
                # the column set', plan 'D-1 (ii), D-13, D-14; re-assessed at each monthly regrade (card 2.3)', no
                # POOL or INFEASIBLE item."
                projected = NOT_STARTED_DATA
                gaps.insert(len(cfg.get("tf_items", {}).get(tf, [])), Gap(
                    item="NO-ROWS", gap="no usable graded rows under ADR 0104 §4 for the column set",
                    plan="D-1 (ii), D-13, D-14; re-assessed at each monthly regrade (card 2.3)",
                    owner="data-engineer", due="next monthly regrade"))
            elif learn is not None and learn and learn / RHO_INST < need:
                bound = int(learn / RHO_INST)
                k = learn // RILEY_PER_PARAM
                projected = projected or "INFEASIBLE"
                gaps.append(Gap(item="INFEASIBLE", gap=f"no pooling reaches {need:,} (bound {bound:,} at rho_inst "
                                f"{RHO_INST})", plan="data remedies first: more independent instruments (D-13), "
                                "learn extended to 2003-09 if D-1 grades it, measured rho_day/rho_inst (D-12) may "
                                f"raise the bound; meanwhile cap inputs at max_inputs ({k}) or no model at this tf "
                                "(rule-only)", owner="data-engineer + ml-researcher", due="2026-12-11"))
            elif learn is not None and learn < need:
                gaps.append(Gap(item="POOL", gap=f"learn n_eff {learn:,} < {need:,} for a {req.inputs}-input block "
                                f"(max {learn // RILEY_PER_PARAM} inputs)", plan="deliver the pooled cross-class set "
                                f"at this tf (card 2.2 pooling, rho_inst {RHO_INST}; bound {int(learn / RHO_INST):,}) "
                                "and the per-cell input cap to the card", owner="data-engineer + ml-researcher",
                                due="2026-12-16"))
            if judge is not None and judge and judge < req.n_judge():
                gaps.append(Gap(item="POWER", gap=f"judge n_eff {judge:,} < {req.n_judge():,} at keep "
                                f"{req.keep_share}", plan="judge at a lower label-blind keep share (A22 row) or "
                                "pooled; the 2003-09 test_old years once D-1 grades them; more independent "
                                "instruments (D-13)", owner="quant-reviewer + data-engineer", due="2026-12-11"))
            if u.get("ungraded_projection") and projected is None:
                projected = str(u["ungraded_projection"])          # quant C2: gold before D-1
            out.append(certify(req, learn, judge, gaps, projected, method))
    return out


__all__ = ["METHODS", "NOT_STARTED_DATA", "unit_method", "POWER_N_EFF", "RHO_INST", "RILEY_PER_PARAM", "Certificate", "Gap", "Requirement", "certificates",
           "certify", "power_n", "r2_from_auc"]
