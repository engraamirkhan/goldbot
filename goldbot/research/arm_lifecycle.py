"""The automatic kill-only read and decision per demo arm (owner directive 2026-11-18, build 2, as revised by the quant
review of the same day; the rule is docs/research/arm-lifecycle-2026-11-18.md, written before any arm is read under
it). A nightly job (`ops.jobs.arm_lifecycle`) after the daily review. This is a KILL-ONLY lifecycle under HV Rule 3
(as amended 2026-11-15), NOT the ADR 0086 forward read: it can retire an arm, it never promotes or sizes one.

The rule, as registered:
* Decided: role strategy and learning arms, running, not spec-retired. Controls, cost arms and owner experiments are
  never decided. Every decided arm's existing kill rules are read (a learning arm's loss cap, its read at 300
  admissible fills, its last day). A fixed-window experiment registered to run to its end (`params.prereg` in
  FIXED_WINDOW_PREREGS: the red-box week arms RB*/RBC*, to 2026-11-22 22:00 UTC) is decided by its kill rules only,
  never on evidence.
* Figures (aux.db `arm_lifecycle_reads`, one row per arm a day: the journal of every decision, HOLD included): net
  GBP and trades from the host ledger (`exploration_trades`, every demo account of the broker), fills and mean R
  (fill label), the live-minus-replay gap (fill_r - replay_r on the arm's own fills, the S5 quantity), and the excess
  over the arm's matched replay twin.
* The excess (quant B1): on the arm's REPLAYED candidates, taken and refused alike (r2 replay label `label_sim_r` not
  null; `exploration_data.read_obs`, replay r2, evidence `posterior`, gold), a learning arm's from its `since` on:
  excess = label_sim_r - twin. The twin: the mean r2 replay label of the matched control's candidates (`controls`:
  the strategy ranking's pairing; a learning arm's `learning.control`), taken or not, in the same (UTC hour, side)
  cell, decided on or before the candidate's session day (point in time). Replay against replay: no fill cost enters
  either side. The replay-parity monitor only VOIDS a read (its broker x band scope revoked, or not PASS: HOLD); it
  never shifts the excess. A candidate whose cell has no control label is unmatched; above MAX_UNMATCHED the read is
  not readable.
* Frozen path (quant B4, B5): each (broker, arm, session day) term, the day's mean excess, is written once, and only
  once the day's labels have settled: now >= the day's end + the arm's longest hold (the time barrier of its
  candidates or of its row; a spec_rule arm without one, its spec's time exit, else SETTLE_DEFAULT; a hold that
  reaches a weekend gets the weekend added) + SETTLE_PAD (`settled_at`). The number of the day's candidates still
  unlabelled then is stored with it; above VOID_UNLABELLED of them the day is stored void (no term) and logged. The
  e-process reads the stored, non-void terms only, so a later label or control row never rewrites a past term, and no
  day is cut short while its labels are still arriving (outcome-dependent truncation).
* The test: the EVAL-2 / replay-parity e-process (`eval2.forward.e_edge`: one term per session day, running maximum,
  valid at every look) on the stored terms. Worse (H0 excess >= 0) decides; better is reported, not evidence. Read
  only from MIN_N candidates over MIN_DAYS session days.
* Decisions: RETIRE when a kill rule fires or the worse stream reaches E_RETIRE (= 1 / q x 2: ADR 0085's q = 0.05,
  Bonferroni over the arm's two streams); otherwise HOLD. No promotion, no size change.
* Applied: a RETIRE through the engine's existing retirement (aux.db `exploration_arm_status`, read once a minute by
  `engine.exploration.refresh_retired`: no new entry, every exit of an open position kept; R153 de-registers it once
  flat). Real-money accounts are never read or written: the hosts are demo probe_only.

Discovered learning arms (docs/research/prereg-amendment-2026-11-18-discovered-arms.md, 5.1; `discovered`): the
same read, after two voids, with one more kill rule.
* "A bracket that fails either check is refused ... The candidate is void for the read" (section 4): a candidate the
  engine journalled `arm_registry_bracket_void` at its entry, or one whose row fails `arm_rules_spec.bracket_void`
  against the recompute from the bars closed at its decision (`engine_recompute`: the engine's own
  `arm_registry.decision_bracket`, the same window, so a bracket the engine accepted is never voided here), is left
  out of the terms. A candidate the night cannot recompute (no bars) is left out too, but is not a refusal: the read
  fails closed without blaming the arm.
* "Any change to the control's parameters after registration voids the arm ... its reads are void from the change
  onward, and it retires" (section 2.2): from the first `arm_registry_control_changed` journal row on, no candidate
  of that session day or later enters a term, every read is void (never validated) and the arm retires.
* Rule 5, bracket integrity: "A refused bracket (section 4) on 3 or more candidates, or on any taken candidate"
  retires the arm, journalled with its reason.
A discovered arm read without the journal or a recompute holds, never read as evidence (its kill rules still decide).
The frozen day terms (the settle lag, append-only, void days) are unchanged: the voids only choose which candidates
enter a day before it is frozen.

Reads, plus the three aux.db writes above; no order path.
"""
from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable, Collection, Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord, load_record_json
from goldbot.db.connection import Database
from goldbot.research.arm_rules_spec import BRACKET_PREFIX, Bracket, bracket_void
from goldbot.research.arms import PER_TRADE_KEY, Arm, Learning
from goldbot.research.eval2.forward import TF_BAND, e_edge
from goldbot.research.exploration_data import GOLD, REPLAY_V2, read_obs, session_day

