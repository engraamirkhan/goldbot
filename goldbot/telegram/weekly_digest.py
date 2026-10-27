"""The owner's Sunday weekly digest (docs/ux/daily-learning-report.md section 3, lane L5a): sent at the Sunday
telegram.digest_at slot instead of the daily ("D2: Weekly digest replaces the Sunday 06:45 UTC daily"; Saturday's
slot still carries Friday's day). The week is the five opportunity days Mon-Fri (Sun 22:00 to Fri 22:00 UTC).

Same rules as the daily (goldbot/telegram/digest.py): labs by role label, £ and R with explicit signs, no identifiers,
no buttons, one "could not read" line per broken source, and the arms blinded until their read ("Per arm: n and
status only, in fixed config order", spec 3.4). At most WEEKLY_LINES non-empty lines, then the ranking section (ADR
0053, backlog 137): the ranking over each version's life with its move since last week, and the week's own top three.

Lines whose source is not recorded yet are left out, never estimated: the week's health failures (no alert history
is kept), the worst drawdown of the week (needs-field 5: a drawdown history written at the 22:00 roll), the broker
cost comparison, the H-62 model outcome and next week's news event. "Learning paid/cost" is the labs' net P&L of the
week and the usable labels written in it (ADR 0043 ROI reading: demo money per admissible label).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.config import Settings
from goldbot.ops.report_days import day_bounds
from goldbot.telegram import labs
from goldbot.telegram.digest import (
    DEFAULT_ROADMAP,
    Line,
    _engine_rows,
    _section,
    _text,
    _utc,
    arms_section,
    day_name,
    decisions_line,
    fit,
    health_part,
    lab_engines,
    lab_money,
    labs_section,
    local_end,
    overview_line,
    with_ranking,
)

WEEKLY_LINES = 25                   # spec 3: "Budget: <= 25 logical lines"
ARMS_WIDTH = 110                    # three phone rows, less word-wrap slack
NIGHTS = 5                          # the learner's nights in a Mon-Fri week (21:30 UTC, before each day's 22:00 roll)
MAX_RESEARCH = 3                    # spec 3.2: "at most 3 bullets, newest first"
# the evaluator's decision in plain words (its free-text reason is never printed)
VERDICT_WORDS = {"continue": "passed this step, continues", "stop": "closed, did not pass its pre-set rule",
                 "inconclusive": "no decision yet (inconclusive)"}
# spec 3.2's unsourced lines (week's health failures, worst drawdown, broker costs, model outcome, next news) are
# left out, never estimated; this one line says where they are
MORE_LINE = "More detail on the dashboard"


def week_days(slot: pd.Timestamp) -> list[str]:
    """The Mon-Fri opportunity days before Sunday `slot`, as YYYY-MM-DD."""
    friday = _utc(slot).normalize() - pd.Timedelta(days=2)
    return [f"{friday - pd.Timedelta(days=4 - i):%Y-%m-%d}" for i in range(5)]


def weekly_health(report: Any) -> str:
    """"✅ Health ok now" or the daily's attention words (the week's failure history is not recorded)."""
    h = health_part(report)
    return "✅ Health ok now" if h.startswith("✅") else h


def family_name(family: str) -> str:
    """"box_break_close" -> "Box break close", as the dashboard words a family (web/src/lib/home.ts)."""
    return family.replace("_", " ").strip().capitalize() or "A trial"


def learning_line(state_dir: Path, engines: list[dict[str, Any]], lo: pd.Timestamp, hi: pd.Timestamp, *,
                  shown: bool = True) -> str:
    """Spec 3.2: "Learning paid +£x demo this week (real money £0) · n usable labels", or, after a loss, "Learning cost
    £x demo (real money £0) · £y per usable label (n)". Exploration runs on demo hosts only (the RiskGate's E2), so
    real money is £0 by construction. Per currency, never summed across. Not `shown` (a lab's week total or one of
    its days was withheld, quant 2026-10-26): "—", since the sum would give the withheld figure by subtraction."""
    sums = lab_money(state_dir, engines, lo, hi)
    with labs.aux(state_dir) as conn:
        n = labs.labels_written(conn, [str(e.get("account")) for e in lab_engines(engines)], lo, hi) \
            if conn is not None else 0
    if sums and not shown:
        return f"Learning: — demo this week (real money £0) · {labs.plural(n, 'usable label')}"
    if not sums:
        return f"Learning: no lab trade closed this week · {labs.plural(n, 'usable label')}"
    if len(sums) > 1:
        terms = " | ".join(labs.money(p, c or None) for c, (p, _, _) in sorted(sums.items()))
        return f"Learning this week: {terms} demo (real money £0) · {labs.plural(n, 'usable label')}"
    ccy, (pnl, _, _) = next(iter(sums.items()))
    if pnl >= 0:
        return f"Learning paid {labs.money(pnl, ccy or None)} demo this week (real money £0) · " \
               f"{labs.plural(n, 'usable label')}"
    per = f"{labs.money(-pnl / n, ccy or None, sign=False)} per usable label ({n:,})" if n else "no usable label yet"
    return f"Learning cost {labs.money(-pnl, ccy or None, sign=False)} demo (real money £0) · {per}"


def learner_week(state_dir: Path, lo: pd.Timestamp, hi: pd.Timestamp) -> str:
    """D4: the nights the learner ran and whether any share changed; never which arms, direction or size."""
    with labs.aux(state_dir) as conn:
        lr = labs.learner_runs(conn, lo, hi) if conn is not None else labs.LearnerRuns()
    nights = len({f"{r:%Y-%m-%d}" for r in lr.runs})
    if not nights:
        return "⚠️ Learner did not run this week: equal shares in use"
    return f"Learner this week: ran {nights} of {NIGHTS} nights · " + \
        ("shares updated" if lr.changed else "no share changed")


def research_lines(registry_path: Path | None, settings: Settings | None, lo: pd.Timestamp, hi: pd.Timestamp
                   ) -> list[str]:
    """Spec 3.2: the evaluator's verdicts recorded in the week, newest first, at most MAX_RESEARCH, each as its family,
    "trial n of budget" and the decision in a word. The verdict's free-text reason is never printed."""
    from goldbot.research.registry import VERDICT, quarter_of
    rows: list[dict[str, Any]] = []
    if registry_path is not None and registry_path.exists():
        try:
            rows = [json.loads(x) for x in registry_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except (ValueError, OSError):
            return ["Research this week: could not read the registry"]
    got = []
    for r in rows:
        if not isinstance(r, dict) or r.get("status") != VERDICT:
            continue
        try:
            ts = _utc(pd.Timestamp(str(r["ts"])))
        except (KeyError, ValueError, TypeError):
            continue
        if lo <= ts < hi:
            got.append((ts, r))
    if not got:
        return ["Research this week: no new result"]
    got.sort(key=lambda x: x[0], reverse=True)
    out = ["Research this week:"]
    for ts, r in got[:MAX_RESEARCH]:
        trial = (r.get("verdict_of") or {}).get("trial")
        budget = settings.research.budget_for(quarter_of(ts.to_pydatetime())) if settings is not None else None
        of = f"trial {trial} of {budget}" if trial is not None and budget is not None else f"trial {trial}"
        out.append(f"  • {family_name(str(r.get('family') or ''))} ({of}): "
                   f"{VERDICT_WORDS.get(str(r.get('decision')), 'verdict recorded')}")
    if len(got) > MAX_RESEARCH:
        out[-1] += f" · +{len(got) - MAX_RESEARCH} more on the dashboard"
    return out


def not_running_line() -> list[str]:
    """Spec 2.4: "Account not enabled: omitted from the daily; listed once in the weekly"."""
    from goldbot.ops.accounts import account_ids
    ids = account_ids()
    if ids is None:
        return []
    off = [labs.role(a) for a in labs.LABS if a in ids.listed and a not in ids.enabled]
    return [f"Not running: {', '.join(off)}"] if off else []


def build_weekly(state_dir: str | Path, now: pd.Timestamp, *, as_of: pd.Timestamp | None = None, report: Any = None,
                 settings: Settings | None = None, roadmap: Path = DEFAULT_ROADMAP,
                 registry_path: Path | None = None) -> str:
    """The weekly digest for the Sunday slot `as_of` (default `now`): the Mon-Fri before it."""
    sd = Path(state_dir)
    now = _utc(now)
    slot = now if as_of is None else _utc(as_of)
    days = week_days(slot)
    lo, hi = day_bounds(days[0])[0], day_bounds(days[-1])[1]
    engines, torn = _engine_rows(sd)
    first = pd.Timestamp(days[0])
    start = f"{first:%a} {first.day}" if first.month == pd.Timestamp(days[-1]).month else day_name(first)
    out = [Line(f"📅 Week {start} – {day_name(days[-1])} · ended {hi:%a %H:%M} UTC"
                f"{local_end(hi, settings)}"),
           Line(_text(lambda: overview_line(sd, now, report, settings, engines, health_words=weekly_health,
                                            entries=False), "Health"))]
    if torn:
        out.append(Line(torn))
    try:
        lab_lines, spans, aux_ok = labs_section(sd, engines, lo, hi, settings, weekday=True, by_day=days)
    except Exception as exc:                # noqa: BLE001
        lab_lines, spans, aux_ok = [Line(f"Labs: could not read ({type(exc).__name__})")], [], False
    shown = all((s.totals_shown and all(s.day_shown(d) for d in days)) or not s.closed for s in spans) \
        and len(spans) == len(lab_engines(engines))
    out += lab_lines
    if aux_ok and lab_engines(engines):
        out.append(Line(_text(lambda: learning_line(sd, engines, lo, hi, shown=shown), "Learning"), 2))
        out += arms_section(sd, engines, ARMS_WIDTH, lo, hi)
        out.append(Line(_text(lambda: learner_week(sd, lo, hi), "Learner"), 2))
    out += [Line(x, 2) for x in _section(lambda: research_lines(registry_path, settings, lo, hi), "Research")]
    out += [Line(x, 2) for x in _section(not_running_line, "Accounts")]
    out.append(Line(MORE_LINE, 2))
    out.append(Line(_section(lambda: decisions_line(sd, roadmap), "For you")[0]))
    from goldbot.telegram.ranking_text import weekly
    return with_ranking(labs.guard("\n".join(fit(out, WEEKLY_LINES))), _section(lambda: weekly(sd, hi), "Ranking"))
