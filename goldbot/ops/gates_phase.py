"""Roadmap phase gates and the stop rule, evaluated from evidence (design: Roadmap; rows P6, P7).

The design's roadmap has four gates (`accounts.GATES`), each "gated by evidence, not by calendar: a gate that is not
met extends the phase rather than being waived". Its thresholds, verbatim from the roadmap figure:

* foundation_to_backtest ("leakage audit"): shuffle test at chance; as-of audit clean; broker and Dukascopy bars agree.
* backtest_to_paper: DSR >= 1.0 on the true trial count; positive in 3 separate years; >= 500 backtest trades;
  live-tick spread model.
* paper_to_tiny_live: >= 150 paper trades; expectancy within 50% of backtest; fills within 30% of target; chaos drill
  passed.
* tiny_live_to_full_size: >= 300 live trades; expectancy within 40% of paper; drawdown under 1.5x backtest; brokers
  within 15%.

Stop rule (P6): "The project stops if, after 18 months of paper plus live, the pooled trade count exceeds 500 and the
lower 90% confidence bound on expectancy is still below zero, or if any single incident produces a loss larger than
the weekly cap."

Numbers the design commits live in `config/settings.yaml` `gates:`; the ones it does not commit (the "at chance"
tolerance, how closely the feeds must agree, minimum phase durations, trades per broker) carry a proposed default
marked `PROPOSED: owner sign-off required`, since the design wants the thresholds "committed in writing before the
first paper trade". Interpretations the code makes are stated where they are made:

* "within X% of" means no more than X% below the reference (better than the reference always passes) and needs a
  positive reference; "brokers within 15%" compares the two brokers' live expectancy to the larger of the two.
* Expectancy and drawdown are on the per-trade net return `ret`, the unit the backtest reports (research/metrics.py),
  so paper, live and backtest compare like for like.
* "lower 90% confidence bound" is the one-sided 90% bound, mean - t(0.90, n-1) * sd / sqrt(n).
* A stop-rule "incident" is one closed trade whose net loss exceeds `risk.weekly_cap` of its account's equity before
  it; it counts at any time, paper or live.

What this module does and does not do:

* It READS evidence: state/closed_trades.jsonl (one ClosedTrade per line: the paper and live record), the trial
  registry (DSR, positive years, backtest trades), state/costs_<account>.json (spread from own ticks, measured fills),
  state/gate_evidence.json (leakage audit, feed agreement, chaos drill: recorded with `run.py gate-evidence`),
  state/shadow_book.json (informational) and state/phase_state.json (which gates are recorded and when).
* It WRITES only state/gate_report.json (the last report) and, through `record_evidence`, state/gate_evidence.json.
* It never records a gate, never edits phase_state.json or accounts.yaml, never unlocks a live account, never halts,
  closes or resizes anything. Recording stays `run.py record-gate` (accounts.record_gate) and live still needs
  `unlock_live` plus the typed phrase. A stop-rule breach is reported here and as a health FAIL (the Telegram alert
  path announces it once); stopping is the owner's decision (/halt), beyond what the RiskGate already enforces.

  python -m goldbot.ops.run gates [--json] [--no-write]
  python -m goldbot.ops.run gate-evidence <name> (--value X | --passed yes|no) [--detail TEXT]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import Field
from scipy import stats

from goldbot.base import FrozenRecord, UtcTimestamp, write_atomic
from goldbot.config import GateSettings, Settings
from goldbot.ops.accounts import GATES
from goldbot.research.metrics import max_drawdown

CLOSED_TRADES_FILE = "closed_trades.jsonl"
EVIDENCE_FILE = "gate_evidence.json"
REPORT_FILE = "gate_report.json"
PHASE_FILE = "phase_state.json"
# Owner- or job-recorded evidence for the items the code cannot measure itself (foundation gate, chaos drill).
EVIDENCE_NAMES = ("shuffle_auc", "asof_violations", "feed_mismatch_share", "chaos_drill")
NEVER_UNLOCKS = ("This report never records a gate or unlocks live: record with `run.py record-gate`, "
                 "enable live with `accounts unlock-live` and the typed phrase.")


class ClosedTrade(FrozenRecord):
    """One closed paper (demo account) or live trade, net of every cost: the record the gates and the stop rule read."""

    account_id: str
    broker: str
    mode: Literal["demo", "live"]
    exit_utc: UtcTimestamp
    ret: float                         # net return per trade, side * (exit - entry) / entry less costs (backtest unit)
    pnl: float                         # net P&L in account currency
    equity_before: float = Field(gt=0)


class GateItem(FrozenRecord):
    name: str
    met: bool
    evidence: str
    need: str
    required: bool = True              # False: shown for context, does not decide the gate


class GateStatus(FrozenRecord):
    gate: str
    met: bool
    recorded: bool
    items: list[GateItem]


class StopRuleStatus(FrozenRecord):
    breached: bool
    reasons: list[str]
    pooled_trades: int
    months: float
    lower_bound: float | None
    worst_loss_frac: float | None      # largest single-trade loss as a fraction of equity before it
    detail: str


class GateReport(FrozenRecord):
    ts: UtcTimestamp
    phase: int
    gates_passed: list[str]
    gates: list[GateStatus]
    stop_rule: StopRuleStatus
    errors: list[str] = Field(default_factory=list)
    note: str = NEVER_UNLOCKS


# ---------------------------------------------------------------------------------------------- evidence readers
def load_closed_trades(state_dir: Path) -> tuple[list[ClosedTrade], str | None]:
    """The paper and live record; (trades, error). An unreadable line is an error, never silently dropped."""
    f = Path(state_dir) / CLOSED_TRADES_FILE
    if not f.exists():
        return [], None
    out: list[ClosedTrade] = []
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(ClosedTrade.model_validate_json(line))
        except ValueError as exc:
            return out, f"{CLOSED_TRADES_FILE} line {i} unreadable: {str(exc).splitlines()[0][:120]}"
    return out, None


def append_closed_trade(state_dir: Path, trade: ClosedTrade) -> None:
    """Append one closed trade to the record (the writer the engine calls when a position closes)."""
    f = Path(state_dir) / CLOSED_TRADES_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(trade.model_dump_json() + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def phase_record(state_dir: Path) -> tuple[dict[str, Any], dict[str, pd.Timestamp], str | None]:
    """(phase_state, {gate: recorded at}, error). Read only."""
    f = Path(state_dir) / PHASE_FILE
    if not f.exists():
        return {"phase": 0, "gates_passed": []}, {}, None
    try:
        st = _read_json(f)
        when = {str(e["gate"]): pd.Timestamp(e["ts_utc"]) for e in st.get("gate_log", []) if "gate" in e and "ts_utc" in e}
        return st, when, None
    except (ValueError, OSError, AttributeError, TypeError, KeyError) as exc:
        return {"phase": 0, "gates_passed": []}, {}, f"{PHASE_FILE} unreadable: {exc}"


def load_evidence(state_dir: Path) -> dict[str, dict[str, Any]]:
    f = Path(state_dir) / EVIDENCE_FILE
    try:
        d = _read_json(f) if f.exists() else {}
        return d if isinstance(d, dict) else {}
    except (ValueError, OSError):
        return {}


def record_evidence(state_dir: Path, name: str, *, value: float | None = None, passed: bool | None = None,
                    detail: str = "", now: pd.Timestamp | None = None) -> dict[str, Any]:
    """Record one evidence item (a measured value or a pass/fail with a note) in state/gate_evidence.json. Evidence
    only: it records no gate and changes no phase."""
    if name not in EVIDENCE_NAMES:
        raise ValueError(f"unknown evidence {name!r}; known: {', '.join(EVIDENCE_NAMES)}")
    if (value is None) == (passed is None):
        raise ValueError("give exactly one of a value or pass/fail")
    if value is not None and not math.isfinite(value):
        raise ValueError("value must be a finite number")
    ev = load_evidence(state_dir)
    ts = now if now is not None else pd.Timestamp.now("UTC")
    ev[name] = {"value": value, "passed": passed, "detail": detail, "ts_utc": ts.isoformat()}
    write_atomic(Path(state_dir) / EVIDENCE_FILE, json.dumps(ev, indent=1))
    return ev


def _trial_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def best_backtest_trial(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The evaluated walk-forward trial (never a holdout scoring) with the highest model-filtered DSR."""
    def dsr(r: dict[str, Any]) -> float:
        v = ((r.get("results") or {}).get("model_filtered") or {}).get("dsr")
        return float(v) if v is not None else -1.0
    cands = [r for r in rows if r.get("status") == "evaluated" and (r.get("results") or {}).get("model_filtered")]
    return max(cands, key=dsr) if cands else None