log = logging.getLogger(__name__)

MIN_N = 50                      # replayed candidates (EVAL-2 MIN_FILLS_ON_TRACK's size)
MIN_DAYS = 10                   # S5_MIN_DAYS
E_PER_STREAM = 20.0             # 1 / q per stream, q = 0.05 (ADR 0085; the parity monitor's and E_KILL_EXEC's form)
STREAMS = 2                     # worse and better, per arm
E_RETIRE = E_PER_STREAM * STREAMS
MAX_UNMATCHED = 0.10            # HV Rule 3: above 10 % of candidates in cells with no control label, "not readable"
SETTLE_PAD = pd.Timedelta(days=1)       # B5: a day freezes this long after its end + the arm's longest hold
SETTLE_DEFAULT = pd.Timedelta(days=10)  # B5: the hold of an arm whose row and spec name none
VOID_UNLABELLED = 0.05          # B5: a settled day with more of its candidates unlabelled is stored void
DECIDED_ROLES = frozenset({"strategy", "learning"})
# quant B2: experiments registered to run to a fixed end, matched on params.prereg (the file name, without .md#anchor)
FIXED_WINDOW_PREREGS = frozenset({"prereg-amendment-2026-11-15-redbox-week"})
RULE = "arm lifecycle (docs/research/arm-lifecycle-2026-11-18.md)"
BRACKET_RETIRE_N = 3            # amendment 5.1 (5): "A refused bracket (section 4) on 3 or more candidates"
BRACKET_VOID_ACTION = "arm_registry_bracket_void"           # engine.arm_registry.BRACKET_VOID (the journal's action)
CONTROL_CHANGED_ACTION = "arm_registry_control_changed"     # engine.arm_registry.CONTROL_CHANGED
# (arm, candidate row) -> the bracket recomputed from the bars closed at its decision; None: not recomputable tonight
Recompute = Callable[[Arm, Mapping[str, Any]], Bracket | None]

Decision = Literal["HOLD", "RETIRE"]


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
    fixed_window: bool = False
    n_trades: int = 0                       # closed filled trades in the host ledger
    net_gbp: float = 0.0
    n_fills: int = 0                        # taken candidates with a fill label
    mean_r: float | None = None             # mean fill label
    gap_r: float | None = None              # mean fill_r - replay_r over the arm's own fills
    n_cands: int = 0                        # replayed candidates, taken and refused
    n_excess: int = 0                       # of them, matched to a twin, in the frozen terms
    n_days: int = 0                         # frozen day terms
    excess_r: float | None = None           # mean of the frozen day terms
    unmatched_share: float | None = None
    validated: bool = False
    e_worse: float = 1.0
    e_better: float = 1.0                   # reported, not evidence
    e_retire: float = E_RETIRE
    kill: str | None = None
    n_bracket_void: int = 0                 # discovered arms: candidates whose bracket was refused (5.1 (5))
    void_from: str | None = None            # discovered arms: the control change every read is void from (2.2)
    decision: Decision = "HOLD"
    applied: bool = False
    reason: str = ""


