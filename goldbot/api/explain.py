"""Read-only views for the dashboard's Health and Research screens (design: Explainability; Drift and health).

Everything here reads files other processes write and never changes them:

* state/drift.json (ops/jobs.py drift_watch): per champion AgentHealth, sticky halts, size factors, system halt;
* state/shadow_book.json (engine/shadow.py): closed shadow trades, for the reliability curve and the CUSUM trace;
* state/agents.json (population league): the allocator's capital share per agent;
* the deploy, data-quality and drift health checks (ops/health.py), evaluated on request;
* state/research_registry.jsonl (trial registry), state/research_plan.json (research director);
* docs/research/hypotheses.md (hypothesis portfolio): its markdown tables, parsed;
* models/registry.json (model registry): each champion's backtest trade rate, which sets its calibrated CUSUM h;
* control.json, approvals/done, risk_<account>.json, engine_<account>.json: the auto-mode card (row A10), through the
  same evidence check `/mode auto` runs (telegram/automode.py auto_mode_eligibility_from_state).

Most of these files are absent until the VPS runs; every builder returns an empty, well-formed view then.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.api.schema import (
    AgentHealthRow,
    AutoModeView,
    CusumPoint,
    CusumTrace,
    ForcedPropose,
    GateCheck,
    HealthCheckRow,
    HealthView,
    HypothesisDoc,
    HypothesisTable,
    PlanFamily,
    PlanFocus,
    PlanMove,
    PlanReservation,
    PsiHeatmap,
    ReliabilityBin,
    ReliabilityCurve,
    ResearchPlanView,
    ResearchView,
    RetiredFamilyView,
    SystemHaltView,
    TrialBudget,
    TrialRow,
    VetoTestView,
)
from goldbot.config import DriftSettings, Settings, TelegramSettings

REVIEW_COMMAND = "python -m goldbot.ops.run drift-review"
CLEAR_COMMAND = 'python -m goldbot.ops.run drift-review --clear "what you checked"'
PSI_MAX_FEATURES = 15            # heatmap rows: the features with the highest PSI across agents
RELIABILITY_BINS = 10
MAX_CURVES = 6
MAX_TRIALS = 200
PLAN_MAX_AGE_DAYS = 21           # ops/jobs.py PLAN_MAX_AGE_DAYS (not imported: jobs pulls in the research stack)
DEFAULT_QUARTER_BUDGET = 20      # research/director.py DEFAULT_QUARTER_BUDGET


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ts(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    try:
        t = pd.Timestamp(v)
    except (ValueError, TypeError):
        return None
    if pd.isna(t):
        return None
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).to_pydatetime()


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


# ---------------------------------------------------------------------------------------------- health
def _drift(state: Path) -> tuple[dict[str, Any], str | None]:
    f = state / "drift.json"
    if not f.exists():
        return {}, None
    try:
        d = _read(f)
    except (ValueError, OSError) as exc:
        return {}, f"drift.json unreadable ({type(exc).__name__}): the engines halt new entries until it is fixed"
    if not isinstance(d, dict):
        return {}, "drift.json invalid: the engines halt new entries until it is fixed"
    return d, None


def _capital(state: Path) -> dict[str, float]:
    try:
        rows = _read(state / "agents.json") if (state / "agents.json").exists() else []
    except (ValueError, OSError):
        return {}
    return {str(r["agent_id"]): float(r.get("capital_weight", 0.0)) for r in rows
            if isinstance(r, dict) and "agent_id" in r}


def _closed_by_version(state: Path) -> tuple[dict[str, list[Any]], dict[str, str]]:
    """Closed, taken shadow trades per version (oldest exit first) and each version's agent id."""
    if not (state / "shadow_book.json").exists():
        return {}, {}
    from goldbot.engine.shadow import ShadowBook
    try:
        book = ShadowBook(state)
    except (ValueError, OSError):
        return {}, {}
    out: dict[str, list[Any]] = {}
    agent: dict[str, str] = {}
    for v, b in book.books.items():
        done = [(t.exit_ts, t) for t in b.closed if t.taken and t.exit_ts is not None and t.exit is not None]
        out[v] = [t for _, t in sorted(done, key=lambda x: x[0])]
        everything = b.closed + b.open
        if everything:
            agent[v] = everything[0].agent_id
    return out, agent


