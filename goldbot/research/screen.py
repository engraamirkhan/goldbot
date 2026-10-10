"""Primary-signal screen (proposal P4): a rule enters model research only if it has an edge of its own.

Meta-labelling raises the precision of a primary signal; it cannot create an edge the rule lacks (López de Prado
2018, ch. 3). Before any model is fitted, the rule's own expectancy is measured over the walk-forward research window
(every labelled candidate that exits before the holdout starts; the holdout is never seen) on mid prices, one position
at a time, with exactly the labels the walk-forward would use:

    pass  <=>  gross mean R > 0,  t-stat of the gross mean R >= MIN_T (2.0),  at least `min_events` events.

The event floor is `research.screen_min_events` (default MIN_EVENTS, 1,000), with an optional override for rules whose
signal is computed on daily bars, `research.screen_min_events_daily` (null: the default floor). Changing either is an
owner decision, fixed before the run it applies to (docs/research/preregistration-2027Q1.md, ruling A).

Three verdicts: **pass**; **inconclusive (event floor)** when the rule meets every criterion except the event floor
(positive and significant, but on too few events): still a recorded, charged trial and still no model, but it cannot
retire the hypothesis; **fail** otherwise, which retires the rule from model research.

Net expectancy (spread + slippage + commission + swap) is reported next to it but does not decide: a positive gross edge is
what a meta-model can filter towards a positive net one.

The screen is a look at the data that selects rules, so each screened configuration is one trial in the registry
(charged to the quarter's budget and counted by the deflated Sharpe), whether or not it passes. When it passes, the
walk-forward that follows is the same trial. `scripts/research_pass.py` refuses to fit a model for a configuration
that fails unless `--skip-screen` is given, and records the screen in the registry row either way.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.research.pipeline import Prepared, rule_only

if TYPE_CHECKING:
    from goldbot.config import ResearchSettings
    from goldbot.specialists.base import Specialist

MIN_EVENTS = 1000
MIN_T = 2.0
PASS, FAIL, INCONCLUSIVE = "pass", "fail", "inconclusive (event floor)"


def rule_text(min_events: int = MIN_EVENTS) -> str:
    return f"rule-only gross mean R > 0 with t >= {MIN_T} on >= {min_events:,} events (research window, holdout excluded)"


RULE = rule_text()


def signal_timeframe(spec: Specialist) -> str:
    """The timeframe the rule's signal is computed on: `signal_tf` when the configuration sets one, else its own."""
    return str(spec.config.get("signal_tf") or spec.timeframe)


def min_events_for(signal_tf: str, research: ResearchSettings) -> int:
    """The screen's event floor for a rule whose signal is on `signal_tf` (module docstring)."""
    if signal_tf == "1d" and research.screen_min_events_daily is not None:
        return int(research.screen_min_events_daily)
    return int(research.screen_min_events)


def screen_verdict(rule: dict[str, Any], min_events: int = MIN_EVENTS) -> dict[str, Any]:
    """The screen's decision on a rule-only result ({"gross": expectancy, "net": expectancy})."""
    g, n_ = rule.get("gross") or {}, rule.get("net") or {}
    n, mean_r, t = int(g.get("n", 0)), float(g.get("mean_r", 0.0)), float(g.get("t_stat", 0.0))
    checks = [
        {"name": "events", "passed": n >= min_events, "detail": f"{n:,} events (need {min_events:,})"},
        {"name": "gross_mean_r", "passed": n > 0 and mean_r > 0, "detail": f"gross mean R {mean_r:+.3f} (need > 0)"},
        {"name": "gross_t", "passed": t >= MIN_T, "detail": f"t-stat {t:.2f} (need >= {MIN_T})"},
    ]
    failed = [c["name"] for c in checks if not c["passed"]]
    verdict = PASS if not failed else (INCONCLUSIVE if failed == ["events"] else FAIL)
    return {"passed": not failed, "verdict": verdict, "rule": rule_text(min_events), "min_events": min_events,
            "checks": checks, "n": n, "gross_mean_r": mean_r, "gross_t": t, "net_mean_r": n_.get("mean_r"),
            "net_t": n_.get("t_stat"), "rule_only": rule}


def screen(prep: Prepared | list[Prepared], min_events: int = MIN_EVENTS) -> dict[str, Any]:
    """Screen one prepared configuration, or the union of several (a pooled trial: every member's candidates)."""
    preps = prep if isinstance(prep, list) else [prep]
    gross = [p.gross for p in preps if not p.gross.empty]
    net = [p.labels for p in preps if not p.labels.empty]
    g = pd.concat(gross, ignore_index=True) if gross else pd.DataFrame()
    n = pd.concat(net, ignore_index=True) if net else pd.DataFrame()
    out = screen_verdict(rule_only(g, n), min_events)
    if len(preps) > 1:
        out["members"] = {p.spec.family: screen_verdict(rule_only(p.gross, p.labels), min_events) for p in preps}
    return out


def is_inconclusive(s: dict[str, Any] | None) -> bool:
    """The screen failed only on the event floor: a recorded trial that cannot retire the hypothesis."""
    return bool(s and s.get("verdict") == INCONCLUSIVE)


def screen_lines(s: dict[str, Any] | None, skipped: bool = False) -> list[str]:
    """Markdown for a report."""
    if not s:
        return []
    if s["passed"]:
        head = "**PASS**"
    elif is_inconclusive(s):
        head = ("**INCONCLUSIVE (event floor)** (skipped with --skip-screen)" if skipped else
                "**INCONCLUSIVE (event floor)**: positive and significant on fewer events than the floor; no model "
                "fitted, and this result cannot retire the hypothesis")
    else:
        head = "**FAIL** (skipped with --skip-screen)" if skipped else "**FAIL**: screen failed, no model fitted"
    out = [f"### Primary-signal screen: {head}", "", f"- rule: {s['rule']}"]
    out += [f"- {'pass' if c['passed'] else 'FAIL'} {c['name']}: {c['detail']}" for c in s["checks"]]
    net = s.get("net_mean_r")
    if net is not None:
        out.append(f"- net of costs (reported, not a criterion): mean R {float(net):+.3f}, t-stat {float(s.get('net_t') or 0.0):.2f}")
    for fam, m in (s.get("members") or {}).items():
        out.append(f"- member {fam}: {m['n']:,} events, gross mean R {m['gross_mean_r']:+.3f}, t {m['gross_t']:.2f} "
                   f"({'would pass' if m['passed'] else 'would fail'} alone)")
    return out + [""]