def decided(arm: Arm) -> bool:
    """Whether the rule reads `arm` (module docstring: roles, running, no spec retirement)."""
    return arm.role in DECIDED_ROLES and arm.status == "run" and not arm.retired


def fixed_window(arm: Arm) -> bool:
    """A fixed-window experiment (FIXED_WINDOW_PREREGS, on params.prereg): kill rules only, never evidence."""
    pre = str(arm.params.get("prereg") or "").split("#", 1)[0].rsplit("/", 1)[-1].removesuffix(".md")
    return pre in FIXED_WINDOW_PREREGS


def discovered(arm: Arm) -> bool:
    """A discovered learning arm (the amendment's): role learning with a per-trade bracket `spec_exit:<hash12>`."""
    return arm.role == "learning" and str(arm.params.get(PER_TRADE_KEY, "")).startswith(BRACKET_PREFIX)


def engine_recompute(bars: Mapping[str, pd.DataFrame]) -> Recompute:
    """The recompute from each candidate's account's 1m bars (`bars`: account -> store-schema 1m bars with
    `visible_at`): the engine's `arm_registry.decision_bracket`, the arm's own lookback window. None when the account
    has no bars or none closed by the decision."""
    from goldbot.engine.arm_registry import decision_bracket

    def recompute(arm: Arm, row: Mapping[str, Any]) -> Bracket | None:
        b = bars.get(str(row.get("account_id")))
        if b is None or b.empty:
            return None
        return decision_bracket(b, arm, pd.Timestamp(row["decision_utc"]))
    return recompute


def _ns_or_none(v: Any) -> int | None:
    return None if v is None or pd.isna(v) else int(pd.Timestamp(v).value)


def _num(v: Any) -> float | int | None:
    return None if v is None or pd.isna(v) else v


def _candidate_row(row: Mapping[str, Any]) -> dict[str, object]:
    """A read_obs row as `bracket_void` reads a candidate row (plain ints, epoch ns)."""
    hold = _num(row.get("max_hold_s"))
    return {"stop_dist": None if (v := _num(row.get("stop_dist"))) is None else float(v),
            "target_dist": None if (v := _num(row.get("target_dist"))) is None else float(v),
            "max_hold_s": None if hold is None else int(hold),
            "decision_ns": _ns_or_none(row.get("decision_utc")),
            "recorded_ns": None if (v := _num(row.get("recorded_ns"))) is None else int(v),
            "entry_ns": _ns_or_none(row.get("entry_utc"))}


def _journalled(journal: pd.DataFrame, action: str, arm_id: str, accounts: Collection[str]) -> list[dict[str, Any]]:
    """The journal's `action` rows of `arm_id` on `accounts` (store `decisions` schema: ts_utc, account_id, agent_id,
    action, detail JSON), each its detail plus `ts_utc` and `account_id`, in time order."""
    if journal.empty:
        return []
    j = journal[(journal["action"] == action) & journal["account_id"].astype(str).isin(set(accounts))]
    out = []
    for rec in j.sort_values("ts_utc").to_dict("records"):
        try:
            detail = json.loads(rec["detail"]) if isinstance(rec.get("detail"), str) else dict(rec.get("detail") or {})
        except (ValueError, TypeError):
            detail = {}
        if detail.get("arm") == arm_id or rec.get("agent_id") == "explore:" + arm_id:
            out.append({**detail, "ts_utc": pd.Timestamp(rec["ts_utc"]), "account_id": str(rec["account_id"])})
    return out