def _reliability(agent_id: str, version: str | None, trades: list[Any]) -> ReliabilityCurve:
    p = np.array([float(t.p) for t in trades], dtype=float)
    y = np.array([t.barrier == "target" for t in trades], dtype=float)
    bins: list[ReliabilityBin] = []
    if len(p):
        idx = np.clip((p * RELIABILITY_BINS).astype(int), 0, RELIABILITY_BINS - 1)
        for b in range(RELIABILITY_BINS):
            m = idx == b
            if m.any():
                bins.append(ReliabilityBin(lo=b / RELIABILITY_BINS, hi=(b + 1) / RELIABILITY_BINS, n=int(m.sum()),
                                           mean_p=round(float(p[m].mean()), 4), hit_rate=round(float(y[m].mean()), 4)))
    ece = round(float(sum(x.n * abs(x.mean_p - x.hit_rate) for x in bins) / len(p)), 4) if len(p) else None
    brier = round(float(np.mean((p - y) ** 2)), 4) if len(p) else None
    return ReliabilityCurve(agent_id=agent_id, version=version, n=int(len(p)), ece=ece, brier=brier, bins=bins)


def _trade_rates(settings: Settings | None) -> dict[str, float]:
    """Backtest trade rate per model version from the model registry (read as a file: ModelRegistry() creates its
    folder, and this view never writes)."""
    root = Path(settings.research.models_dir) if settings is not None else Path("models")
    try:
        rows = _read(root / "registry.json") if (root / "registry.json").exists() else []
    except (ValueError, OSError):
        return {}
    out: dict[str, float] = {}
    for r in rows if isinstance(rows, list) else []:
        tpw = _f(_d(_d(r).get("backtest")).get("trades_per_week"))
        if tpw is not None and tpw > 0 and r.get("version"):
            out[str(r["version"])] = tpw
    return out


def _mean_p(trades: list[Any]) -> float | None:
    """Mean model p of the scored trades (research/drift.py mean_taken_p): the residuals' in-control win probability."""
    ps = [min(max(float(t.p), 1e-3), 1 - 1e-3) for t in trades if t.exit is not None and abs(t.entry - t.stop) > 0]
    return float(np.mean(ps)) if ps else None


def cusum_h(trades: list[Any], trades_per_week: float | None,
            ds: DriftSettings) -> tuple[float, Literal["calibrated", "fixed"], str, float | None]:
    """(h, source, note, false-alarm rate) for one agent's trace. Row M25: h is research/cusum.py `calibrated_h` at the
    agent's backtest trade rate and the mean p of its trades (5% false alarms per quarter of trading), the h the drift
    watch alarms on. Falls back to the fixed `drift.cusum_h`, labelled, when the calibration module is not installed,
    the backtest recorded no trade rate, or the calibration fails. Read-only: nothing is written."""
    fixed = float(ds.cusum_h)
    try:
        cal = importlib.import_module("goldbot.research.cusum")
    except ImportError:
        return fixed, "fixed", f"fixed h {fixed:g}: CUSUM calibration (research/cusum.py) not installed", None
    if trades_per_week is None:
        return fixed, "fixed", f"fixed h {fixed:g}: the backtest recorded no trade rate to calibrate on", None
    target: Any = getattr(ds, "cusum_false_alarm", None) or getattr(cal, "FALSE_ALARM_QUARTER", 0.05)
    rate = float(target)
    try:
        h = float(cal.calibrated_h(trades_per_week, ds.cusum_k, rate, p=_mean_p(trades)))
    except Exception as exc:                  # a calibration fault must not hide the trace
        return fixed, "fixed", f"fixed h {fixed:g}: calibration failed ({type(exc).__name__})", None
    if not math.isfinite(h) or h <= 0:
        return fixed, "fixed", f"fixed h {fixed:g}: calibration returned no usable h", None
    return round(h, 3), "calibrated", (f"h {h:.2f} calibrated to {rate:.0%} false alarms a quarter at "
                                       f"{trades_per_week:.1f} trades/week"), rate


