"""Cross-feed checks: the two price sources kept separate and compared, never blended (design: Data architecture).

* D12 daily reconciliation. "Bars are built locally from ticks ... a daily reconciliation against the broker's own M1
  logs any divergence." `reconcile_account` rebuilds the engine's 1m bars from its stored ticks (the engine's own code
  path, `ticks_to_1m`) over the last day and compares them minute by minute with the broker's M1
  (`Broker.get_bars(symbol, "1m", n)`, direct or through the bridge). A minute diverges when

      |engine bid close - broker close| > CLOSE_TOL_POINTS x point      (close_mismatch)
      or it is on the broker but not in the engine's bars               (missing_engine)
      or it is in the engine's bars but not on the broker               (missing_broker)

  over in-session minutes of the window. divergence = diverging minutes / broker minutes; severity "ok" at or below
  WARN_FRACTION (0.5%), "warning" up to ERROR_FRACTION (5%), "error" above it (or when the broker returned bars and
  the engine stored no tick in the window). Each kind with a non-zero count is one dq_events row (source = account) at
  that severity, the report is state/reconcile_<account>.json, and `check_reconciliation` turns it into a health check
  (warning -> warn, error -> fail, older than RECONCILE_MAX_AGE_HOURS -> warn). The broker's M1 is kept in the store
  table `bars_1m_broker` (source = account), so broker history accumulates for the survival check below.

* D20 spike confirmation. The same run screens both feeds with check_bars and keeps a spike warning only when the
  other feed shows no matching move (quality.confirm_spikes).

* D23/F10 survival. "Every signal must survive on both feeds"; "a signal that only works on broker-fed bars is treated
  as a feed artefact and dropped." `survival_check` runs the rule-only screen (research.screen) of one specialist
  configuration on Dukascopy bars and on broker bars over their common period outside the holdout, each feed on its
  own bars (same code, same windows). With D = Dukascopy's gross rule-only result and B = the broker's:

      survives      <=>  D: mean R > 0 and t >= SURVIVE_DUKA_T (2.0, the screen's MIN_T)
                         B: mean R > 0 and t >= SURVIVE_BROKER_T (1.0)
      insufficient  <=>  overlap < MIN_OVERLAP_DAYS (90) calendar days, or fewer than MIN_OVERLAP_EVENTS (100)
                         gross events on either feed
      broker_only   <=>  B passes its band (mean R > 0, t >= 2.0) and D does not: a feed artefact, dropped
      fails         <=>  D passes its band and B does not (wrong sign or t < 1.0)
      no_signal     <=>  neither feed shows the signal on the overlap

  Only "survives" passes. The check can only discard a configuration that was already screened (and recorded as a
  trial), never select one, so it is not charged to the trial budget; the holdout window is cut out of the overlap.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import Record, UtcTimestamp, write_atomic
from goldbot.data.calendar import DEFAULT_SESSIONS, SessionTable
from goldbot.data.quality import DQEvent, check_bars, confirm_spikes, events_frame
from goldbot.data.resample import BAR_COLUMNS, ticks_to_1m
from goldbot.data.store import Store

if TYPE_CHECKING:
    from goldbot.ops.health import Check, HealthContext

# ------------------------------------------------------------------------------------------- D12 reconciliation
POINT_DEFAULT = 0.01              # XAUUSD point on both brokers when the terminal's symbol_info is unavailable
CLOSE_TOL_POINTS = 2.0            # a close more than 2 points away from the broker's is a mismatch
WARN_FRACTION = 0.005             # diverging minutes / broker minutes above this: warning
ERROR_FRACTION = 0.05             # ... above this: error
RECONCILE_WINDOW = pd.Timedelta(days=1)
RECONCILE_BARS = 1500             # M1 bars asked of the broker (a day is at most 1,440)
RECONCILE_MAX_AGE_HOURS = 96      # the job runs Mon-Fri: an older report (a missed weekday) warns
BROKER_TABLE = "bars_1m_broker"


def reconcile_file(state_dir: Path, account_id: str) -> Path:
    return Path(state_dir) / f"reconcile_{account_id}.json"


class ReconcileReport(Record):
    account_id: str
    ts_utc: UtcTimestamp
    window_from: UtcTimestamp
    window_to: UtcTimestamp
    tolerance: float                       # price units (CLOSE_TOL_POINTS x point)
    broker_minutes: int
    engine_minutes: int
    matched: int                           # minutes on both feeds
    close_mismatch: int
    missing_engine: int
    missing_broker: int
    max_close_diff: float
    divergence: float                      # diverging minutes / broker minutes
    severity: str                          # "ok" | "warning" | "error"
    spikes_flagged: int = 0                # spikes check_bars flagged on either feed
    spikes_unconfirmed: int = 0            # ... of which the other feed shows no matching move (kept as warnings)
    note: str = ""

    def save(self, state_dir: Path) -> None:
        write_atomic(reconcile_file(state_dir, self.account_id), self.model_dump_json(indent=1), durable=False)


def broker_m1_bars(raw: pd.DataFrame, point: float = POINT_DEFAULT) -> pd.DataFrame:
    """The broker's M1 (MT5 rates: bid OHLC, tick volume, spread in points) in the store's bar columns: ask = bid +
    spread x point, spread_mean = spread_max = spread x point, visible at minute close."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    r = raw.copy()
    ts = pd.to_datetime(r["ts_utc"], utc=True)
    spread = r["spread"].astype(float) * point if "spread" in r.columns else pd.Series(0.0, index=r.index)
    out = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(seconds=60)})
    for f in ("open", "high", "low", "close"):
        out[f"bid_{f}"] = r[f].astype(float)
        out[f"ask_{f}"] = r[f].astype(float) + spread
    out["tick_count"] = r["tick_count"].astype(float) if "tick_count" in r.columns else 0.0
    out["spread_mean"], out["spread_max"] = spread, spread
    return out.drop_duplicates("ts_utc", keep="last").sort_values("ts_utc").reset_index(drop=True)[BAR_COLUMNS]