def _integrity(obs: pd.DataFrame, arm: Arm, *, journal: pd.DataFrame, recompute: Recompute,
               accounts: Collection[str]) -> tuple[pd.DataFrame, int, bool, pd.Timestamp | None]:
    """(`obs` without the arm's void candidates, refused brackets, whether one was on a taken candidate, the control
    change): the module docstring's discovered-arm voids. Refused: a journalled entry void, or `bracket_void` against
    the recompute (one the night cannot recompute is left out, not refused); counted per (account, decision)."""
    block = _learning(arm)
    since = pd.Timestamp(block.since, tz="UTC") if block is not None else None
    refused = {(str(e["account_id"]), _ns_or_none(e.get("bar")))
               for e in _journalled(journal, BRACKET_VOID_ACTION, arm.id, accounts)}
    changes = _journalled(journal, CONTROL_CHANGED_ACTION, arm.id, accounts)
    change = changes[0]["ts_utc"] if changes else None
    mine = obs[obs["arm_id"] == arm.id]
    if since is not None:
        mine = mine[mine["decision_utc"] >= since]
    drop, taken = [], False
    rows: list[dict[str, Any]] = [{str(k): v for k, v in r.items()} for r in mine.to_dict("records")]
    for i, row in zip(mine.index, rows, strict=True):
        key = (str(row.get("account_id")), _ns_or_none(row.get("decision_utc")))
        why: str | None = "journalled" if key in refused else None
        if why is None:
            rec = recompute(arm, row)
            if rec is None:
                drop.append(i)                              # not recomputable tonight: void, not a refusal
                continue
            why = bracket_void(_candidate_row(row), rec)
        if why is not None:
            drop.append(i)
            refused.add(key)
            taken = taken or bool(row.get("taken"))
    if change is not None:
        drop += list(mine.index[mine["session_day"].astype(str) >= session_day(change)])
    return obs.drop(index=sorted(set(drop))), len(refused), taken, change


def _learning(arm: Arm) -> Learning | None:
    try:
        return Learning.model_validate(arm.learning) if arm.learning is not None else None
    except ValueError:
        return None


def twin_means(cands: pd.DataFrame, ctrl: pd.DataFrame) -> np.ndarray:
    """Each candidate's matched replay twin: the mean r2 label of the control's candidates in its (UTC hour, side) cell
    decided on or before its session day (NaN: no control label there yet)."""
    out = np.full(len(cands), np.nan)
    if ctrl.empty or cands.empty:
        return out
    c = pd.DataFrame({"h": ctrl["decision_utc"].dt.hour.to_numpy(), "s": ctrl["side"].astype(int).to_numpy(),
                      "d": ctrl["session_day"].astype(str).to_numpy(), "r": ctrl["label_sim_r"].to_numpy(dtype=float)})
    hours, sides = cands["decision_utc"].dt.hour.to_numpy(), cands["side"].astype(int).to_numpy()
    days = cands["session_day"].astype(str).to_numpy()
    for (h, side), g in c.groupby(["h", "s"]):
        per = g.groupby("d")["r"].agg(["sum", "count"]).sort_index()
        cs, cn, keys = per["sum"].cumsum().to_numpy(), per["count"].cumsum().to_numpy(), per.index.to_numpy()
        for i in np.flatnonzero((hours == h) & (sides == side)):
            k = int(np.searchsorted(keys, days[i], side="right")) - 1
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


def _kill(conn: Any, arm: Arm, cap: float, day: str, accounts: Collection[str]) -> str | None:
    """The existing kill rules of a learning arm (`engine.exploration_learning`), read over `accounts` only (the
    broker the RETIRE is written for; safety review S1): its loss cap, its read at 300 admissible fills, its last day.
    None for any other arm, or when none fires."""
    from goldbot.engine.exploration_learning import read
    from goldbot.research.arms import LEARNING_READ_FILLS
    block = _learning(arm) if arm.role == "learning" else None
    if block is None:
        return None
    r = read(conn, arm.id, cap, pd.Timestamp(block.since, tz="UTC"), accounts)
    if -r.pnl >= block.loss_cap_gbp - 1e-9:
        return f"learning_loss_cap {block.loss_cap_gbp:g} reached"
    if r.fills >= LEARNING_READ_FILLS:
        return f"learning_read_due at {r.fills} admissible fills"
    if day > block.until:
        return f"learning_expired after {block.until}"
    return None


def arm_hold(arm: Arm) -> pd.Timedelta:
    """The arm's own time barrier (B5): its row's `hold_bars` x its timeframe; a spec_rule arm's spec time exit
    (`exit.time_bars`); else SETTLE_DEFAULT."""
    from goldbot.config import tf_seconds
    tf = tf_seconds(arm.timeframe)
    if isinstance(arm.params.get("hold_bars"), int | float):
        return pd.Timedelta(seconds=float(arm.params["hold_bars"]) * tf)
    if arm.rule == "spec_rule":
        try:
            from goldbot.research.autodisc.spec import StrategySpec
            bars = StrategySpec.model_validate_json(str(arm.params.get("spec"))).exit.time_bars
        except ValueError:
            bars = None
        if bars:
            return pd.Timedelta(seconds=bars * tf)
    return SETTLE_DEFAULT