def _cusum(agent_id: str, version: str, trades: list[Any], ds: DriftSettings,
           trades_per_week: float | None = None) -> CusumTrace:
    """The drift watch's downward CUSUM (research/drift.py residual_cusum) on the same residuals, step by step, against
    the calibrated h (`cusum_h`)."""
    from goldbot.research.drift import trade_residuals
    k = ds.cusum_k
    h, source, note, rate = cusum_h(trades, trades_per_week, ds)
    s, alarm, pts = 0.0, False, []
    for t in trades:
        z = trade_residuals([t])
        if not z:
            continue
        s = max(0.0, s - z[0] - k)
        alarm = alarm or s > h
        ts = _ts(t.exit_ts)
        if ts is not None:
            pts.append(CusumPoint(ts=ts, z=round(z[0], 4), s=round(s, 4)))
    pm = _mean_p(trades)
    return CusumTrace(agent_id=agent_id, version=version, k=k, h=h, alarm=alarm, points=pts, h_source=source,
                      h_note=note, trades_per_week=trades_per_week, p_mean=round(pm, 4) if pm is not None else None,
                      false_alarm=rate)


def _checks(state: Path, settings: Settings | None) -> list[HealthCheckRow]:
    from goldbot.ops.health import HealthContext, check_data_quality, check_deploy, check_drift
    ctx = HealthContext(state_dir=state, now=pd.Timestamp.now("UTC"), settings=settings,
                        settings_error=None if settings else "settings unreadable", get_secret=lambda _k: None)
    out = []
    for fn in (check_deploy, check_data_quality, check_drift):
        try:
            c = fn(ctx)
            out.append(HealthCheckRow(name=c.name, status=c.status, reason=c.reason))
        except Exception as exc:                 # one broken check must not hide the others
            out.append(HealthCheckRow(name=fn.__name__.removeprefix("check_"), status="warn",
                                      reason=f"check failed: {type(exc).__name__}: {exc}"[:300]))
    return out


