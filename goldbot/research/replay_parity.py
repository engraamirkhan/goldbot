"""The replay-parity monitor (performance-analyst spec 2026-11-15; docs/research/
prereg-amendment-2026-11-15-replay-only-controls.md section 3): a replay-only control's twin is its r2 replay labels,
so the replay must keep agreeing with what real fills do. A step of the daily review (`daily_review.run_daily_review`),
in the EVAL-2 S5 form (`eval2.forward.s5_read`: day-clustered, per broker and TF band).

The rule, as registered:
* population: closed gold exploration_trades with a fill, not in evidence_exclusions, joined on candidate_id to their r2
  label (`exploration_replay`, replay_version r2, not void) (`parity_fills`);
* gap per fill = side x (exit - fill) / stop_usd - label_mid_r (R, + = the fill did better than the replay's gross
  path); agreement = the fill's exit reason against the label's barrier, time / weekend / broker exits counting as time
  (`exit_barrier`);
* window: the latest fills reaching >= MIN_FILLS (300) over >= MIN_DAYS (10) session days, per broker x TF band
  (`eval2.forward.TF_BAND`) and pooled (every broker and band);
* FIRE when the day-clustered 95 % CI of the mean gap lies entirely outside [-TOL_R, +TOL_R] (0.02 R), or barrier
  agreement is below MIN_AGREEMENT (90 %) in two consecutive non-overlapping 300-fill windows.

On a fire (`record_fire`): a health alert (an `Incident` of the review, and the feedback-loop check for ALERT_DAYS), an
automatic review_actions row (kind `replay_parity`) and the scope's replay-twin reads marked unvalidated (`revoked`):
the EVAL-2 S5 gate that already does this, so the S5 gap is charged in the deciding cost (`catalogue.costs.deciding_cost`
`s5_revoked`) and the L4 forward reads stop counting (`eval2.forward.apply_s5` `revoked`). A revocation is never
lifted here: only a dated amendment re-validates a scope, and nothing here ever re-enables a broker-traded control.

The thresholds are the amendment's pre-registered numbers, module constants as S5's are (`forward.S5_TOL_R`), never a
setting. Reads only, plus the review_actions row; no order path.
"""
from __future__ import annotations

import json
import math
from collections.abc import Collection, Sequence
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.db.connection import Database
from goldbot.execution.costs import DEMO_EXIT_AT_LEVEL
from goldbot.instruments import GOLD
from goldbot.research import review_actions as ra
from goldbot.research.eval2.forward import TF_BAND, _day_se
from goldbot.research.exploration_data import REPLAY_V2, session_day

TOL_R = 0.02                    # the S5 tolerance (forward.S5_TOL_R)
MIN_FILLS = 300
MIN_DAYS = 10
MIN_AGREEMENT = 0.90
AGREEMENT_WINDOW = 300          # fills per agreement window; two consecutive, non-overlapping
Z_TWO_SIDED_95 = 1.959963984540054
ALERT_DAYS = 3                  # the feedback-loop check fails this long after a fire (the revocation stays)
POOLED = "pooled"
KIND: Literal["replay_parity"] = "replay_parity"
RULE = "replay parity (prereg-amendment-2026-11-15-replay-only-controls.md section 3)"
# a fill's exit reason as a label barrier: the stops (resting, the engine's stop close, a runner's trailed stop) and the
# target; every other exit (the hold, the Friday cut, a broker or manual close) is time
_BARRIER = {"stop": "stop", "engine_stop_close": "stop", "trail_close": "stop", "target": "target"}

Status = Literal["PASS", "FIRE", "INSUFFICIENT"]


class ParityRead(FrozenRecord):
    """One scope's read: `scope` "<broker>:<band>" or "pooled"."""
    scope: str
    broker: str
    band: str
    n: int
    n_days: int
    mean_gap_r: float | None = None
    ci_lo_r: float | None = None
    ci_hi_r: float | None = None
    agreement: float | None = None          # the latest AGREEMENT_WINDOW fills
    agreement_prev: float | None = None     # the AGREEMENT_WINDOW fills before them
    status: Status
    reason: str


def exit_barrier(reason: str | None) -> str:
    return _BARRIER.get(str(reason), "time")