def settled_at(day: str, hold: pd.Timedelta) -> pd.Timestamp:
    """When session day `day`'s labels have settled (B5): its end (the next session's start), plus `hold`, plus a
    weekend when the hold reaches one, plus SETTLE_PAD."""
    from goldbot.data.timeutil import NEW_YORK
    from goldbot.research.exploration_data import session_start
    end = session_start(f"{pd.Timestamp(day) + pd.Timedelta(days=1):%Y-%m-%d}")
    due = end + hold
    ny = end.tz_convert(NEW_YORK)
    friday_close = ny.dayofweek == 4 and ny.hour >= 17       # the week's last session: its hold runs past the weekend
    if friday_close or any(d.dayofweek >= 5 for d in pd.date_range(end.normalize(), due.normalize(), freq="D")):
        due += pd.Timedelta(days=2)
    return due + SETTLE_PAD


def day_terms(obs: pd.DataFrame, arm: Arm, *, control: str | None, day: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """(the arm's days: day, term (NaN: no matched candidate), n, n_all, n_unlabelled, hold_s; the figures). The
    candidates are the arm's replayed ones up to `day`, taken and refused (quant B1); every candidate of a day counts
    in n_all, the unlabelled ones (no r2 label yet) in n_unlabelled; the fill figures are its taken ones with a fill
    label."""
    block = _learning(arm)
    since = pd.Timestamp(block.since, tz="UTC") if block is not None else None
    a = obs[(obs["arm_id"] == arm.id) & (obs["session_day"].astype(str) <= day)]
    if since is not None:
        a = a[a["decision_utc"] >= since]
    cands = a[a["label_sim_r"].notna()].reset_index(drop=True)
    fills = a[a["taken"] & a["label_fill_r"].notna()]
    paired = fills[fills["label_sim_r"].notna()]
    fig: dict[str, Any] = dict(
        n_fills=len(fills), n_cands=len(cands),
        mean_r=float(fills["label_fill_r"].mean()) if len(fills) else None,
        gap_r=float((paired["label_fill_r"] - paired["label_sim_r"]).mean()) if len(paired) else None)
    cols = ["day", "term", "n", "n_all", "n_unlabelled", "hold_s"]
    if control is None or a.empty:
        return pd.DataFrame(columns=cols), fig
    every = pd.DataFrame({"day": a["session_day"].astype(str).to_numpy(),
                          "unlabelled": a["label_sim_r"].isna().to_numpy(),
                          "hold_s": pd.to_numeric(a["max_hold_s"], errors="coerce").to_numpy(dtype=float)
                          if "max_hold_s" in a else np.nan})
    per = every.groupby("day", sort=True).agg(n_all=("unlabelled", "size"), n_unlabelled=("unlabelled", "sum"),
                                              hold_s=("hold_s", "max")).reset_index()
    twin = twin_means(cands, obs[(obs["arm_id"] == control) & obs["label_sim_r"].notna()])
    ok = np.isfinite(twin)
    fig["unmatched_share"] = float(1 - ok.mean()) if len(cands) else None
    x = pd.DataFrame({"day": cands["session_day"].astype(str).to_numpy()[ok],
                      "excess": cands["label_sim_r"].to_numpy(dtype=float)[ok] - twin[ok]})
    terms = x.groupby("day", sort=True)["excess"].agg(term="mean", n="count").reset_index()
    out = per.merge(terms, on="day", how="left")
    out["n"] = out["n"].fillna(0).astype(int)
    return out[cols], fig


def freeze(db: Database, broker: str, arm: Arm, days: pd.DataFrame, *, now: pd.Timestamp) -> pd.DataFrame:
    """Write each SETTLED day not stored yet (B5: `settled_at` with the longer of its candidates' holds and the arm's
    own, `arm_hold`; INSERT OR IGNORE: the first settled read stands), void when more than VOID_UNLABELLED of its
    candidates are still unlabelled; a settled day with no matched candidate is not stored. Then every stored,
    non-void term of the arm in day order: the path the e-process reads (quant B4)."""
    own = arm_hold(arm)
    rows = []
    for rec in days.to_dict("records"):
        day, held, term = str(rec["day"]), float(rec["hold_s"]), float(rec["term"])
        n, n_all, unlabelled = int(rec["n"]), int(rec["n_all"]), int(rec["n_unlabelled"])
        hold = max(own, pd.Timedelta(seconds=held)) if np.isfinite(held) else own
        if now < settled_at(day, hold):
            continue
        void = n_all > 0 and unlabelled / n_all > VOID_UNLABELLED
        if void:
            log.warning("arm lifecycle %s %s: session day %s stored void (%d of %d candidates unlabelled when it "
                        "settled)", broker, arm.id, day, unlabelled, n_all)
        elif not np.isfinite(term):
            continue
        rows.append((broker, arm.id, day, None if void else term, n, unlabelled, int(void), int(now.value)))
    with db.write() as conn:
        conn.executemany("INSERT OR IGNORE INTO arm_lifecycle_terms (broker, arm_id, day, term, n, n_unlabelled, "
                         "void, recorded_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        got = conn.execute("SELECT day, term, n FROM arm_lifecycle_terms WHERE broker = ? AND arm_id = ? AND void = 0 "
                           "ORDER BY day", (broker, arm.id)).fetchall()
    return pd.DataFrame(got, columns=["day", "term", "n"])


def read_arm(db: Database, obs: pd.DataFrame, arm: Arm, *, broker: str, control: str | None,
             accounts: Collection[str], parity: Mapping[str, ParityGap], cap: float, day: str,
             now: pd.Timestamp, journal: pd.DataFrame | None = None,
             recompute: Recompute | None = None) -> ArmRead:
    """One arm's figures, its frozen terms and their validity (module docstring); the decision is `decide`'s. A
    discovered arm's voids and integrity kill read `journal` (the broker's `decisions` rows) and `recompute`."""
    block = _learning(arm)
    since = pd.Timestamp(block.since, tz="UTC") if block is not None else None
    with db.connection() as conn:
        n_trades, net = _ledger(conn, arm.id, accounts, since)
        kill = _kill(conn, arm, cap, day, accounts)
    extra: dict[str, Any] = {}
    unverified = change = None
    if discovered(arm):
        if journal is None or recompute is None:
            unverified = "bracket integrity unverified: " + ("no journal read" if journal is None else
                                                              "no bars to recompute") + " (amendment 5.1): no evidence"
        else:
            obs, n_void, on_taken, change = _integrity(obs, arm, journal=journal, recompute=recompute,
                                                       accounts=accounts)
            extra["n_bracket_void"] = n_void
            if change is not None:
                extra["void_from"] = change.isoformat()
            if kill is None and change is not None:
                kill = (f"control changed after registration at {change.isoformat()}: every read void from then on "
                        "(amendment 2.2)")
            elif kill is None and on_taken:
                kill = "bracket integrity: a refused bracket on a taken candidate (amendment 5.1 (5))"
            elif kill is None and n_void >= BRACKET_RETIRE_N:
                kill = f"bracket integrity: {n_void} refused brackets on candidates (amendment 5.1 (5))"
    terms, fig = day_terms(obs, arm, control=control, day=day)
    base: dict[str, Any] = dict(day=day, broker=broker, arm_id=arm.id, role=arm.role, tf=arm.timeframe,
                                control_arm=control, fixed_window=fixed_window(arm), n_trades=n_trades,
                                net_gbp=round(net, 2), kill=kill, **fig, **extra)
    if control is None:
        return ArmRead(**base, reason="no matched control: no replay twin")
    if unverified is not None:
        return ArmRead(**base, reason=unverified)                   # nothing frozen from unchecked candidates
    stored = freeze(db, broker, arm, terms, now=pd.Timestamp(now))
    x, d = stored["term"].to_numpy(dtype=float), stored["day"].astype(str).to_numpy()
    base.update(n_excess=int(stored["n"].sum()), n_days=len(stored),
                excess_r=float(x.mean()) if len(x) else None,
                e_worse=e_edge(-x, day=d), e_better=e_edge(x, day=d))
    if change is not None:
        return ArmRead(**base, reason=f"void: control changed at {change.isoformat()}")
    scope = parity.get(f"{broker}:{TF_BAND.get(arm.timeframe, arm.timeframe)}")
    unmatched = fig.get("unmatched_share")
    why = None
    if scope is None or scope.revoked or scope.status != "PASS":
        why = ("control unvalidated: replay-parity scope " + (f"{scope.scope} {scope.status}"
               + (" revoked" if scope.revoked else "") if scope is not None else "not read"))
    elif base["n_excess"] < MIN_N or base["n_days"] < MIN_DAYS:
        why = f"insufficient: {base['n_excess']} candidates over {base['n_days']} days (needs {MIN_N} / {MIN_DAYS})"
    elif unmatched is not None and unmatched > MAX_UNMATCHED:
        why = f"not readable: {unmatched:.0%} of candidates in cells with no control label"
    return ArmRead(**base, validated=why is None, reason=why or "")


def decide(r: ArmRead) -> ArmRead:
    """The decision (module docstring): RETIRE on a kill rule or the worse stream, else HOLD."""
    if r.kill is not None:
        return r.model_copy(update={"decision": "RETIRE", "reason": f"kill rule: {r.kill}"})
    if r.fixed_window:
        return r.model_copy(update={"decision": "HOLD", "reason": "fixed-window experiment: its kill rules only, "
                                                                  "never evidence"})
    if not r.validated:
        return r.model_copy(update={"decision": "HOLD"})
    if r.e_worse >= E_RETIRE:
        return r.model_copy(update={"decision": "RETIRE",
                                    "reason": f"worse than its replay twin: e {r.e_worse:.1f} >= {E_RETIRE:g}"})
    return r.model_copy(update={"decision": "HOLD", "reason": f"no decision: e worse {r.e_worse:.1f} < "
                                                              f"{E_RETIRE:g}; e better {r.e_better:.1f} reported, "
                                                              "not evidence"})


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
        parity: Mapping[str, ParityGap] | None = None, journal: pd.DataFrame | None = None,
        recompute: Recompute | None = None) -> list[ArmRead]:
    """Every decided arm of every broker in `hosts` (account -> broker, demo probe_only accounts only), read,
    decided, journalled (`arm_lifecycle_reads`) and a RETIRE applied (`exploration_arm_status`). `parity`: the
    scopes (default `parity_gaps`; an unreadable monitor holds every arm on evidence). `journal` (the hosts'
    `decisions` rows) and `recompute` (`engine_recompute`): a discovered arm's voids; without them it holds."""
    brokers = sorted(set(hosts.values()))
    if parity is None:
        try:
            parity = parity_gaps(db, brokers, before=pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1))
        except Exception as exc:                # noqa: BLE001 - fail closed: no evidence decision tonight
            log.error("replay-parity reads failed (%s): every twin unvalidated", type(exc).__name__)
            parity = {}
    out: list[ArmRead] = []
    for broker in brokers:
        accounts = {a for a, b in hosts.items() if b == broker}
        obs = read_obs(db, broker=broker, replay_version=REPLAY_V2, evidence="posterior", instrument=GOLD)
        for arm in sorted(arms, key=lambda a: a.id):
            if not decided(arm):
                continue
            block = _learning(arm)
            control = controls.get(arm.id) or (block.control if block is not None else None)
            out.append(decide(read_arm(db, obs, arm, broker=broker, control=control, accounts=accounts,
                                       parity=parity, cap=float(caps.get(arm.id, 0.0)), day=day, now=now,
                                       journal=journal, recompute=recompute)))
    record(db, out, now=now)
    return out