def health_view(state: Path, settings: Settings | None) -> HealthView:
    ds = settings.drift if settings is not None else DriftSettings()
    d, err = _drift(state)
    raw_agents = _d(d.get("agents"))
    halted = _d(d.get("halted"))
    capital = _capital(state)
    closed, version_agent = _closed_by_version(state)

    agents: list[AgentHealthRow] = []
    for aid, a in sorted(raw_agents.items()):
        if not isinstance(a, dict):
            continue
        h = _d(halted.get(aid)) or None
        agents.append(AgentHealthRow(
            agent_id=aid, version=str(a.get("version", "")), size_factor=_f(a.get("size_factor")) or 1.0,
            halted=bool(a.get("halted")) or h is not None, halted_since=_ts(h.get("since")) if h else None,
            halt_reasons=[str(r) for r in (h.get("reasons") or [])] if h else [],
            notes=[str(n) for n in a.get("notes") or []], psi_warn=list(a.get("psi_warn") or []),
            psi_size_down=list(a.get("psi_size_down") or []), n_live_rows=int(a.get("n_live_rows") or 0),
            ece=_f(a.get("ece")), brier=_f(a.get("brier")), n_calib=int(a.get("n_calib") or 0),
            cusum=_f(a.get("cusum")) or 0.0, cusum_alarm=bool(a.get("cusum_alarm")), dd_30d=_f(a.get("dd_30d")) or 0.0,
            backtest_dd=_f(a.get("backtest_dd")), capital_weight=capital.get(aid)))

    # PSI heatmap: features ranked by their worst PSI across agents
    worst: dict[str, float] = {}
    for a in raw_agents.values():
        for feat, v in ((a.get("psi") or {}) if isinstance(a, dict) else {}).items():
            x = _f(v)
            if x is not None:
                worst[feat] = max(worst.get(feat, 0.0), x)
    feats = sorted(worst, key=lambda c: (-worst[c], c))[:PSI_MAX_FEATURES]
    cols = [r.agent_id for r in agents if (raw_agents[r.agent_id].get("psi") or {})]
    values = [[_f((raw_agents[aid].get("psi") or {}).get(feat)) for aid in cols] for feat in feats]
    psi = PsiHeatmap(features=feats, agents=cols, values=values, warn=ds.psi_warn, size_down=ds.psi_size_down)

    # which shadow versions to chart: the champions drift_watch checked, else the book's newest versions
    pairs = [(r.agent_id, r.version) for r in agents if r.version in closed] if agents else \
        [(version_agent.get(v, v), v) for v in list(closed)[-MAX_CURVES:]]
    reliability: list[ReliabilityCurve] = []
    pooled: list[Any] = []
    for aid, v in pairs[:MAX_CURVES]:
        tail = closed[v][-ds.calib_trades:]           # the drift watch's trailing window
        if tail:
            reliability.append(_reliability(aid, v, tail))
            pooled += tail
    if len(reliability) > 1:
        reliability.insert(0, _reliability("all", None, pooled))
    rates = _trade_rates(settings)
    cusum = [_cusum(aid, v, closed[v], ds, rates.get(v)) for aid, v in pairs[:MAX_CURVES] if closed[v]]

    sh = d.get("system_halt")
    if err:
        system = SystemHaltView(since=None, reasons=[err], review_command=REVIEW_COMMAND, clear_command=CLEAR_COMMAND)
    elif sh:
        reasons = sh.get("reasons") if isinstance(sh, dict) else None
        system = SystemHaltView(since=_ts(sh.get("since")) if isinstance(sh, dict) else None,
                                reasons=[str(r) for r in reasons] if isinstance(reasons, list) and reasons
                                else ["drift system halt"], review_command=REVIEW_COMMAND, clear_command=CLEAR_COMMAND)
    else:
        system = None
    errors = _d(d.get("errors"))
    return HealthView(generated_utc=_now(), drift_ts=_ts(d.get("ts")), drift_error=err, system_halt=system,
                      agents=agents, psi=psi, reliability=reliability, cusum=cusum, dd_mult=ds.dd_mult,
                      checks=_checks(state, settings), errors={str(k): str(v) for k, v in errors.items()})


# ---------------------------------------------------------------------------------------------- research
def _registry(state: Path) -> list[dict[str, Any]]:
    f = state / "research_registry.jsonl"
    if not f.exists():
        return []
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict):
            rows.append(r)
    return rows


def _trial(r: dict[str, Any], timeframes: dict[str, str]) -> TrialRow:
    res = _d(r.get("results"))
    ro = _d(res.get("rule_only"))
    gross = _d(ro.get("gross"))
    net = _d(ro.get("net"))
    gates: list[GateCheck] = []
    passed: bool | None = None
    g = res.get("gates")
    hv = res.get("holdout_verdict")
    if isinstance(g, dict):
        passed = g.get("passed") if isinstance(g.get("passed"), bool) else None
        gates = [GateCheck(name=str(c.get("name", "")), passed=bool(c.get("passed")), detail=str(c.get("detail", "")))
                 for c in g.get("checks") or [] if isinstance(c, dict)]
    elif isinstance(hv, dict):
        passed = bool(hv.get("passed"))
        gates = [GateCheck(name="holdout", passed=passed, detail=str(hv.get("rule", "")))]
    family = str(r.get("family", ""))
    cfg = _d(r.get("config"))
    n = gross.get("n", net.get("n"))
    return TrialRow(trial=int(r.get("trial") or 0), ts=_ts(r.get("ts")), family=family,
                    timeframe=timeframes.get(family) or (str(cfg["timeframe"]) if cfg.get("timeframe") else None),
                    status=str(r.get("status", "")), agent_id=str(r.get("agent_id", "")),
                    gross_r=_f(gross.get("mean_r")), gross_t=_f(gross.get("t_stat")), net_r=_f(net.get("mean_r")),
                    net_t=_f(net.get("t_stat")), n=int(n) if isinstance(n, (int, float)) else None,
                    gates_passed=passed, gates=gates, rationale=str(r.get("rationale", "")))


