"""CPCV and PBO at promotion (design, Validation: "Combinatorial purged CV (6 groups, 2 test, 15 paths)" with PBO
against the family's other trials before a gate-passing trial is promoted; row M16; ADR 0083 as amended).

The quarterly `cpcv_quarterly` job retired (ADR 0083) because no trial passes the gates; the evidence it produced is
now demanded where it matters, at promotion. A gate-passing trial (`registry.promotion_refusal` is None) is promoted
only when it has CPCV evidence (`TrialRegistry.evidence`, kind "cpcv") stamped in the promotion's quarter that:
* ran: the payload has at least one backtest path with a finite mean R (not an `error` or a `skipped` result, not
  an empty path list);
* ran on the trial's configuration (config hash) and stated feature version (an unstated one does not count);
* is not vetoed: `cpcv.verdict`, recomputed here rather than read from the payload, flags it fragile when PBO >
  `cpcv.PBO_FRAGILE` (0.5) or more than half its paths have negative mean R (ADR 0003: CPCV and PBO can only veto);
* computed PBO when the family has other walk-forward trials on the trial's timeframe (`peers`): without PBO the
  selection among them is unmeasured. A trial with no such peer has nothing to compute PBO against; its paths
  verdict alone decides.

Missing evidence is produced on demand (`ensure_evidence`): CPCV of the trial compared with its most recent peers
(at most CPCV_MAX_CONFIGS configurations, itself included; PBO a lower bound against `cpcv.selection_set`), attached
to the trial and only to it. CPCV is not a trial: no budget slot, no deflated-Sharpe count (research/cpcv.py). A run
that raises or skips the trial attaches nothing and refuses; the next promotion attempt runs it again.

Evidence lives in the registry's local sidecar (registry_sync does not carry it; ADR 0002), so a promotion reads the
evidence of the host that promotes. Health check `cpcv_evidence` (ops/checks/cpcv.py) lists gate-passing trials
without clean evidence for the current quarter.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Callable

from goldbot.research import cpcv
from goldbot.research.registry import TrialRegistry, quarter_of, row_hash
from goldbot.research.registry import promotion_refusal as research_refusal

log = logging.getLogger(__name__)

CPCV_MAX_CONFIGS = 8     # PBO compares a promoted trial with at most this many of its family's trials (itself in)

# (configurations compared, the selection set's trial numbers, decision timeframe) -> cpcv.cpcv_trials' result
Runner = Callable[[list[dict[str, Any]], list[int], str], dict[str, Any]]


def _timeframe(row: dict[str, Any]) -> str | None:
    """The trial's decision timeframe when CPCV can re-evaluate it (`cpcv.cpcv_eligible`), else None."""
    try:
        return cpcv.trial_timeframe(row) if cpcv.cpcv_eligible(row) is None else None
    except (KeyError, ValueError, TypeError):
        return None


def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def gate_passing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Walk-forward trials CPCV can re-evaluate whose research pass counts for a promotion: the trials this gate and
    the health check hold to CPCV evidence."""
    return [r for r in rows if _timeframe(r) is not None and research_refusal(r, rows) is None]


def peers(row: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The family's walk-forward trials of other configurations on the trial's timeframe, in registry order: what PBO
    compares it with. A rerun of the trial's own configuration (same config hash) was no alternative to select among."""
    tf, h = _timeframe(row), row_hash(row)
    return [r for r in rows if r.get("family") == row.get("family") and row_hash(r) != h and _timeframe(r) == tf]


