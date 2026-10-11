"""Primary-signal screen (proposal P4): a rule enters model research only if it has an edge of its own.

Meta-labelling raises the precision of a primary signal; it cannot create an edge the rule lacks (López de Prado
2018, ch. 3). Before any model is fitted, the rule's own expectancy is measured over the walk-forward research window
(every labelled candidate that exits before the holdout starts; the holdout is never seen) on mid prices, one position
at a time, with exactly the labels the walk-forward would use:

    pass  <=>  gross mean R > 0,  t-stat of the gross mean R >= MIN_T (2.0),  at least `min_events` events.

The event floor is `research.screen_min_events` (default MIN_EVENTS, 1,000), with an optional override for rules whose
signal is computed on daily bars, `research.screen_min_events_daily` (null: the default floor). Changing either is an
owner decision, fixed before the run it applies to (docs/research/preregistration-2027Q1.md, ruling A). The daily
override is **retire-only** (ruling A: "150 rule-only events, retire-only", read strictly, quant review 2026-10-11): it
is the events a daily rule needs to FAIL, while a pass still needs `research.screen_min_events` (`screen_floors`).

Three verdicts: **pass** (on at least the pass floor); **inconclusive (event floor)** whenever the rule has fewer events
than the retire floor, whatever the sign or t of its gross mean R (too few events to retire it, and too few to pass),
and when it has enough to retire but fewer than the pass floor and meets the sign and the t (a daily rule with 150-999
events): still a recorded, charged trial and still no model, but it cannot retire the hypothesis; **fail** when it has
at least the retire floor's events and misses the sign or the t, which retires the rule from model research.

Net expectancy (spread + slippage + commission + swap) is reported next to it but does not decide: a positive gross edge is
what a meta-model can filter towards a positive net one. Every report also splits the rule-only net R by side
(long-only, short-only) and counts positive net years over every candidate (`rule_only_split`): pre-registered
reading rules (H-01) are read on these numbers.

The screen is a look at the data that selects rules, so each screened configuration is one trial in the registry
(charged to the quarter's budget and counted by the deflated Sharpe), whether or not it passes. When it passes, the
walk-forward that follows is the same trial. `scripts/research_pass.py` refuses to fit a model for a configuration
that fails unless `--skip-screen` is given, and records the screen in the registry row either way.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.research.gates import CHOP_YEARS, MIN_TRADES_PER_YEAR
from goldbot.research.metrics import expectancy
from goldbot.research.pipeline import Prepared, rule_only

if TYPE_CHECKING:
    from goldbot.config import ResearchSettings
    from goldbot.specialists.base import Specialist

MIN_EVENTS = 1000
MIN_T = 2.0
PASS, FAIL, INCONCLUSIVE = "pass", "fail", "inconclusive (event floor)"


def rule_text(min_events: int = MIN_EVENTS, pass_min_events: int | None = None) -> str:
    """The screen's rule in words; with a retire-only floor below the pass floor, both are named."""
    pass_n = max(min_events, pass_min_events or min_events)
    if pass_n == min_events:
        return f"rule-only gross mean R > 0 with t >= {MIN_T} on >= {min_events:,} events (research window, holdout excluded)"
    return (f"rule-only gross mean R > 0 with t >= {MIN_T} on >= {pass_n:,} events; >= {min_events:,} events to fail and "
            f"retire (retire-only floor) (research window, holdout excluded)")


RULE = rule_text()


def signal_timeframe(spec: Specialist) -> str:
    """The timeframe the rule's signal is computed on: `signal_tf` when the configuration sets one, else its own."""
    return str(spec.config.get("signal_tf") or spec.timeframe)


def min_events_for(signal_tf: str, research: ResearchSettings) -> int:
    """The screen's retire floor for a rule whose signal is on `signal_tf`: the events it needs for any decisive verdict
    (module docstring); the daily override applies to daily signals only."""
    if signal_tf == "1d" and research.screen_min_events_daily is not None:
        return int(research.screen_min_events_daily)
    return int(research.screen_min_events)


