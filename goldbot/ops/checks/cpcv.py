"""The CPCV evidence health check (M16; ADR 0083 as amended: CPCV moved from the retired quarterly job to the
promotion gate, research/cpcv_gate.py).

Design: CPCV/PBO evidence ("6 groups, 2 test", PBO against the family's other trials) stands before a gate-passing
trial is promoted. The gate produces missing evidence on demand at promotion; this check makes a gap visible before
then, so a promotion is never held back by surprise and a failed CPCV run is never silent:

  warn   a gate-passing trial (`cpcv_gate.gate_passing`) whose evidence for the current UTC quarter is missing, failed,
         ran on another feature version, or has no PBO although the family has other walk-forward trials
         (`cpcv_gate.evidence_gap`). Fix: `scripts/research_pass.py --cpcv <trial#> [<peer#> ...]` on the host
         whose registry promotes (the evidence sidecar is local, ADR 0002), or let the next tournament run it.
  ok     every gate-passing trial has evidence; a trial the evidence vetoes (ADR 0003: PBO > 0.5 or most paths
         negative) has its evidence, so it is named in the reason, not flagged: the veto is the gate working.

An unreadable registry or evidence file is "can't check", a WARN, never a pass. Reads only: no CPCV run, no write.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from goldbot.ops.checks._base import Check
from goldbot.ops.checks.registry import BLOCK_TAIL, register_check

if TYPE_CHECKING:
    from goldbot.ops.health import HealthContext

NAME = "cpcv_evidence"
MAX_NAMED = 5                  # trials named in the reason; the rest are counted


def _registry_path(state_dir: Path, registry: str) -> Path:
    """`research.registry` as the jobs resolve it: absolute, or relative to the state directory's parent."""
    p = Path(registry)
    return p if p.is_absolute() else Path(state_dir).parent / p


def _named(items: list[str]) -> str:
    more = len(items) - MAX_NAMED
    return "; ".join(items[:MAX_NAMED]) + (f"; and {more} more" if more > 0 else "")


@register_check("global", block=BLOCK_TAIL, pos=180)
def check_cpcv_evidence(ctx: HealthContext) -> Check:
    """M16 at promotion: WARN naming each gate-passing trial without usable CPCV evidence this quarter (module
    docstring)."""
    from goldbot.research import cpcv
    from goldbot.research.cpcv_gate import evidence_gap, evidence_veto, gate_passing, quarter_evidence
    from goldbot.research.registry import TrialRegistry, quarter_of
    from goldbot.research.registry_sync import read_rows
    if ctx.settings is None:
        return Check(name=NAME, status="warn", reason="can't check: settings do not load")
    path = _registry_path(ctx.state_dir, ctx.settings.research.registry)
    if not path.exists():
        return Check(name=NAME, status="ok", reason="no trial registry")
    reg = TrialRegistry(path)
    try:
        rows = read_rows(path)
        evidence = reg.evidence(kind=cpcv.EVIDENCE_KIND)
    except (OSError, ValueError) as exc:
        return Check(name=NAME, status="warn", reason=f"can't check: {path.name} unreadable ({type(exc).__name__})")
    passing = gate_passing(rows)
    if not passing:
        return Check(name=NAME, status="ok", reason="no gate-passing trial: no CPCV evidence needed")
    q = quarter_of(ctx.now.to_pydatetime())
    gaps, vetoes = [], []
    for row in passing:
        ev = quarter_evidence(evidence, row, q)
        gap = evidence_gap(row, ev, rows, q)
        if gap is not None:
            gaps.append(f"{gap} ({row.get('family')})")
        elif ev is not None and (veto := evidence_veto(row, ev)) is not None:
            vetoes.append(veto)
    vetoed = f"; vetoed: {_named(vetoes)}" if vetoes else ""
    if gaps:
        return Check(name=NAME, status="warn",
                     reason=f"{len(gaps)} of {len(passing)} gate-passing trial(s) without usable CPCV evidence for "
                            f"{q}: {_named(gaps)}. Run scripts/research_pass.py --cpcv <trial#> [<peer#> ...] "
                            f"before promotion{vetoed}")
    return Check(name=NAME, status="ok",
                 reason=f"{len(passing)} gate-passing trial(s), each with CPCV evidence for {q}{vetoed}")
