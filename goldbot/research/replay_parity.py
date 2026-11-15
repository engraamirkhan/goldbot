"""The replay-parity monitor (performance-analyst spec 2026-11-15; docs/research/
prereg-amendment-2026-11-15-replay-only-controls.md section 3, as amended by the quant and code reviews of 0fb8fbff):
a replay-only control's twin is its r2 replay labels, so the replay must keep agreeing with what real fills do. A step
of the daily review (`daily_review.run_daily_review`), in the EVAL-2 S5 form.

The rule, as registered:
* population and gap are S5's: `exploration_data.fill_replay_pairs` (gold, replay r2, not void, not excluded for the
  cost cells), closed before the reviewed day's end. Gap per fill = fill_r - replay_r (label_fill_r - r2 sim_r, both
  over the candidate's own stop_dist, both net of the spread), the entry leg alone (`entry_fill_minus_replay_r`) for a
  broker in `costs.DEMO_EXIT_AT_LEVEL` (`parity_fills`);
* the gap test: one one-sided e-process per direction beyond +-TOL_R (`eval2.forward.e_execution`, one term per session
  day, running maximum), FIRE when either reaches E_PER_STREAM x the streams formed (2 per scope, Bonferroni), once the
  scope has >= MIN_FILLS fills over >= MIN_DAYS session days. Valid at every look, so reading it every day costs no
  false fires (quant MEDIUM 5: the fixed-window 95 % CI rule fired on 97 % of null runs under daily re-looks);
* agreement = the fill's exit leg against the label's barrier over the exits a label can have (`exit_leg`: stop
  incl. the engine's stop close, target, time); every other exit (trailed stop, weekend flat, broker or manual close,
  orphan) is left out of both counts and its share reported. FIRE below MIN_AGREEMENT in two consecutive
  non-overlapping AGREEMENT_WINDOW-fill windows spanning >= MIN_DAYS session days;
* scopes: broker x TF band (`eval2.forward.TF_BAND`); and pooled, every broker and band but the informational 1m-5m,
  which alerts only. The latest >= MIN_FILLS / MIN_DAYS window's mean gap and 95 % CI are reported, never judged.

On a fire (`record_fire`): an `Incident` of the review, the feedback-loop check for ALERT_DAYS, and for a broker x band
scope an automatic review_actions row (kind `replay_parity`). That row is the revocation (`revoked`): the S5 gap is
charged in the deciding cost (`catalogue.costs.deciding_cost` `s5_revoked`) and the L4 forward reads stop counting
(`eval2.runner.run_forward_read` `revoked`, read by `eval2.runner.forward_revocations`, which refuses when unreadable).
A revocation is never lifted here: only a dated amendment re-validates a scope, and nothing here re-enables a
broker-traded control.

The thresholds are the amendment's pre-registered numbers, module constants as S5's are, never a setting. Reads only,
plus the review_actions row; no order path.
"""
from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.db.connection import Database
from goldbot.execution.costs import DEMO_EXIT_AT_LEVEL
from goldbot.research import review_actions as ra
from goldbot.research.eval2.forward import INFORMATIONAL_BANDS, TF_BAND, _day_se, e_execution
from goldbot.research.exploration_data import EXIT_REASON_LEG, REPLAY_V2, fill_replay_pairs

TOL_R = 0.02                    # the S5 tolerance (forward.S5_TOL_R)
MIN_FILLS = 300
MIN_DAYS = 10
MIN_AGREEMENT = 0.90
AGREEMENT_WINDOW = 300          # fills per agreement window; two consecutive, non-overlapping
E_PER_STREAM = 20.0             # 1 / 0.05 per stream, times the streams formed (forward.E_KILL_EXEC's form, C3)
Z_TWO_SIDED_95 = 1.959963984540054
ALERT_DAYS = 3                  # the feedback-loop check fails this long after a fire (the revocation stays)
POOLED = "pooled"
KIND: Literal["replay_parity"] = "replay_parity"
RULE = "replay parity (prereg-amendment-2026-11-15-replay-only-controls.md section 3)"
# a fill's exit as a label barrier: S5's legs plus the engine's stop close (it closes at the stop the label reads).
# A runner's trailed stop is not the label's stop: left out with every exit a label cannot have
_LEG: dict[str, str] = {**EXIT_REASON_LEG, "engine_stop_close": "stop"}
COLUMNS = ["candidate_id", "broker", "tf", "session_day", "exit_ns", "gap_r", "exit_reason", "label_barrier"]

Status = Literal["PASS", "FIRE", "INSUFFICIENT"]


