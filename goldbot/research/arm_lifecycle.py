"""The automatic forward read and decision per demo arm (owner directive 2026-11-18, build 2; the rule is
docs/research/arm-lifecycle-2026-11-18.md, written before any arm is read under it). A nightly job
(`ops.jobs.arm_lifecycle`) after the daily review.

The rule, as registered:
* Population, per (broker, arm): the arm's gold fills on the broker's demo hosts with a fill label and an r2 replay
  label (`exploration_data.read_obs`, replay r2, evidence `posterior`), a learning arm's from its `since` on.
  Decided: role strategy and learning arms, running, not spec-retired. Never decided: controls, cost arms, owner
  experiments, and a fixed-window experiment registered to run to its end (a row with `params.end_utc`: the red-box
  week arms RB*/RBC* run to 2026-11-22 22:00 UTC).
* Figures (aux.db `arm_lifecycle_reads`, one row per arm a day: the journal of every decision, HOLD included): net
  GBP and trades from the host ledger (`exploration_trades`, every demo account of the broker), fills, mean R (fill
  label), the live-minus-replay gap (fill_r - replay_r on the arm's own fills, the S5 quantity), and the excess over
  the arm's matched replay twin.
* The matched replay twin (HV Rule 3 as amended 2026-11-15): the r2 replay labels of the arm's matched control
  (`controls`: the strategy ranking's pairing; a learning arm's `learning.control`), every candidate of it, taken or
  not, in the same UTC-hour cell, decided on or before the fill's session day (point in time). The control's mean is
  raised by max(0, the replay-parity scope's latest reported mean gap) before the excess is taken; a scope revoked
  by the parity monitor, or with no reported gap, leaves the twin unvalidated (HOLD; only a kill rule decides). A
  fill whose cell has no control label is unmatched; above MAX_UNMATCHED of the fills the read is not readable.
* The test: the EVAL-2 / replay-parity e-process (`eval2.forward.e_edge`: one term per session day, running maximum,
  valid at every look, so a nightly re-read costs no false decision) on the per-fill excess, two one-sided streams:
  worse (H0 excess >= 0) and better (H0 excess <= 0). Read only from MIN_FILLS fills over MIN_DAYS session days.
* Decisions: RETIRE when a kill rule fires (a learning arm's loss cap, its read at 300 fills, its last day) or the
  worse stream reaches E_RETIRE (= E_PER_STREAM x the 2 streams of the arm: 1 / q with ADR 0085's q = 0.05, per
  stream, Bonferroni over the arm's two). SIZE_UP when the arm is a qualified strategy arm (never a learning arm:
  "a learning arm trades the minimum lot"), the better stream reaches E_PER_STREAM x 2 x m (m: the arms readable
  for SIZE_UP in the run, Bonferroni: a size-up widens risk, so the stricter bar), and its mean R and net GBP are
  positive. Otherwise HOLD.
* Applied: a RETIRE through the engine's existing retirement (aux.db `exploration_arm_status`, read once a minute by
  `engine.exploration.refresh_retired`: no new entry, every exit of an open position kept; the de-registration
  follows once flat). A SIZE_UP is journalled as a proposal only (`applied` False): trading-safety 2026-11-18 item 2
  rules that size is part of the arm hash, a resize is a NEW arm and the hot-loaded registry refuses a lot above the
  minimum, so it goes through review. Real-money accounts are never read or written: the hosts are demo probe_only.

Reads, plus the two aux.db writes above; no order path.
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Collection, Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, load_record_json
from goldbot.db.connection import Database, now_ns
from goldbot.research.arms import Arm, Learning
from goldbot.research.eval2.forward import TF_BAND, e_edge
from goldbot.research.exploration_data import GOLD, REPLAY_V2, read_obs

log = logging.getLogger(__name__)

MIN_FILLS = 50                  # EVAL-2 MIN_FILLS_ON_TRACK
MIN_DAYS = 10                   # S5_MIN_DAYS
E_PER_STREAM = 20.0             # 1 / q per stream, q = 0.05 (ADR 0085; the parity monitor's and E_KILL_EXEC's form)
STREAMS = 2                     # worse and better, per arm
E_RETIRE = E_PER_STREAM * STREAMS
MAX_UNMATCHED = 0.10            # HV Rule 3: above 10 % of fills in cells with no control label, "not readable"
DECIDED_ROLES = frozenset({"strategy", "learning"})
SIZE_UP_ROLES = frozenset({"strategy"})
RULE = "arm lifecycle (docs/research/arm-lifecycle-2026-11-18.md)"

Decision = Literal["HOLD", "RETIRE", "SIZE_UP"]


class ParityGap(FrozenRecord):
    """One replay-parity scope as the monitor reported it (`research.replay_parity.ParityRead`)."""
    scope: str
    status: str
    mean_gap_r: float | None = None
    revoked: bool = False


class ArmRead(FrozenRecord):
    """One (broker, arm)'s nightly read and its decision (aux.db `arm_lifecycle_reads.body`)."""
    day: str
    broker: str
    arm_id: str
    role: str
    tf: str
    control_arm: str | None = None
    n_trades: int = 0                       # closed filled trades in the host ledger
    net_gbp: float = 0.0
    n_fills: int = 0                        # fills with a fill label and an r2 label
    n_days: int = 0
    mean_r: float | None = None
    gap_r: float | None = None              # mean fill_r - replay_r over the arm's own fills
    n_excess: int = 0
    excess_r: float | None = None
    unmatched_share: float | None = None
    control_raise_r: float = 0.0
    validated: bool = False
    e_worse: float = 1.0
    e_better: float = 1.0
    e_retire: float = E_RETIRE
    e_size_up: float | None = None
    kill: str | None = None
    decision: Decision = "HOLD"
    applied: bool = False
    reason: str = ""


