"""Trial registry: every evaluated variant is recorded with its config hash, fold results and a global
trial counter. The counter feeds the deflated-Sharpe correction, so the more we search the higher
the bar. Stored as JSON lines so it is diffable and survives without a database server.

Research discipline (docs/proposals/2026-10-design-improvements.md, P2):
* a quarterly budget of pre-registered trials (research.trial_budget_quarter): `check_budget` refuses a run that would
  exceed it. The count is every registry row stamped in the quarter, whatever its status (each one was a look at the
  data); the research director plans from the same count (`quarter_trials`), so there is one cap and one count.
  Check, run and record happen under `locked()` so two writers cannot both pass the last free slot;
* a held-out window is scored at most once per configuration (`holdout_scored`, rows with status "holdout");
* the population's shadow -> live promotion needs a research trial of the configuration that passed the design's
  gates (`passed_gates`);
* pre-registration (deferred item L4): `preregister` writes a row with status "preregistered" BEFORE a run, holding its
  config and the rule its result will be read by. A preregistered row is a promise, not a look at the data: it is not
  a trial (`is_trial`), takes no budget slot and does not raise the deflated-Sharpe count; the result row that follows
  carries the same trial number and config hash and a `preregistration` reference;
* the pre-registered queue's reservation (research.reserved_trials_quarter, director.reserved_trials): the manual paths
  (research_pass.py, discovery) check through `check_budget_reserved`, so only a run matching a pending queued
  `preregistered` row (family and config hash) may spend the reserved trials, and it is linked to that row; any other
  run gets budget - used - reserved, except that a manual run whose family and agent have a pending queued row with
  another config hash is refused rather than falling back (`QueuedConfigMismatch`, BACKLOG 50; the runners also
  verify the data-v1 digests the row pins). A queued row counts in its `target_quarter` (default `planned_quarter`: the
  next quarter when written within PLAN_AHEAD_DAYS of its start), and a trial uses it up whichever quarter it runs in.
  A queued row must be written before its target quarter starts (`director.is_queued`);
  a pre-registration a run writes for itself just before it runs (`preregister`'s default `queue=False`, an ad-hoc
  discovery) is not part of the queue: it neither holds nor uses the reservation;
* contingent slots (ADR 0007, `reserve_slot`): a queued row with no config hash and a `slot` key (parent, kind, k,
  derivation rule) holds one reserved trial for a run that cannot be configured before the quarter (a discovery's
  k-th survivor, a follow-up, a survivor's holdout scoring). It holds only while its condition can still be met and
  is freed by its own rule otherwise (`director.reserved_trials`, no row written); an in-quarter ad-hoc
  pre-registration naming it (`preregister(slot_ref=)`) uses it once, and only for exactly the derived configuration,
  never a retired family (`check_budget_reserved`, `director.slot_refusal`);
* verdicts (`record_verdict`): the evaluator's first reading of a trial ("continue"/"stop"/"inconclusive"), not a
  trial; a rule-only parent's follow-up slots go live on "continue";
* withdrawal (`withdraw`): a "withdrawn" row naming a pre-registration that has not run. Not a trial, no budget; the
  named row leaves the queue and frees its reserved trial. reserved = max(setting - run - withdrawn - expired slots,
  pending, 0);
* a feature-discovery trial (status "discovery", goldbot/research/discovery.py) is ONE trial, but choosing survivors
  from its selection frequencies looks at many features, so `n_trials_effective` (registry trials plus every
  discovery's K_eff: features screened when survivors go forward as features, groups screened only when whole groups
  do) is the count the deflated Sharpe of any later trial uses;
* evidence (row M16): a re-evaluation of an already-registered configuration that chooses nothing, such as
  combinatorial purged CV (research/cpcv.py), is attached to that trial in a sidecar file
  (`<registry>.evidence.jsonl`, `attach_evidence` / `evidence`), never written as a trial row: it takes no budget
  slot and does not raise the deflated-Sharpe count. The sidecar is local to the host that wrote it: registry_sync
  and research.yml do not carry it (evidence is advisory and can only veto; docs/decisions/0002)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Collection, Iterator, Sequence

LOCK_STALE_S = 4 * 3600          # a lock older than this was left by a crashed writer (a trial runs well under it)
LOCK_WAIT_S = 6 * 3600


PREREGISTERED = "preregistered"
WITHDRAWN = "withdrawn"                 # a withdrawal of a pre-registration (`TrialRegistry.withdraw`): not a trial
VERDICT = "verdict"                     # an evaluator's reading of a trial (`TrialRegistry.record_verdict`): not a trial
VERDICT_DECISIONS: tuple[str, ...] = ("continue", "stop", "inconclusive")
DISCOVERY = "discovery"
FROM_DISCOVERY_KEY = "from_discovery"   # set on a config whose features came from a discovery trial: its `row_ref`
SURVIVOR_KEY = "survivor"               # ... and the discovery survivor (cluster, group or feature) the config tests
SLOT_KINDS: tuple[str, ...] = ("survivor", "followup", "holdout")
NOT_TRIALS = frozenset({PREREGISTERED, WITHDRAWN, VERDICT})
# the parent's pre-registered reading rule a survivor/holdout slot (and a discovery's follow-up) is read by: H-02's
# (preregistration-2027Q1.md: cluster stability >= 0.6 AND OOF AUC >= 0.53 with the lower 95% bound > 0.50)
# the screened-row decision rules coded in director.preregistered_decisions (preregistration-2027Q1.md): a
# pre-registration names one with `rule_id`, and only a trial linked to it is read by it
# the discovery survivors' rule (preregistration-2027Q1.md O-5/O-6, E-6): an evaluated trial linked to a pre-registration
# with one of these ids passes only with the day-summed DSR reading as well (`director.survivor_refusal`)
DAY_SUMMED_RULE_IDS: tuple[str, ...] = ("O-5", "O-6")
PREREGISTERED_RULE_IDS: tuple[str, ...] = ("H-01", "H-04", "C4H", *DAY_SUMMED_RULE_IDS)
DEFAULT_SLOT_RULE: dict[str, float] = {"min_stability": 0.6, "min_auc": 0.53, "min_auc_lower_bound": 0.5}


class TrialBudgetExceeded(ValueError):
    """The quarter's pre-registered trial budget would be exceeded."""