class ParityRead(FrozenRecord):
    """One scope's read: `scope` "<broker>:<band>" or "pooled" (alerts only: `revokes` False)."""
    scope: str
    broker: str
    band: str
    n: int
    n_days: int
    e_value: float = 1.0                    # the larger direction's running maximum
    e_threshold: float = E_PER_STREAM
    mean_gap_r: float | None = None         # the latest window's, reported only
    ci_lo_r: float | None = None
    ci_hi_r: float | None = None
    agreement: float | None = None          # the latest AGREEMENT_WINDOW label-exit fills
    agreement_prev: float | None = None     # the AGREEMENT_WINDOW before them
    excluded_share: float | None = None     # fills whose exit no label can have, out of the agreement counts
    status: Status
    revokes: bool = True
    reason: str


def exit_leg(reason: str | None) -> str | None:
    """The label barrier a fill's exit reason reads as, or None when no label can have it (left out)."""
    return _LEG.get(str(reason)) if reason is not None else None


def parity_fills(db: Database, brokers: Collection[str], *, before: pd.Timestamp | None = None) -> pd.DataFrame:
    """The population (module docstring) of `brokers`, in exit order (COLUMNS). `before`: only fills closed before
    it (the review's day end: point in time)."""
    frames = []
    for broker in sorted(brokers):
        p = fill_replay_pairs(db, broker=broker, replay_version=REPLAY_V2)
        if p.empty:
            continue
        gap = p["entry_fill_minus_replay_r"] if broker in DEMO_EXIT_AT_LEVEL else p["fill_r"] - p["replay_r"]
        exit_ns = pd.to_datetime(p["exit_utc"], utc=True).astype("int64")
        frames.append(pd.DataFrame({"candidate_id": p["candidate_id"], "broker": p["broker"], "tf": p["tf"],
                                    "session_day": p["session_day"], "exit_ns": exit_ns,
                                    "gap_r": gap.astype(float), "exit_reason": p["exit_reason"],
                                    "label_barrier": p["label_barrier"]}))
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    if before is not None:
        out = out[out["exit_ns"] < int(before.value)]
    out = out[np.isfinite(out["gap_r"].to_numpy(dtype=float))]
    return out.sort_values(["exit_ns", "candidate_id"], kind="stable").reset_index(drop=True)[COLUMNS]


def gap_evidence(gap: np.ndarray, day: np.ndarray) -> float:
    """The larger of the two one-sided e-processes beyond +-TOL_R (fill better / worse than its replay), each a
    running maximum with one term per session day (`eval2.forward.e_execution`)."""
    g = np.asarray(gap, dtype=float)
    return max(e_execution(g, tol_r=TOL_R, day=day), e_execution(-g, tol_r=TOL_R, day=day))


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


def read_scope(f: pd.DataFrame, *, scope: str, broker: str, band: str, streams: int = 2,
               revokes: bool = True) -> ParityRead:
    """One scope's read of its rows `f` (in exit order) by the module docstring's rule; `streams`: the e-processes
    formed in this run (the Bonferroni divisor)."""
    gap = f["gap_r"].to_numpy(dtype=float)
    day = f["session_day"].astype(str).to_numpy()
    n, n_days = len(gap), len(set(day.tolist()))
    legs = f["exit_reason"].map(exit_leg)
    eligible = legs.notna().to_numpy()
    excluded = float(1 - eligible.mean()) if n else None
    agree = (legs[eligible].astype(str).to_numpy() == f["label_barrier"].astype(str).to_numpy()[eligible])
    a_day = day[eligible]
    w = AGREEMENT_WINDOW
    a_now = float(agree[-w:].mean()) if len(agree) >= w else None
    a_prev = float(agree[-2 * w:-w].mean()) if len(agree) >= 2 * w else None
    split = (a_now is not None and a_prev is not None and a_now < MIN_AGREEMENT and a_prev < MIN_AGREEMENT
             and len(set(a_day[-2 * w:].tolist())) >= MIN_DAYS)
    threshold = E_PER_STREAM * streams
    e = gap_evidence(gap, day) if n else 1.0
    enough = n >= MIN_FILLS and n_days >= MIN_DAYS
    off = enough and e >= threshold
    note = "; entry leg only (demo exits fill at the level)" if broker in DEMO_EXIT_AT_LEVEL else ""
    parts = [f"{n} fills over {n_days} session days", f"gap e-value {e:.1f} (fires at {threshold:.0f})"]
    mean = lo = hi = None
    if (start := _window(day)) is not None:
        x, d = gap[start:], day[start:]
        mean, se = float(x.mean()), _day_se(x, d)
        lo, hi = mean - Z_TWO_SIDED_95 * se, mean + Z_TWO_SIDED_95 * se
        parts.append(f"latest window mean gap {mean:+.4f} R, 95 % CI [{lo:+.4f}, {hi:+.4f}] (reported, not judged)")
    if a_now is not None:
        parts.append(f"barrier agreement {a_now:.1%}" + (f" (previous window {a_prev:.1%})" if a_prev is not None else ""))
    if excluded:
        parts.append(f"{excluded:.1%} of fills closed by an exit no label has (left out of agreement)")
    status: Status = "FIRE" if off or split else "PASS" if enough else "INSUFFICIENT"
    if status == "INSUFFICIENT":
        parts.insert(0, f"insufficient (needs {MIN_FILLS} fills / {MIN_DAYS} days)")
    return ParityRead(scope=scope, broker=broker, band=band, n=n, n_days=n_days, e_value=e, e_threshold=threshold,
                      mean_gap_r=mean, ci_lo_r=lo, ci_hi_r=hi, agreement=a_now, agreement_prev=a_prev,
                      excluded_share=excluded, status=status, revokes=revokes, reason="; ".join(parts) + note)