def decided(arm: Arm) -> bool:
    """Whether the rule decides `arm` (module docstring: roles, running, no spec retirement, no fixed window)."""
    return (arm.role in DECIDED_ROLES and arm.status == "run" and not arm.retired
            and "end_utc" not in arm.params and not arm.entry_refused.startswith("learning arm expired"))


def _learning(arm: Arm) -> Learning | None:
    try:
        return Learning.model_validate(arm.learning) if arm.learning is not None else None
    except ValueError:
        return None


def twin_means(fills: pd.DataFrame, ctrl: pd.DataFrame) -> np.ndarray:
    """Each fill's matched replay twin: the mean r2 label of the control's candidates in its UTC-hour cell decided on
    or before its session day (NaN: no control label there yet)."""
    out = np.full(len(fills), np.nan)
    if ctrl.empty or fills.empty:
        return out
    c = pd.DataFrame({"h": ctrl["decision_utc"].dt.hour.to_numpy(), "d": ctrl["session_day"].astype(str).to_numpy(),
                      "r": ctrl["label_sim_r"].to_numpy(dtype=float)})
    for h, g in c.groupby("h"):
        days = g.groupby("d")["r"].agg(["sum", "count"]).sort_index()
        cs, cn, keys = days["sum"].cumsum().to_numpy(), days["count"].cumsum().to_numpy(), days.index.to_numpy()
        sel = np.flatnonzero(fills["decision_utc"].dt.hour.to_numpy() == h)
        for i in sel:
            k = int(np.searchsorted(keys, str(fills["session_day"].iloc[i]), side="right")) - 1
            if k >= 0:
                out[i] = cs[k] / cn[k]
    return out


def _ledger(conn: Any, arm_id: str, accounts: Collection[str], since: pd.Timestamp | None) -> tuple[int, float]:
    from goldbot.engine.exploration import LEDGER, _ns
    where, args = "arm_id = ? AND closed = 1", list[Any]([arm_id])
    if since is not None:
        where, args = where + " AND ts_ns >= ?", [*args, _ns(since)]
    n, pnl = 0, 0.0
    for t in LEDGER.select(conn, where, tuple(args)):
        if t.account_id in accounts and t.fill_price is not None and t.exit_utc is not None:
            n += 1
            pnl += t.pnl if t.pnl is not None else -((t.stop_loss or 0.0) + (t.cost or 0.0))
    return n, pnl


def _kill(conn: Any, arm: Arm, cap: float, day: str) -> str | None:
    """The existing kill rules of a learning arm (`engine.exploration_learning`): its loss cap, its read at 300
    admissible fills, its last day. None for any other arm, or when none fires."""
    from goldbot.engine.exploration_learning import read
    from goldbot.research.arms import LEARNING_READ_FILLS
    block = _learning(arm) if arm.role == "learning" else None
    if block is None:
        return None
    r = read(conn, arm.id, cap, pd.Timestamp(block.since, tz="UTC"))
    if -r.pnl >= block.loss_cap_gbp - 1e-9:
        return f"learning_loss_cap {block.loss_cap_gbp:g} reached"
    if r.fills >= LEARNING_READ_FILLS:
        return f"learning_read_due at {r.fills} admissible fills"
    if day > block.until:
        return f"learning_expired after {block.until}"
    return None


