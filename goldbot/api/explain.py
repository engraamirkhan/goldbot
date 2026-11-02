"""Read-only views for the dashboard's Health screen and auto-mode card (design: Explainability; Drift and health).
The Research screen and its view were retired on 2026-11-02 (owner: remove the research and feeds screens).

Everything here reads files other processes write and never changes them:

* state/drift.json (ops/jobs.py drift_watch): per champion AgentHealth, sticky halts, size factors, system halt;
* state/shadow_book.json (engine/shadow.py): closed shadow trades, for the reliability curve and the CUSUM trace;
* state/agents.json (population league): the allocator's capital share per agent;
* the deploy, data-quality and drift health checks (ops/health.py), evaluated on request;
* models/registry.json (model registry): each champion's backtest trade rate, which sets its calibrated CUSUM h;
* control.json, approvals/done, risk_<account>.json, engine_<account>.json: the auto-mode card (row A10), through the
  same evidence check `/mode auto` runs (telegram/automode.py auto_mode_eligibility_from_state).

Most of these files are absent until the VPS runs; every builder returns an empty, well-formed view then.
"""
from __future__ import annotations

import importlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from goldbot.api.schema import (
    AgentHealthRow,
    AskReasonView,
    AutoModeView,
    CusumPoint,
    CusumTrace,
    EntryModeAccount,
    ForcedPropose,
    HealthCheckRow,
    HealthView,
    PsiHeatmap,
    ReliabilityBin,
    ReliabilityCurve,
    SystemHaltView,
    VetoTestView,
)
from goldbot.base import load_record
from goldbot.config import DriftSettings, Settings, TelegramSettings
from goldbot.ops.accounts import engine_state_files
from goldbot.telegram.approvals import AskReason, ask_reason_text

log = logging.getLogger(__name__)


# "OSError: [Errno 2] ..." -> "OSError"; then any absolute path (POSIX, ~/, a Windows drive or a UNC \\host\share)
# -> "<path>": a quoted one whole (spaces included), an unquoted one up to the first space or separator
_EXC_TEXT = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Exit|Interrupt|Warning))\b: .*", re.S)
_ROOT = r"(?:[A-Za-z]:\\|\\\\|~/|/)"
_QUOTED_PATH = re.compile(rf"(['\"]){_ROOT}[^'\"]*\1")
_PATH = re.compile(rf"(?<![\w.]){_ROOT}[^\s'\",;)]+")


def redact(text: str) -> str:
    """Fault text for a non-owner role: the first line, an exception's type without its message, no paths. The
    services write the full text (scheduler job errors, drift errors, health reasons); the owner reads it whole."""
    first = text.splitlines()[0] if text else ""
    return _PATH.sub("<path>", _QUOTED_PATH.sub(r"\1<path>\1", _EXC_TEXT.sub(r"\1", first)))


def _fault(what: str, exc: BaseException) -> str:
    """Every role reads these views: show the exception type only and log the detail (paths, values) server-side."""
    log.warning("%s: %s: %s", what, type(exc).__name__, exc)
    return f"{what} ({type(exc).__name__})"


REVIEW_COMMAND = "python -m goldbot.ops.run drift-review"
CLEAR_COMMAND = 'python -m goldbot.ops.run drift-review --clear "what you checked"'
PSI_MAX_FEATURES = 15            # heatmap rows: the features with the highest PSI across agents
RELIABILITY_BINS = 10
MAX_CURVES = 6


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


def _checks(state: Path, settings: Settings | None, is_owner: bool = False) -> list[HealthCheckRow]:
    """The health checks the Health screen shows. A check that handles its own fault may put the exception text
    in its reason (goldbot/ops/health.py): kept for the owner, redacted for every other role."""
    from goldbot.ops.health import HealthContext, check_data_quality, check_deploy, check_drift
    ctx = HealthContext(state_dir=state, now=pd.Timestamp.now("UTC"), settings=settings,
                        settings_error=None if settings else "settings unreadable", get_secret=lambda _k: None)
    out = []
    for fn in (check_deploy, check_data_quality, check_drift):
        try:
            c = fn(ctx)
            out.append(HealthCheckRow(name=c.name, status=c.status, reason=c.reason if is_owner else redact(c.reason)))
        except Exception as exc:                 # one broken check must not hide the others
            out.append(HealthCheckRow(name=fn.__name__.removeprefix("check_"), status="warn",
                                      reason=_fault("check failed", exc)))
    return out