def _severity(fraction: float) -> str:
    return "error" if fraction > ERROR_FRACTION else ("warning" if fraction > WARN_FRACTION else "ok")


def reconcile(engine: pd.DataFrame, broker: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp,
              tolerance: float, sessions: SessionTable = DEFAULT_SESSIONS) -> dict[str, Any]:
    """Minute-by-minute comparison over in-session minutes in [start, end) (see the module docstring)."""
    def frame(b: pd.DataFrame) -> pd.Series:
        if b.empty:
            return pd.Series(dtype=float)
        ts = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True)).as_unit("ns")
        s = pd.Series(b["bid_close"].astype(float).to_numpy(), index=ts)
        s = s[~s.index.duplicated(keep="last")]
        s = s[(s.index >= start) & (s.index < end)]
        return s[sessions.is_open(pd.DatetimeIndex(s.index))] if len(s) else s
    e, b = frame(engine), frame(broker)
    both = e.index.intersection(b.index)
    diff = (e.reindex(both) - b.reindex(both)).abs()
    n: dict[str, Any] = {"broker_minutes": len(b), "engine_minutes": len(e), "matched": len(both),
         "close_mismatch": int((diff > tolerance + 1e-9).sum()),
         "missing_engine": len(b.index.difference(e.index)), "missing_broker": len(e.index.difference(b.index)),
         "max_close_diff": float(diff.max()) if len(diff) else 0.0}
    bad = n["close_mismatch"] + n["missing_engine"] + n["missing_broker"]
    n["divergence"] = bad / len(b) if len(b) else (1.0 if len(e) else 0.0)
    n["severity"] = _severity(n["divergence"])
    if len(b) and not len(e):
        n["severity"] = "error"
    first = {"close_mismatch": diff[diff > tolerance + 1e-9].index,
             "missing_engine": b.index.difference(e.index), "missing_broker": e.index.difference(b.index)}
    n["first"] = {k: (pd.Timestamp(v[0]) if len(v) else None) for k, v in first.items()}
    return n


def _point(broker: Any, symbol: str) -> float:
    try:
        p = float(broker.symbol_info(symbol).point)
        return p if p > 0 else POINT_DEFAULT
    except Exception:            # an unreachable terminal is reported by get_bars below; the default point suffices
        return POINT_DEFAULT