def read_arm(db: Database, obs: pd.DataFrame, arm: Arm, *, broker: str, control: str | None,
             accounts: Collection[str], parity: Mapping[str, ParityGap], cap: float, day: str) -> ArmRead:
    """One arm's figures and its validation (module docstring); the decision is `decide`'s."""
    block = _learning(arm)
    since = pd.Timestamp(block.since, tz="UTC") if block is not None else None
    with db.connection() as conn:
        n_trades, net = _ledger(conn, arm.id, accounts, since)
        kill = _kill(conn, arm, cap, day)
    f = obs[(obs["arm_id"] == arm.id) & obs["taken"] & obs["label_fill_r"].notna() & obs["label_sim_r"].notna()]
    if since is not None:
        f = f[f["decision_utc"] >= since]
    f = f[f["session_day"].astype(str) <= day].reset_index(drop=True)
    fill_r = f["label_fill_r"].to_numpy(dtype=float)
    days = f["session_day"].astype(str).to_numpy()
    base: dict[str, Any] = dict(day=day, broker=broker, arm_id=arm.id, role=arm.role, tf=arm.timeframe,
                                control_arm=control, n_trades=n_trades, net_gbp=round(net, 2), n_fills=len(f),
                                n_days=len(set(days.tolist())), kill=kill,
                                mean_r=float(fill_r.mean()) if len(f) else None,
                                gap_r=float((fill_r - f["label_sim_r"].to_numpy(dtype=float)).mean()) if len(f) else None)
    scope = parity.get(f"{broker}:{TF_BAND.get(arm.timeframe, arm.timeframe)}")
    if control is None:
        return ArmRead(**base, reason="no matched control: no replay twin")
    ctrl = obs[(obs["arm_id"] == control) & obs["label_sim_r"].notna()]
    twin = twin_means(f, ctrl)
    matched = np.isfinite(twin)
    unmatched = float(1 - matched.mean()) if len(f) else None
    raise_r = max(0.0, scope.mean_gap_r) if scope is not None and scope.mean_gap_r is not None else 0.0
    excess = fill_r[matched] - (twin[matched] + raise_r)
    figures: dict[str, Any] = dict(n_excess=int(matched.sum()), excess_r=float(excess.mean()) if len(excess) else None,
                   unmatched_share=unmatched, control_raise_r=raise_r,
                   e_worse=e_edge(-excess, day=days[matched]), e_better=e_edge(excess, day=days[matched]))
    why = None
    if scope is None or scope.revoked or scope.status != "PASS" or scope.mean_gap_r is None:
        why = ("control unvalidated: replay-parity scope " + (f"{scope.scope} {scope.status}"
               + (" revoked" if scope.revoked else "") if scope is not None else "not read"))
    elif len(f) < MIN_FILLS or base["n_days"] < MIN_DAYS:
        why = f"insufficient: {len(f)} fills over {base['n_days']} days (needs {MIN_FILLS} / {MIN_DAYS})"
    elif unmatched is not None and unmatched > MAX_UNMATCHED:
        why = f"not readable: {unmatched:.0%} of fills in cells with no control label"
    return ArmRead(**base, **figures, validated=why is None, reason=why or "")


def decide(r: ArmRead, *, m: int) -> ArmRead:
    """The decision (module docstring); `m`: the arms readable for SIZE_UP in this run (Bonferroni)."""
    size_bar = E_PER_STREAM * STREAMS * max(1, m)
    if r.kill is not None:
        return r.model_copy(update={"decision": "RETIRE", "e_size_up": size_bar, "reason": f"kill rule: {r.kill}"})
    if not r.validated:
        return r.model_copy(update={"decision": "HOLD", "e_size_up": size_bar})
    if r.e_worse >= E_RETIRE:
        return r.model_copy(update={"decision": "RETIRE", "e_size_up": size_bar,
                                    "reason": f"worse than its replay twin: e {r.e_worse:.1f} >= {E_RETIRE:g}"})
    if r.role in SIZE_UP_ROLES and r.e_better >= size_bar and (r.mean_r or 0.0) > 0 and r.net_gbp > 0:
        return r.model_copy(update={"decision": "SIZE_UP", "e_size_up": size_bar,
                                    "reason": f"better than its replay twin: e {r.e_better:.1f} >= {size_bar:g}; "
                                              "proposal only (a resize is a new hashed arm, reviewed)"})
    return r.model_copy(update={"decision": "HOLD", "e_size_up": size_bar,
                                "reason": f"no decision: e worse {r.e_worse:.1f}, e better {r.e_better:.1f}"})


def parity_gaps(db: Database, brokers: Collection[str], *, before: pd.Timestamp) -> dict[str, ParityGap]:
    """The replay-parity scopes (`research.replay_parity`, branch feat/replay-only-controls). The SEAM: until that
    module is deployed this returns {} and every twin reads unvalidated (HOLD; kill rules still decide). Raises when
    the monitor is there but its reads fail (the caller holds every arm)."""
    try:
        rp: Any = importlib.import_module("goldbot.research.replay_parity")
    except ModuleNotFoundError:
        log.warning("replay-parity monitor not deployed: every replay twin reads unvalidated")
        return {}
    revoked = rp.revoked(db)
    return {r.scope: ParityGap(scope=r.scope, status=r.status, mean_gap_r=r.mean_gap_r, revoked=r.scope in revoked)
            for r in rp.reads(rp.parity_fills(db, brokers, before=before))}


