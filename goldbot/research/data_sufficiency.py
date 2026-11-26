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
from collections.abc import Mapping, Sequence
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
            ul, uj = unit_rows(cfg, unit)
            learn = _at(ul, i)
            judge = _at(uj, i)
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


# --------------------------------------------------------------------------------------------- consumers and routes
# Owner directive 2026-11-26: the data team provides the data every model needs (organic, pooled, synthetic or other)
# and never concludes "not enough data". Each consumer states its own requirement; each route that can close a short
# cell states its COUNTED n_eff gain per cell and its effort, and routes are ranked by counted n_eff per day of work.
RouteKind = Literal["organic", "pooled", "synthetic", "other"]
# aug-v1 (SYNTH-ml R10, ml-8): synthetic, bootstrap and augmented rows count 0 toward n_eff, power and every gate;
# offset (phase) rows count through uniqueness only, which card 2.2 already does (phase rows add no n_eff).
SYNTHETIC_COUNTS_ZERO = True


class Consumer(FrozenRecord):
    """One model consumer of the data, with the requirement its card or plan states."""
    id: str
    label: str
    inputs: int                                # Riley 430 n_eff per input on the learn window
    keep_share: float = 0.33                   # A22 power row of the judged read
    tfs: tuple[str, ...]                       # the timeframes it is judged (or trained) at
    forward_only: bool = False                 # judged on forward rows only (agg-v1): history counts do not serve it
    forward_n_eff: int | None = None           # its own forward requirement per cell
    source: str

    def n_train(self) -> int:
        return RILEY_PER_PARAM * self.inputs

    def n_judge(self) -> int:
        return POWER_N_EFF[self.keep_share]


class Route(FrozenRecord):
    """A way to close short cells. `gain` = COUNTED n_eff added per cell ("unit/tf" -> n_eff, card 2.2 method, learn
    window unless the key ends in ":judge"); a route whose rows never count (synthetic) carries 0 and says what it adds
    for training in `training_gain`."""
    id: str
    kind: RouteKind
    title: str
    gain: dict[str, int]
    training_gain: str = ""
    effort_days: float
    signoff: str = ""                          # who signs before use (quant for every synthetic route)
    p_deliver: float = 1.0                     # the data team's probability the gain lands as stated (ranking only)
    basis: Literal["measured", "projected"] = "projected"
    owner: str
    due: str
    status: str
    pr: str = ""

    def counted(self) -> int:
        return int(sum(self.gain.values()))

    def per_day(self) -> float:
        """Expected counted n_eff per day of work: counted x p_deliver / effort_days (the ranking key)."""
        return self.counted() * self.p_deliver / self.effort_days


def check_route(r: Route) -> Route:
    """The aug-v1 rules as code: a synthetic route counts 0 n_eff and names the quant's sign-off; every route has a
    positive effort, an owner and a date."""
    if r.effort_days <= 0:
        raise ValueError(f"{r.id}: effort_days must be > 0")
    if not 0 < r.p_deliver <= 1:
        raise ValueError(f"{r.id}: p_deliver must be in (0, 1]")
    if not (r.owner and r.due):
        raise ValueError(f"{r.id}: a route needs an owner and a date")
    if r.kind == "synthetic":
        if SYNTHETIC_COUNTS_ZERO and any(v != 0 for v in r.gain.values()):
            raise ValueError(f"{r.id}: synthetic rows count 0 toward n_eff, power and gates (aug-v1 R10)")
        if "quant" not in r.signoff:
            raise ValueError(f"{r.id}: every synthetic route is signed by the quant before use")
    return r


def rank_routes(routes: Sequence[Route]) -> list[Route]:
    """Expected counted n_eff per day of work, highest first; ties by the earlier due date, then id."""
    return sorted((check_route(r) for r in routes), key=lambda r: (-r.per_day(), r.due, r.id))


def pooled_supply(members: Sequence[Sequence[int | None]], rho_inst: float) -> list[int | None]:
    """Card 2.2 pooling of per-instrument n_eff rows (same tf order): sum / (1 + (k - 1) rho_inst), k = the members
    with rows at that tf. None where no member is counted."""
    out: list[int | None] = []
    for col in zip(*members, strict=True):
        vals = [int(v) for v in col if v]
        out.append(None if not vals else int(sum(vals) / (1 + (len(vals) - 1) * rho_inst)))
    return out


# card 2.2 holds (scripts/research/sufficiency_counts.py, solved from the card's gold row): minutes below a day
CARD_HOLD_MIN = {"1m": 60, "5m": 240, "15m": 480, "30m": 960, "1h": 1380}
CARD_HOLD_DAYS = {"2h": 2, "4h": 5, "1d": 10, "1w": 20}
CARD_RHO_DAY = 0.25