def parity_fills(db: Database, brokers: Collection[str], *, before: pd.Timestamp | None = None) -> pd.DataFrame:
    """The population (module docstring) of `brokers`, in exit order: candidate_id, broker, tf, session_day, exit_ns,
    gap_r, exit_barrier, label_barrier. `before`: only fills closed before it (the review's day end: point in time)."""
    cols = ["candidate_id", "broker", "tf", "session_day", "exit_ns", "gap_r", "exit_barrier", "label_barrier"]
    if not brokers:
        return pd.DataFrame(columns=cols)
    marks = ",".join("?" * len(brokers))
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT t.client_order_id, o.broker, o.tf, o.decision_ns, t.exit_ns, t.body, r.mid_r, r.barrier "
            "FROM exploration_trades t "
            "JOIN exploration_obs o ON o.candidate_id = t.client_order_id "
            "JOIN exploration_replay r ON r.candidate_id = t.client_order_id AND r.replay_version = ? "
            "LEFT JOIN exploration_label_void v ON v.candidate_id = t.client_order_id AND v.replay_version = ? "
            "LEFT JOIN evidence_exclusions x ON x.client_order_id = t.client_order_id "
            f"WHERE t.closed = 1 AND t.position_id IS NOT NULL AND t.exit_ns IS NOT NULL AND o.broker IN ({marks}) "
            "AND v.candidate_id IS NULL AND x.client_order_id IS NULL AND t.exit_ns < ? ORDER BY t.exit_ns, 1",
            (REPLAY_V2, REPLAY_V2, *sorted(brokers),
             int(before.value) if before is not None else 2**63 - 1)).fetchall()
    out = []
    for cid, broker, tf, decision_ns, exit_ns, body, mid_r, barrier in rows:
        b = json.loads(body)
        if str(b.get("symbol") or GOLD) != GOLD:
            continue
        try:
            side, fill, exit_p, stop = int(b["side"]), float(b["fill_price"]), float(b["exit_price"]), \
                float(b["stop_usd"])
        except (KeyError, TypeError, ValueError):
            continue
        gap = side * (exit_p - fill) / stop - float(mid_r) if stop > 0 else math.nan
        if not math.isfinite(gap):
            continue
        out.append((str(cid), str(broker), str(tf), session_day(pd.Timestamp(int(decision_ns), tz="UTC")),
                    int(exit_ns), gap, exit_barrier(b.get("exit_reason")), str(barrier)))
    return pd.DataFrame(out, columns=cols)


def _window(day: np.ndarray) -> int | None:
    """The start index of the shortest suffix with >= MIN_FILLS fills over >= MIN_DAYS session days, or None."""
    n = len(day)
    if n < MIN_FILLS:
        return None
    seen: set[str] = set(day[n - MIN_FILLS:].tolist())
    start = n - MIN_FILLS
    while len(seen) < MIN_DAYS and start > 0:
        start -= 1
        seen.add(str(day[start]))
    return start if len(seen) >= MIN_DAYS else None


def read_scope(f: pd.DataFrame, *, scope: str, broker: str, band: str) -> ParityRead:
    """One scope's read of its rows `f` (in exit order) by the module docstring's rule."""
    gap = f["gap_r"].to_numpy(dtype=float)
    day = f["session_day"].astype(str).to_numpy()
    agree = (f["exit_barrier"].astype(str) == f["label_barrier"].astype(str)).to_numpy()
    w = AGREEMENT_WINDOW
    a_now = float(agree[-w:].mean()) if len(agree) >= w else None
    a_prev = float(agree[-2 * w:-w].mean()) if len(agree) >= 2 * w else None
    split = a_now is not None and a_prev is not None and a_now < MIN_AGREEMENT and a_prev < MIN_AGREEMENT
    note = "; exit leg at the level on demo (gap read optimistic)" if broker in DEMO_EXIT_AT_LEVEL else ""
    start = _window(day)
    if start is None:
        status: Status = "FIRE" if split else "INSUFFICIENT"
        reason = (f"insufficient: {len(gap)} fills over {len(set(day.tolist()))} session days (needs {MIN_FILLS} / "
                  f"{MIN_DAYS})" + (f"; barrier agreement {a_now:.1%} and {a_prev:.1%} < {MIN_AGREEMENT:.0%}"
                                    if split else ""))
        return ParityRead(scope=scope, broker=broker, band=band, n=len(gap), n_days=len(set(day.tolist())),
                          agreement=a_now, agreement_prev=a_prev, status=status, reason=reason + note)
    x, d = gap[start:], day[start:]
    mean, se = float(x.mean()), _day_se(x, d)
    lo, hi = mean - Z_TWO_SIDED_95 * se, mean + Z_TWO_SIDED_95 * se
    off = lo > TOL_R or hi < -TOL_R
    status = "FIRE" if off or split else "PASS"
    parts = [f"mean gap {mean:+.4f} R, 95 % CI [{lo:+.4f}, {hi:+.4f}] "
             f"{'outside' if off else 'not outside'} +-{TOL_R} R on {len(x)} fills / {len(set(d.tolist()))} days"]
    if a_now is not None:
        parts.append(f"barrier agreement {a_now:.1%}" + (f" (previous window {a_prev:.1%})" if a_prev is not None else ""))
    return ParityRead(scope=scope, broker=broker, band=band, n=len(x), n_days=len(set(d.tolist())), mean_gap_r=mean,
                      ci_lo_r=lo, ci_hi_r=hi, agreement=a_now, agreement_prev=a_prev, status=status,
                      reason="; ".join(parts) + note)