def record(db: Database, reads: list[ArmRead], *, now: pd.Timestamp) -> None:
    """Journal every read (one row per day, broker and arm; a re-run of the day replaces it) and apply each RETIRE
    through `exploration_arm_status` (INSERT OR IGNORE: a retirement is permanent and never re-dated)."""
    ns = int(pd.Timestamp(now).value)
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
    """The job's result: counts by decision and the arms retired."""
    retired = [f"{r.broker}:{r.arm_id}" for r in reads if r.decision == "RETIRE"]
    return {"arms": len(reads), "hold": len(reads) - len(retired), "retire": len(retired), "retired": retired,
            "validated": sum(r.validated for r in reads)}


__all__ = ["BRACKET_RETIRE_N", "E_PER_STREAM", "E_RETIRE", "FIXED_WINDOW_PREREGS", "Recompute", "discovered",
           "engine_recompute", "MAX_UNMATCHED", "MIN_DAYS", "MIN_N", "SETTLE_DEFAULT",
           "SETTLE_PAD", "VOID_UNLABELLED", "ArmRead", "ParityGap", "arm_hold", "day_terms", "settled_at", "decide", "decided", "fixed_window", "freeze", "parity_gaps", "read_arm",
           "reads_of", "record", "run", "summary", "twin_means"]
