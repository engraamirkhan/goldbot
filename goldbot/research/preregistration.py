"""Write a quarter's pre-registered queue into the trial registry (BACKLOG 47; ADR 0007).

Design rule (preregistration-2027Q1.md, "Writing the queued rows"): "Each row above is written with
`TrialRegistry.preregister(..., queue=True, target_quarter="2027Q1")` before 2027-01-01 (a queued row written once its
quarter is under way is refused, and a hand-written one is treated as ad hoc)", and the survivor slots with
`reserve_slot(h02_row, "survivor", k, derivation_rule, "2027Q1", survivor_pool=[...])`, k = 1..4.

The spec is a reviewed YAML extract of the pre-registration (`docs/research/preregistration-<quarter>.yaml`). Before
anything is written, `plan_quarter` rebuilds each row's config the way its runner will (`research_pass.py
--variants`, `--discover`: `resolve_variant`, `discovery.discovery_trial_config`), so the queued row and the run hash
the same, and refuses a spec whose pinned config hash, agent id or feature version differs; whose reading or
derivation rule is not the pre-registration's own text; or whose rows + slots differ from its reservation and
`research.reserved_trials_quarter`, or exceed `research.trial_budget_quarter`. `write_quarter` writes only through
`TrialRegistry.preregister` / `reserve_slot` under the registry lock, is idempotent (a row or slot already in the
registry is left alone), refuses a row of the quarter it does not know, and refuses to write once the quarter has
started unless `allow_late` states a reason, in which case the rows are written as ad-hoc pre-registrations naming
their rule (`queue=False`, `rule_ref`) that hold no reservation, and no slot is written (a slot exists only before its
quarter). A pre-registration is not a trial (`registry.is_trial`): nothing here spends budget or writes an outcome.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from goldbot.base import FrozenRecord
from goldbot.research import registry as registry_mod
from goldbot.research.registry import (
    PREREGISTERED,
    PREREGISTERED_RULE_IDS,
    TrialRegistry,
    config_hash,
    quarter_start,
    refers_to,
    row_ref,
)

_ID = r"^[A-Z0-9][A-Za-z0-9-]*$"
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
NAMED_CLUSTERS = ("H02_CLUSTERS",)        # cluster maps of `research.discovery` a discovery spec may name


class SpecError(ValueError):
    """The spec is not the pre-registration, breaks a registry rule, or does not fit the quarter's budget."""


class LateWrite(SpecError):
    """A queued row or slot would be written after its quarter started."""


class SpecRow(FrozenRecord):
    """One queued row of the spec: a specialist configuration (`research_pass.py --specialist ... --variants`) or a
    discovery (`--discover`). `pending` names what is still owed (not writable); `withdrawn` the reason a row was
    dropped by its own pre-registered rule before the freeze (left out, and out of the reservation)."""
    id: str = Field(pattern=_ID)
    kind: Literal["specialist", "discovery"] = "specialist"
    specialist: str
    variant: str | dict[str, Any] | None = None        # a preset name or overrides; None only while pending
    discovery: dict[str, Any] | None = None            # kind discovery: DiscoveryConfig overrides
    group_mode: str = "column"                         # kind discovery: --families
    macro: bool = False                                # the run adds the macro drivers (macro-v1)
    bars_from: str | None = None                       # kind discovery: the run's first and last 1m bar
    bars_to: str | None = None
    agent_id: str | None = None                        # pins, checked against the rebuilt config
    config_hash: str | None = Field(None, pattern=r"^[0-9a-f]{12}$")
    feature_version: str = Field(pattern=r"^f-[0-9a-f]{10}$")
    rule_id: str | None = None
    reading_rule: str = Field(min_length=1)
    rationale: str
    pending: str | None = None
    withdrawn: str | None = None

    @field_validator("rule_id")
    @classmethod
    def _coded_rule(cls, v: str | None) -> str | None:
        if v is not None and v not in PREREGISTERED_RULE_IDS:
            raise ValueError(f"rule_id must be one of {PREREGISTERED_RULE_IDS} (director.preregistered_decisions), "
                             f"got {v!r}")
        return v

    @field_validator("bars_from", "bars_to")
    @classmethod
    def _run_timestamp_text(cls, v: str | None, info: Any) -> str | None:
        """`discovery_pass` writes `str(b1["ts_utc"].iloc[0])` (a UTC Timestamp) into the hashed config, so the spec
        must hold exactly that text: any other spelling of the same instant would never match the run (quant M4)."""
        if v is None:
            return v
        import pandas as pd
        try:
            ts = pd.Timestamp(v)
        except ValueError as exc:
            raise ValueError(f"{info.field_name} {v!r} is not a timestamp: {exc}") from None
        if ts.tzinfo is None or ts.utcoffset() != pd.Timedelta(0):
            raise ValueError(f"{info.field_name} {v!r} must be UTC, as the run's ts_utc is (e.g. "
                             f"'2025-09-30 20:59:00+00:00')")
        if str(ts) != v:
            raise ValueError(f"{info.field_name} {v!r} must be written as the run writes it: {str(ts)!r}")
        return v

    @field_validator("rationale")
    @classmethod
    def _names_the_commit(cls, v: str) -> str:
        if "{commit}" not in v:
            raise ValueError("the rationale names the pre-registration's freeze commit: put {commit} in it")
        return v

    @model_validator(mode="after")
    def _complete_unless_pending(self) -> SpecRow:
        if self.kind == "specialist" and (self.discovery is not None or self.bars_from or self.bars_to):
            raise ValueError(f"{self.id}: discovery, bars_from and bars_to are for kind discovery")
        if self.pending is None and self.withdrawn is None:
            missing = [f for f in ("variant", "config_hash") if getattr(self, f) is None]
            if self.kind == "discovery":
                missing = [f for f in ("variant", "bars_from", "bars_to") if getattr(self, f) is None]
            if missing:
                raise ValueError(f"{self.id}: {', '.join(missing)} missing; mark the row pending until it is settled")
        return self