def reads(fills: pd.DataFrame) -> list[ParityRead]:
    """Every broker x TF band with a fill, then the pooled read (every broker and band together)."""
    out: list[ParityRead] = []
    if fills.empty:
        return [read_scope(fills, scope=POOLED, broker=POOLED, band="all")]
    band = fills["tf"].map(lambda t: TF_BAND.get(str(t), str(t)))
    for (broker, b), g in fills.groupby([fills["broker"], band], sort=True):
        out.append(read_scope(g, scope=f"{broker}:{b}", broker=str(broker), band=str(b)))
    out.append(read_scope(fills, scope=POOLED, broker=POOLED, band="all"))
    return out


def _week(day: str) -> str:
    """The Saturday that closes `day`'s review week (review_actions.week: "the review's Saturday")."""
    d = pd.Timestamp(day)
    return f"{d + pd.Timedelta(days=(5 - d.dayofweek) % 7):%Y-%m-%d}"


def record_fire(db: Database, fired: Sequence[ParityRead], *, day: str, now: pd.Timestamp) -> list[str]:
    """One automatic review_actions row per fired scope (kind `replay_parity`, id stable over the scope, so a scope
    fires once: the revocation stands until a dated amendment). Returns the scopes recorded now."""
    ns = int(pd.Timestamp(now).value)
    new: list[str] = []
    with db.write() as conn:
        for r in fired:
            a = ra.ReviewAction(action_id=ra.action_id(KIND, r.scope), week=_week(day), kind=KIND, subject=r.scope,
                                title=f"replay parity fired: {r.scope} twin reads unvalidated",
                                detail=r.reason, automatic=True, source="daily_review",
                                broker=None if r.broker == POOLED else r.broker, rule=RULE)
            cur = conn.execute("INSERT OR IGNORE INTO review_actions (action_id, week, rank, kind, automatic, "
                               "recorded_ns, body) VALUES (?, ?, 0, ?, 1, ?, ?)",
                               (a.action_id, a.week, KIND, ns, a.model_dump_json()))
            if cur.rowcount:
                new.append(r.scope)
    return new


def fires(db: Database) -> list[tuple[str, pd.Timestamp]]:
    """Every recorded fire: (scope, when), oldest first. A missing table reads as none."""
    try:
        with db.connection() as conn:
            rows = conn.execute("SELECT body, recorded_ns FROM review_actions WHERE kind = ? ORDER BY recorded_ns",
                                (KIND,)).fetchall()
    except Exception as exc:                 # noqa: BLE001 - a release before migration 30 has no table
        if "no such table" in str(exc):
            return []
        raise
    return [(str(json.loads(b)["subject"]), pd.Timestamp(int(ns), tz="UTC")) for b, ns in rows]


def revoked(db: Database) -> frozenset[str]:
    """The scopes whose replay-twin reads are unvalidated (`eval2.forward.revoked_for` reads them)."""
    return frozenset(s for s, _ in fires(db))


__all__ = ["ALERT_DAYS", "KIND", "MIN_AGREEMENT", "MIN_DAYS", "MIN_FILLS", "POOLED", "TOL_R", "ParityRead",
           "exit_barrier", "fires", "parity_fills", "read_scope", "reads", "record_fire", "revoked"]