def screen_floors(signal_tf: str, research: ResearchSettings) -> tuple[int, int]:
    """(retire floor, pass floor) for a rule whose signal is on `signal_tf`: the daily override is retire-only, so a
    pass always needs `research.screen_min_events` (owner ruling A, read strictly)."""
    retire = min_events_for(signal_tf, research)
    return retire, max(retire, int(research.screen_min_events))


def screen_verdict(rule: dict[str, Any], min_events: int = MIN_EVENTS,
                   pass_min_events: int | None = None) -> dict[str, Any]:
    """The screen's decision on a rule-only result ({"gross": expectancy, "net": expectancy}). `min_events`: the retire
    floor (fewer events: inconclusive, whatever the sign or t); `pass_min_events`: the events a pass needs (None: the
    same floor). Between the two the verdict is "fail" or "inconclusive", never "pass" (module docstring)."""
    pass_n = max(min_events, pass_min_events or min_events)
    g, n_ = rule.get("gross") or {}, rule.get("net") or {}
    n, mean_r, t = int(g.get("n", 0)), float(g.get("mean_r", 0.0)), float(g.get("t_stat", 0.0))
    need = f"need {pass_n:,}" if pass_n == min_events else f"need {pass_n:,} to pass, {min_events:,} to fail and retire"
    checks = [
        {"name": "events", "passed": n >= pass_n, "detail": f"{n:,} events ({need})"},
        {"name": "gross_mean_r", "passed": n > 0 and mean_r > 0, "detail": f"gross mean R {mean_r:+.3f} (need > 0)"},
        {"name": "gross_t", "passed": t >= MIN_T, "detail": f"t-stat {t:.2f} (need >= {MIN_T})"},
    ]
    if n < min_events:
        verdict = INCONCLUSIVE          # too few events to retire, and too few to pass
    elif any(not c["passed"] for c in checks[1:]):
        verdict = FAIL                  # enough events to retire, and the sign or the t is missed
    else:
        verdict = PASS if n >= pass_n else INCONCLUSIVE     # a retire-only floor never passes
    return {"passed": verdict == PASS, "verdict": verdict, "rule": rule_text(min_events, pass_n),
            "min_events": min_events, "pass_min_events": pass_n, "checks": checks, "n": n, "gross_mean_r": mean_r,
            "gross_t": t, "net_mean_r": n_.get("mean_r"), "net_t": n_.get("t_stat"), "rule_only": rule}


def screen(prep: Prepared | list[Prepared], min_events: int = MIN_EVENTS,
           pass_min_events: int | None = None) -> dict[str, Any]:
    """Screen one prepared configuration, or the union of several (a pooled trial: every member's candidates), with the
    retire floor `min_events` and the pass floor `pass_min_events` (`screen_floors`; None: one floor)."""
    preps = prep if isinstance(prep, list) else [prep]
    gross = [p.gross for p in preps if not p.gross.empty]
    net = [p.labels for p in preps if not p.labels.empty]
    g = pd.concat(gross, ignore_index=True) if gross else pd.DataFrame()
    n = pd.concat(net, ignore_index=True) if net else pd.DataFrame()
    out = screen_verdict(rule_only(g, n), min_events, pass_min_events)
    if len(preps) > 1:
        out["members"] = {p.spec.family: screen_verdict(rule_only(p.gross, p.labels), min_events, pass_min_events)
                          for p in preps}
    return out