def _retired(p: dict[str, Any]) -> list[RetiredFamilyView]:
    """Retired families from the plan's evidence rows (retired_id set: retired, or reinstated by new evidence) and its
    retired_floor, with the exploration floor each keeps this quarter."""
    floors = {str(k): int(v) for k, v in _d(p.get("retired_floor")).items()}
    out: dict[str, RetiredFamilyView] = {}
    for e in p.get("evidence") or []:
        if isinstance(e, dict) and e.get("retired_id"):
            fam = str(e["family"])
            out[fam] = RetiredFamilyView(
                family=fam, hypothesis_id=str(e["retired_id"]), since=str(e["retired_since"]) if e.get("retired_since") else None,
                reason=str(e["retired_status"]) if e.get("retired_status") else None, floor=floors.get(fam, 0),
                reinstated=not bool(e.get("retired")), new_evidence=[str(x) for x in e.get("new_evidence") or []])
    for fam, f in floors.items():
        out.setdefault(fam, RetiredFamilyView(family=fam, hypothesis_id=None, since=None, reason=None, floor=f,
                                              reinstated=False, new_evidence=[]))
    return [out[k] for k in sorted(out)]


def _doc_sha(docs_dir: Path | None) -> str | None:
    """sha256 of hypotheses.md as the director hashes it (research/director.py: the text, UTF-8)."""
    f = docs_dir / "research" / "hypotheses.md" if docs_dir is not None else None
    try:
        return hashlib.sha256(f.read_text(encoding="utf-8").encode()).hexdigest() if f is not None and f.exists() else None
    except OSError:
        return None