def run(db: Database, *, hosts: Mapping[str, str], arms: Collection[Arm], controls: Mapping[str, str],
        caps: Mapping[str, float], day: str, now: pd.Timestamp,
        parity: Mapping[str, ParityGap] | None = None) -> list[ArmRead]:
    """Every decided arm of every broker in `hosts` (account -> broker, demo probe_only accounts only), read,
    decided, journalled (`arm_lifecycle_reads`) and a RETIRE applied (`exploration_arm_status`). `parity`: the
    scopes (default `parity_gaps`; an unreadable monitor holds every arm on evidence)."""
    brokers = sorted(set(hosts.values()))
    if parity is None:
        try:
            parity = parity_gaps(db, brokers, before=pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1))
        except Exception as exc:                # noqa: BLE001 - fail closed: no evidence decision tonight
            log.error("replay-parity reads failed (%s): every twin unvalidated", type(exc).__name__)
            parity = {}
    reads: list[ArmRead] = []
    for broker in brokers:
        accounts = {a for a, b in hosts.items() if b == broker}
        obs = read_obs(db, broker=broker, replay_version=REPLAY_V2, evidence="posterior", instrument=GOLD)
        for arm in sorted(arms, key=lambda a: a.id):
            if not decided(arm):
                continue
            block = _learning(arm)
            control = controls.get(arm.id) or (block.control if block is not None else None)
            reads.append(read_arm(db, obs, arm, broker=broker, control=control, accounts=accounts, parity=parity,
                                  cap=float(caps.get(arm.id, 0.0)), day=day))
    m = sum(1 for r in reads if r.validated and r.role in SIZE_UP_ROLES)
    out = [decide(r, m=m) for r in reads]
    record(db, out, now=now)
    return out


def record(db: Database, reads: list[ArmRead], *, now: pd.Timestamp) -> None:
    """Journal every read (one row per day, broker and arm; a re-run of the day replaces it) and apply each RETIRE
    through `exploration_arm_status` (INSERT OR IGNORE: a retirement is permanent and never re-dated)."""
    ns = int(pd.Timestamp(now).value) if now is not None else now_ns()
    with db.write() as conn:
        for r in reads:
            applied = r.decision == "RETIRE"
            body = r.model_copy(update={"applied": applied})
            conn.execute("INSERT OR REPLACE INTO arm_lifecycle_reads (day, broker, arm_id, decision, recorded_ns, "
                         "body) VALUES (?, ?, ?, ?, ?, ?)",
                         (r.day, r.broker, r.arm_id, r.decision, ns, body.model_dump_json()))
            if applied:
                conn.execute("INSERT OR IGNORE INTO exploration_arm_status (broker, arm_id, status, day, rule, "
                             "recorded_ns) VALUES (?, ?, 'retired', ?, ?, ?)",
                             (r.broker, r.arm_id, r.day, f"{RULE}: {r.reason}", ns))
    for r in reads:
        if r.decision != "HOLD":
            log.warning("arm lifecycle %s %s on %s: %s", r.decision, r.arm_id, r.broker, r.reason)


def reads_of(db: Database, day: str) -> list[ArmRead]:
    """The day's journalled reads (reports and tests)."""
    with db.connection() as conn:
        rows = conn.execute("SELECT body FROM arm_lifecycle_reads WHERE day = ? ORDER BY broker, arm_id",
                            (day,)).fetchall()
    return [load_record_json(ArmRead, b, source="arm_lifecycle_reads") for (b,) in rows]


def summary(reads: list[ArmRead]) -> dict[str, Any]:
    """The job's result: counts by decision and the arms decided."""
    by: dict[str, list[str]] = {"HOLD": [], "RETIRE": [], "SIZE_UP": []}
    for r in reads:
        by[r.decision].append(f"{r.broker}:{r.arm_id}")
    return {"arms": len(reads), **{k.lower(): len(v) for k, v in by.items()},
            "retired": by["RETIRE"], "size_up_proposed": by["SIZE_UP"],
            "validated": sum(r.validated for r in reads)}


__all__ = ["E_PER_STREAM", "E_RETIRE", "MAX_UNMATCHED", "MIN_DAYS", "MIN_FILLS", "ArmRead", "ParityGap", "decide",
           "decided", "parity_gaps", "read_arm", "reads_of", "record", "run", "summary", "twin_means"]