def reads(fills: pd.DataFrame) -> list[ParityRead]:
    """Every broker x TF band with a fill, then the pooled read (no informational 1m-5m band; alerts only)."""
    band = fills["tf"].map(lambda t: TF_BAND.get(str(t), str(t))) if not fills.empty else pd.Series(dtype=str)
    groups = list(fills.groupby([fills["broker"], band], sort=True)) if not fills.empty else []
    pooled = fills[~band.isin(INFORMATIONAL_BANDS)] if not fills.empty else fills
    streams = 2 * (len(groups) + 1)
    out = [read_scope(g, scope=f"{b}:{bd}", broker=str(b), band=str(bd), streams=streams) for (b, bd), g in groups]
    out.append(read_scope(pooled, scope=POOLED, broker=POOLED, band="all", streams=streams, revokes=False))
    return out


def _week(day: str) -> str:
    """The Saturday that closes `day`'s review week (review_actions.week: "the review's Saturday")."""
    d = pd.Timestamp(day)
    return f"{d + pd.Timedelta(days=(5 - d.dayofweek) % 7):%Y-%m-%d}"


def record_fire(db: Database, fired: Sequence[ParityRead], *, day: str, now: pd.Timestamp) -> list[str]:
    """One automatic review_actions row per fired scope that revokes (kind `replay_parity`, id stable over the scope,
    so a scope fires once: the revocation stands until a dated amendment). A pooled fire is not recorded (alerts
    only). Returns the scopes recorded now."""
    ns = int(pd.Timestamp(now).value)
    new: list[str] = []
    with db.write() as conn:
        for r in fired:
            if not r.revokes or r.status != "FIRE":
                continue
            a = ra.ReviewAction(action_id=ra.action_id(KIND, r.scope), week=_week(day), kind=KIND, subject=r.scope,
                                title=f"replay parity fired: {r.scope} twin reads unvalidated",
                                detail=r.reason, automatic=True, source="daily_review", broker=r.broker, rule=RULE)
            cur = conn.execute("INSERT OR IGNORE INTO review_actions (action_id, week, rank, kind, automatic, "
                               "recorded_ns, body) VALUES (?, ?, 0, ?, 1, ?, ?)",
                               (a.action_id, a.week, KIND, ns, a.model_dump_json()))
            if cur.rowcount:
                new.append(r.scope)
    return new


def fires(db: Database) -> list[tuple[str, pd.Timestamp]]:
    """Every recorded fire: (scope, when), oldest first. Raises when the table cannot be read (fail closed: the
    caller refuses, `eval2.runner.forward_revocations`)."""
    with db.connection() as conn:
        rows = conn.execute("SELECT body, recorded_ns FROM review_actions WHERE kind = ? ORDER BY recorded_ns",
                            (KIND,)).fetchall()
    return [(str(json.loads(b)["subject"]), pd.Timestamp(int(ns), tz="UTC")) for b, ns in rows]


def revoked(db: Database) -> frozenset[str]:
    """The scopes whose replay-twin reads are unvalidated (`eval2.forward.revoked_for` reads them)."""
    return frozenset(s for s, _ in fires(db))


__all__ = ["ALERT_DAYS", "E_PER_STREAM", "KIND", "MIN_AGREEMENT", "MIN_DAYS", "MIN_FILLS", "POOLED", "TOL_R",
           "ParityRead", "exit_leg", "fires", "gap_evidence", "parity_fills", "read_scope", "reads", "record_fire",
           "revoked"]