def compare_set(row: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The trial and its most recent peers, at most CPCV_MAX_CONFIGS in all."""
    return [row, *peers(row, rows)[-(CPCV_MAX_CONFIGS - 1):]]


def quarter_evidence(evidence: list[dict[str, Any]], row: dict[str, Any], quarter: str) -> dict[str, Any] | None:
    """The latest CPCV evidence of the trial stamped in `quarter` for its configuration, or None."""
    mine = [e for e in evidence if e.get("kind") == cpcv.EVIDENCE_KIND and e.get("trial") == int(row["trial"])
            and e.get("quarter") == quarter and (e.get("config_hash") in (None, row_hash(row)))]
    return mine[-1] if mine else None


def evidence_gap(row: dict[str, Any], ev: dict[str, Any] | None, rows: list[dict[str, Any]],
                 quarter: str) -> str | None:
    """Why the trial has no usable CPCV evidence for `quarter` (missing, failed, another feature version, or no PBO
    although peers exist), or None when it has: what the health check flags."""
    t = int(row["trial"])
    if ev is None:
        return f"trial #{t} has no CPCV evidence for {quarter}"
    payload = ev.get("payload") or {}
    if "paths" not in payload:
        return f"CPCV failed for trial #{t}: {payload.get('error') or payload.get('skipped') or 'no backtest paths'}"
    # quant review: no finite path leaves the negative-paths veto nothing to read, so it is a gap, never a pass
    finite = [p for p in payload.get("paths") or [] if _finite(p.get("mean_r"))]
    if not finite:
        return f"CPCV for trial #{t} has no backtest path with a finite mean R"
    fv, ran = row.get("feature_version"), payload.get("feature_version")
    if not ran or fv != ran:       # an unstated version cannot show the run used the trial's inputs
        return f"trial #{t}'s CPCV ran on feature version {ran or 'unstated'}, the trial on {fv or 'unstated'}"
    if (payload.get("pbo") or {}).get("pbo") is None:
        others = [int(r["trial"]) for r in peers(row, rows)]
        if others:
            return (f"trial #{t}'s CPCV evidence has no PBO although the family has other walk-forward trials on its "
                    f"timeframe ({', '.join(f'#{o}' for o in others)})")
    return None


def evidence_veto(row: dict[str, Any], ev: dict[str, Any]) -> str | None:
    """ADR 0003's veto on the evidence, `cpcv.verdict` recomputed from the payload (a stored verdict is not trusted)."""
    payload = ev.get("payload") or {}
    v = cpcv.verdict(payload, payload.get("pbo"))
    return f"CPCV veto for trial #{int(row['trial'])} (ADR 0003): {'; '.join(v['reasons'])}" if v["fragile"] else None


def evidence_refusal(row: dict[str, Any], ev: dict[str, Any] | None, rows: list[dict[str, Any]],
                     quarter: str) -> str | None:
    """Why `ev` does not clear the trial for promotion (module docstring), or None when it does."""
    gap = evidence_gap(row, ev, rows, quarter)
    if gap is not None or ev is None:
        return gap
    return evidence_veto(row, ev)


def ensure_evidence(registry: TrialRegistry, row: dict[str, Any], rows: list[dict[str, Any]], run: Runner,
                    now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """This quarter's CPCV evidence of the trial, run with `run` and attached to the trial when there is none.
    Returns (evidence, None), or (None, why) when the run raised or skipped the trial (nothing attached)."""
    q = quarter_of(now)
    ev = quarter_evidence(registry.evidence(int(row["trial"]), cpcv.EVIDENCE_KIND), row, q)
    if ev is not None:
        return ev, None
    t, tf = int(row["trial"]), _timeframe(row)
    if tf is None:
        return None, f"trial #{t}: {cpcv.cpcv_eligible(row) or 'no decision timeframe'}"
    try:
        compare = compare_set(row, rows)
        res = run(compare, cpcv.selection_set(rows, {str(row["family"])}, tf), tf)
        mine = res["per_trial"][t]
        if "paths" not in mine:
            return None, f"CPCV failed for trial #{t}: {mine.get('error') or mine.get('skipped') or 'no backtest paths'}"
        ev = registry.attach_evidence(t, cpcv.EVIDENCE_KIND, cpcv.evidence_payload(mine, res.get("pbo"),
                                                                                    f"promotion {q}"), now=now)
    except Exception as exc:                # a failed run is a refusal, never a promotion and never a crash
        log.exception("cpcv at promotion: trial %s", t)
        return None, f"CPCV failed for trial #{t}: {type(exc).__name__}: {exc}"
    return ev, None


def promotion_refusal(registry: TrialRegistry, row: dict[str, Any], rows: list[dict[str, Any]], *, now: datetime,
                      run: Runner | None = None) -> str | None:
    """Why the gate-passing trial `row` may not be promoted on its CPCV evidence, or None when it may. With `run`,
    missing evidence for the quarter of `now` is produced on demand first (`ensure_evidence`); without, it refuses."""
    q = quarter_of(now)
    if run is None:
        ev = quarter_evidence(registry.evidence(int(row["trial"]), cpcv.EVIDENCE_KIND), row, q)
    else:
        ev, why = ensure_evidence(registry, row, rows, run, now)
        if why is not None:
            return why
    return evidence_refusal(row, ev, rows, q)