def _plan(state: Path, docs_dir: Path | None = None) -> tuple[ResearchPlanView | None, str | None]:
    f = state / "research_plan.json"
    if not f.exists():
        return None, None
    try:
        p = _read(f)
        created = _ts(p["created_utc"])
        if created is None:
            raise ValueError("created_utc missing")
        sha = str(p["hypotheses_sha256"]) if p.get("hypotheses_sha256") else None
        now_sha = _doc_sha(docs_dir)
        focus = [PlanFocus(rank=int(x["rank"]), family=str(x["family"]), budget=int(x["budget"]),
                           evidence=float(x["evidence"]), reasons=[str(s) for s in x.get("reasons") or []])
                 for x in p.get("focus") or []]
        evidence = [PlanFamily(family=str(e["family"]), trials=int(e.get("trials") or 0),
                               evidence=float(e.get("evidence") or 0.0), blocked=bool(e.get("blocked")),
                               flags=[str(s) for s in e.get("flags") or []], median_auc=_f(e.get("median_auc")),
                               best_dsr=_f(e.get("best_dsr")), shadow_trades=int(e.get("shadow_trades") or 0))
                    for e in p.get("evidence") or []]
        view = ResearchPlanView(
            created_utc=created, stale=(_now() - created).total_seconds() > PLAN_MAX_AGE_DAYS * 86400,
            quarter=str(p["quarter"]), quarter_budget=int(p["quarter_budget"]), quarter_used=int(p["quarter_used"]),
            total_budget=int(p.get("total_budget") or 0),
            budget={str(k): int(v) for k, v in (p.get("budget") or {}).items()},
            grid_budget={str(k): int(v) for k, v in (p.get("grid_budget") or {}).items()},
            unallocated=int(p.get("unallocated") or 0), holdout_from=str(p.get("holdout_from", "")),
            holdout_to=str(p.get("holdout_to", "")), focus=focus, evidence=evidence,
            quarter_reserved=int(p.get("quarter_reserved") or 0),
            reservation=PlanReservation.model_validate(p["reservation"]) if isinstance(p.get("reservation"), dict) else None,
            retired=_retired(p), reinstate_t=_f(p.get("reinstate_t")),
            moves=[PlanMove(family=str(m["family"]), source=str(m.get("source", "")), detail=str(m.get("detail", "")),
                            budget_before=int(m.get("budget_before") or 0), budget_after=int(m.get("budget_after") or 0),
                            share_before=_f(m.get("share_before")), share_after=_f(m.get("share_after")),
                            shift_pct=_f(m.get("shift_pct"))) for m in p.get("moves") or [] if isinstance(m, dict)],
            attribution_note=str(p["attribution_note"]) if p.get("attribution_note") else None,
            hypotheses_note=str(p["hypotheses_note"]) if p.get("hypotheses_note") else None,
            hypotheses_sha256=sha, hypotheses_sha256_now=now_sha,
            hypotheses_changed=sha is not None and now_sha is not None and sha != now_sha,
            hypotheses_drift=[str(x) for x in p.get("hypotheses_drift") or []])
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return None, f"research_plan.json unreadable ({type(exc).__name__}: {exc})"[:300]
    return view, None


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _cell(s: str) -> str:
    return _BOLD.sub(r"\1", s.strip()).replace("`", "")