def reconcile_account(store: Store, broker: Any, account_id: str, symbol: str, now: pd.Timestamp, *,
                      state_dir: Path | None = None, sessions: SessionTable = DEFAULT_SESSIONS) -> ReconcileReport:
    """D12 for one account over the day before `now` (the current, incomplete minute excluded): engine bars from the
    account's stored ticks against the broker's M1; writes the broker's M1 to `bars_1m_broker`, the divergence and
    unconfirmed-spike events to dq_events (source = account) and, with `state_dir`, the report file."""
    end = pd.Timestamp(now).tz_convert("UTC").floor("min")
    start = end - RECONCILE_WINDOW
    point = _point(broker, symbol)
    tol = CLOSE_TOL_POINTS * point
    bb = broker_m1_bars(broker.get_bars(symbol, "1m", RECONCILE_BARS), point)
    bb = bb[(bb["ts_utc"] >= start) & (bb["ts_utc"] < end)].reset_index(drop=True)
    ticks = store.read("ticks", source=account_id, symbol=symbol, start=start, end=end)
    eb = ticks_to_1m(ticks[["ts_utc", "bid", "ask"]], sessions) if not ticks.empty else pd.DataFrame(columns=BAR_COLUMNS)
    n = reconcile(eb, bb, start=start, end=end, tolerance=tol, sessions=sessions)
    events: list[DQEvent] = []
    for kind, label in (("close_mismatch", f"closes more than {tol:.2f} from the broker's M1 (max {n['max_close_diff']:.2f})"),
                        ("missing_engine", "broker M1 minutes with no engine bar"),
                        ("missing_broker", "engine bars with no broker M1 minute")):
        if n[kind]:
            events.append(DQEvent(ts_utc=n["first"][kind], check=f"reconcile_{kind}",
                                  severity="error" if n["severity"] == "error" else "warning",
                                  detail=f"{n[kind]} {label} of {n['broker_minutes']} ({n['divergence']:.2%} diverging)"))
    # D20: a spike on one feed is a warning only with no matching move on the other
    flagged = unconfirmed = 0
    for mine, other, name in ((eb, bb, "broker M1"), (bb, eb, "engine bars")):
        if len(mine) < 2:
            continue
        checked, ev = check_bars(mine.assign(dq_flag="")[BAR_COLUMNS], sessions=sessions)
        _, kept = confirm_spikes(checked, ev, other)
        flagged += sum(e.check == "spike" for e in ev)
        for e in kept:
            if e.check == "spike":
                unconfirmed += 1
                events.append(DQEvent(ts_utc=e.ts_utc, check="spike", severity="warning",
                                      detail=f"{'broker' if mine is bb else 'engine'} feed: {e.detail}; no match on the {name}"))
    note = ""
    if n["broker_minutes"] and not n["engine_minutes"]:
        note = "the engine stored no ticks in the window (engine down or not collecting)"
    elif not n["broker_minutes"]:
        note = "the broker returned no M1 bars in the window (market closed or terminal disconnected)"
    if not bb.empty:
        store.append(BROKER_TABLE, bb, source=account_id, symbol=symbol)
    if events:
        store.append("dq_events", events_frame(events), source=account_id, symbol=symbol, dedupe=False)
    rep = ReconcileReport(account_id=account_id, ts_utc=pd.Timestamp(now), window_from=start, window_to=end,
                          tolerance=tol, spikes_flagged=flagged, spikes_unconfirmed=unconfirmed, note=note,
                          **{k: v for k, v in n.items() if k != "first"})
    if state_dir is not None:
        rep.save(state_dir)
    return rep