def health_view(state: Path, settings: Settings | None, is_owner: bool = False) -> HealthView:
    """is_owner: fault text (drift errors, system-halt reasons, check reasons) whole; other roles get `redact`."""
    def say(text: str) -> str:
        return text if is_owner else redact(text)
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
                                reasons=[say(str(r)) for r in reasons] if isinstance(reasons, list) and reasons
                                else ["drift system halt"], review_command=REVIEW_COMMAND, clear_command=CLEAR_COMMAND)
    else:
        system = None
    errors = _d(d.get("errors"))
    return HealthView(generated_utc=_now(), drift_ts=_ts(d.get("ts")), drift_error=err, system_halt=system,
                      agents=agents, psi=psi, reliability=reliability, cusum=cusum, dd_mult=ds.dd_mult,
                      checks=_checks(state, settings, is_owner), errors={str(k): say(str(v)) for k, v in errors.items()})


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
    listed = {(x.account_id, x.kind) for x in out}
    for acct, d in _engine_files(state):       # what each demo engine publishes: the card's own reason (backlog 92)
        r = _published_reason(d)
        if r is not None and (acct, r.kind) not in listed:
            listed.add((acct, r.kind))
            out.append(ForcedPropose(account_id=acct, kind=r.kind, detail=ask_reason_text(r),
                                     until=datetime.fromtimestamp(r.until, tz=timezone.utc) if r.until else None))
    return out


def ask_reason_view(r: AskReason | None) -> AskReasonView | None:
    """The card's and the entry-mode card's reason, in the Telegram card's words (one source: `ask_reason_text`)."""
    if r is None:
        return None
    until = datetime.fromtimestamp(r.until, tz=timezone.utc) if r.until is not None else None
    return AskReasonView(kind=r.kind, text=ask_reason_text(r), until=until)


def _published_reason(e: dict[str, Any]) -> AskReason | None:
    """An engine file's `ask_reason` (engine/auto_entry.py `published_reason`), on a demo engine only; None when
    absent, on live or paper, or not a valid reason (reporting only: never guessed)."""
    raw = e.get("ask_reason")
    if e.get("mode") != "demo" or not isinstance(raw, dict):
        return None
    try:                                    # ADR 0010: a newer engine's extra field is dropped, not fatal
        return load_record(AskReason, raw, source="engine_<account>.json ask_reason")
    except ValueError:
        return None


def _engine_files(state: Path) -> list[tuple[str, dict[str, Any]]]:
    """(account, engine file) for every enabled account whose file reads; an unreadable one is skipped."""
    out = []
    for f in engine_state_files(state):    # enabled accounts only: a disabled one's leftover is not shown
        try:
            d = _d(_read(f))
        except (ValueError, OSError):
            continue
        out.append((str(d.get("account") or f.stem.removeprefix("engine_")), d))
    return out


def _entry_accounts(state: Path) -> list[EntryModeAccount]:
    """Backlog 92: one row per enabled account's engine file, its class, the mode it runs now and why it asks."""
    out: list[EntryModeAccount] = []
    for acct, d in _engine_files(state):
        mode, ts = d.get("mode"), _f(d.get("ts"))
        out.append(EntryModeAccount(
            account_id=acct, account_mode=mode if mode in ("demo", "live", "paper") else None,
            effective_mode="auto" if d.get("approval_mode") == "auto" else "propose",
            ask_reason=ask_reason_view(_published_reason(d)),
            last_seen_utc=datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None))
    return sorted(out, key=lambda a: a.account_id)


def _engine_modes(state: Path) -> dict[str, str]:
    return {acct: str(d.get("approval_mode") or "propose") for acct, d in _engine_files(state)}


def _mode_by(by: str | None) -> str | None:
    """"telegram:<id>" -> "telegram", "dashboard:<email>" -> "dashboard"; "engine:<account>" is kept."""
    return by if by is None or by.startswith("engine:") else by.split(":", 1)[0]


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
        err = _fault("evidence unreadable", exc)
        e = automode.Eligibility(eligible=False, reasons=[err], since=c.mode_ts, decided=0, approved=0, rejected=0,
                                 breaches=[], test=automode.VetoTest(n_approved=0, n_rejected=0, alpha=tg.auto_alpha))
    t = e.test
    return AutoModeView(
        # the channel that switched, for every role (A15): a control.json from before it names who, cut here; an
        # engine's kill switch keeps "engine:<account>"
        owner_mode=c.approval_mode, mode_by=_mode_by(c.mode_by),
        mode_since=datetime.fromtimestamp(c.mode_ts, tz=timezone.utc) if c.mode_ts else None, mode_reason=c.mode_reason,
        engine_modes=_engine_modes(state), eligible=e.eligible, reasons=list(e.reasons), decided=e.decided,
        approved=e.approved, rejected=e.rejected, min_proposals=tg.auto_min_proposals,
        min_outcomes_per_side=tg.auto_min_outcomes_per_side, breaches=list(e.breaches),
        test=VetoTestView(n_approved=t.n_approved, n_rejected=t.n_rejected, mean_r_approved=t.mean_r_approved,
                          mean_r_rejected=t.mean_r_rejected, diff=t.diff, ci_low=t.ci_low, ci_high=t.ci_high,
                          p_value=t.p_value, alpha=t.alpha, indistinguishable=t.indistinguishable),
        forced_propose=_forced_propose(state, datetime.now(timezone.utc).timestamp()), accounts=_entry_accounts(state),
        # settings risk.approval_window_seconds; null (unknown, never a guessed 90) when settings cannot be read
        window_s=settings.risk.approval_window_seconds if settings is not None else None,
        can_enable=is_owner and e.eligible and c.approval_mode != "auto", error=err)