def card_per_day(tf: str) -> float:
    """n_eff per graded session day of one instrument by the card 2.2 method."""
    if tf in CARD_HOLD_MIN:
        u = 1380 / CARD_HOLD_MIN[tf]
        return u / (1 + (u - 1) * CARD_RHO_DAY)
    return 1 / CARD_HOLD_DAYS[tf]


class ConsumerCell(FrozenRecord):
    consumer: str
    cell: str                                  # unit/tf
    n_eff_learn: int | None
    n_eff_judge: int | None
    need_train: int
    need_judge: int
    short_learn: int                           # 0 when met
    short_judge: int
    status: Literal["MEETS", "SHORT", "FORWARD"]
    routes: tuple[str, ...]                    # routes with a counted gain on this cell, ranked
    closed_by: str | None = None               # the ranked route whose cumulative gain (at full delivery) closes it
    closed_due: str | None = None
    residual_learn: int = 0                    # shortfall left after every route delivers in full
    residual_judge: int = 0
    max_inputs_after: int | None = None        # Riley input cap on learn supply after every route


def consumer_cells(consumers: Sequence[Consumer], supply: Mapping[str, Mapping[str, tuple[int | None, int | None]]],
                   routes: Sequence[Route], inherit: Mapping[str, Sequence[str]] | None = None) -> list[ConsumerCell]:
    """Every consumer x unit x tf it uses. `supply`: unit -> tf -> (learn, judge). A SHORT cell lists the ranked
    routes that add counted n_eff to it, the route whose cumulative gain closes it (`closed_by`, at full delivery) or
    the residual after every route; a cell with no route at all raises (the owner rule: a gap always has a plan).
    `inherit`: unit -> other units whose route gains also reach it (gold's cells become the pooled body's once A2
    pools them, so a gain on the pooled body reaches gold too; it is not counted twice in a route's total)."""
    ranked = rank_routes(routes)
    inherit = inherit or {}
    out = []
    for c in consumers:
        for unit, by_tf in supply.items():
            for tf in c.tfs:
                if tf not in by_tf:
                    continue
                learn, judge = by_tf[tf]
                cell = f"{unit}/{tf}"
                keys = [cell, *(f"{a}/{tf}" for a in inherit.get(unit, ()))]
                steps = [(r, sum(r.gain.get(k, 0) for k in keys), sum(r.gain.get(f"{k}:judge", 0) for k in keys))
                         for r in ranked]
                steps = [x for x in steps if x[1] > 0 or x[2] > 0]
                rs = tuple(r.id for r, _, _ in steps)
                if c.forward_only:
                    out.append(ConsumerCell(consumer=c.id, cell=cell, n_eff_learn=learn, n_eff_judge=judge,
                                            need_train=c.n_train(), need_judge=c.forward_n_eff or c.n_judge(),
                                            short_learn=0, short_judge=0, status="FORWARD", routes=rs))
                    continue
                sl = max(0, c.n_train() - (learn or 0))
                sj = max(0, c.n_judge() - (judge or 0))
                status: Literal["MEETS", "SHORT"] = "SHORT" if sl or sj else "MEETS"
                if status == "SHORT" and not rs:
                    raise ValueError(f"{c.id} {cell}: SHORT with no route (the gap needs a plan and a date)")
                cl = cj = 0
                closed: Route | None = None
                for r, gl, gj in steps:
                    cl, cj = cl + gl, cj + gj
                    if closed is None and status == "SHORT" and cl >= sl and cj >= sj:
                        closed = r
                out.append(ConsumerCell(consumer=c.id, cell=cell, n_eff_learn=learn, n_eff_judge=judge,
                                        need_train=c.n_train(), need_judge=c.n_judge(), short_learn=sl,
                                        short_judge=sj, status=status, routes=rs,
                                        closed_by=None if closed is None else closed.id,
                                        closed_due=None if closed is None else closed.due,
                                        residual_learn=max(0, sl - cl), residual_judge=max(0, sj - cj),
                                        max_inputs_after=((learn or 0) + cl) // RILEY_PER_PARAM))
    return out


# D-12 (PR #466, signed): the binding day-mean rho_u,inst per class membership and hold bucket
# (docs/research/rho-measurement-2026-11.json `binding.rho_u_inst_day_mean`, the `max` over its variants). Quant S-1 on
# #486: "Pooled projections use the D-12 binding day-mean rho_u,inst for the body's membership and hold bucket; 0.2 is
# the D-13 admission bound for a new instrument vs gold, never a pooling divisor." R-e: a single cell divisor is
# reporting only; a pooled read's Stage 0 Riley gate uses card 2.3's per-column charges (D-12 rho_u, D-12a rho_x).
LABEL_H_S = {"1m": 3_600, "5m": 14_400, "15m": 28_800, "30m": 57_600, "1h": 86_400, "2h": 172_800, "4h": 432_000,
             "1d": 864_000, "1w": 1_728_000}         # dl-v1 H_BY_TF (the label horizon sets the hold bucket)


def hold_bucket(tf: str) -> str:
    h = LABEL_H_S[tf]
    return "le60m" if h <= 3_600 else ("1h-4h" if h <= 14_400 else "gt4h")


def measured_rho(cfg: dict[str, Any], src: dict[str, Any]) -> dict[str, float]:
    """tf -> binding rho_u,inst for `src` = {file, key, stat}: the D-12 value of that membership at the tf's bucket."""
    import json

    from goldbot.config import ROOT
    d = json.loads((ROOT / src["file"]).read_text())["binding"]["rho_u_inst_day_mean"]
    return {tf: min(1.0, float(d[f"{src['key']}|{hold_bucket(tf)}"][src.get("stat", "max")])) for tf in cfg["tfs"]}


def _rho_row(cfg: dict[str, Any], pdef: dict[str, Any], override: float | None = None) -> list[float]:
    if override is not None:
        return [override] * len(cfg["tfs"])
    if "rho_inst_from" in pdef:
        m = measured_rho(cfg, pdef["rho_inst_from"])
        return [m[tf] for tf in cfg["tfs"]]
    return [float(pdef["rho_inst"])] * len(cfg["tfs"])


def pooled_supply_by_tf(rows: Sequence[Sequence[int | None]], rho: Sequence[float]) -> list[int | None]:
    """`pooled_supply` with a divisor per tf."""
    out: list[int | None] = []
    for i, col in enumerate(zip(*rows, strict=True)):
        vals = [int(v) for v in col if v]
        out.append(None if not vals else int(sum(vals) / (1 + (len(vals) - 1) * rho[i])))
    return out


def unit_rows(cfg: dict[str, Any], unit: str) -> tuple[list[int | None] | None, list[int | None] | None]:
    """A unit's learn and judge rows; a card-pooled unit (`card_pooled: {k, rho_inst, rho_inst_from}`) is re-divided
    at the D-12 binding rho of its membership and bucket: x (1 + (k-1) rho_card) / (1 + (k-1) rho_measured)."""
    u = cfg["units"][unit]
    learn = None if u["learn"] is None else [None if v is None else int(v) for v in u["learn"]]
    judge = None if u["judge"] is None else [None if v is None else int(v) for v in u["judge"]]
    cp = u.get("card_pooled")
    if cp:
        k, r0 = int(cp["k"]), float(cp["rho_inst"])
        m = measured_rho(cfg, cp["rho_inst_from"])
        f = [(1 + (k - 1) * r0) / (1 + (k - 1) * m[tf]) for tf in cfg["tfs"]]
        learn = None if learn is None else [None if v is None else int(v * f[i]) for i, v in enumerate(learn)]
        judge = None if judge is None else [None if v is None else int(v * f[i]) for i, v in enumerate(judge)]
    return learn, judge


def _measured_learn(u: dict[str, Any]) -> list[int | None] | None:
    m = u.get("measured") or {}
    row = m.get("learn_T2_core6")
    return None if row is None else [None if v is None else int(v) for v in row]


def members(cfg: dict[str, Any], rows: dict[str, Any]) -> list[list[int | None]]:
    """A pooled body's member rows; a string "unit.key" points at a unit's measured row (one source of truth)."""
    out = []
    for v in rows.values():
        if isinstance(v, str):
            unit, key = v.split(".", 1)
            v = cfg["units"][unit]["measured"][key]
        out.append([None if x is None else int(x) for x in v])
    return out


def supply(cfg: dict[str, Any]) -> dict[str, dict[str, tuple[int | None, int | None]]]:
    """unit -> tf -> (learn, judge) for the consumer table. Learn is the dl-v1 MEASURED T2 core-6 n_eff where the unit
    has it (the judged head, tier survival and input lookback applied), else the contract's card count; B units load
    no intraday row (quant C2). Pooled bodies follow (`pooled_supply` at their rho_inst)."""
    from goldbot.config import TF_SECONDS
    tfs = list(cfg["tfs"])
    out: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    for unit, u in cfg["units"].items():
        if u["learn"] is None:
            continue
        meas = _measured_learn(u)
        daily_only = u.get("tiers_kept") == "B"
        row: dict[str, tuple[int | None, int | None]] = {}
        for i, tf in enumerate(tfs):
            if daily_only and TF_SECONDS.get(tf, 0) < TF_SECONDS["1d"]:
                row[tf] = (0, 0)
                continue
            ul, uj = unit_rows(cfg, unit)
            learn = meas[i] if meas is not None and meas[i] is not None else (None if ul is None else ul[i])
            row[tf] = (learn, None if uj is None else uj[i])
        out[unit] = row
    for name, pdef in (cfg.get("pooled") or {}).items():
        rho = _rho_row(cfg, pdef)
        lrn = pooled_supply_by_tf(members(cfg, pdef["learn_members"]), rho)
        jdg = pooled_supply_by_tf(members(cfg, pdef["judge_members"]), rho)
        out[f"pooled_{name}"] = {tf: (lrn[i], jdg[i]) for i, tf in enumerate(tfs)}
    return out


def routes(cfg: dict[str, Any]) -> list[Route]:
    """The contract's routes with every derived gain resolved from the counts in the same file (`gain_from`), so no
    gain is typed by hand where it can be computed: `pooled` (pooled body at the D-12 binding rho minus the base unit,
    learn and judge), `project` (card 2.2 projection of graded instruments), `measured` (one measured row minus
    another, e.g. untiered minus tiered), `measured_2h` (each unit's measured 2h cell, built only with the bar rule)."""
    tfs = list(cfg["tfs"])
    sup = supply(cfg)
    out = []
    for r in cfg.get("routes") or []:
        r = dict(r)
        gf = r.pop("gain_from", None) or {}
        gain = {k: int(v) for k, v in (r.get("gain") or {}).items()}
        if "pooled" in gf:
            pdef = cfg["pooled"][gf["pooled"]]
            rho = _rho_row(cfg, pdef, gf.get("rho"))
            lrn = pooled_supply_by_tf(members(cfg, pdef["learn_members"]), rho)
            jdg = pooled_supply_by_tf(members(cfg, pdef["judge_members"]), rho)
            base = sup[gf["base"]]
            for i, tf in enumerate(tfs):
                bl_, bj_ = base[tf]
                gain[f"{gf['base']}/{tf}"] = max(0, (lrn[i] or 0) - (bl_ or 0))
                gain[f"{gf['base']}/{tf}:judge"] = max(0, (jdg[i] or 0) - (bj_ or 0))
        for unit, spec in (gf.get("project") or {}).items():
            # card 2.2 projection of k instruments graded A over ldays / jdays session days: intraday cells only (a B
            # unit's months already load at 1d/1w), or every tf when the 4th element is "all" (new instruments)
            k, ldays, jdays = spec[:3]
            every_tf = len(spec) > 3 and spec[3] == "all"
            for tf in tfs:
                if every_tf or tf not in ("1d", "1w"):
                    pooled_k = 1 + (int(k) - 1) * RHO_INST
                    gain[f"{unit}/{tf}"] = int(card_per_day(tf) * int(ldays) * int(k) / pooled_k)
                    gain[f"{unit}/{tf}:judge"] = int(card_per_day(tf) * int(jdays) * int(k) / pooled_k)
        if "measured" in gf:
            m = cfg["units"][gf["measured"]]["measured"]
            for i, tf in enumerate(tfs):
                a, b = m[gf["to"]][i], m[gf["from"]][i]
                if a is not None and b is not None:
                    gain[f"{gf['measured']}/{tf}"] = max(0, int(a) - int(b))
        if gf.get("measured_2h"):
            i = tfs.index("2h")
            for unit, u in cfg["units"].items():
                v = (_measured_learn(u) or [None] * len(tfs))[i]
                if v:
                    gain[f"{unit}/2h"] = int(v)
        r["gain"] = {k: v for k, v in gain.items() if v > 0}
        out.append(Route(**r))
    return out


def inherit(cfg: dict[str, Any]) -> dict[str, list[str]]:
    """Units whose cells take a pooled body's route gains (`pooled.<name>.serves`)."""
    out: dict[str, list[str]] = {}
    for name, pdef in (cfg.get("pooled") or {}).items():
        for unit in pdef.get("serves") or ():
            out.setdefault(unit, []).append(f"pooled_{name}")
    return out


def consumers(cfg: dict[str, Any]) -> list[Consumer]:
    return [Consumer(id=k, **{**v, "tfs": tuple(v["tfs"])}) for k, v in (cfg.get("consumers") or {}).items()]


__all__ = ["METHODS", "NOT_STARTED_DATA", "unit_method", "POWER_N_EFF", "RHO_INST", "RILEY_PER_PARAM", "SYNTHETIC_COUNTS_ZERO", "Certificate", "Consumer",
           "ConsumerCell", "Gap", "Requirement", "Route", "certificates", "certify", "check_route", "consumer_cells",
           "consumers", "hold_bucket", "inherit", "measured_rho", "members", "pooled_supply", "pooled_supply_by_tf", "unit_rows", "power_n", "r2_from_auc", "rank_routes", "routes", "supply"]