def check_reconciliation(ctx: "HealthContext", account_id: str) -> "Check":
    """Health check over state/reconcile_<account>.json (the daily feed_reconcile job)."""
    from goldbot.ops.health import Check
    name = f"reconcile:{account_id}"
    f = reconcile_file(ctx.state_dir, account_id)
    if not f.exists():
        return Check(name=name, status="ok", reason="no reconciliation yet (feed_reconcile has not run)")
    try:
        r = ReconcileReport.model_validate_json(f.read_text())
    except (ValueError, OSError) as exc:
        return Check(name=name, status="warn", reason=f"reconcile file unreadable: {exc}")
    age_h = (ctx.now - r.ts_utc).total_seconds() / 3600
    msg = (f"{r.divergence:.2%} of {r.broker_minutes} broker minutes diverge (close {r.close_mismatch}, missing engine "
           f"{r.missing_engine}, missing broker {r.missing_broker}); {r.spikes_unconfirmed} unconfirmed spikes")
    msg += f"; {r.note}" if r.note else ""
    if age_h > RECONCILE_MAX_AGE_HOURS:
        return Check(name=name, status="warn", reason=f"last reconciliation {age_h:.0f} h ago: {msg}")
    status = {"error": "fail", "warning": "warn"}.get(r.severity, "ok")
    return Check(name=name, status=status, reason=msg)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- D23/F10 survival
SURVIVE_DUKA_T = 2.0              # = research.screen.MIN_T: the significance band a signal must reach on Dukascopy
SURVIVE_BROKER_T = 1.0            # the broker feed must agree in sign with at least this t
MIN_OVERLAP_DAYS = 90
MIN_OVERLAP_EVENTS = 100
SURVIVAL_RULE = (f"Dukascopy gross mean R > 0 with t >= {SURVIVE_DUKA_T} and broker gross mean R > 0 with t >= "
                 f"{SURVIVE_BROKER_T}, on the common period outside the holdout (>= {MIN_OVERLAP_DAYS} days, "
                 f">= {MIN_OVERLAP_EVENTS} events per feed)")


class SurvivalResult(Record):
    verdict: str                      # survives | fails | broker_only | no_signal | insufficient_overlap
    passed: bool
    rule: str = SURVIVAL_RULE
    overlap_from: UtcTimestamp | None = None
    overlap_to: UtcTimestamp | None = None
    overlap_days: float = 0.0
    dukascopy: dict[str, Any] = {}    # {"n", "mean_r", "t"} gross rule-only on the overlap
    broker: dict[str, Any] = {}
    detail: str = ""


def _band(r: dict[str, Any], t_min: float) -> bool:
    return int(r.get("n", 0)) > 0 and float(r.get("mean_r", 0.0)) > 0 and float(r.get("t", 0.0)) >= t_min


def survival_verdict(duka: dict[str, Any], broker: dict[str, Any], overlap_days: float) -> tuple[str, str]:
    """(verdict, detail) from the two feeds' gross rule-only results ({"n", "mean_r", "t"}) on their overlap."""
    nd, nb = int(duka.get("n", 0)), int(broker.get("n", 0))
    if overlap_days < MIN_OVERLAP_DAYS or nd < MIN_OVERLAP_EVENTS or nb < MIN_OVERLAP_EVENTS:
        return "insufficient_overlap", (f"{overlap_days:.0f} days, {nd} Dukascopy / {nb} broker events (need "
                                        f"{MIN_OVERLAP_DAYS} days and {MIN_OVERLAP_EVENTS} events each); broker history "
                                        "accumulates on the VPS (bars_1m_broker)")
    d_sig, b_sig = _band(duka, SURVIVE_DUKA_T), _band(broker, SURVIVE_DUKA_T)
    if d_sig and _band(broker, SURVIVE_BROKER_T):
        return "survives", "both feeds agree in sign and significance"
    if d_sig:
        return "fails", (f"Dukascopy t {float(duka.get('t', 0)):.2f}, broker mean R {float(broker.get('mean_r', 0)):+.3f} "
                         f"t {float(broker.get('t', 0)):.2f} (need > 0 and >= {SURVIVE_BROKER_T})")
    if b_sig:
        return "broker_only", "significant on broker bars only: a feed artefact, dropped (design F10)"
    return "no_signal", "neither feed shows the signal on the overlap"