def rule_only_split(net: pd.DataFrame) -> dict[str, Any]:
    """The rule's net R (every cost) split for the pre-registered reading rules: long-only (side == 1), short-only
    (side == -1), and per calendar year of entry over every candidate (both sides). A year is positive with at least
    gates.MIN_TRADES_PER_YEAR trades and positive mean net R (preregistration-2027Q1.md, H-01 "Positive year")."""
    empty: dict[str, Any] = {"long": {"n": 0}, "short": {"n": 0}, "years": {}, "positive_years": [],
                             "n_positive_years": 0, "chop_year_positive": False, "long_years": {},
                             "long_positive_years": [], "n_long_positive_years": 0, "long_chop_year_positive": False}
    if net.empty or not {"side", "ret", "risk", "ts_utc"} <= set(net.columns):
        return empty
    side = net["side"].to_numpy()
    out = {**empty, **{name: expectancy(net["ret"].to_numpy()[side == s], net["risk"].to_numpy()[side == s])
                       for name, s in (("long", 1), ("short", -1))}}
    year = pd.DatetimeIndex(pd.to_datetime(net["ts_utc"], utc=True)).year

    def by_year(frame: pd.DataFrame, yr: Any) -> tuple[dict[str, Any], list[int]]:
        ys = {str(y): expectancy(g["ret"].to_numpy(), g["risk"].to_numpy()) for y, g in frame.groupby(yr)}
        return ys, [int(y) for y, e in ys.items() if e.get("n", 0) >= MIN_TRADES_PER_YEAR and e["mean_r"] > 0]
    years, positive = by_year(net, year)
    # H-01 reads the positive-year test on both sides together AND on long-only (quant re-review)
    longs = side == 1
    long_years, long_positive = by_year(net[longs], year[longs])
    return {**out, "years": years, "positive_years": positive, "n_positive_years": len(positive),
            "chop_year_positive": any(y in CHOP_YEARS for y in positive),
            "long_years": long_years, "long_positive_years": long_positive,
            "n_long_positive_years": len(long_positive),
            "long_chop_year_positive": any(y in CHOP_YEARS for y in long_positive)}


def rule_only_split_lines(s: dict[str, Any] | None) -> list[str]:
    """Markdown for a report: the rule-only net R by side and the positive net years."""
    if not s:
        return []
    out = ["#### Rule-only net R by side and year (every candidate, no model)", "",
           "| side | n | net mean R | t-stat (R) |", "|---|---:|---:|---:|"]
    for name, label in (("long", "long-only (side == 1)"), ("short", "short-only (side == -1)")):
        e = s.get(name) or {}
        out.append(f"| {label} | {e['n']} | {e['mean_r']:+.3f} | {e['t_stat']:.2f} |" if e.get("n") else f"| {label} | 0 | | |")
    pos = s.get("positive_years") or []
    out += ["", f"- positive net years (both sides, >= {MIN_TRADES_PER_YEAR} trades each, by entry year): "
                f"{s.get('n_positive_years', 0)} ({', '.join(str(y) for y in pos) or 'none'}); one of "
                f"{', '.join(str(y) for y in CHOP_YEARS)}: {'yes' if s.get('chop_year_positive') else 'no'}",
            f"- positive net years (long-only): {s.get('n_long_positive_years', 0)} "
            f"({', '.join(str(y) for y in s.get('long_positive_years') or []) or 'none'}); one of "
            f"{', '.join(str(y) for y in CHOP_YEARS)}: {'yes' if s.get('long_chop_year_positive') else 'no'}", ""]
    return out


def is_inconclusive(s: dict[str, Any] | None) -> bool:
    """The screen stopped on an event floor (below the retire floor, or an edge below the pass floor): a recorded trial
    that cannot retire the hypothesis."""
    return bool(s and s.get("verdict") == INCONCLUSIVE)


def screen_lines(s: dict[str, Any] | None, skipped: bool = False) -> list[str]:
    """Markdown for a report."""
    if not s:
        return []
    if s["passed"]:
        head = "**PASS**"
    elif is_inconclusive(s):
        retire_only = int(s.get("n", 0)) >= int(s.get("min_events", 0))
        why = (f"enough events to fail but fewer than the {int(s.get('pass_min_events', 0)):,} a pass needs (the "
               f"{int(s['min_events']):,}-event floor is retire-only)" if retire_only
               else "fewer events than the floor (whatever the sign or t)")
        head = ("**INCONCLUSIVE (event floor)** (skipped with --skip-screen)" if skipped else
                f"**INCONCLUSIVE (event floor)**: {why}; no model fitted, and this result cannot retire the hypothesis")
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