class SpecSlot(FrozenRecord):
    """Contingent survivor slots on a discovery row (ADR 0007): one per `k`. Q1 writes survivor slots only (no
    follow-up and no holdout slots, preregistration-2027Q1.md table 2)."""
    parent: str = Field(pattern=_ID)
    kind: Literal["survivor"]
    k: list[int] = Field(min_length=1)
    survivor_pool: list[str] = Field(min_length=1)
    rule: dict[str, float]
    derivation_rule: str = Field(min_length=1)

    @field_validator("k")
    @classmethod
    def _one_to_n(cls, v: list[int]) -> list[int]:
        if v != list(range(1, len(v) + 1)):
            raise ValueError(f"slot indices k count 1, 2, ... once each, got {v}")
        return v

    @field_validator("survivor_pool")
    @classmethod
    def _unique_pool(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError(f"survivor_pool names each cluster once, got {v}")
        return v

    @field_validator("rule")
    @classmethod
    def _slot_rule_keys(cls, v: dict[str, float]) -> dict[str, float]:
        bad = sorted(set(v) - set(registry_mod.DEFAULT_SLOT_RULE))
        if bad:
            raise ValueError(f"slot rule keys are {sorted(registry_mod.DEFAULT_SLOT_RULE)}, got {bad}")
        return v


class QuarterSpec(FrozenRecord):
    """The reviewed spec of one quarter's pre-registered queue."""
    quarter: str = Field(pattern=r"^\d{4}Q[1-4]$")
    source: str                                        # the pre-registration the rules are quoted from
    freeze_commit: str | None = None                   # fills {commit} in each rationale; required to write
    reservation: int = Field(ge=0)
    rows: list[SpecRow] = Field(min_length=1)
    slots: list[SpecSlot] = Field(default_factory=list)

    @field_validator("freeze_commit")
    @classmethod
    def _commit(cls, v: str | None) -> str | None:
        if v is not None and not _COMMIT.match(v):
            raise ValueError(f"freeze_commit is a git commit id (7-40 hex), got {v!r}")
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> QuarterSpec:
        ids = [r.id for r in self.rows]
        if len(set(ids)) != len(ids):
            raise ValueError(f"row ids are unique, got {ids}")
        return self


class PlannedRow(FrozenRecord):
    """A queued row as it will be written (`TrialRegistry.preregister`); config None while pending."""
    id: str
    family: str
    agent_id: str | None
    config: dict[str, Any] | None
    config_hash: str | None
    feature_version: str
    rule_id: str | None
    reading_rule: str
    rationale: str
    plan: dict[str, Any]
    pending: str | None
    withdrawn: str | None


class PlannedSlot(FrozenRecord):
    """A contingent slot as it will be written (`TrialRegistry.reserve_slot`)."""
    parent: str
    kind: str
    k: int
    survivor_pool: list[str]
    rule: dict[str, float]
    derivation_rule: str

    @property
    def id(self) -> str:
        return f"{self.parent}#{self.k}"


class QuarterPlan(FrozenRecord):
    quarter: str
    source: str
    freeze_commit: str | None
    reservation: int
    setting: int                                       # research.reserved_for(quarter) when planned
    budget: int
    rows: list[PlannedRow]
    slots: list[PlannedSlot]

    @property
    def active_rows(self) -> list[PlannedRow]:
        return [r for r in self.rows if r.withdrawn is None]

    @property
    def open_items(self) -> list[str]:
        """What the spec still owes (a pending row, the freeze commit): no write of any kind until settled."""
        out = [f"{r.id} pending: {r.pending}" for r in self.active_rows if r.pending]
        if self.freeze_commit is None:
            out.append("freeze_commit is empty (the pre-registration's freeze commit fills {commit})")
        return out

    @property
    def blockers(self) -> list[str]:
        """Why the queue cannot be written before its quarter (empty: writable): the open items, and a reservation
        in the settings that differs from the spec's (a late write checks the settings its own way, `write_quarter`)."""
        out = self.open_items
        if self.setting != self.reservation:
            out.append(f"research.reserved_trials_quarter holds {self.setting} for {self.quarter}, the spec "
                       f"{self.reservation}: change config/settings.yaml in the same reviewed change")
        return out

    @property
    def writable(self) -> bool:
        return not self.blockers


class WriteAction(FrozenRecord):
    id: str
    action: Literal["written", "present", "skipped"]
    detail: str = ""


# ---------------------------------------------------------------------------------------------- planning
def _norm(text: str) -> str:
    return " ".join(text.split())


_BULLET = re.compile(r"^(\s*)- (.*)$")
_READING = re.compile(r"^Reading rule\b\s*:?\s*")


def reading_rules(doc_text: str) -> set[str]:
    """Every "Reading rule" bullet of the pre-registration, whitespace-normalised: the bullet's text after the words
    "Reading rule" (and a colon straight after them) to the end of the bullet, so a bracketed qualifier is part of
    the rule ("Reading rule (..., see *Net tests*): **continue** ..."). A bullet ends at a blank line, a heading, or
    the next line not indented deeper than its dash (quant review M3: a spec's rule is a whole bullet, never a part)."""
    out: set[str] = set()
    lines = doc_text.splitlines()
    for i, line in enumerate(lines):
        m = _BULLET.match(line)
        if not m or not _READING.match(m.group(2)):
            continue
        indent, parts = len(m.group(1)), [_READING.sub("", m.group(2), count=1)]
        for nxt in lines[i + 1:]:
            if not nxt.strip() or len(nxt) - len(nxt.lstrip()) <= indent or nxt.lstrip().startswith("#"):
                break
            parts.append(nxt.strip())
        out.add(_norm(" ".join(parts)))
    return out


def resolve_variant(family: str, variant: str | dict[str, Any]) -> dict[str, Any]:
    """`--variants` entry -> the overrides the runner builds the specialist from: a preset name (e.g. "slow" for tsmom,
    H-01) stands for its overrides; keys must be settings of the family, so a typo is never run as the defaults; the
    specialist must accept them (e.g. its timeframe). ValueError otherwise. Shared with `research_pass.py`."""
    from goldbot.specialists import SPECIALISTS
    from goldbot.specialists.base import FEATURE_SEED_KEY, TIMEFRAME_KEY
    if family not in SPECIALISTS:
        raise ValueError(f"unknown specialist {family!r}")
    cls = SPECIALISTS[family]
    if isinstance(variant, str):
        if variant not in cls.presets:
            raise ValueError(f"unknown {family} preset {variant!r}; presets: {sorted(cls.presets)}")
        variant = dict(cls.presets[variant])
    allowed = set(cls.default_config) | set(cls.optional_config) | {TIMEFRAME_KEY, FEATURE_SEED_KEY}
    bad = sorted(set(variant) - allowed)
    if bad:
        raise ValueError(f"unknown {family} settings {bad}; allowed: {sorted(allowed)}")
    SPECIALISTS[family](**variant)
    return dict(variant)


def _feature_names(macro: bool) -> list[str]:
    import pandas as pd

    from goldbot.research.pipeline import research_feature_names
    return research_feature_names({"macro": pd.DataFrame({"present": [1]})} if macro else None)


def _discovery_config(row: SpecRow, spec: Any, research: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(the discovery trial's registry config, its pre-registration plan), as `discovery.discovery_pass` builds them."""
    from goldbot.research import discovery as disc
    from goldbot.research.registry import DISCOVERY
    over = dict(row.discovery or {})
    if isinstance(over.get("clusters"), str):
        if over["clusters"] not in NAMED_CLUSTERS:
            raise SpecError(f"{row.id}: clusters names one of {NAMED_CLUSTERS}, got {over['clusters']!r}")
        over["clusters"] = getattr(disc, over["clusters"])
    try:
        cfg = disc.DiscoveryConfig(**over)
        disc.check_group_config(cfg)
    except ValueError as exc:                          # pydantic's ValidationError included
        raise SpecError(f"{row.id}: discovery config: {exc}") from None
    if row.group_mode not in disc.GROUP_MODES:
        raise SpecError(f"{row.id}: group_mode is one of {disc.GROUP_MODES}, got {row.group_mode!r}")
    names = _feature_names(row.macro)
    config = disc.discovery_trial_config(spec, cfg, row.group_mode, names, str(row.bars_from), str(row.bars_to),
                                         research.holdout_window())
    plan = {"features": names, "group_mode": row.group_mode, "trial_status": DISCOVERY,
            "survivor_unit": cfg.survivor_unit}
    return config, plan


def _plan_row(row: SpecRow, qs: QuarterSpec, research: Any, rules: set[str]) -> PlannedRow:
    from goldbot.features.registry import feature_version
    from goldbot.research.discovery import discovery_family
    from goldbot.specialists import SPECIALISTS
    if _norm(row.reading_rule) not in rules:
        raise SpecError(f"{row.id}: the reading rule is not the whole text of one of {qs.source}'s \"Reading rule\" "
                        f"bullets word for word (everything after \"Reading rule\" to the end of the bullet)")
    want_fv = feature_version(_feature_names(row.macro))
    if row.feature_version != want_fv:
        raise SpecError(f"{row.id}: feature version {row.feature_version} is not the run's ({want_fv}, macro "
                        f"{'on' if row.macro else 'off'})")
    if row.specialist not in SPECIALISTS:
        raise SpecError(f"{row.id}: unknown specialist {row.specialist!r}")
    family = discovery_family(row.specialist) if row.kind == "discovery" else row.specialist
    commit = qs.freeze_commit or "<freeze commit>"
    plan: dict[str, Any] = {"quarter": qs.quarter, "spec_id": row.id, "source": qs.source,
                            "freeze_commit": qs.freeze_commit}
    config: dict[str, Any] | None = None
    agent_id: str | None = None
    if row.variant is not None:
        try:
            spec = SPECIALISTS[row.specialist](**resolve_variant(row.specialist, row.variant))
        except ValueError as exc:
            raise SpecError(f"{row.id}: {exc}") from None
        agent_id = spec.agent_id
        if row.kind == "discovery":
            if row.bars_from is not None and row.bars_to is not None:
                config, extra = _discovery_config(row, spec, research)
                plan.update(extra)
        else:
            config = spec.config
    h = config_hash(config) if config is not None else None
    if row.config_hash is not None and h is not None and row.config_hash != h:
        raise SpecError(f"{row.id}: config_hash {row.config_hash} differs from the config the runner builds ({h})")
    if row.agent_id is not None and agent_id is not None and row.agent_id != agent_id:
        raise SpecError(f"{row.id}: agent_id {row.agent_id} differs from the runner's ({agent_id})")
    return PlannedRow(id=row.id, family=family, agent_id=agent_id, config=config, config_hash=h,
                      feature_version=row.feature_version, rule_id=row.rule_id, reading_rule=row.reading_rule,
                      rationale=row.rationale.replace("{commit}", commit), plan=plan, pending=row.pending,
                      withdrawn=row.withdrawn)


def plan_quarter(qs: QuarterSpec, *, research: Any, doc_text: str, quarter: str) -> QuarterPlan:
    """Validate `qs` against the pre-registration (`doc_text`), the registry's rules and the quarter's budget, and
    return the rows and slots as they will be written. SpecError for anything the registry, the doc or the budget
    would not accept. `research`: the settings' research section (trial budget, reservation, holdout)."""
    if qs.quarter != quarter:
        raise SpecError(f"the spec is for quarter {qs.quarter}, not {quarter}")
    doc, rules = _norm(doc_text), reading_rules(doc_text)
    rows = [_plan_row(r, qs, research, rules) for r in qs.rows]
    by_id = {r.id: r for r in qs.rows}
    slots: list[PlannedSlot] = []
    for s in qs.slots:
        parent = by_id.get(s.parent)
        if parent is None:
            raise SpecError(f"slot parent {s.parent!r}: no row of the spec has that id")
        if parent.kind != "discovery":
            raise SpecError(f"a survivor slot's parent is a discovery row, not {s.parent} ({parent.kind})")
        if parent.withdrawn is not None:
            raise SpecError(f"slot parent {s.parent} is withdrawn: drop its slots too")
        if len(s.k) > len(s.survivor_pool):
            raise SpecError(f"{len(s.k)} survivor slots on {s.parent} but only {len(s.survivor_pool)} clusters in "
                            f"the survivor_pool")
        if _norm(s.derivation_rule) not in doc:
            raise SpecError(f"slots on {s.parent}: the derivation rule is not {qs.source}'s own text word for word")
        rule = {**registry_mod.DEFAULT_SLOT_RULE, **s.rule}
        slots += [PlannedSlot(parent=s.parent, kind=s.kind, k=k, survivor_pool=list(s.survivor_pool), rule=rule,
                              derivation_rule=s.derivation_rule) for k in s.k]
    n = sum(r.withdrawn is None for r in rows) + len(slots)
    if n != qs.reservation:
        raise SpecError(f"rows + slots = {n} (withdrawn rows left out), but the spec's reservation is "
                        f"{qs.reservation}")
    budget = int(research.trial_budget_quarter)
    if n > budget:
        raise SpecError(f"{n} queued rows and slots exceed the {quarter} trial budget of {budget} "
                        f"(research.trial_budget_quarter, an owner decision)")
    return QuarterPlan(quarter=quarter, source=qs.source, freeze_commit=qs.freeze_commit, reservation=qs.reservation,
                       setting=research.reserved_for(quarter), budget=budget, rows=rows, slots=slots)


# ---------------------------------------------------------------------------------------------- writing
def _in_quarter(r: dict[str, Any], quarter: str) -> bool:
    return r.get("target_quarter") == quarter or (r.get("plan") or {}).get("quarter") == quarter


def _existing_row(rows: list[dict[str, Any]], p: PlannedRow, quarter: str) -> dict[str, Any] | None:
    """The registry's pre-registration of this row for the quarter (queued, or ad hoc written late), withdrawn or not."""
    return next((r for r in rows if r.get("status") == PREREGISTERED and not r.get("slot")
                 and r.get("family") == p.family and r.get("config_hash") == p.config_hash
                 and _in_quarter(r, quarter)), None)


def _existing_slot(rows: list[dict[str, Any]], parent: dict[str, Any], s: PlannedSlot) -> dict[str, Any] | None:
    return next((r for r in rows if r.get("status") == PREREGISTERED and isinstance(r.get("slot"), dict)
                 and r["slot"].get("kind") == s.kind and r["slot"].get("k") == s.k
                 and refers_to(r["slot"].get("parent"), parent)), None)


def _slot_parent_withdrawn(rows: list[dict[str, Any]], slot_row: dict[str, Any]) -> bool:
    from goldbot.research.director import is_withdrawn
    parent = next((r for r in rows if not r.get("slot") and refers_to(slot_row["slot"].get("parent"), r)), None)
    return parent is not None and is_withdrawn(rows, parent)


def _live_queued(rows: list[dict[str, Any]], quarter: str) -> list[dict[str, Any]]:
    """The quarter's queued rows and slots still standing: not withdrawn, and a slot's parent not withdrawn."""
    from goldbot.research.director import is_queued, is_withdrawn, prereg_quarter
    return [r for r in rows if is_queued(r) and prereg_quarter(r) == quarter and not is_withdrawn(rows, r)
            and not (isinstance(r.get("slot"), dict) and _slot_parent_withdrawn(rows, r))]


def _strays(rows: list[dict[str, Any]], plan: QuarterPlan) -> list[dict[str, Any]]:
    """Standing queued rows of the quarter the plan does not name (another writer's, or a stale spec's). A withdrawn
    row, and a slot whose parent is withdrawn, no longer stand (quant M2: dropping a withdrawn row from the spec must
    not block the next run)."""
    known = {(r.family, r.config_hash) for r in plan.rows}
    parents = [r for r in rows if r.get("status") == PREREGISTERED and not r.get("slot")
               and any((r.get("family"), r.get("config_hash")) == (p.family, p.config_hash)
                       for p in plan.rows if any(s.parent == p.id for s in plan.slots))]
    ks = {(s.kind, s.k) for s in plan.slots}
    out = []
    for r in _live_queued(rows, plan.quarter):
        slot = r.get("slot")
        if isinstance(slot, dict):
            if (slot.get("kind"), slot.get("k")) in ks and any(refers_to(slot.get("parent"), p) for p in parents):
                continue
        elif (r.get("family"), r.get("config_hash")) in known:
            continue
        out.append(r)
    return out


def write_quarter(reg: TrialRegistry, plan: QuarterPlan, *, allow_late: str | None = None) -> list[WriteAction]:
    """Write the plan's rows, then its slots, through the registry's own write path, under its lock. Idempotent: a
    row or slot the registry already holds for the quarter (withdrawn included) is left alone ("present"). Refuses
    (SpecError), before writing anything: a plan with open items or, before the quarter, a settings reservation that
    differs from the spec's; a registry holding a standing queued row of the quarter the plan does not name; and a
    slot still to be written on a parent the registry holds as withdrawn. Refuses (LateWrite) any write on or after
    the quarter's start unless `allow_late` states why, and then only when `research.reserved_trials_quarter` equals
    the queued rows and slots already standing in the registry (normally 0; quant M1: a late write adds none, so a
    reservation left in the settings would hold trials no run can use): the rows are then ad-hoc pre-registrations
    naming their rule (`queue=False`, `rule_ref`), which hold no reservation, and the slots are skipped (rows 6-9 of
    the Q1 plan are lost; H-02 survivors come from the open budget). Writes no trial and no outcome."""
    from goldbot.research.director import is_withdrawn
    if plan.open_items:
        raise SpecError("the spec is not writable yet: " + "; ".join(plan.open_items))
    late = registry_mod._utcnow() >= quarter_start(plan.quarter)
    if late and allow_late is None:
        raise LateWrite(f"{plan.quarter} started on {quarter_start(plan.quarter).date()}: a queued row or slot must "
                        f"be written before its quarter; pass --allow-late with a reason to write ad-hoc rows")
    if late and not str(allow_late).strip():
        raise LateWrite("--allow-late states its reason")
    if not late and not plan.writable:
        raise SpecError("the spec is not writable yet: " + "; ".join(plan.blockers))
    out: list[WriteAction] = []
    with reg.locked():
        rows = reg._rows()
        strays = _strays(rows, plan)
        if strays:
            raise SpecError(f"the registry holds queued {plan.quarter} rows not in the spec: "
                            + ", ".join(f"#{r.get('trial')} {r.get('family')} {r.get('config_hash')}" for r in strays)
                            + "; withdraw them (TrialRegistry.withdraw) or add them to the spec")
        if late:
            standing = len(_live_queued(rows, plan.quarter))
            if plan.setting != standing:
                raise LateWrite(
                    f"research.reserved_trials_quarter holds {plan.setting} for {plan.quarter}, but a late write adds "
                    f"no queued row or slot and {standing} stand in the registry: set it to {standing} in the same "
                    f"change, or the reservation holds trials no run can use (the survivor slots are lost; H-02 "
                    f"survivors would come from the open budget)")
        found = {p.id: _existing_row(rows, p, plan.quarter) for p in plan.active_rows}
        if not late:
            for s in plan.slots:
                parent = found.get(s.parent)
                if parent is not None and is_withdrawn(rows, parent) and _existing_slot(rows, parent, s) is None:
                    raise SpecError(f"{s.parent} is withdrawn in the registry (#{parent.get('trial')}): no slot is "
                                    f"reserved on it; drop it and its slots from the spec")
        written: dict[str, dict[str, Any]] = {}
        for p in plan.active_rows:
            prior = found[p.id]
            if prior is not None:
                written[p.id] = prior
                state = "withdrawn" if is_withdrawn(rows, prior) else "pending"
                out.append(WriteAction(id=p.id, action="present", detail=f"#{prior.get('trial')} {state}"))
                continue
            kw: dict[str, Any] = {"queue": True, "target_quarter": plan.quarter}
            plan_extra: dict[str, Any] = {}
            if late:
                kw = {"queue": False, "rule_ref": f"{plan.source.rsplit('/', 1)[-1]}#{p.id}"}
                plan_extra = {"late_reason": str(allow_late).strip()}
            row = reg.preregister(agent_id=p.agent_id or "research-director", family=p.family, config=p.config or {},
                                  feature_version=p.feature_version, rationale=p.rationale,
                                  reading_rule=p.reading_rule, plan={**p.plan, **plan_extra}, rule_id=p.rule_id, **kw)
            written[p.id] = row
            out.append(WriteAction(id=p.id, action="written", detail=f"#{row['trial']} {row['ts']}"))
        for s in plan.slots:
            if late:
                out.append(WriteAction(id=s.id, action="skipped",
                                       detail="a slot exists only before its quarter (ADR 0007)"))
                continue
            parent = written[s.parent]
            rows = reg._rows()
            prior_slot = _existing_slot(rows, parent, s)
            if prior_slot is not None:
                out.append(WriteAction(id=s.id, action="present", detail=f"slot {s.k} {prior_slot.get('ts')}"))
                continue
            row = reg.reserve_slot(row_ref(parent), s.kind, s.k, s.derivation_rule, plan.quarter,
                                   rationale=f"contingent {s.kind} slot {s.k} of {s.parent} ({plan.source}, commit "
                                             f"{plan.freeze_commit})",
                                   survivor_pool=s.survivor_pool, rule=s.rule)
            out.append(WriteAction(id=s.id, action="written", detail=f"slot {s.k} {row['ts']}"))
    return out


def render_plan(plan: QuarterPlan, rows: list[dict[str, Any]] | None = None) -> str:
    """The rows and slots as they will be written, each marked new / present / pending, for --dry-run and review."""
    import json
    rows = rows or []
    lines = [f"# {plan.quarter} pre-registered queue from {plan.source} (freeze commit {plan.freeze_commit or '-'})",
             f"reservation {plan.reservation} = {len(plan.active_rows)} queued rows + {len(plan.slots)} contingent slots;"
             f" trial budget {plan.budget}; a pre-registration is not a trial (no budget spent, no outcome)", ""]
    parents: dict[str, dict[str, Any] | None] = {}
    for p in plan.rows:
        found = None if p.config_hash is None else _existing_row(rows, p, plan.quarter)
        parents[p.id] = found
        state = ("withdrawn: " + str(p.withdrawn)) if p.withdrawn else ("PENDING: " + str(p.pending)) if p.pending \
            else (f"present (#{found.get('trial')})" if found else "new")
        lines += [f"## {p.id}  [{state}]",
                  f"- family {p.family}, agent {p.agent_id or '-'}, config_hash {p.config_hash or '-'}, "
                  f"feature version {p.feature_version}, rule_id {p.rule_id or '-'}",
                  f"- rationale: {p.rationale}",
                  f"- config: {json.dumps(p.config, sort_keys=True) if p.config is not None else '-'}",
                  f"- reading rule: {_norm(p.reading_rule)}", ""]
    for s in plan.slots:
        parent = parents.get(s.parent)
        found = _existing_slot(rows, parent, s) if parent else None
        lines += [f"## {s.id}  survivor slot {s.k} on {s.parent}  [{'present' if found else 'new'}]",
                  f"- pool {', '.join(s.survivor_pool)}; rule {json.dumps(s.rule, sort_keys=True)}",
                  f"- derivation rule: {_norm(s.derivation_rule)}", ""]
    lines.append("writable" if plan.writable else "not writable yet: " + "; ".join(plan.blockers))
    return "\n".join(lines)