def parse_tables(md: str) -> list[HypothesisTable]:
    """Every pipe table in a markdown document, titled by the heading above it."""
    tables: list[HypothesisTable] = []
    title = ""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            title = line.lstrip("#").strip()
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|?$", lines[i + 1].strip()):
            cols = [_cell(c) for c in line.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [_cell(c) for c in lines[i].strip().strip("|").split("|")]
                rows.append((cells + [""] * len(cols))[:len(cols)])
                i += 1
            tables.append(HypothesisTable(title=title, columns=cols, rows=rows))
            continue
        i += 1
    return tables


def _hypotheses(docs_dir: Path) -> HypothesisDoc:
    f = docs_dir / "research" / "hypotheses.md"
    rel = "docs/research/hypotheses.md"
    if not f.exists():
        return HypothesisDoc(path=rel, updated_utc=None, tables=[], note="hypotheses.md not found next to the API")
    try:
        text = f.read_text(encoding="utf-8")
    except OSError as exc:
        return HypothesisDoc(path=rel, updated_utc=None, tables=[], note=f"unreadable: {exc}")
    updated = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
    return HypothesisDoc(path=rel, updated_utc=updated, tables=parse_tables(text), note=None)


def research_view(state: Path, settings: Settings | None, docs_dir: Path) -> ResearchView:
    from goldbot.research.registry import quarter_of, quarter_trials
    from goldbot.specialists import SPECIALISTS
    rows = _registry(state)
    now = _now()
    q = quarter_of(now)
    cap = settings.research.trial_budget_quarter if settings is not None else DEFAULT_QUARTER_BUDGET
    used = quarter_trials(rows, q)
    tfs = {k: str(v.timeframe) for k, v in SPECIALISTS.items()}
    trials = sorted((_trial(r, tfs) for r in rows), key=lambda t: -t.trial)[:MAX_TRIALS]
    plan, plan_err = _plan(state, docs_dir)
    return ResearchView(generated_utc=now, budget=TrialBudget(quarter=q, budget=cap, used=used, left=max(cap - used, 0)),
                        trials=trials, trials_total=len(rows), plan=plan, plan_error=plan_err,
                        hypotheses=_hypotheses(docs_dir))


# ---------------------------------------------------------------------------------------------- auto mode (A10)
def _forced_propose(state: Path, now: float) -> list[ForcedPropose]:
    """Engine overrides that keep propose-and-approve whatever control.json says (engine/runner.py
    `_refresh_account`): the 30-day propose-only lock after a re-arm, and the 12% kill switch while the account is
    halted. An unreadable risk file is listed too: the evidence check fails closed on it."""
    out: list[ForcedPropose] = []
    for f in sorted(state.glob("risk_*.json")):
        acct = f.stem.removeprefix("risk_")
        try:
            d = _d(_read(f))
        except (ValueError, OSError):
            out.append(ForcedPropose(account_id=acct, kind="risk_unreadable",
                                     detail="risk state unreadable: auto mode is not offered until it is fixed"))
            continue
        until = _ts(d.get("propose_only_until"))
        if until is not None and until.timestamp() > now:
            out.append(ForcedPropose(account_id=acct, kind="rearm_lock", until=until,
                                     detail="propose-and-approve for 30 days after a re-arm"))
        if d.get("stage") == "halted":
            out.append(ForcedPropose(account_id=acct, kind="kill_switch",
                                     detail="12% drawdown kill switch: halted and back to propose until re-armed"))
    return out


def _engine_modes(state: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in sorted(state.glob("engine_*.json")):
        try:
            d = _d(_read(f))
        except (ValueError, OSError):
            continue
        out[str(d.get("account") or f.stem.removeprefix("engine_"))] = str(d.get("approval_mode") or "propose")
    return out


def automode_view(state: Path, settings: Settings | None, is_owner: bool) -> AutoModeView:
    """The auto-mode card: the owner's mode, what each engine runs, and the exact evidence check `/mode auto` runs
    (telegram/automode.py auto_mode_eligibility_from_state), so the card and Telegram never disagree. Read-only."""
    from goldbot.telegram import automode
    from goldbot.telegram.bus import ApprovalBus
    tg = settings.telegram if settings is not None else TelegramSettings()
    c = ApprovalBus(state).control()
    err: str | None = None
    try:
        e = automode.auto_mode_eligibility_from_state(state, tg)
    except Exception as exc:                   # fail closed: an unreadable evidence base offers nothing
        err = f"evidence unreadable ({type(exc).__name__}: {exc})"[:300]
        e = automode.Eligibility(eligible=False, reasons=[err], since=c.mode_ts, decided=0, approved=0, rejected=0,
                                 breaches=[], test=automode.VetoTest(n_approved=0, n_rejected=0, alpha=tg.auto_alpha))
    t = e.test
    return AutoModeView(
        owner_mode=c.approval_mode, mode_by=c.mode_by,
        mode_since=datetime.fromtimestamp(c.mode_ts, tz=timezone.utc) if c.mode_ts else None, mode_reason=c.mode_reason,
        engine_modes=_engine_modes(state), eligible=e.eligible, reasons=list(e.reasons), decided=e.decided,
        approved=e.approved, rejected=e.rejected, min_proposals=tg.auto_min_proposals,
        min_outcomes_per_side=tg.auto_min_outcomes_per_side, breaches=list(e.breaches),
        test=VetoTestView(n_approved=t.n_approved, n_rejected=t.n_rejected, mean_r_approved=t.mean_r_approved,
                          mean_r_rejected=t.mean_r_rejected, diff=t.diff, ci_low=t.ci_low, ci_high=t.ci_high,
                          p_value=t.p_value, alpha=t.alpha, indistinguishable=t.indistinguishable),
        forced_propose=_forced_propose(state, datetime.now(timezone.utc).timestamp()),
        can_enable=is_owner and e.eligible and c.approval_mode != "auto", error=err)
