"""Primary-signal screen (proposal P4): a rule enters model research only if it has an edge of its own.

Meta-labelling raises the precision of a primary signal; it cannot create an edge the rule lacks (López de Prado
2018, ch. 3). Before any model is fitted, the rule's own expectancy is measured over the walk-forward research window
(every labelled candidate that exits before the holdout starts; the holdout is never seen) on mid prices, one position
at a time, with exactly the labels the walk-forward would use:

    pass  <=>  gross mean R > 0,  t-stat of the gross mean R >= MIN_T (2.0),  at least MIN_EVENTS (1,000) events.

Net expectancy (spread + slippage + commission + swap) is reported next to it but does not decide: a positive gross edge is
what a meta-model can filter towards a positive net one.

The screen is a look at the data that selects rules, so each screened configuration is one trial in the registry
(charged to the quarter's budget and counted by the deflated Sharpe), whether or not it passes. When it passes, the
walk-forward that follows is the same trial. `scripts/research_pass.py` refuses to fit a model for a configuration
that fails unless `--skip-screen` is given, and records the screen in the registry row either way.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from goldbot.research.pipeline import Prepared, rule_only

MIN_EVENTS = 1000
MIN_T = 2.0
RULE = f"rule-only gross mean R > 0 with t >= {MIN_T} on >= {MIN_EVENTS:,} events (research window, holdout excluded)"


def screen_verdict(rule: dict[str, Any]) -> dict[str, Any]:
    """The screen's decision on a rule-only result ({"gross": expectancy, "net": expectancy})."""
    g, n_ = rule.get("gross") or {}, rule.get("net") or {}
    n, mean_r, t = int(g.get("n", 0)), float(g.get("mean_r", 0.0)), float(g.get("t_stat", 0.0))
    checks = [
        {"name": "events", "passed": n >= MIN_EVENTS, "detail": f"{n:,} events (need {MIN_EVENTS:,})"},
        {"name": "gross_mean_r", "passed": n > 0 and mean_r > 0, "detail": f"gross mean R {mean_r:+.3f} (need > 0)"},
        {"name": "gross_t", "passed": t >= MIN_T, "detail": f"t-stat {t:.2f} (need >= {MIN_T})"},
    ]
    return {"passed": all(c["passed"] for c in checks), "rule": RULE, "checks": checks, "n": n, "gross_mean_r": mean_r,
            "gross_t": t, "net_mean_r": n_.get("mean_r"), "net_t": n_.get("t_stat"), "rule_only": rule}


def screen(prep: Prepared | list[Prepared]) -> dict[str, Any]:
    """Screen one prepared configuration, or the union of several (a pooled trial: every member's candidates)."""
    preps = prep if isinstance(prep, list) else [prep]
    gross = [p.gross for p in preps if not p.gross.empty]
    net = [p.labels for p in preps if not p.labels.empty]
    g = pd.concat(gross, ignore_index=True) if gross else pd.DataFrame()
    n = pd.concat(net, ignore_index=True) if net else pd.DataFrame()
    out = screen_verdict(rule_only(g, n))
    if len(preps) > 1:
        out["members"] = {p.spec.family: screen_verdict(rule_only(p.gross, p.labels)) for p in preps}
    return out


def screen_lines(s: dict[str, Any] | None, skipped: bool = False) -> list[str]:
    """Markdown for a report."""
    if not s:
        return []
    head = "**PASS**" if s["passed"] else ("**FAIL** (skipped with --skip-screen)" if skipped else "**FAIL**: screen failed, no model fitted")
    out = [f"### Primary-signal screen: {head}", "", f"- rule: {s['rule']}"]
    out += [f"- {'pass' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}" for c in s["checks"]]
    net = s.get("net_mean_r")
    if net is not None:
        out.append(f"- net of costs (reported, not a criterion): mean R {float(net):+.3f}, t-stat {float(s.get('net_t') or 0.0):.2f}")
    for fam, m in (s.get("members") or {}).items():
        out.append(f"- member {fam}: {m['n']:,} events, gross mean R {m['gross_mean_r']:+.3f}, t {m['gross_t']:.2f} "
                   f"({'would pass' if m['passed'] else 'would fail'} alone)")
    return out + [""]