def _cost_tables(state_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(Path(state_dir).glob("costs_*.json")):
        try:
            d = _read_json(f)
            if isinstance(d, dict):
                out.append(d)
        except (ValueError, OSError):
            continue
    return out


# ---------------------------------------------------------------------------------------------- statistics
def mean_ret(trades: list[ClosedTrade]) -> float | None:
    return float(np.mean([t.ret for t in trades])) if trades else None


def lower_bound(rets: list[float], confidence: float) -> float | None:
    """One-sided lower confidence bound on the mean: mean - t(confidence, n-1) * sd / sqrt(n); None below 2 trades."""
    n = len(rets)
    if n < 2:
        return None
    a = np.asarray(rets, dtype=float)
    sd = float(np.std(a, ddof=1))
    return float(np.mean(a) - stats.t.ppf(confidence, n - 1) * sd / math.sqrt(n))


def within(value: float | None, reference: float | None, frac: float) -> bool:
    """`value` is no more than `frac` below a positive `reference` (better always passes)."""
    return value is not None and reference is not None and reference > 0 and value >= (1 - frac) * reference


def _months(start: pd.Timestamp, end: pd.Timestamp) -> float:
    return max((end - start).total_seconds() / (365.25 / 12 * 86400), 0.0)


def _fmt(v: float | None, spec: str = ".5f") -> str:
    return "n/a" if v is None else format(v, spec)


# ---------------------------------------------------------------------------------------------- stop rule (P6)
def evaluate_stop_rule(trades: list[ClosedTrade], paper_start: pd.Timestamp | None, weekly_cap: float,
                       cfg: GateSettings, now: pd.Timestamp) -> StopRuleStatus:
    """P6. Pooled paper + live trades since the paper phase began (the backtest_to_paper record; the first trade when
    it is not recorded). Breached when (elapsed >= stop_after_months AND pooled > stop_min_pooled_trades AND the
    one-sided lower bound < 0) OR any single trade lost more than `weekly_cap` of its equity before it."""
    start = paper_start if paper_start is not None else (min(t.exit_utc for t in trades) if trades else None)
    pooled = [t for t in trades if start is None or t.exit_utc >= start]
    months = _months(start, now) if start is not None else 0.0
    lb = lower_bound([t.ret for t in pooled], cfg.stop_confidence)
    reasons: list[str] = []
    if months >= cfg.stop_after_months and len(pooled) > cfg.stop_min_pooled_trades and lb is not None and lb < 0:
        reasons.append(f"after {months:.1f} months and {len(pooled)} pooled trades the lower {cfg.stop_confidence:.0%} "
                       f"bound on expectancy is {lb:.5f} (< 0)")
    losses = [(-t.pnl / t.equity_before, t) for t in trades]
    worst = max(losses, key=lambda x: x[0]) if losses else None
    over = [x for x in losses if x[0] > weekly_cap]
    if over and worst is not None:
        frac, t = worst
        more = f" ({len(over)} such trades)" if len(over) > 1 else ""
        reasons.append(f"single loss of {frac:.2%} of equity on {t.account_id} ({t.mode}) closed {t.exit_utc:%Y-%m-%d %H:%M} "
                       f"UTC exceeds the weekly cap {weekly_cap:.1%}{more}")
    detail = (f"{len(pooled)} pooled trades (needs > {cfg.stop_min_pooled_trades}), {months:.1f} of "
              f"{cfg.stop_after_months} months, lower {cfg.stop_confidence:.0%} bound {_fmt(lb)}, "
              f"worst single loss {_fmt(worst[0] if worst else None, '.2%')} (weekly cap {weekly_cap:.1%})")
    return StopRuleStatus(breached=bool(reasons), reasons=reasons, pooled_trades=len(pooled), months=round(months, 2),
                          lower_bound=lb, worst_loss_frac=worst[0] if worst else None, detail=detail)


# ---------------------------------------------------------------------------------------------- gates (P7)
def _evidence_item(ev: dict[str, dict[str, Any]], name: str, need: str, ok: Any) -> GateItem:
    e = ev.get(name)
    if not e:
        return GateItem(name=name, met=False, evidence="not recorded (run.py gate-evidence)", need=need)
    shown = f"value {e['value']}" if e.get("value") is not None else ("passed" if e.get("passed") else "failed")
    note = f" — {e['detail']}" if e.get("detail") else ""
    return GateItem(name=name, met=bool(ok(e)), evidence=f"{shown} on {str(e.get('ts_utc', '?'))[:10]}{note}", need=need)


def _previous(i: int, passed: list[str]) -> list[GateItem]:
    if i == 0:
        return []
    prev = GATES[i - 1]
    return [GateItem(name="previous_gate", met=prev in passed, evidence="recorded" if prev in passed else "not recorded",
                     need=f"{prev} recorded first")]


def _incident_item(stop: StopRuleStatus, weekly_cap: float) -> GateItem:
    bad = stop.worst_loss_frac is not None and stop.worst_loss_frac > weekly_cap
    return GateItem(name="no_incident", met=not bad,
                    evidence=f"worst single loss {_fmt(stop.worst_loss_frac, '.2%')}",
                    need=f"no single loss > weekly cap {weekly_cap:.1%} (stop rule)")


def evaluate_gates(state_dir: Path, settings: Settings, now: pd.Timestamp, trials_path: Path | None = None) -> GateReport:
    """Every gate's items against the evidence; never writes anything (see `write_report`)."""
    state_dir = Path(state_dir)
    cfg, cap = settings.gates, settings.risk.weekly_cap
    errors: list[str] = []
    st, when, err = phase_record(state_dir)
    if err:
        errors.append(err)
    passed = [str(g) for g in st.get("gates_passed", [])]
    trades, err = load_closed_trades(state_dir)
    if err:
        errors.append(err)
    ev = load_evidence(state_dir)
    rows = _trial_rows(trials_path if trials_path is not None else _registry_path(state_dir, settings))
    best = best_backtest_trial(rows)
    mf = ((best or {}).get("results") or {}).get("model_filtered") or {}
    bt_mean = float(mf["mean_ret"]) if mf.get("mean_ret") is not None else None
    bt_dd = float(mf["max_dd"]) if mf.get("max_dd") is not None else None

    paper_start, live_start = when.get("backtest_to_paper"), when.get("paper_to_tiny_live")
    paper = [t for t in trades if t.mode == "demo" and (paper_start is None or t.exit_utc >= paper_start)]
    live = [t for t in trades if t.mode == "live" and (live_start is None or t.exit_utc >= live_start)]
    stop = evaluate_stop_rule(trades, paper_start, cap, cfg, now)
    gates: list[GateStatus] = []

    # 0 foundation -> backtest: the leakage audit
    tol = cfg.shuffle_auc_tolerance
    items = [
        _evidence_item(ev, "shuffle_auc", f"|AUC - 0.5| <= {tol} (at chance)",
                       lambda e: e.get("value") is not None and abs(float(e["value"]) - 0.5) <= tol + 1e-12),
        _evidence_item(ev, "asof_violations", "0 (as-of audit clean)",
                       lambda e: e.get("value") is not None and float(e["value"]) == 0),
        _evidence_item(ev, "feed_mismatch_share", f"<= {cfg.feed_max_mismatch_share} of bars (broker and Dukascopy agree)",
                       lambda e: e.get("value") is not None and float(e["value"]) <= cfg.feed_max_mismatch_share),
    ]
    gates.append(_status(0, passed, items))

    # 1 backtest -> paper: from the trial registry and the measured spread table
    dsr = mf.get("dsr")
    gchecks = {c.get("name"): c for c in (((best or {}).get("results") or {}).get("gates") or {}).get("checks", [])}
    years = gchecks.get("positive_years") or {}
    spread_tables = [c for c in _cost_tables(state_dir) if int(c.get("n_ticks") or 0) > 0 and c.get("spread")]
    trial = f"trial #{best.get('trial')} ({best.get('family')})" if best else "no evaluated trial"
    items = _previous(1, passed) + [
        GateItem(name="dsr", met=dsr is not None and float(dsr) >= cfg.backtest_min_dsr,
                 evidence=f"{_fmt(float(dsr) if dsr is not None else None, '.3f')} at {trial}; registry now holds {len(rows)} trials",
                 need=f">= {cfg.backtest_min_dsr} on the true trial count"),
        GateItem(name="positive_years", met=bool(years.get("passed")), evidence=str(years.get("detail") or "n/a"),
                 need="positive in 3 separate years incl. 2021-22 (research gates)"),
        GateItem(name="backtest_trades", met=int(mf.get("n") or 0) >= cfg.backtest_min_trades,
                 evidence=f"{int(mf.get('n') or 0)} model-filtered trades", need=f">= {cfg.backtest_min_trades}"),
        GateItem(name="live_tick_spread_model", met=bool(spread_tables),
                 evidence=", ".join(f"{c.get('account_id')} {int(c.get('n_ticks') or 0):,} ticks" for c in spread_tables)
                 or "no cost table built from own ticks", need="a spread table measured from the terminal's own ticks"),
    ]
    gates.append(_status(1, passed, items))

    # 2 paper -> tiny live
    p_mean = mean_ret(paper)
    p_days = (now - paper_start).total_seconds() / 86400 if paper_start is not None else 0.0
    slip = _fill_slippage(state_dir)
    items = _previous(2, passed) + [
        GateItem(name="paper_trades", met=len(paper) >= cfg.paper_min_trades,
                 evidence=f"{len(paper)} closed demo trades"
                 + ("" if (state_dir / CLOSED_TRADES_FILE).exists() else f" (no {CLOSED_TRADES_FILE} yet)"),
                 need=f">= {cfg.paper_min_trades}"),
        GateItem(name="paper_days", met=p_days >= cfg.paper_min_days, evidence=f"{p_days:.0f} days since backtest_to_paper",
                 need=f">= {cfg.paper_min_days} days"),
        GateItem(name="paper_expectancy", met=within(p_mean, bt_mean, cfg.paper_expectancy_within),
                 evidence=f"paper {_fmt(p_mean)} vs backtest {_fmt(bt_mean)} per trade",
                 need=f"no more than {cfg.paper_expectancy_within:.0%} below a positive backtest expectancy"),
        GateItem(name="fills", met=slip is not None and slip[0] <= (1 + cfg.paper_fills_within) * slip[1],
                 evidence=(f"measured slippage ${slip[0]:.3f}/oz on {slip[2]} fills vs modelled ${slip[1]:.3f}"
                           if slip else "no measured fills in the cost tables"),
                 need=f"within {cfg.paper_fills_within:.0%} of the modelled slippage"),
        _evidence_item(ev, "chaos_drill", "passed", lambda e: e.get("passed") is True),
        _incident_item(stop, cap),
        _shadow_item(state_dir),
    ]
    gates.append(_status(2, passed, items))

    # 3 tiny live -> full size
    l_mean = mean_ret(live)
    l_days = (now - live_start).total_seconds() / 86400 if live_start is not None else 0.0
    l_dd = max_drawdown(np.asarray([t.ret for t in sorted(live, key=lambda t: t.exit_utc)])) if live else None
    by_broker = {b: [t for t in live if t.broker == b] for b in sorted({t.broker for t in live})}
    means = {b: mean_ret(ts) for b, ts in by_broker.items()}
    enough = [b for b, ts in by_broker.items() if len(ts) >= cfg.broker_min_trades]
    if len(enough) >= 2:
        vals = [float(means[b] or 0.0) for b in enough]
        spread, scale = max(vals) - min(vals), max(abs(v) for v in vals)
        brokers_ok = scale > 0 and spread <= cfg.brokers_within * scale
    else:
        brokers_ok = False
    items = _previous(3, passed) + [
        GateItem(name="live_trades", met=len(live) >= cfg.live_min_trades, evidence=f"{len(live)} closed live trades",
                 need=f">= {cfg.live_min_trades}"),
        GateItem(name="live_days", met=l_days >= cfg.live_min_days, evidence=f"{l_days:.0f} days since paper_to_tiny_live",
                 need=f">= {cfg.live_min_days} days"),
        GateItem(name="live_expectancy", met=within(l_mean, p_mean, cfg.live_expectancy_within),
                 evidence=f"live {_fmt(l_mean)} vs paper {_fmt(p_mean)} per trade",
                 need=f"no more than {cfg.live_expectancy_within:.0%} below a positive paper expectancy"),
        GateItem(name="live_drawdown", met=l_dd is not None and bt_dd is not None and l_dd < cfg.live_dd_mult * bt_dd,
                 evidence=f"live max drawdown {_fmt(l_dd, '.4f')} vs backtest {_fmt(bt_dd, '.4f')}",
                 need=f"under {cfg.live_dd_mult}x backtest"),
        GateItem(name="brokers_agree", met=brokers_ok,
                 evidence=", ".join(f"{b} {len(ts)} trades {_fmt(means[b])}" for b, ts in by_broker.items()) or "no live trades",
                 need=f"two brokers with >= {cfg.broker_min_trades} trades each, expectancy within {cfg.brokers_within:.0%}"),
        _incident_item(stop, cap),
    ]
    gates.append(_status(3, passed, items))
    return GateReport(ts=now, phase=int(st.get("phase", 0) or 0), gates_passed=passed, gates=gates, stop_rule=stop,
                      errors=errors)


def _status(i: int, passed: list[str], items: list[GateItem]) -> GateStatus:
    return GateStatus(gate=GATES[i], met=all(it.met for it in items if it.required), recorded=GATES[i] in passed, items=items)


def _registry_path(state_dir: Path, settings: Settings) -> Path:
    p = Path(settings.research.registry)
    return p if p.is_absolute() else Path(state_dir).parent / p


def _fill_slippage(state_dir: Path) -> tuple[float, float, int] | None:
    """(fill-weighted measured market-order slippage $/oz, the modelled prior, fills) over every cost table."""
    tot, n, prior = 0.0, 0, None
    for c in _cost_tables(state_dir):
        for key, s in (c.get("slippage") or {}).items():
            if str(key).endswith(":market") and not s.get("from_prior") and int(s.get("n") or 0) > 0:
                tot += float(s["mean"]) * int(s["n"])
                n += int(s["n"])
        if c.get("slippage_prior_usd") is not None:
            prior = float(c["slippage_prior_usd"])
    return (tot / n, prior, n) if n and prior is not None else None


def _shadow_item(state_dir: Path) -> GateItem:
    f = Path(state_dir) / "shadow_book.json"
    rets: list[float] = []
    try:
        for book in (_read_json(f) if f.exists() else {}).values():
            rets += [float(t["ret"]) for t in book.get("closed", []) if t.get("taken", True) and t.get("ret") is not None]
    except (ValueError, OSError, AttributeError, TypeError, KeyError):
        pass
    m = float(np.mean(rets)) if rets else None
    return GateItem(name="shadow_record", met=True, required=False,
                    evidence=f"{len(rets)} closed shadow trades, mean {_fmt(m)}", need="context only (not a gate item)")


# ---------------------------------------------------------------------------------------------- output
def render(report: GateReport) -> str:
    lines = [f"phase {report.phase}; gates recorded: {', '.join(report.gates_passed) or 'none'}"]
    for g in report.gates:
        rec = "recorded" if g.recorded else ("MET, not recorded" if g.met else "not recorded")
        lines.append(f"\n{g.gate}: {'MET' if g.met else 'NOT MET'} ({rec})")
        for it in g.items:
            mark = "info" if not it.required else ("ok" if it.met else "--")
            lines.append(f"  [{mark:4}] {it.name:<22} {it.evidence}  (need {it.need})")
    s = report.stop_rule
    lines.append(f"\nstop rule: {'BREACHED' if s.breached else 'not breached'} — {s.detail}")
    for r in s.reasons:
        lines.append(f"  ! {r}")
    if s.breached:
        lines.append("  The design says the project stops. Nothing here halts trading: send /halt (or Halt on the dashboard)"
                     " to stop entries; the RiskGate caps still apply.")
    for e in report.errors:
        lines.append(f"error: {e}")
    lines.append(f"\n{report.note}")
    return "\n".join(lines)


def write_report(state_dir: Path, report: GateReport) -> Path:
    f = Path(state_dir) / REPORT_FILE
    write_atomic(f, report.model_dump_json(indent=1), durable=False)
    return f


def main(argv: list[str], state_dir: str | Path = "state", settings: Settings | None = None,
         now: pd.Timestamp | None = None) -> int:
    """`gates [--json] [--no-write]`: print every gate as met / not met with its evidence (exit 0 always: a gate
    not met is the normal state of a phase); writes state/gate_report.json unless --no-write."""
    from goldbot.config import load_settings
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run gates")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    rep = evaluate_gates(Path(state_dir), settings or load_settings(), now if now is not None else pd.Timestamp.now("UTC"))
    print(rep.model_dump_json(indent=1) if args.json else render(rep))
    if not args.no_write:
        write_report(Path(state_dir), rep)
    return 0


def evidence_main(argv: list[str], state_dir: str | Path = "state") -> int:
    """`gate-evidence <name> (--value X | --passed yes|no) [--detail TEXT]`."""
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run gate-evidence")
    ap.add_argument("name", help="one of: " + ", ".join(EVIDENCE_NAMES))
    ap.add_argument("--value", type=float)
    ap.add_argument("--passed", choices=("yes", "no"))
    ap.add_argument("--detail", default="")
    args = ap.parse_args(argv)
    try:
        record_evidence(Path(state_dir), args.name, value=args.value,
                        passed=None if args.passed is None else args.passed == "yes", detail=args.detail)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"recorded evidence {args.name} (no gate recorded; see `run.py gates`)")
    return 0