def overlap_segments(duka: pd.DataFrame, broker: pd.DataFrame,
                     holdout: tuple[pd.Timestamp, pd.Timestamp] | None) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """[start, end) pieces of the common period of the two 1m frames, with the holdout window cut out."""
    if duka.empty or broker.empty:
        return []
    td, tb = pd.to_datetime(duka["ts_utc"], utc=True), pd.to_datetime(broker["ts_utc"], utc=True)
    s, e = max(td.min(), tb.min()), min(td.max(), tb.max()) + pd.Timedelta(minutes=1)
    if s >= e:
        return []
    if holdout is None or holdout[1] <= s or holdout[0] >= e:
        return [(s, e)]
    return [(a, b) for a, b in ((s, min(e, holdout[0])), (max(s, holdout[1]), e)) if a < b]


def _rule_on(spec: Any, b1: pd.DataFrame, segments: list[tuple[pd.Timestamp, pd.Timestamp]],
             extra_cost_usd: float) -> dict[str, Any]:
    """Gross rule-only result (the screen's numbers) of `spec` on `b1` restricted to `segments`, each prepared alone."""
    from goldbot.data.resample import resample_bars
    from goldbot.features.mtf import TF_LABEL, context_tfs
    from goldbot.research.pipeline import prepare, rule_only
    from goldbot.research.screen import screen_verdict
    ts = pd.to_datetime(b1["ts_utc"], utc=True)
    gross, net = [], []
    for a, b in segments:
        part = b1[(ts >= a) & (ts < b)].reset_index(drop=True)
        if len(part) < 2:
            continue
        tf = spec.timeframe
        b_dec = resample_bars(part, tf)
        context = {TF_LABEL[x]: resample_bars(part, x) for x in context_tfs(tf)}
        p = prepare(spec, b_dec, context, extra_cost_usd=extra_cost_usd)
        gross += [p.gross] if not p.gross.empty else []
        net += [p.labels] if not p.labels.empty else []
    g = pd.concat(gross, ignore_index=True) if gross else pd.DataFrame()
    n = pd.concat(net, ignore_index=True) if net else pd.DataFrame()
    v = screen_verdict(rule_only(g, n))
    return {"n": v["n"], "mean_r": v["gross_mean_r"], "t": v["gross_t"], "screen_passed": v["passed"]}


def survival_check(spec: Any, duka_1m: pd.DataFrame, broker_1m: pd.DataFrame, *,
                   holdout: tuple[pd.Timestamp, pd.Timestamp] | None, extra_cost_usd: float = 0.0) -> SurvivalResult:
    """D23/F10: the rule-only screen of one configuration on each feed over their common period (module docstring)."""
    segs = overlap_segments(duka_1m, broker_1m, holdout)
    days = float(sum((b - a).total_seconds() for a, b in segs) / 86400)
    frm = segs[0][0] if segs else None
    to = segs[-1][1] if segs else None
    if days < MIN_OVERLAP_DAYS:           # not worth labelling: the verdict cannot be a pass
        verdict, detail = survival_verdict({}, {}, days)
        return SurvivalResult(verdict=verdict, passed=False, overlap_from=frm, overlap_to=to, overlap_days=days,
                              detail=detail)
    d = _rule_on(spec, duka_1m, segs, extra_cost_usd)
    b = _rule_on(spec, broker_1m, segs, extra_cost_usd)
    verdict, detail = survival_verdict(d, b, days)
    return SurvivalResult(verdict=verdict, passed=verdict == "survives", overlap_from=frm, overlap_to=to,
                          overlap_days=days, dukascopy=d, broker=b, detail=detail)


def survival_lines(spec_label: str, r: SurvivalResult) -> list[str]:
    """Markdown for a report."""
    head = "**SURVIVES**" if r.passed else f"**{r.verdict.upper().replace('_', ' ')}**"
    out = [f"### Cross-feed survival ({spec_label}): {head}", "", f"- rule: {r.rule}"]
    if r.overlap_from is not None:
        out.append(f"- overlap {r.overlap_from:%Y-%m-%d} .. {r.overlap_to:%Y-%m-%d} ({r.overlap_days:.0f} days outside the holdout)")
    for name, x in (("Dukascopy", r.dukascopy), ("broker", r.broker)):
        if x:
            out.append(f"- {name}: {int(x['n']):,} events, gross mean R {float(x['mean_r']):+.3f}, t {float(x['t']):.2f}")
    return out + [f"- {r.detail}", ""]
