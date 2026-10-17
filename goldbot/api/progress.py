"""The Progress tab's three read-only views (ROADMAP W2-3, docs/ux/progress-screens.md): the stop rule and the road to
live (`GET /api/gates`), results (`GET /api/attribution`) and your account's trades (`GET /api/trades/summary`).

Design rules this module keeps:
* "Gates are evaluated on request, not read from gate_report.json": `gates_phase.evaluate_gates` runs with nothing
  written (the `--no-write` path), cached for 60 s (`GatesCache`). Nothing here records a gate, unlocks live, halts or
  changes a setting.
* "Verdicts, staleness, proposed flags and the stop-rule status all come from the API": the browser holds no policy.
* "Accounts appear by config name. Any run of 5 or more digits is cut to its last two, applied by the API so no client
  can forget it" (`entry_text.account_name`). No email, login, host or operator-typed text (the `--detail` note of
  `gate-evidence` stays in the CLI); "can't check" reasons are mapped codes with owner wording, the error goes to the
  server log.
* Results rows are judged at their own bar (ADR 0022; quant review of the spec, F1: each row's `t_bar`), and an exit
  target or stop row reads "by construction" (F2): a target exit's R is positive and a stop exit's is -1 R by
  definition, so their sign is no evidence.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar

import pandas as pd
from pydantic import ValidationError

from goldbot.api.schema import (
    AttributionReportView,
    AttributionView,
    DayNet,
    GateItemView,
    GateProgressView,
    GatesView,
    GateView,
    ImputedView,
    ProposedThreshold,
    ResultCell,
    ResultCosts,
    ResultExitCell,
    ResultLiveFills,
    StopIncidentView,
    StopRuleView,
    TradesSummary,
)
from goldbot.base import load_record
from goldbot.config import SchedulerSettings, ScheduleSettings, Settings
from goldbot.ops import gates_phase as gp
from goldbot.ops.monthly_report import NORTH_STAR_DAY_GBP
from goldbot.ops.report_days import day_buckets, report_day, weekdays
from goldbot.ops.scheduler import Schedule
from goldbot.research.attribution import AttributionReport, Cell, load_report
from goldbot.telegram.entry_text import account_name

log = logging.getLogger(__name__)

__all__ = ["GateItemView", "GateView", "GatesCache", "GatesView", "attribution_view", "gates_view", "trades_summary"]

GATES_TTL_S = 60.0                 # "cached for 60 s" (spec Data)
SUMMARY_DAYS = 30                  # "last 30 opportunity days"
BY_CONSTRUCTION = ("target", "stop")   # exit rows whose sign is fixed by the exit itself (quant F2)
CALM = "Nothing is paused by this; exits always run."

# "can't check" reasons in the owner's words (the loss budget's pattern, account_view._CANT_CHECK)
CANT_PHASE = "The gate record (phase_state.json) is unreadable, so the gates are shown as if none is recorded."
CANT_TRADES = ("The closed-trade record is partly unreadable, so trade counts may be low and the stop rule can't be "
               f"fully checked. {CALM}")
CANT_LOST = ("An engine could not write a closed trade to the record, so a loss may be missing and the stop rule "
             f"can't be fully checked. {CALM}")
CANT_OTHER = f"Part of the gate evidence is unreadable (details in the server log). {CALM}"
CANT_FAILED = f"The gates couldn't be worked out (details in the server log). {CALM}"
CANT_SETTINGS = f"The settings don't load, so the gates can't be checked. {CALM}"
CANT_RESULTS = "The daily results file is unreadable, so results can't be shown. Trading is not affected."

T = TypeVar("T")


# ---------------------------------------------------------------------------------------------- shared
class GatesCache:
    """One gate evaluation served for `ttl_s` seconds (the design's 60 s, as Health's own cadence), so a phone polling
    every minute and a refocused tab never re-read the whole record twice. Thread-safe: one build at a time."""

    def __init__(self, ttl_s: float = GATES_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._ttl, self._clock = ttl_s, clock
        self._lock = threading.Lock()
        self._at: float | None = None
        self._value: Any = None

    def get(self, build: Callable[[], T]) -> T:
        with self._lock:
            now = self._clock()
            if self._at is None or now - self._at >= self._ttl:
                self._value, self._at = build(), now
            value: T = self._value
            return value


class _Names:
    """Replaces every known account config name in free text by its cut form (`account_name`). Only known names are
    replaced: cutting every 5-digit run would also mangle numbers ("0.00123", "12345 trades")."""

    def __init__(self, ids: Iterable[str]):
        self._ids = sorted({i for i in ids if i and account_name(i) != i}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for i in self._ids:
            text = text.replace(i, account_name(i))
        return text


def _known_accounts(state_dir: Path, engines: list[dict[str, Any]]) -> list[str]:
    """Account ids that can appear in gate evidence: the cost tables, closed-trade files and engines."""
    ids = [str(e.get("account") or "") for e in engines]
    for pattern, prefix, suffix in (("costs_*.json", "costs_", ".json"), ("closed_trades_*.jsonl", "closed_trades_", ".jsonl"),
                                    ("engine_*.json", "engine_", ".json")):
        ids += [f.name[len(prefix):-len(suffix)] for f in state_dir.glob(pattern)]
    return ids


def _utc(ts: pd.Timestamp | None) -> Any:
    return None if ts is None else ts.to_pydatetime()


# ---------------------------------------------------------------------------------------------- GET /api/gates
def _cant(errors: list[str]) -> list[str]:
    out: list[str] = []
    for e in errors:
        text = (CANT_PHASE if e.startswith(gp.PHASE_FILE) else CANT_TRADES if e.startswith(gp.TRADES_UNREADABLE)
                else CANT_OTHER)
        log.warning("gates view: %s", e)                 # the raw error goes to the server log only
        if text not in out:
            out.append(text)
    return out


def _stop_view(s: gp.StopRuleStatus, settings: Settings, cant: list[str]) -> StopRuleView:
    status: Literal["ok", "not_started", "breached", "cant_check"]
    if s.breached:
        status = "breached"
    elif cant:
        status = "cant_check"
    elif s.clock_start_utc is None:
        status = "not_started"
    else:
        status = "ok"
    g = settings.gates
    return StopRuleView(
        status=status, cant_check=" ".join(cant) or None, months=s.months, after_months=g.stop_after_months,
        pooled_trades=s.pooled_trades, min_pooled_trades=g.stop_min_pooled_trades, confidence=g.stop_confidence,
        lower_bound=s.lower_bound, min_trades_for_estimate=gp.MIN_TRADES_FOR_BOUND, worst_loss_frac=s.worst_loss_frac,
        weekly_cap=settings.risk.weekly_cap, clock_start_utc=_utc(s.clock_start_utc), clock_source=s.clock_source,
        breach=s.breach, incident_count=s.incident_count,
        incidents=[StopIncidentView(account=account_name(x.account), mode=x.mode,
                                    closed_utc=x.closed_utc.to_pydatetime(), loss_frac=x.loss_frac) for x in s.incidents],
        imputed=ImputedView(n=s.imputed.n_gross, charged_usd=s.imputed.total_usd, unpriced=s.imputed.n_unpriced))


def gates_view(state_dir: str | Path, settings: Settings, now: pd.Timestamp, engines: list[dict[str, Any]],
               trials_path: Path | None = None) -> GatesView:
    """The road to live and the stop rule, evaluated now from the evidence with nothing written. A closed-trade write
    an engine reports lost (`closed_records_lost`, which health already FAILs on) makes the stop rule "can't check":
    a missing loss can hide an incident."""
    state_dir = Path(state_dir)
    rep = gp.evaluate_gates(state_dir, settings, now, trials_path=trials_path)
    names = _Names(_known_accounts(state_dir, engines))
    pending = settings.gates.pending_sign_off()
    cant = _cant(rep.errors)
    stop_cant = [c for c in cant if c in (CANT_TRADES, CANT_PHASE)]
    if any(e.get("closed_records_lost") for e in engines):
        stop_cant.append(CANT_LOST)
    gates = [GateView(gate=g.gate, met=g.met, recorded=g.recorded, recorded_utc=_utc(g.recorded_utc), items=[
        GateItemView(name=i.name, met=i.met, required=i.required, need=names(i.need), evidence=names(i.evidence),
                     proposed=i.threshold in pending,
                     progress=GateProgressView(**i.progress.model_dump()) if i.progress else None,
                     earliest_utc=_utc(i.earliest_utc), ci95=i.ci95)
        for i in g.items]) for g in rep.gates]
    return GatesView(generated_utc=now.to_pydatetime(), phase=rep.phase, gates=gates,
                     stop_rule=_stop_view(rep.stop_rule, settings, stop_cant),
                     proposed_count=len(pending),
                     proposed=[ProposedThreshold(name=n, value=float(getattr(settings.gates, n))) for n in pending],
                     cant_check=" ".join(dict.fromkeys(cant + stop_cant)) or None)


def gates_unavailable(now: pd.Timestamp, settings: Settings | None) -> GatesView:
    """The view when the gates cannot be evaluated at all (settings do not load, or the evaluation raised)."""
    text = CANT_SETTINGS if settings is None else CANT_FAILED
    pending = settings.gates.pending_sign_off() if settings is not None else []
    return GatesView(generated_utc=now.to_pydatetime(), gates=[], proposed_count=len(pending),
                     proposed=[ProposedThreshold(name=n, value=float(getattr(settings.gates, n)))
                               for n in pending] if settings is not None else [],
                     stop_rule=StopRuleView(status="cant_check", cant_check=text,
                                            min_trades_for_estimate=gp.MIN_TRADES_FOR_BOUND),
                     cant_check=text)


# ---------------------------------------------------------------------------------------------- GET /api/attribution
def _cell(c: Cell, *, fixed: bool = False) -> dict[str, Any]:
    """A cell for the screen; `fixed` (an exit target or stop row) reads "by construction" with no range."""
    d = c.model_dump()
    if fixed:
        d.update(verdict="by construction", verdict_adj="by construction", ci_adj=None)
    return d


def _report_view(rep: AttributionReport) -> AttributionReportView:
    breakdowns = {b: {k: ResultCell(**_cell(c, fixed=b == "exit" and k in BY_CONSTRUCTION)) for k, c in rows.items()}
                  for b, rows in rep.breakdowns.items()}
    costs = rep.costs.model_dump()
    costs["source"] = account_name(costs["source"])
    return AttributionReportView(
        as_of=rep.as_of.to_pydatetime(), window_from=rep.window_from.to_pydatetime(), min_trades=rep.min_trades,
        t_bar=rep.t_bar, m=rep.m, n_candidates=rep.n_candidates, n_taken=rep.n_taken,
        overall=ResultCell(**_cell(rep.overall)), challengers=ResultCell(**_cell(rep.challengers)),
        breakdowns=breakdowns,
        exits={k: ResultExitCell(**_cell(c, fixed=k in BY_CONSTRUCTION)) for k, c in rep.exits.items()},
        costs=ResultCosts(**costs), live={account_name(a): ResultLiveFills(**lf.model_dump()) for a, lf in rep.live.items()},
        notes=[account_name(n) for n in rep.notes])


def attribution_view(state_dir: str | Path, settings: Settings | None, now: pd.Timestamp) -> AttributionView:
    """state/attribution.json (the daily job) without `trades` and `calibration`. `stale` once the job's last slot plus
    its `max_late_hours` has passed without a report as new as that slot (the scheduler's own lateness rule)."""
    spec: ScheduleSettings = (settings.scheduler.attribution if settings is not None
                              else SchedulerSettings.model_fields["attribution"].default)
    sched = Schedule(**spec.model_dump())
    nxt = sched.next_slot(now).to_pydatetime()
    try:
        raw = load_report(state_dir)
        rep = load_record(AttributionReport, raw, source="attribution.json") if raw is not None else None
    except (OSError, ValueError, ValidationError) as exc:
        log.warning("attribution view: report unreadable: %s", type(exc).__name__)
        return AttributionView(report=None, stale=False, next_run_utc=nxt, cant_check=CANT_RESULTS)
    if rep is None:
        return AttributionView(report=None, stale=False, next_run_utc=nxt)
    due = sched.last_slot(now - pd.Timedelta(hours=spec.max_late_hours))
    return AttributionView(report=_report_view(rep), stale=due is not None and rep.as_of < due, next_run_utc=nxt)


# ---------------------------------------------------------------------------------------------- GET /api/trades/summary
def _mode(v: Any) -> Literal["demo", "live", "paper"] | None:
    return v if v in ("demo", "live", "paper") else None


def trades_summary(state_dir: str | Path, now: pd.Timestamp, engines: list[dict[str, Any]],
                   days: int = SUMMARY_DAYS) -> list[TradesSummary]:
    """Per account (every engine and every account with a closed trade), the last `days` opportunity days to the one
    holding `now`, on the monthly report's rules (monthly_report.account_month): money from net rows in the account
    currency only; gross and other-currency rows counted and flagged, never summed; days at £50+ on a GBP account
    only, counted and never aimed at (ADR 0004)."""
    trades, err = gp.load_closed_trades(Path(state_dir))
    if err:
        log.warning("trades summary: %s", err)
    last = report_day(now)
    first = f"{date.fromisoformat(last) - timedelta(days=days - 1):%Y-%m-%d}"
    eng = {str(e.get("account")): e for e in engines if e.get("account")}
    by_acc: dict[str, list[gp.ClosedTrade]] = {a: [] for a in eng}
    last_mode: dict[str, Literal["demo", "live"]] = {}
    for t in sorted(trades, key=lambda t: t.exit_utc):
        last_mode[t.account_id] = t.mode              # an account with no engine state: the mode of its last trade
        rows = by_acc.setdefault(t.account_id, [])
        if first <= report_day(t.exit_utc) <= last:
            rows.append(t)
    out = []
    for acc, ts in sorted(by_acc.items()):
        e = eng.get(acc, {})
        ccy = e.get("currency") if isinstance(e.get("currency"), str) else None
        net = [t for t in ts if gp.pnl_basis(t) == "net"]
        money = [t for t in net if t.pnl_currency is None or t.pnl_currency.upper() == (ccy or "").upper()]
        per_day = [DayNet(day=d, net=round(sum(t.pnl for t in rows), 2), n=len(rows))
                   for d, rows in sorted(day_buckets(money, lambda t: t.exit_utc).items())]
        out.append(TradesSummary(
            account=account_name(acc), mode=_mode(e.get("mode")) or last_mode.get(acc), currency=ccy,
            days=days, first_day=first, last_day=last, net=round(sum(t.pnl for t in money), 2), n=len(ts),
            net_n=len(money), days_traded=len(per_day), trading_days=weekdays(first, last), target=NORTH_STAR_DAY_GBP,
            days_at_target=sum(d.net >= NORTH_STAR_DAY_GBP for d in per_day) if ccy == "GBP" else None,
            best_day=max(per_day, key=lambda d: d.net) if per_day else None,
            worst_day=min(per_day, key=lambda d: d.net) if per_day else None, by_day=per_day,
            gross_rows=len(ts) - len(net), other_currency_rows=len(net) - len(money)))
    return out
