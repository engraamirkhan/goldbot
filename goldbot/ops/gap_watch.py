"""Bounded spawning: the daily gap_watch job (docs/TRADER_LIFECYCLE.md section 3, "Spawn agents as required").

Nothing spawns without a gap record, and every spawn goes through an existing code path. The detectors are
deterministic functions of recorded state:

* uncovered_timeframe  a decision timeframe a registered family allows with no live or shadow agent on it;
* family_dead          every agent of a family is retired;
* drift_halt           an agent halted by drift_watch (state/drift.json `halted`);
* system_halt          drift_watch's system halt;
* regime               recent realised volatility (mean of the last `regime_window_days` daily values) sits in a
                       tercile of the history that no champion's training window contains enough days of;
* dq_errors            error-severity data-quality events on at least `dq_min_error_days` distinct UTC days within
                       `dq_window_days`;
* no_founder           a family in the research plan with no agent at all;
* research_ready       a walk-forward trial that passed the research gates whose configuration has no agent yet.

What may answer a gap (and nothing else):

* trading agents: `Population.spawn_founder` only. Zero-capital SHADOW founders of registered families whose
  configuration is the family's default on another of its timeframes or a passed trial's; at most
  `gaps.founders_per_month` a month, only into the population's reserved shadow slots, never while a system halt is
  set or an account is at a drawdown stage (8% or worse), never in a family the research plan blocks (lookahead). They
  are promoted only by the tournament's gates (DSR with n_pop trials and a passed trial of their exact config);
* staff agents: an on-demand run of an EXISTING read-only role (ON_DEMAND_ROLES) with the gaps as context, at most
  `gaps.staff_runs_per_week` in any rolling 7 days, each gap once, and only when the month's agent budget still
  covers the role's per-run cap (agents.monthly_cap_usd). A role holding any write tool is refused;
* research: a hypothesis for the regime and dead-family gaps (at most `gaps.hypotheses_per_run`, each gap once),
  which still has to pass the research gates;
* anything bigger (a new family, a new role, a timeframe the retrain cannot train) is a BACKLOG suggestion for a
  human or a PR, never created here.

Output: state/gaps.json (the gaps, the actions taken, the actions refused with the reason, gaps already handled) and an
append-only audit trail state/gap_ledger.jsonl that the weekly staff cap and the once-per-gap rules read.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.agents.roles import ROLES
from goldbot.agents.runner import AgentRunner
from goldbot.agents.tools import hypothesis_id
from goldbot.base import FrozenRecord, UtcTimestamp, write_atomic
from goldbot.config import GapSettings
from goldbot.research import population as pop_mod
from goldbot.research.population import Population, SpawnRefused, founder_base
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import TIMEFRAME_KEY, AgentIdentity

GAPS_FILE = "gaps.json"
LEDGER_FILE = "gap_ledger.jsonl"
HYPOTHESES_FILE = "hypotheses.jsonl"
# gap kind -> the existing read-only role run on demand for it
ON_DEMAND_ROLES = {"dq_errors": "data_steward", "drift_halt": "risk_officer", "system_halt": "risk_officer"}
WRITE_TOOLS = frozenset({"file_hypothesis", "update_hypothesis", "run_trial"})
HYPOTHESIS_KINDS = ("regime", "family_dead")
NO_SPAWN_STAGES = frozenset({"size_down", "halted"})   # risk gate drawdown stages: 8% size-down, 12% halt
TERCILES = ("low", "mid", "high")

GapKind = Literal["uncovered_timeframe", "family_dead", "drift_halt", "system_halt", "regime", "dq_errors",
                  "no_founder", "research_ready"]
ActionKind = Literal["spawn_founder", "seed_default_founder", "staff_run", "file_hypothesis", "suggest"]


class Gap(FrozenRecord):
    gap_id: str                       # deterministic: the same condition gives the same id on the next run
    kind: GapKind
    detail: str
    family: str | None = None
    timeframe: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class GapAction(FrozenRecord):
    gap_ids: list[str]
    action: ActionKind
    target: str                       # agent id, role, hypothesis id or "BACKLOG"
    detail: str


class GapRefusal(FrozenRecord):
    gap_ids: list[str]
    action: ActionKind
    target: str
    reason: str


class GapReport(FrozenRecord):
    ts: UtcTimestamp
    gaps: list[Gap]
    actions: list[GapAction]
    refused: list[GapRefusal]
    already_handled: list[str]        # gap ids answered on an earlier run (each gap is answered once)
    notes: list[str]
    limits: dict[str, Any]

    @property
    def spawned(self) -> list[str]:
        return [a.target for a in self.actions if a.action in ("spawn_founder", "seed_default_founder")]


# ---------------------------------------------------------------------------------------------- detectors
def member_timeframe(family: str, config: dict[str, Any]) -> str:
    return str(config.get(TIMEFRAME_KEY, SPECIALISTS[family].timeframe)) if family in SPECIALISTS else "?"


def uncovered_timeframes(pop: Population) -> list[Gap]:
    """A timeframe a registered family allows with no live or shadow agent, for families that have agents (a family
    with none is a no_founder gap)."""
    out = []
    for fam, cls in sorted(SPECIALISTS.items()):
        members = [m for m in pop.members.values() if m.family == fam]
        if not members:
            continue
        covered = {member_timeframe(fam, m.config) for m in members if m.status in ("live", "shadow")}
        for tf in (cls.timeframe, *cls.timeframes):
            if tf not in covered:
                out.append(Gap(gap_id=f"uncovered_timeframe:{fam}:{tf}", kind="uncovered_timeframe", family=fam,
                               timeframe=tf, detail=f"{fam} allows {tf} but no live or shadow agent runs on it"))
    return out


def dead_families(pop: Population) -> list[Gap]:
    out = []
    for fam in sorted({m.family for m in pop.members.values()}):
        members = [m for m in pop.members.values() if m.family == fam]
        if members and all(m.status == "retired" for m in members):
            out.append(Gap(gap_id=f"family_dead:{fam}", kind="family_dead", family=fam,
                           detail=f"all {len(members)} agents of {fam} are retired"))
    return out


def drift_halts(drift: dict[str, Any]) -> list[Gap]:
    out = []
    for aid, h in sorted((drift.get("halted") or {}).items()):
        since = str((h or {}).get("since", ""))
        out.append(Gap(gap_id=f"drift_halt:{aid}:{since}", kind="drift_halt",
                       detail=f"{aid} halted by drift_watch since {since}: {'; '.join((h or {}).get('reasons') or [])}",
                       data={"agent_id": aid, "version": (h or {}).get("version")}))
    sysh = drift.get("system_halt")
    if sysh:
        since = str(sysh.get("since", ""))
        out.append(Gap(gap_id=f"system_halt:{since}", kind="system_halt",
                       detail=f"system halt since {since}: {'; '.join(sysh.get('reasons') or [])}"))
    return out


def daily_realised_vol(bars: pd.DataFrame) -> pd.Series:
    """Daily realised volatility from bars: sqrt of the sum of squared log returns of the mid close per UTC day."""
    if bars.empty:
        return pd.Series(dtype=float)
    close = (bars["bid_close"] + bars["ask_close"]) / 2.0
    ts = pd.DatetimeIndex(pd.to_datetime(bars["ts_utc"], utc=True))
    r = pd.Series(np.log(close.to_numpy(float)), index=ts).sort_index().diff().dropna()
    if r.empty:
        return pd.Series(dtype=float)
    day = pd.DatetimeIndex(r.index).normalize()
    rv = ((r ** 2).groupby(day).sum()) ** 0.5
    return rv[rv > 0]


def regime_gap(daily_vol: pd.Series, champion_windows: list[tuple[pd.Timestamp, pd.Timestamp]], now: pd.Timestamp,
               window_days: int) -> tuple[list[Gap], str]:
    """The current volatility tercile (mean of the last `window_days` daily values against the terciles of the whole
    history) is a gap when no champion's training window holds at least `window_days` days in that tercile.
    Returns (gaps, note)."""
    if not champion_windows:
        return [], "regime: no champion, nothing to compare"
    vol = daily_vol.dropna().sort_index()
    if len(vol) < 3 * window_days:
        return [], f"regime: {len(vol)} days of volatility history, need {3 * window_days}"
    q1, q2 = float(vol.quantile(1 / 3)), float(vol.quantile(2 / 3))

    def tercile(x: float) -> int:
        return 0 if x <= q1 else (1 if x <= q2 else 2)

    current = float(vol.iloc[-window_days:].mean())
    now_t = tercile(current)
    seen = [0, 0, 0]
    for start, end in champion_windows:
        for v in vol[(vol.index >= start) & (vol.index < end)]:
            seen[tercile(float(v))] += 1
    note = (f"regime: recent vol {current:.5f} is {TERCILES[now_t]} (terciles {q1:.5f} / {q2:.5f}); champion training "
            f"days per tercile {dict(zip(TERCILES, seen))}")
    if seen[now_t] >= window_days:
        return [], note
    return [Gap(gap_id=f"regime:vol_{TERCILES[now_t]}:{now:%Y-%m}", kind="regime",
                detail=f"recent realised volatility is in the {TERCILES[now_t]} tercile; champions trained on "
                       f"{seen[now_t]} such days (< {window_days})",
                data={"current": current, "terciles": [q1, q2], "seen": seen})], note


def dq_repeats(dq: pd.DataFrame, now: pd.Timestamp, window_days: int, min_days: int) -> list[Gap]:
    if dq.empty or "severity" not in dq.columns:
        return []
    ts = pd.to_datetime(dq["ts_utc"], utc=True)
    errs = dq[(dq["severity"] == "error") & (ts >= now - pd.Timedelta(days=window_days)) & (ts <= now)]
    days = sorted({str(d.date()) for d in pd.to_datetime(errs["ts_utc"], utc=True)})
    if len(days) < min_days:
        return []
    kinds = ", ".join(f"{k} x{n}" for k, n in errs["check"].value_counts().items()) if "check" in errs else ""
    iso = now.isocalendar()
    return [Gap(gap_id=f"dq_errors:{iso.year}-W{iso.week:02d}", kind="dq_errors",
                detail=f"{len(errs)} error events on {len(days)} days in the last {window_days} ({kinds})",
                data={"days": days})]


def plan_families(plan: dict[str, Any] | None) -> set[str]:
    if not plan:
        return set()
    return set(plan.get("budget") or {}) | {str(e.get("family")) for e in plan.get("evidence") or [] if e.get("family")}


def unfounded_plan_families(plan: dict[str, Any] | None, pop: Population) -> list[Gap]:
    have = {m.family for m in pop.members.values()}
    return [Gap(gap_id=f"no_founder:{fam}", kind="no_founder", family=fam,
                detail=f"{fam} is in the research plan but has no agent"
                       + ("" if fam in SPECIALISTS else " and is not a registered family"))
            for fam in sorted(plan_families(plan) - have)]


def research_ready(trials: list[dict[str, Any]], pop: Population) -> list[Gap]:
    """Walk-forward trials (not holdout scorings) that passed the research gates and whose configuration has no agent:
    one founder per trial id at most (identical configurations share one agent id)."""
    out, seen = [], set()
    for r in trials:
        fam = r.get("family")
        if r.get("status") != "evaluated" or fam not in SPECIALISTS:
            continue
        if ((r.get("results") or {}).get("gates") or {}).get("passed") is not True:
            continue
        cfg = dict(r.get("config") or {})
        aid = AgentIdentity(family=fam, config=cfg).agent_id
        if aid in pop.members or aid in seen:
            continue
        seen.add(aid)
        out.append(Gap(gap_id=f"research_ready:trial{r.get('trial')}", kind="research_ready", family=fam,
                       timeframe=member_timeframe(fam, cfg),
                       detail=f"trial {r.get('trial')} of {fam} passed the research gates; no agent runs its config",
                       data={"trial": r.get("trial"), "config": cfg}))
    return out


# ---------------------------------------------------------------------------------------------- ledger
def read_ledger(state_dir: Path) -> list[dict[str, Any]]:
    f = state_dir / LEDGER_FILE
    if not f.exists():
        return []
    rows = []
    for line in f.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _append_ledger(state_dir: Path, row: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / LEDGER_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def _read_hypotheses(state_dir: Path) -> list[dict[str, Any]]:
    f = state_dir / HYPOTHESES_FILE
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------------------------- the run
def run_gap_watch(*, now: pd.Timestamp, settings: GapSettings, population: Population, state_dir: Path,
                  walkforward_tfs: set[str], drift: dict[str, Any], dq: pd.DataFrame, daily_vol: pd.Series,
                  champion_windows: list[tuple[pd.Timestamp, pd.Timestamp]], plan: dict[str, Any] | None,
                  trials: list[dict[str, Any]], engine_stages: dict[str, str],
                  runner: AgentRunner | None) -> GapReport:
    """Detect the gaps, answer each within the caps, write state/gaps.json. The caller saves the population."""
    gaps = (research_ready(trials, population) + unfounded_plan_families(plan, population)
            + uncovered_timeframes(population) + dead_families(population) + drift_halts(drift)
            + dq_repeats(dq, now, settings.dq_window_days, settings.dq_min_error_days))
    regime, regime_note = regime_gap(daily_vol, champion_windows, now, settings.regime_window_days)
    gaps += regime
    notes = [regime_note]
    actions: list[GapAction] = []
    refused: list[GapRefusal] = []
    handled: list[str] = []
    ledger = read_ledger(state_dir)

    # ---- trading agents: zero-capital shadow founders only
    block = None
    if drift.get("system_halt"):
        block = "system halt pending the owner's review: no spawning"
    elif stages := sorted(a for a, s in engine_stages.items() if s in NO_SPAWN_STAGES):
        block = f"drawdown stage 8% or worse on {', '.join(stages)}: no spawning"
    blocked = frozenset(str(e.get("family")) for e in (plan or {}).get("evidence") or [] if e.get("blocked"))
    for g in gaps:
        if g.kind not in ("research_ready", "no_founder", "uncovered_timeframe"):
            continue
        fam = g.family or "?"
        if g.kind == "no_founder" and fam not in SPECIALISTS:
            actions.append(GapAction(gap_ids=[g.gap_id], action="suggest", target="BACKLOG",
                                     detail=f"{fam} is planned but not registered: a new family goes by PR "
                                            "(strategy-researcher -> quant-reviewer -> implementer)"))
            continue
        if g.kind == "no_founder":
            if block:
                refused.append(GapRefusal(gap_ids=[g.gap_id], action="seed_default_founder", target=fam, reason=block))
                continue
            for aid in population.ensure_founders(now):       # the existing rule: one default founder per family
                actions.append(GapAction(gap_ids=[g.gap_id], action="seed_default_founder", target=aid,
                                         detail="default founder (existing rule), shadow, zero capital"))
            continue
        tf = g.timeframe or SPECIALISTS[fam].timeframe
        if g.kind == "research_ready":
            cfg, origin = dict(g.data["config"]), f"trial {g.data['trial']}"
        else:
            cfg, origin = founder_base(fam, tf), f"{fam} default on {tf}"
        target = AgentIdentity(family=fam, config=cfg).agent_id
        if tf not in walkforward_tfs:
            reason = (f"no walk-forward window for {tf} in settings: the Saturday retrain cannot train it "
                      "(BACKLOG 13, 4h founder path)")
            refused.append(GapRefusal(gap_ids=[g.gap_id], action="spawn_founder", target=target, reason=reason))
            actions.append(GapAction(gap_ids=[g.gap_id], action="suggest", target="BACKLOG",
                                     detail=f"add a {tf} walk-forward window so {fam} can be trained on {tf}"))
            continue
        if block:
            refused.append(GapRefusal(gap_ids=[g.gap_id], action="spawn_founder", target=target, reason=block))
            continue
        try:
            m = population.spawn_founder(fam, cfg, now, gap_id=g.gap_id, origin=origin,
                                         max_per_month=settings.founders_per_month, blocked=blocked)
        except SpawnRefused as exc:
            refused.append(GapRefusal(gap_ids=[g.gap_id], action="spawn_founder", target=target, reason=str(exc)))
            continue
        actions.append(GapAction(gap_ids=[g.gap_id], action="spawn_founder", target=m.agent_id,
                                 detail=f"shadow founder ({origin}), zero capital; live only through the gates"))
        _append_ledger(state_dir, {"ts": now.isoformat(), "action": "spawn_founder", "gap_ids": [g.gap_id],
                                   "target": m.agent_id, "origin": origin})

    # ---- staff agents: on-demand runs of existing read-only roles
    done = {gid for r in ledger if r.get("action") == "staff_run" for gid in r.get("gap_ids") or []}
    week_runs = sum(1 for r in ledger if r.get("action") == "staff_run"
                    and pd.Timestamp(r["ts"]) > now - pd.Timedelta(days=7))
    by_role: dict[str, list[Gap]] = {}
    for g in gaps:
        if g.kind in ON_DEMAND_ROLES:
            by_role.setdefault(ON_DEMAND_ROLES[g.kind], []).append(g)
    for role_name, rgaps in sorted(by_role.items()):
        new = [g for g in rgaps if g.gap_id not in done]
        handled += [g.gap_id for g in rgaps if g.gap_id in done]
        if not new:
            continue
        ids = [g.gap_id for g in new]
        role = ROLES.get(role_name)
        why: str | None = None
        if role is None:
            why = f"no role {role_name!r}: new roles go by PR with a security review"
        elif set(role.tools) & WRITE_TOOLS:
            why = f"{role_name} holds write tools {sorted(set(role.tools) & WRITE_TOOLS)}: on-demand runs are read-only"
        elif runner is None:
            why = "no anthropic-api-key in the keyring"
        elif week_runs >= settings.staff_runs_per_week:
            why = f"weekly cap reached: {week_runs} on-demand staff runs in 7 days (max {settings.staff_runs_per_week})"
        elif runner.ledger.remaining(now) < role.max_cost_usd:
            why = (f"monthly agent budget: {runner.ledger.remaining(now):.2f} USD left of "
                   f"{runner.ledger.cap:.2f}, below the role's per-run cap {role.max_cost_usd:.2f}")
        if why is not None or role is None or runner is None:
            refused.append(GapRefusal(gap_ids=ids, action="staff_run", target=role_name, reason=why or "refused"))
            continue
        context = "On-demand run by gap_watch for these gaps:\n" + "\n".join(f"- {g.gap_id}: {g.detail}" for g in new)
        run = runner.run(role, now, extra_context=context)
        week_runs += 1
        actions.append(GapAction(gap_ids=ids, action="staff_run", target=role_name,
                                 detail=f"status {run.status}, cost {run.cost_usd:.3f} USD, report {run.report_path}"))
        _append_ledger(state_dir, {"ts": now.isoformat(), "action": "staff_run", "gap_ids": ids, "target": role_name,
                                   "status": run.status, "cost_usd": round(run.cost_usd, 4)})

    # ---- research: hypotheses for the regime and dead-family gaps (they still face the research gates)
    filed_gaps = {str(r.get("gap_id")) for r in _read_hypotheses(state_dir) if r.get("gap_id")}
    filed = 0
    for g in gaps:
        if g.kind not in HYPOTHESIS_KINDS:
            continue
        if g.gap_id in filed_gaps:
            handled.append(g.gap_id)
            continue
        if filed >= settings.hypotheses_per_run:
            refused.append(GapRefusal(gap_ids=[g.gap_id], action="file_hypothesis", target=g.family or "portfolio",
                                      reason=f"hypothesis cap reached: {settings.hypotheses_per_run} per run"))
            continue
        if g.kind == "regime":
            title = f"Regime gap: {g.gap_id.split(':')[1].replace('_', ' ')} not seen in the champions' training"
            change = "a pre-registered trial of the plan's focus family on a training window that includes this regime"
        else:
            title = f"Family {g.family} has no active agent"
            change = (f"a pre-registered trial of a new {g.family} configuration (within +-50% of its defaults); "
                      "a passed trial becomes a shadow founder automatically")
        row = {"ts": now.isoformat(), "title": title, "family": g.family or "portfolio", "rationale": g.detail,
               "proposed_change": change, "evidence": json.dumps(g.data, default=str), "status": "proposed",
               "source": "gap_watch", "gap_id": g.gap_id}
        row["id"] = hypothesis_id(row)
        with open(state_dir / HYPOTHESES_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        filed += 1
        actions.append(GapAction(gap_ids=[g.gap_id], action="file_hypothesis", target=row["id"], detail=title))

    report = GapReport(
        ts=now, gaps=gaps, actions=actions, refused=refused, already_handled=sorted(set(handled)), notes=notes,
        limits={"founders_per_month": settings.founders_per_month, "reserved_shadow_slots": pop_mod.GAP_RESERVED_SLOTS,
                "shadow_cap": pop_mod.SHADOW_CAP, "live_cap": pop_mod.LIVE_CAP,
                "gap_founders_this_month": len(population.gap_founders_this_month(now)),
                "staff_runs_per_week": settings.staff_runs_per_week, "staff_runs_last_7_days": week_runs,
                "hypotheses_per_run": settings.hypotheses_per_run,
                "monthly_cap_usd": runner.ledger.cap if runner is not None else None,
                "agent_budget_left_usd": round(runner.ledger.remaining(now), 4) if runner is not None else None})
    write_atomic(state_dir / GAPS_FILE, report.model_dump_json(indent=1))
    return report