class PreregisteredForLaterQuarter(TrialBudgetExceeded):
    """The configuration is pre-registered (queued) for a quarter that has not started: running it now would spend this
    quarter's budget on a trial the plan put in a later one, and read it by a rule meant for later data."""


class QueuedConfigMismatch(TrialBudgetExceeded):
    """The quarter holds a pending queued row of the run's family and agent with another config hash: the run is
    refused rather than charged to the open budget (BACKLOG 50; `director.queued_mismatch`)."""


def config_hash(config: dict) -> str:
    return hashlib.sha1(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


def quarter_of(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc)
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


PLAN_AHEAD_DAYS = 14             # a queued pre-registration written this close to a quarter's start targets that quarter
_QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")


def quarter_start(quarter: str) -> datetime:
    """00:00 UTC on the first day of `quarter` ("2027Q1" -> 2027-01-01); ValueError for anything else."""
    if not _QUARTER_RE.match(quarter):
        raise ValueError(f"a quarter looks like 2027Q1, got {quarter!r}")
    return datetime(int(quarter[:4]), 3 * (int(quarter[5]) - 1) + 1, 1, tzinfo=timezone.utc)


def _utcnow() -> datetime:
    """The registry's clock for pre-registrations (a function so tests can set it; callers cannot pass a time)."""
    return datetime.now(timezone.utc)


def planned_quarter(ts: datetime | None = None) -> str:
    """The quarter a queued pre-registration written at `ts` is planned for (its default `target_quarter`): the quarter
    of `ts`, or the next one when `ts` falls within PLAN_AHEAD_DAYS of the next quarter's start (a Q1 plan written in
    the last two weeks of December counts in Q1)."""
    ts = ts or datetime.now(timezone.utc)
    ts = ts.astimezone(timezone.utc) if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    return quarter_of(ts + timedelta(days=PLAN_AHEAD_DAYS))


def is_trial(row: dict[str, Any]) -> bool:
    """Every row is a trial (a look at the data) except a pre-registration, which is written before the look, and the
    withdrawal of one (no look at all)."""
    return row.get("status") not in NOT_TRIALS


def row_ref(row: dict[str, Any]) -> dict[str, Any]:
    """How a row names another (a slot's parent, a `slot_ref`, a withdrawal): by timestamp and config hash, which
    registry_sync keeps, with the trial number for the reader only (a merge renumbers rows)."""
    return {"ts": row.get("ts"), "config_hash": row.get("config_hash"), "trial": row.get("trial")}


def refers_to(ref: Any, row: dict[str, Any]) -> bool:
    """`ref` (a `row_ref`) names `row`: same timestamp and config hash."""
    return isinstance(ref, dict) and ref.get("ts") is not None and ref.get("ts") == row.get("ts") \
        and ref.get("config_hash") == row.get("config_hash")


def discovery_k_eff(disc: dict[str, Any]) -> int:
    """K_eff of one discovery's results: the recorded `k_eff`, else by its survivor unit: `n_groups_screened` only when
    whole groups go forward, otherwise `n_features_screened` (a feature-level survivor was picked from every screened
    column), falling back to the groups when a row recorded no feature count."""
    if disc.get("k_eff") is not None:
        return int(disc["k_eff"])
    groups = int(disc.get("n_groups_screened") or 0)
    if disc.get("survivor_unit") == "group":
        return groups
    return int(disc.get("n_features_screened") or groups)


def n_trials_effective(rows: list[dict[str, Any]]) -> int:
    """The deflated-Sharpe trial count for a later trial: registry trials plus every discovery trial's K_eff (indicator
    survey 4b: N = registry count + K_eff). A survivor of a discovery was chosen by looking at K_eff units' selection
    frequencies over the whole research window (features, or groups when only whole groups go forward), so its later
    trial pays for them."""
    trials = [r for r in rows if is_trial(r)]
    k_eff = sum(discovery_k_eff((r.get("results") or {}).get("discovery") or {})
                for r in trials if r.get("status") == DISCOVERY)
    return len(trials) + k_eff


LOOP_RULE_REF = "loop"      # rule_ref of a trial the continuous research loop ran (ADR 0029): charged per run, not per quarter


def quarter_trials(rows: list[dict[str, Any]], quarter: str) -> int:
    """Trial rows (`is_trial`) stamped (`ts`, UTC) in `quarter`, whatever their status: the one count the budget uses.
    A loop trial (`rule_ref` == LOOP_RULE_REF) is not in it: it is charged against `research.loop_trials_per_run` for
    its run (ADR 0029), but it is still a row, so `n_trials_effective` (the deflated Sharpe's K) counts it."""
    n = 0
    for r in rows:
        if not is_trial(r) or r.get("rule_ref") == LOOP_RULE_REF:
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if quarter_of(ts) == quarter:
            n += 1
    return n


def costs_recorded(results: dict[str, Any]) -> bool:
    """The result records the research cost treatment: the swap it charged (`swap`, a SwapSpec dump) and a stated cost
    source (`cost_source`, e.g. "PRIORS ONLY: ..." or the cost table it used)."""
    swap = results.get("swap")
    source = results.get("cost_source")
    return isinstance(swap, dict) and "long_usd_per_lot" in swap and isinstance(source, str) and bool(source.strip())


def gate_pass_counts(row: dict[str, Any]) -> bool:
    """The row's recorded gate pass counts: `results.gates.passed` is True AND the row records the research cost
    treatment (`costs_recorded`). The one predicate every reader of results.gates.passed uses (passed_gates, CPCV, the
    director's slots; the phase gate's backtest evidence and gap watch through `promotion_refusal`, which adds the
    latest-rerun and discovery-holdout rules). Quant review, 2026-10-11: "A pass from before swap was charged is not a
    pass of the swap-charged configuration." Status is the caller's to check."""
    results = row.get("results")
    if not isinstance(results, dict):
        return False
    return (results.get("gates") or {}).get("passed") is True and costs_recorded(results)


def row_hash(row: dict[str, Any]) -> str:
    """The row's config hash (recomputed from its config when a hand-built row has none)."""
    return str(row.get("config_hash") or config_hash(row.get("config") or {}))


def _row_when(row: dict[str, Any]) -> datetime:
    """The row's `ts` (UTC); an unreadable stamp sorts first, so it never outranks a stamped rerun."""
    try:
        ts = datetime.fromisoformat(str(row.get("ts")))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def latest_evaluated(rows: list[dict[str, Any]], family: str, h: str,
                     feature_version: str | None = None) -> dict[str, Any] | None:
    """The latest walk-forward row (status "evaluated", by `ts`, then registry order) of one configuration (family and
    config hash; `feature_version` None: any version) that records the research cost treatment, or None. Quant review
    2026-10-16: reruns of one configuration are not best-of-N; "only the latest evaluated row ... decides". A row
    without the cost treatment is no rerun of the swap-charged configuration and decides nothing."""
    cands = [(i, r) for i, r in enumerate(rows)
             if r.get("status") == "evaluated" and r.get("family") == family and row_hash(r) == h
             and (feature_version is None or r.get("feature_version") == feature_version)
             and costs_recorded(r.get("results") or {})]
    return max(cands, key=lambda c: (_row_when(c[1]), c[0]))[1] if cands else None


def holdout_passed(rows: list[dict[str, Any]], family: str, h: str, feature_version: str | None = None) -> bool:
    """The configuration's holdout scoring (status "holdout", same family and config hash; `feature_version` None: any
    version) passed, with its cost treatment recorded."""
    return any(r.get("status") == "holdout" and r.get("family") == family and row_hash(r) == h
               and (feature_version is None or r.get("feature_version") == feature_version)
               and costs_recorded(r.get("results") or {})
               and ((r.get("results") or {}).get("holdout_verdict") or {}).get("passed") is True for r in rows)


def promotion_refusal(row: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    """Why the walk-forward `row` is not a research pass a promotion may rest on, or None when it is. The rule
    `TrialRegistry.passed_gates` applies, for a reader holding a row and the registry (the phase gate's backtest
    evidence, gap watch's research-ready founders):
    * its own pass counts (`gate_pass_counts`: gates passed, cost treatment recorded);
    * it is the latest evaluated run of its configuration on its feature version (`latest_evaluated`): a later
      failing rerun revokes it;
    * a configuration chosen from a feature-discovery trial (config key FROM_DISCOVERY_KEY) also passed its holdout
      scoring on that feature version (`holdout_passed`): "its features were picked on the same window the
      walk-forward scored, which the deflated Sharpe corrects only partly" (quant review 2026-10-16, HIGH)."""
    if row.get("status") != "evaluated":
        return "not a walk-forward trial"
    if not gate_pass_counts(row):
        return "did not pass the research gates with its costs recorded"
    family, h, fv = str(row.get("family")), row_hash(row), row.get("feature_version")
    latest = latest_evaluated(rows, family, h, fv)
    if latest is not None and latest != row:
        return (f"a later rerun of the same configuration (trial #{latest.get('trial')}) decides"
                + ("" if gate_pass_counts(latest) else ", and it did not pass"))
    if (row.get("config") or {}).get(FROM_DISCOVERY_KEY):
        from goldbot.research.director import survivor_refusal  # director imports this module
        why = survivor_refusal(rows, row)
        if why is not None:
            return why
        if not holdout_passed(rows, family, h, fv):
            return "discovery-derived configuration without a passed holdout scoring"
    return None


def promotion_pass_counts(row: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """`row`'s research pass counts for a promotion (`promotion_refusal` is None)."""
    return promotion_refusal(row, rows) is None


class TrialRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def _rows(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    @property
    def n_trials(self) -> int:
        return sum(1 for r in self._rows() if is_trial(r))

    @property
    def n_trials_effective(self) -> int:
        return n_trials_effective(self._rows())

    def record(self, *, agent_id: str, family: str, config: dict, feature_version: str, rationale: str,
               results: dict, status: str = "evaluated", budget_quarter: str | None = None,
               preregistration: dict[str, Any] | None = None, rule_ref: str | None = None) -> dict:
        row: dict[str, Any] = {
            "trial": self.n_trials + 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id, "family": family, "config_hash": config_hash(config), "config": config,
            "feature_version": feature_version, "rationale": rationale, "results": results, "status": status,
        }
        if budget_quarter is not None:
            row["budget_quarter"] = budget_quarter
        if rule_ref:
            row["rule_ref"] = rule_ref          # the pre-registered rule a manual run cites (director.cited_rule)
        if preregistration is not None:
            row["preregistration"] = {"ts": preregistration["ts"], "trial": preregistration["trial"],
                                      "config_hash": preregistration["config_hash"]}
            pre_fv = preregistration.get("feature_version")
            if pre_fv and pre_fv != feature_version:
                # the run's features are not the pre-registered ones (e.g. a feature-version bump in between): kept
                # linked, with the deviation on the row for the evaluator to read against the reading rule
                row["preregistration"]["deviation"] = {"feature_version": {"preregistered": pre_fv,
                                                                           "run": feature_version}}
        self._append(row)
        return row

    def _stamp(self) -> datetime:
        """The registry's clock (`_utcnow`) for a pre-registration, slot or withdrawal, moved on by a microsecond while
        it equals a stamp already in the registry: rows name each other by timestamp (`row_ref`) and registry_sync
        merges on it, so two rows written within one tick of a coarse clock (Windows) must not share one. Never
        earlier than the clock, so it cannot backdate a row."""
        ts = _utcnow()
        seen = {str(r.get("ts")) for r in self._rows()}
        while ts.isoformat() in seen:
            ts += timedelta(microseconds=1)
        return ts

    def _append(self, row: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def preregister(self, *, agent_id: str, family: str, config: dict, feature_version: str, rationale: str,
                    reading_rule: str, plan: dict[str, Any] | None = None, queue: bool = False,
                    target_quarter: str | None = None, slot_ref: dict[str, Any] | None = None,
                    rule_ref: str | None = None, rule_id: str | None = None) -> dict:
        """Write the pre-registration of the next trial BEFORE it runs: its config (hashed as the result row will be),
        the rule its result will be read by, and the plan (e.g. the number of features to be screened). Not a trial:
        it carries the number the result row will take. By default (`queue=False`) it is the pre-registration a run
        writes for itself just before it runs: not part of the pre-registered queue, it neither holds nor uses the
        quarter's reservation (director.reserved_trials). `queue=True` writes a row of the queue, stamped with the
        `target_quarter` it is planned for (default `planned_quarter(now)`), which must not have started yet (ValueError
        otherwise: a row written once its quarter is under way would only be an ad-hoc run spending the reservation);
        it holds and is counted in that quarter's reservation, and only a trial run in that quarter or later uses it
        up. The row is stamped with the registry's clock (`_utcnow`), never a time passed in: a queued row cannot be
        backdated to before its quarter. `target_quarter` without `queue=True` is a ValueError (it would be ignored).

        `slot_ref` (a slot row from `reserve_slot`, or its `row_ref`): this ad-hoc pre-registration claims that
        contingent slot (ADR 0007). It is recorded, not judged, here: `check_budget_reserved` covers the run from the
        reservation only when the slot is live and the config satisfies its derivation rule, and refuses it otherwise.
        Only for `queue=False`, and only naming a slot row of this registry (ValueError otherwise).

        `rule_ref`: the pre-registered rule this ad-hoc run follows (e.g. "preregistration-2027Q1.md#reserve"); in a
        quarter under the E-5 cap (research.open_budget_preregistered_only) the analyst spends the open budget only on
        such a row (`director.rule_preregistration`).

        `rule_id` (PREREGISTERED_RULE_IDS: "H-01", "H-04", "C4H"): the coded decision rule a screened trial linked to
        this row is read by (`director.preregistered_decisions`; the rule is keyed on the link, not on the config's
        shape); "O-5", "O-6" (DAY_SUMMED_RULE_IDS): the discovery survivors' rule an evaluated trial linked to this row
        is read by (`director.survivor_decisions`: gates AND the day-summed DSR). ValueError for an unknown id."""
        if rule_id is not None and rule_id not in PREREGISTERED_RULE_IDS:
            raise ValueError(f"rule_id must be one of {PREREGISTERED_RULE_IDS}, got {rule_id!r}")
        if target_quarter is not None and not queue:
            raise ValueError("target_quarter is only for a queued pre-registration: pass queue=True as well")
        slot_row = None
        if slot_ref is not None:
            if queue:
                raise ValueError("slot_ref is for an in-quarter ad-hoc pre-registration (queue=False) using a slot")
            slot_row = next((r for r in self._rows() if refers_to(row_ref(slot_ref), r) and r.get("slot")), None)
            if slot_row is None:
                raise ValueError(f"slot_ref {row_ref(slot_ref)} names no slot row (TrialRegistry.reserve_slot)")
        ts = self._stamp()
        row: dict[str, Any] = {
            "trial": self.n_trials + 1,
            "ts": ts.isoformat(),
            "agent_id": agent_id, "family": family, "config_hash": config_hash(config), "config": config,
            "feature_version": feature_version, "rationale": rationale, "results": {}, "status": PREREGISTERED,
            "reading_rule": reading_rule, "plan": plan or {},
        }
        if rule_id is not None:
            row["rule_id"] = rule_id
        if not queue:
            row["queue"] = False
            if slot_row is not None:
                row["slot_ref"] = row_ref(slot_row)
            if rule_ref:
                row["rule_ref"] = rule_ref
        else:
            target = target_quarter or planned_quarter(ts)
            if ts >= quarter_start(target):
                raise ValueError(
                    f"a queued pre-registration must be written before its target quarter starts ({target} is under "
                    f"way): pass target_quarter= a later quarter, or queue=False for an ad-hoc pre-registration")
            row["target_quarter"] = target
        self._append(row)
        return row

    def reserve_slot(self, parent_ref: dict[str, Any], kind: str, k: int, derivation_rule: str, target_quarter: str,
                     *, agent_id: str = "research-director", rationale: str = "",
                     survivor_pool: Sequence[str] | None = None, rule: dict[str, float] | None = None,
                     family: str | None = None, config_hashes: Sequence[str] | None = None,
                     overrides: dict[str, Any] | None = None) -> dict:
        """Pre-register a contingent slot of `target_quarter` (ADR 0007): a queued `preregistered` row with no config
        hash, holding one reserved trial for a run whose configuration cannot exist before the quarter (e.g. the k-th
        survivor of a discovery that runs in the quarter), chosen later by `derivation_rule`, written now. Kinds
        (SLOT_KINDS): "survivor" (the parent discovery's k-th survivor by recorded stability), "followup" (the k-th
        follow-up of a parent read "continue"), "holdout" (the holdout scoring of survivor trial k once it passed its
        gates). The slot holds the reservation only while its condition can still be met and is freed by its own rule
        otherwise (`director.reserved_trials`); it is used by exactly one in-quarter ad-hoc pre-registration naming it
        (`preregister(slot_ref=)`) that satisfies the rule (`check_budget_reserved`). `parent_ref`: the parent's
        pre-registration or trial row (or its `row_ref`); a survivor or holdout slot's parent is a discovery. Written
        with the registry's clock, before `target_quarter` starts (ValueError otherwise, as for any queued row).

        Fixed in the row before the quarter (quant re-review): `rule`, the parent's reading rule the slot is read by
        (DEFAULT_SLOT_RULE: H-02's); `survivor_pool`, the pre-registered survivors in priority order (H-02: its
        hypothesis clusters H-03/05/07/08/10; None: the discovery's own survivor unit); and for a follow-up the
        `family` and the allowed configurations (`config_hashes`, or `overrides` every allowed config contains),
        without which a follow-up slot is refused."""
        if kind not in SLOT_KINDS:
            raise ValueError(f"slot kind must be one of {SLOT_KINDS}, got {kind!r}")
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError(f"slot index k counts from 1, got {k!r}")
        if not derivation_rule.strip():
            raise ValueError("a slot needs its derivation rule, written before the quarter")
        ts = self._stamp()
        if ts >= quarter_start(target_quarter):
            raise ValueError(f"a slot must be written before its target quarter starts ({target_quarter} is under way)")
        rows = self._rows()
        parent = next((r for r in rows if r.get("status") != WITHDRAWN and not r.get("slot")
                       and refers_to(row_ref(parent_ref), r)), None)
        if parent is None:
            raise ValueError(f"no parent row {row_ref(parent_ref)} (a pre-registration or trial) in {self.path}")
        if kind != "followup" and not str(parent.get("family", "")).startswith("discovery_"):
            raise ValueError(f"a {kind} slot's parent is a discovery (family discovery_*), not {parent.get('family')}")
        followup: dict[str, Any] = {}
        if kind == "followup":
            if not family:
                raise ValueError("a follow-up slot fixes its family before the quarter (family=)")
            if not config_hashes and not overrides:
                raise ValueError("a follow-up slot fixes its allowed configurations before the quarter: pass "
                                 "config_hashes or overrides")
            followup = {"family": family, "config_hashes": list(config_hashes or []), "overrides": dict(overrides or {})}
        from goldbot.research.director import is_withdrawn  # director imports this module
        if any(r.get("slot", {}).get("kind") == kind and r["slot"].get("k") == k
               and refers_to(r["slot"].get("parent"), parent) and not is_withdrawn(rows, r)
               for r in rows if r.get("status") == PREREGISTERED and isinstance(r.get("slot"), dict)):
            raise ValueError(f"{kind} slot {k} of #{parent.get('trial')} is already reserved")
        row: dict[str, Any] = {
            "trial": self.n_trials + 1, "ts": ts.isoformat(), "agent_id": agent_id, "family": parent.get("family"),
            "config_hash": None, "config": None, "feature_version": None,
            "rationale": rationale or f"contingent {kind} slot {k} of #{parent.get('trial')}", "results": {},
            "status": PREREGISTERED, "reading_rule": derivation_rule, "plan": {}, "queue": True,
            "target_quarter": target_quarter,
            "slot": {"parent": row_ref(parent), "kind": kind, "k": k, "derivation_rule": derivation_rule,
                     "rule": {**DEFAULT_SLOT_RULE, **(rule or {})},
                     "survivor_pool": list(survivor_pool) if survivor_pool is not None else None,
                     **({"followup": followup} if followup else {})},
        }
        self._append(row)
        return row

    def withdraw(self, ref: dict[str, Any], reason: str, *, agent_id: str = "research-director") -> dict:
        """Withdraw a pre-registration (queued, slot or ad hoc) that has not run: writes a "withdrawn" row naming it,
        with the reason. Not a trial (`is_trial`): no budget, no deflated-Sharpe count. The withdrawn row is no longer
        pending: it leaves the queue (`director._prereg_matches`, `later_preregistration`), frees its reserved trial
        (`director.reserved_trials` counts it as withdrawn) and, for a slot's parent, frees the slot. ValueError for an
        unknown pre-registration, one already withdrawn or already run, or an empty reason."""
        from goldbot.research.director import is_withdrawn, preregistration_run
        if not reason.strip():
            raise ValueError("a withdrawal states its reason")
        rows = self._rows()
        target = next((r for r in rows if r.get("status") == PREREGISTERED and refers_to(row_ref(ref), r)), None)
        if target is None:
            raise ValueError(f"no pre-registration {row_ref(ref)} in {self.path}")
        if is_withdrawn(rows, target):
            raise ValueError(f"pre-registration #{target.get('trial')} is already withdrawn")
        if preregistration_run(rows, target) is not None:
            raise ValueError(f"pre-registration #{target.get('trial')} has already run: its trial stands")
        row: dict[str, Any] = {
            "trial": self.n_trials + 1, "ts": self._stamp().isoformat(), "agent_id": agent_id,
            "family": target.get("family"), "config_hash": target.get("config_hash"), "status": WITHDRAWN,
            "withdraws": row_ref(target), "reason": reason, "results": {},
        }
        self._append(row)
        return row

    def record_verdict(self, ref: dict[str, Any], decision: str, reason: str, *, rule: str = "",
                       agent_id: str = "evaluator") -> dict:
        """Record the evaluator's reading of a trial by its pre-registered rule (decision in VERDICT_DECISIONS): a
        "verdict" row naming the trial. Not a trial: no budget, no deflated-Sharpe count. A rule-only parent (H-01,
        H-04, the tsmom 4h continuity trial) writes no reading verdict itself, so its follow-up slots go live only on
        this row's "continue" (`director._Ledger.slot_state`); a discovery's own recorded reading verdict is never
        replaced by one: a discovery row is refused. For a `screened` row the decision must be the one its
        pre-registered rule gives on the recorded results (`director.preregistered_decisions`: H-01, H-04, tsmom 4h,
        else the screen itself; "stop" is also allowed below the event floor when net mean R <= 0, and on a
        below-floor row when the verdict cites the director's ruling, and on a 0-event row; both record
        `family_evidence: False`), and for an evaluated trial linked to an O-5/O-6 pre-registration the one the survivors'
        rule gives (`director.survivor_decisions`: "continue" only with the gates AND the day-summed DSR reading
        passed); a mismatch is refused and the row records `rule_checked`. The first verdict of a
        trial is final (ValueError for a second, an unknown trial or decision, or an empty reason)."""
        from goldbot.research.director import (
            is_below_floor,
            is_degenerate,
            preregistered_decisions,
            survivor_decisions,
            verdict_of,
        )
        if decision not in VERDICT_DECISIONS:
            raise ValueError(f"a verdict decision is one of {VERDICT_DECISIONS}, got {decision!r}")
        if not reason.strip():
            raise ValueError("a verdict states its reason")
        rows = self._rows()
        target = next((r for r in rows if is_trial(r) and refers_to(row_ref(ref), r)), None)
        if target is None:
            raise ValueError(f"no trial row {row_ref(ref)} in {self.path}")
        if target.get("status") == DISCOVERY:
            raise ValueError(f"trial #{target.get('trial')} is a discovery: it is read by its own recorded reading "
                             f"verdict, never by a verdict row")
        if verdict_of(rows, target) is not None:
            raise ValueError(f"trial #{target.get('trial')} already has its verdict: it is final")
        checked = target.get("status") == "screened"
        if checked:
            allowed, which = preregistered_decisions(target, rows, cited_rule=rule)
            if decision not in allowed:
                raise ValueError(f"trial #{target.get('trial')}: {decision!r} is not the pre-registered decision; "
                                 f"the {which} rule gives {sorted(allowed)} on the recorded results")
            rule = rule or which
        degenerate = checked and is_degenerate(target)
        # a degenerate or below-floor stop is not evidence about the family (retirement logic never counts it)
        not_evidence = degenerate or (checked and decision == "stop" and is_below_floor(target))
        if degenerate and decision == "stop":
            reason = f"degenerate config (0 events): {reason}"
        survivor = survivor_decisions(target, rows)
        if survivor is not None:
            allowed, which, why = survivor
            if decision not in allowed:
                raise ValueError(f"trial #{target.get('trial')}: {decision!r} is not the pre-registered decision; "
                                 f"the {which} rule gives {sorted(allowed)}: {why}")
            checked, rule = True, rule or which
        row: dict[str, Any] = {
            "trial": self.n_trials + 1, "ts": self._stamp().isoformat(), "agent_id": agent_id,
            "family": target.get("family"), "config_hash": target.get("config_hash"), "status": VERDICT,
            "verdict_of": row_ref(target), "decision": decision, "reason": reason, "rule": rule,
            "rule_checked": checked, "results": {},
        }
        if not_evidence:
            row["family_evidence"] = False  # a 0-event config says nothing about its family (no retirement count)
        self._append(row)
        return row

    def preregistration(self, family: str, config: dict) -> dict | None:
        """The latest pre-registration of exactly this configuration, or None."""
        h = config_hash(config)
        rows = [r for r in self._rows() if r.get("status") == PREREGISTERED and r.get("family") == family
                and r.get("config_hash") == h]
        return rows[-1] if rows else None

    def row(self, trial: int) -> dict | None:
        """The trial row (not a pre-registration) numbered `trial`, or None."""
        return next((r for r in self._rows() if is_trial(r) and int(r.get("trial", -1)) == int(trial)), None)

    # ------------------------------------------------------------------ evidence on existing trials (not trials)
    @property
    def evidence_path(self) -> Path:
        return self.path.with_name(self.path.stem + ".evidence.jsonl")

    def attach_evidence(self, trial: int, kind: str, payload: dict[str, Any],
                        now: datetime | None = None) -> dict[str, Any]:
        """Attach a re-evaluation (e.g. kind "cpcv") to an existing trial row; raises KeyError for an unknown trial.
        Written to the evidence sidecar, so the trial count, the budget and the deflated Sharpe are unchanged.
        `now`: the time it is stamped with (the scheduler's slot), default the wall clock."""
        row = self.row(trial)
        if row is None:
            raise KeyError(f"no trial #{trial} in {self.path}")
        ts = now or datetime.now(timezone.utc)
        ev = {"trial": int(trial), "kind": kind, "ts": ts.isoformat(), "family": row.get("family"),
              "config_hash": row.get("config_hash"), "quarter": quarter_of(ts), "payload": payload}
        with open(self.evidence_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, default=str) + "\n")
        return ev

    def evidence(self, trial: int | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        """Evidence rows, oldest first, optionally for one trial and/or kind."""
        if not self.evidence_path.exists():
            return []
        rows = [json.loads(line) for line in self.evidence_path.read_text().splitlines() if line.strip()]
        return [e for e in rows
                if (trial is None or e.get("trial") == int(trial)) and (kind is None or e.get("kind") == kind)]

    def by_family(self, family: str) -> list[dict]:
        return [r for r in self._rows() if r["family"] == family]

    def best(self, family: str, key: str = "sharpe_ann") -> dict | None:
        rows = [r for r in self.by_family(family) if key in r.get("results", {})]
        return max(rows, key=lambda r: r["results"][key]) if rows else None

    # ------------------------------------------------------------------ research discipline
    def budget_used(self, quarter: str) -> int:
        return quarter_trials(self._rows(), quarter)

    def check_budget(self, n_new: int, cap: int, now: datetime | None = None) -> str:
        """Raise TrialBudgetExceeded if `n_new` more trials would exceed the cap of the quarter they will be recorded in
        (the wall clock's: rows are stamped when written); returns the quarter."""
        q = quarter_of(now)
        used = self.budget_used(q)
        if used + n_new > cap:
            raise TrialBudgetExceeded(
                f"trial budget exceeded: {q} allows {cap} pre-registered trials, {used} already run, {n_new} requested. "
                f"Every trial raises the deflated-Sharpe bar for all later ones; pre-register fewer variants, wait for "
                f"next quarter, or raise research.trial_budget_quarter / trial_budget_overrides in config/settings.yaml (an owner decision).")
        return q

    def check_budget_reserved(self, jobs: Sequence[tuple[str, dict[str, Any]]], cap: int, reserved_setting: int,
                              now: datetime | None = None, *, retired: Collection[str] | None = None,
                              agents: Sequence[str | None] | None = None
                              ) -> tuple[str, list[dict[str, Any] | None]]:
        """`check_budget` for `jobs` ((family, config) each) that honours the pre-registered queue's reservation, as
        the analyst, the director and the label grid do (a job whose config is queued for a later quarter is refused,
        PreregisteredForLaterQuarter): a job matching a pending queued `preregistered` row of the
        quarter (`director.pending_preregistration`: same family and config hash) may use the full `cap` and must be
        recorded linked to that row (so the reservation goes down by one); every other job must fit in
        cap - used - reserved (`director.reserved_trials`, `reserved_setting` = research.reserved_trials_quarter).
        A job with no pending queued row but an unused ad-hoc pre-registration claiming a contingent slot
        (`preregister(slot_ref=)`, `director.slot_preregistration`) is covered by that slot only when
        `director.slot_refusal` finds nothing wrong (slot of this quarter and live; a survivor or holdout slot: the
        parent's family and spec, `from_discovery` = the parent's `row_ref`, the claimed survivor the k-th of the
        parent's pre-registered ranking and its recorded members as the features; a follow-up: the slot's family and
        allowed configs); anything else raises TrialBudgetExceeded rather than falling back to the open budget
        (withdraw the pre-registration to run it from there). One row covers one run. A slot never covers a retired
        family (`retired`, default the settings' research.retired_families: fail closed when a caller forgets).
        `agents` (one per job; the manual runners research_pass.py and `--discover` pass them): a job with no pending
        queued row, no slot and no later row, whose family and agent (None: the family alone) have a pending queued row
        of the quarter with another config hash, raises QueuedConfigMismatch instead of falling back to the open budget
        (BACKLOG 50, `director.queued_mismatch`).
        Raises TrialBudgetExceeded; returns (the quarter, the matching pre-registration or None, per job)."""
        # director imports this module, so the import is local
        from goldbot.research.director import (
            later_preregistration,
            later_refusal,
            pending_preregistration,
            queued_mismatch,
            reserved_trials,
            slot_preregistration,
            slot_refusal,
        )
        if agents is not None and len(agents) != len(jobs):
            raise ValueError(f"agents names one agent per job: {len(agents)} for {len(jobs)} jobs")
        q = quarter_of(now)
        rows = self._rows()
        preregs: list[dict[str, Any] | None] = []
        for i, (family, config) in enumerate(jobs):
            h = config_hash(config)
            p = pending_preregistration(rows, q, family, h)
            if p is None and (s := slot_preregistration(rows, family, h)) is not None:
                if retired is None:
                    from goldbot.config import load_settings
                    retired = {e.family for e in load_settings().research.retired_families}
                why = slot_refusal(rows, q, s, family, config, h, retired=retired)
                if why is not None:
                    raise TrialBudgetExceeded(
                        f"{family} config {h}: pre-registration #{s.get('trial')} claims a contingent slot, but {why}. "
                        f"A slot covers a run only by its pre-registered derivation rule (ADR 0007); withdraw the "
                        f"pre-registration (TrialRegistry.withdraw) to run it from the open budget instead")
                p = s
            later = None if p is not None else later_preregistration(rows, q, family, config_hash(config))
            if later is not None:
                raise PreregisteredForLaterQuarter(later_refusal(family, later))
            if p is None and agents is not None and (why := queued_mismatch(rows, q, family, agents[i], config, h)):
                raise QueuedConfigMismatch(why)
            preregs.append(None if p is not None and any(p is c for c in preregs) else p)   # one row covers one run
        n_open = sum(p is None for p in preregs)
        reserved = reserved_trials(rows, q, reserved_setting).reserved
        if n_open:
            try:
                self.check_budget(n_open, max(cap - reserved, 0), now)
            except TrialBudgetExceeded as exc:
                raise TrialBudgetExceeded(
                    f"{exc} ({reserved} of the quarter's {cap} are held for the pre-registered queue, "
                    f"research.reserved_trials_quarter; a run may use them only when it matches a pending "
                    f"preregistered row, same family and config hash)") from None
        self.check_budget(len(preregs), cap, now)
        return q, preregs

    def holdout_scored(self, family: str, config: dict) -> bool:
        """The configuration's one holdout scoring has been spent, on ANY feature version: a re-scoring after a
        feature-version bump would be a second look at the same held-out window (deliberately version-agnostic)."""
        h = config_hash(config)
        return any(r.get("status") == "holdout" and r.get("family") == family and r.get("config_hash") == h
                   for r in self._rows())

    def passed_gates(self, family: str, config: dict, feature_version: str | None = None) -> bool:
        """A walk-forward trial (not a holdout scoring) of exactly this configuration passed the design's gates. A
        configuration chosen from a feature-discovery trial (config key FROM_DISCOVERY_KEY) must ALSO have passed its
        holdout scoring: its features were picked on the same window the walk-forward scored, which the deflated
        Sharpe corrects only partly (quant review; preregistration-2027Q1.md). `feature_version`: only rows run on
        that feature version count, for the gates and the holdout pass alike (None: any version).

        Cost treatment (quant review, 2026-10-11): every research run charges swap and states where its costs came
        from, so a row counts only when its results record the swap it charged (`results.swap`) and its cost source
        (`results.cost_source`). A pass from before swap was charged is not a pass of the swap-charged configuration.
        Swap is deliberately not part of `config_hash` (folding it in would move every pinned hash); the swap rates
        themselves may differ between rows (a measured cost table refines the prior), the treatment may not.

        Reruns (quant review 2026-10-16): only the latest evaluated row of the configuration decides
        (`latest_evaluated`), so a later failing rerun revokes an earlier pass; best-of-N over reruns is not a pass.
        `promotion_refusal` is the same rule for a reader that holds one row."""
        h = config_hash(config)
        rows = self._rows()
        latest = latest_evaluated(rows, family, h, feature_version)
        if latest is None or not gate_pass_counts(latest):
            return False
        if not config.get(FROM_DISCOVERY_KEY):
            return True
        from goldbot.research.director import survivor_refusal  # director imports this module
        return survivor_refusal(rows, latest) is None and holdout_passed(rows, family, h, feature_version)

    @contextmanager
    def locked(self, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S) -> Iterator[None]:
        """Exclusive lock beside the registry file, portable (Windows VPS and Linux runners): created with
        O_CREAT | O_EXCL, removed on exit; a lock older than `stale_s` is taken over (its writer crashed)."""
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > stale_s:
                        lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"trial registry lock {lock} held for more than {wait_s:.0f}s") from None
                time.sleep(0.5)
        try:
            os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}".encode())
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)
