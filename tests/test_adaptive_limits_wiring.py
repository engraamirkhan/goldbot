"""ADR 0031 lane B: the adaptive controller wired in. The nightly limits_job decides each demo account's limits for the
next risk day from the FULL history of demo fills and shadow trades since each strategy's freeze (cut off before the
decision day), persists the decision and tells the owner about every change; the engine reads it through ADR 0032's
seam, takes it only at the risk-day roll, only tightens within a day (restarts included), gives the floors for a
decision that is not today's, and stays within the stricter of ADR 0032's state (8% revert -> defaults) and the
ceilings; live never rises above the defaults; exits are never gated."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import Engine, EngineConfig
from goldbot.engine.limits_apply import AdaptiveLimitsController, EngineLimits
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops import health
from goldbot.ops.checks._base import AccountRef
from goldbot.ops.gates_phase import ClosedTrade, EntryAudit, append_closed_trade
from goldbot.ops.jobs import JOBS
from goldbot.ops.limits_job import (
    STALE_DECISION_H,
    atr15_stats,
    bounds_for,
    build_evidence,
    change_notice,
    check_limits_controller,
    decide_account,
    decision_day_of,
    demo_evidence,
    limits_job,
    strategy_freezes,
)
from goldbot.ops.run import engine_limits
from goldbot.ops.services.engine import engine_limits_policy
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.risk.adaptive import AdaptiveChange, AdaptiveState, AdaptiveStore, Limits, day_start_ns
from goldbot.risk.gate import Stage
from goldbot.risk.limits_profile import (
    DEFAULT_PROFILE,
    LimitsEvidence,
    LimitsPolicy,
    LimitsStore,
    RiskCeilings,
    RiskOverrides,
    StaticController,
)
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus

PROFILE = "demo-2026-10-18"
OVERRIDES = RiskOverrides(profile=PROFILE, ceilings=RiskCeilings(daily=0.05, weekly=0.10, min_lot_risk_cap=0.01))
S = load_settings()
DAY = date(2026, 10, 28)                                 # the decision day (a Wednesday)
T0 = pd.Timestamp("2026-10-28 12:00", tz="UTC")          # on it
SLOT = pd.Timestamp("2026-10-27 23:05", tz="UTC")        # the nightly slot that decides it
FREEZE = pd.Timestamp("2024-06-01", tz="UTC")            # v1 went live long ago: full history matters
FLOOR = Limits(daily_cap=0.02, weekly_cap=0.05, supervisor_daily_cap=0.015, supervisor_weekly_cap=0.04,
               min_lot_risk_cap=None)


@pytest.fixture
def phase_file(tmp_path, monkeypatch):
    f = tmp_path / "phase_state.json"
    monkeypatch.setattr(acc_mod, "PHASE_FILE", f)
    return f


def _acc(mode: Literal["demo", "live"] = "demo", overrides: RiskOverrides | None = OVERRIDES,
         name: str = "icm-demo") -> acc_mod.Account:
    return acc_mod.Account(account_id=name, broker="icm", mode=mode, server="s", login=1, terminal_path="t",
                           server_tz="UTC", symbol="XAUUSD", magic_base=260100, enabled=True,
                           daily_loss_limit_abs=50.0, risk_overrides=overrides)


def _models(*versions: tuple[str, pd.Timestamp | None]) -> Any:
    return SimpleNamespace(entries=[SimpleNamespace(version=v, promoted_utc=p) for v, p in versions])


MODELS = _models(("v1", FREEZE), ("challenger", None))
_seq = iter(range(10**9))


def _closed(exit_utc: pd.Timestamp, r: float, *, version: str | None = "v1", exc: bool = False,
            account: str = "icm-demo") -> ClosedTrade:
    return ClosedTrade(account_id=account, broker="icm", mode="demo", exit_utc=exit_utc, ret=r * 0.001, pnl=r * 5.0,
                       equity_before=1_000.0, position_id=next(_seq), r=r, commission=0.0, swap=0.0,
                       pnl_basis="net", ret_basis="pnl", model_version=version, min_lot_exception=exc)


def _engine_file(d: Path, *, equity: float = 1_000.0, hwm: float = 1_000.0, stage: str = "normal",
                 ts: float | None = None, account: str = "icm-demo") -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({"account": account, "mode": "demo", "currency": "GBP",
                                                          "ts": time.time() if ts is None else ts, "equity": equity,
                                                          "balance_closed_hwm": hwm, "stage": stage}))


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def __call__(self, code: str, detail: str) -> None:
        self.events.append((code, detail))


def _decide(tmp_path: Path, acc: acc_mod.Account | None = None, day: date = DAY, sink: _Sink | None = None,
            settings: Any = S, models: Any = MODELS) -> dict[str, Any]:
    a = acc or _acc()
    return decide_account(settings, a, [a], state_dir=tmp_path, models=models, decision_day=day,
                          now_ns=time.time_ns(), dq=sink if sink is not None else _Sink())


def _notices(d: Path) -> list[str]:
    f = d / "agent_runs.jsonl"
    rows = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    return [r["detail"] for r in rows if r["status"] == "notice" and r["role"] == "limits"]


def _save(d: Path, limits: Limits, day: date = DAY, *, decided_ns: int | None = None, account: str = "icm-demo") -> None:
    AdaptiveStore(d).save(AdaptiveState(account_id=account, day=day, limits=limits,
                                        decided_ns=time.time_ns() if decided_ns is None else decided_ns), [])


def _ns(ts: pd.Timestamp) -> int:
    return int(ts.value)


# ---------------------------------------------------------------------------------------------- the nightly job
def test_the_job_decides_the_risk_day_after_its_slot_and_is_scheduled_before_the_roll():
    assert decision_day_of(SLOT) == DAY
    s = S.scheduler.limits_job
    assert "limits_job" in JOBS and s.kind == "daily"
    hh, mm = (int(x) for x in s.at.split(":"))
    assert hh * 60 + mm + s.max_late_hours * 60 < 24 * 60       # never run (caught up) after the 00:00 UTC roll


def test_the_exploration_stop_reads_the_full_history_since_the_freeze_and_survives_a_restart(tmp_path, phase_file):
    # 12 exception trades at -1R, 400 days before the decision: any window would miss them and keep exploring
    old = pd.Timestamp("2025-09-20 10:00", tz="UTC")
    for i in range(12):
        append_closed_trade(tmp_path, _closed(old + pd.Timedelta(days=i), -1.0, exc=True))
    _engine_file(tmp_path)
    out = _decide(tmp_path)
    st = AdaptiveStore(tmp_path).load("icm-demo")
    assert out["demo_trades"] == 12 and st is not None and st.minlot_explore_stopped
    _engine_file(tmp_path)
    _decide(tmp_path, day=DAY + timedelta(days=1))                  # the next night, a new process
    st = AdaptiveStore(tmp_path).load("icm-demo")
    assert st is not None and st.minlot_explore_stopped and st.limits.min_lot_risk_cap is None
    assert st.day == DAY + timedelta(days=1)


def test_evidence_is_every_trade_after_its_freeze_closed_before_the_decision_day(tmp_path):
    from goldbot.ops.cost_imputation import CostImputer
    freezes = strategy_freezes(MODELS)
    before = _closed(FREEZE - pd.Timedelta(days=1), 2.0)                      # before v1 went live
    long_ago = _closed(FREEZE + pd.Timedelta(days=3), 1.0)                     # kept: no window
    last_night = _closed(pd.Timestamp("2026-10-27 23:59", tz="UTC"), -0.5)
    on_the_day = _closed(pd.Timestamp("2026-10-28 00:00", tz="UTC"), 3.0)      # not before the decision day
    unknown = _closed(pd.Timestamp("2026-10-20", tz="UTC"), 0.4, version=None)
    other = _closed(pd.Timestamp("2026-10-20", tz="UTC"), 0.4, account="vantage-demo")
    as_of = day_start_ns(DAY)
    demo, problems = demo_evidence([before, long_ago, last_night, on_the_day, unknown, other], "icm-demo",
                                   CostImputer.from_settings(S), freezes, as_of)
    ev = build_evidence("icm-demo", as_of, demo=demo, shadow=[], freezes=freezes, drawdown=0.0, stage=Stage.NORMAL,
                        cost_ratio_20d=None, atr15=(None, None))
    used = {t.id for t in ev.used()}
    ids = {f"demo:icm-demo:{t.position_id}" for t in (long_ago, last_night, unknown)}
    assert not problems and ev.problems() == [] and used == ids
    assert all(t.exit_ns < as_of for t in ev.trades) and ev.day() == DAY


def test_a_demo_fill_without_a_net_r_fails_closed_to_the_floors(tmp_path, phase_file):
    t = _closed(pd.Timestamp("2026-10-20", tz="UTC"), 1.0).model_copy(update={"r": None})
    append_closed_trade(tmp_path, t)
    _engine_file(tmp_path)
    _save(tmp_path, FLOOR.model_copy(update={"daily_cap": 0.03}), DAY - timedelta(days=1))
    sink = _Sink()
    out = _decide(tmp_path, sink=sink)
    assert out["problems"] and out["limits"]["daily_cap"] == 0.02
    assert "adaptive_limits_evidence_missing" in {c for c, _ in sink.events}


@pytest.mark.parametrize("why", ["missing", "stale", "no_mark"])
def test_an_engine_file_that_is_missing_stale_or_without_a_mark_gives_the_floors_and_a_dq_event(tmp_path, why,
                                                                                               phase_file):
    if why == "stale":
        _engine_file(tmp_path, ts=time.time() - 3 * 3600)
    elif why == "no_mark":
        _engine_file(tmp_path, hwm=0.0)
    sink = _Sink()
    out = _decide(tmp_path, sink=sink)
    st = AdaptiveStore(tmp_path).load("icm-demo")
    assert out["limits"] == FLOOR.model_dump() and st is not None and st.day == DAY and st.decided_ns is not None
    assert {"adaptive_limits_evidence_missing", "adaptive_limits_evidence_invalid"} <= {c for c, _ in sink.events}


def test_every_change_is_told_to_the_owner_with_units_and_no_buttons(tmp_path, phase_file):
    _save(tmp_path, FLOOR.model_copy(update={"daily_cap": 0.03}), DAY - timedelta(days=1))
    _engine_file(tmp_path, stage="size_down")                         # stage SIZE_DOWN -> the floors
    _decide(tmp_path)
    (text,) = _notices(tmp_path)
    assert text.startswith("Limit change (demo, icm-demo): daily loss limit £30 -> £20 (3% -> 2% of £1,000)")
    assert "Why: size-down or halt stage" in text and "Week limit unchanged at £50 (5%)" in text
    assert len(text.splitlines()) <= 2 and "UTC" in text
    _engine_file(tmp_path, stage="size_down")
    _decide(tmp_path, day=DAY + timedelta(days=1))                     # no change: no notice
    assert len(_notices(tmp_path)) == 1


def test_the_notice_masks_an_account_name_that_carries_digits():
    ch = AdaptiveChange(account_id="x", day=DAY, field="daily_cap", old=0.03, new=0.02, reasons=("dd_floor",),
                        evidence_ids=(), ts_ns=0)
    text = change_notice("icm-demo-51234567", [ch], equity=1_000.0, currency="GBP", limits=FLOOR, at=T0,
                         tz="Europe/London")
    assert "51234567" not in text and "icm-demo-…67" in text


def test_the_job_skips_accounts_without_demo_ceilings_and_runs_none_when_switched_off(tmp_path, phase_file):
    accs = [_acc("live", overrides=None, name="icm-live"), _acc("demo", overrides=None, name="vantage-demo")]
    ctx = SimpleNamespace(settings=S, accounts=accs, state_dir=tmp_path, models=MODELS, clock=None,
                          store=SimpleNamespace(append=lambda *a, **k: 0))
    assert limits_job(ctx, SLOT) == {}                                 # type: ignore[arg-type]
    off = S.model_copy(update={"risk": S.risk.model_copy(update={"adaptive_limits": False})})
    ctx.settings, ctx.accounts = off, [_acc()]
    assert limits_job(ctx, SLOT) == {}                                 # type: ignore[arg-type]
    assert isinstance(engine_limits_policy(off, _acc()).controller, StaticController)


def test_atr_needs_a_year_of_closed_bars_and_reads_only_before_the_decision_day():
    end = pd.Timestamp(DAY, tz="UTC")
    ts = pd.date_range(end - pd.Timedelta(days=366), end + pd.Timedelta(hours=2), freq="15min", inclusive="left")
    rng = np.where(ts < end - pd.Timedelta(days=30), 6.0, 3.0)        # calmer lately
    rng = np.where(ts >= end, 50.0, rng)                               # the decision day itself: never read
    bars = pd.DataFrame({"ts_utc": ts, "high": 2400 + rng / 2, "low": 2400 - rng / 2, "close": 2400.0})
    med, p90 = atr15_stats(bars, day_start_ns(DAY))
    assert med == pytest.approx(3.0, rel=0.02) and p90 == pytest.approx(6.0, rel=0.02)
    assert atr15_stats(bars[ts >= end - pd.Timedelta(days=200)], day_start_ns(DAY)) == (None, None)


# ---------------------------------------------------------------------------------------------- health
def _hctx(tmp_path: Path, adaptive: bool = True, now: pd.Timestamp | None = None) -> Any:
    return health.HealthContext(state_dir=tmp_path, now=now or pd.Timestamp.now("UTC"), settings=S,
                                accounts=[AccountRef(account_id="icm-demo", adaptive=adaptive)],
                                get_secret=lambda k: None)


def test_the_limits_controller_check_fails_when_the_last_decision_is_older_than_26_hours(tmp_path):
    assert check_limits_controller(_hctx(tmp_path, adaptive=False), "icm-demo") == []
    (c,) = check_limits_controller(_hctx(tmp_path), "icm-demo")
    assert c.status == "warn" and "no adaptive limits decision" in c.reason
    now = T0                                                           # a Wednesday
    _save(tmp_path, FLOOR, decided_ns=_ns(now - pd.Timedelta(hours=STALE_DECISION_H + 0.5)))
    (c,) = check_limits_controller(_hctx(tmp_path, now=now), "icm-demo")
    assert c.name == "limits_controller:icm-demo" and c.status == "fail" and "26 h" in c.reason
    _save(tmp_path, FLOOR, decided_ns=_ns(now - pd.Timedelta(hours=2)))
    (c,) = check_limits_controller(_hctx(tmp_path, now=now), "icm-demo")
    assert c.status == "ok"
    assert health.check_limits_controller is check_limits_controller
    # no decision is made for Saturday and Sunday: Friday 23:05's (for Friday) is still fresh enough on Sunday night
    sun = pd.Timestamp("2026-11-01 22:30", tz="UTC")
    _save(tmp_path, FLOOR, decided_ns=_ns(pd.Timestamp("2026-10-29 23:05", tz="UTC")))
    assert check_limits_controller(_hctx(tmp_path, now=sun), "icm-demo")[0].status == "ok"
    mon = pd.Timestamp("2026-11-02 01:30", tz="UTC")                   # Sunday night's run missed: fails again
    assert check_limits_controller(_hctx(tmp_path, now=mon), "icm-demo")[0].status == "fail"


# ---------------------------------------------------------------------------------------------- the engine side
UP = FLOOR.model_copy(update={"daily_cap": 0.035, "weekly_cap": 0.08, "supervisor_daily_cap": 0.0225,
                              "min_lot_risk_cap": 0.01})


def _policy(tmp_path: Path, acc: acc_mod.Account | None = None) -> LimitsPolicy:
    a = acc or _acc()
    return LimitsPolicy(a, engine_limits(S, a), AdaptiveLimitsController(tmp_path, a.account_id,
                                                                          bounds_for(S, a, [a])))


def _ev(at: pd.Timestamp, **kw: Any) -> LimitsEvidence:
    return LimitsEvidence(now_ns=_ns(at), **kw)


def test_demo_accounts_with_ceilings_use_the_adaptive_controller(phase_file):
    assert isinstance(engine_limits_policy(S, _acc()).controller, AdaptiveLimitsController)
    assert isinstance(engine_limits_policy(S, _acc(overrides=None)).controller, StaticController)
    assert isinstance(engine_limits_policy(S, _acc(), paper=True).controller, StaticController)


def test_todays_decision_is_traded_and_a_decision_for_another_day_gives_the_floors(tmp_path, phase_file):
    _save(tmp_path, UP)
    lim, cur = _policy(tmp_path).resolve(_ev(T0))
    assert (lim.daily_cap, lim.weekly_cap, lim.min_lot_risk_cap) == (0.035, 0.08, 0.01) and cur.profile == PROFILE
    assert cur.supervisor_daily_cap == 0.0225
    _save(tmp_path, UP, DAY - timedelta(days=1))                       # yesterday's: stale
    lim, cur = _policy(tmp_path).resolve(_ev(T0))
    assert (lim.daily_cap, lim.weekly_cap, lim.min_lot_risk_cap) == (0.02, 0.05, None)
    assert cur.profile == DEFAULT_PROFILE and "not today" in cur.reason


def test_no_or_an_unreadable_decision_gives_the_floors(tmp_path, phase_file):
    lim, _ = _policy(tmp_path).resolve(_ev(T0))
    assert lim.daily_cap == 0.02 and lim.min_lot_risk_cap is None
    from goldbot.db.connection import Database
    _save(tmp_path, UP)
    with Database.open(tmp_path, "core").write() as conn:
        conn.execute("UPDATE adaptive_state SET body = '{\"account_id\": 1}'")
    lim, _ = _policy(tmp_path).resolve(_ev(T0))
    assert lim.daily_cap == 0.02 and lim.min_lot_risk_cap is None


def test_intraday_a_higher_decision_is_refused_and_a_lower_one_applies_at_once(tmp_path, phase_file):
    _save(tmp_path, FLOOR)
    pol = _policy(tmp_path)
    assert pol.resolve(_ev(T0))[0].daily_cap == 0.02
    _save(tmp_path, UP)                                                # a later write the same day: no loosening
    pol.controller.RELOAD_S = 0.0                                      # type: ignore[attr-defined]
    assert pol.resolve(_ev(T0 + pd.Timedelta(hours=1)))[0].daily_cap == 0.02
    assert pol.resolve(_ev(T0 + pd.Timedelta(hours=12)))[0].daily_cap == 0.02     # the roll: UP is for DAY, stale
    _save(tmp_path, UP, DAY + timedelta(days=1))                       # that day's decision, written after its roll
    assert pol.resolve(_ev(T0 + pd.Timedelta(hours=13)))[0].daily_cap == 0.02     # waits for the next roll
    _save(tmp_path, UP, DAY + timedelta(days=2))
    assert pol.resolve(_ev(T0 + pd.Timedelta(hours=36)))[0].daily_cap == 0.035    # the next roll takes it
    _save(tmp_path, FLOOR.model_copy(update={"daily_cap": 0.025}), DAY + timedelta(days=2))
    assert pol.resolve(_ev(T0 + pd.Timedelta(hours=37)))[0].daily_cap == 0.025    # tightening: at once


def test_a_4_percent_drawdown_gives_the_floors_for_the_rest_of_the_day(tmp_path, phase_file):
    _save(tmp_path, UP)
    pol = _policy(tmp_path)
    assert pol.resolve(_ev(T0, drawdown=0.039))[0].daily_cap == 0.035
    assert pol.resolve(_ev(T0, drawdown=0.04))[0].daily_cap == 0.02
    assert pol.resolve(_ev(T0 + pd.Timedelta(hours=1), drawdown=0.0))[0].daily_cap == 0.02


def test_the_8_percent_revert_wins_over_the_controller(tmp_path, phase_file):
    _save(tmp_path, UP)
    lim, cur = _policy(tmp_path).resolve(_ev(T0, reverted=True))
    assert (lim.daily_cap, lim.weekly_cap, lim.min_lot_risk_cap) == (0.02, 0.05, None)
    assert cur.profile == DEFAULT_PROFILE and cur.supervisor_daily_cap is None


def test_a_stored_value_above_the_ceilings_never_raises_the_limits(tmp_path, phase_file):
    # the stored state was written under wider bounds (or edited): the rung at or below it, within today's ceilings
    tight = _acc(overrides=RiskOverrides(profile=PROFILE, ceilings=RiskCeilings(daily=0.03, weekly=0.06,
                                                                               min_lot_risk_cap=0.006)))
    _save(tmp_path, UP)
    lim, _ = _policy(tmp_path, tight).resolve(_ev(T0))
    assert lim.daily_cap == 0.03 and lim.weekly_cap == 0.06 and lim.min_lot_risk_cap == 0.006


def test_live_and_tiny_live_never_get_more_than_the_defaults_from_the_adaptive_controller(tmp_path, phase_file):
    live = _acc("live", overrides=None, name="icm-live")
    b = bounds_for(S, live, [live])
    assert b.ceiling == b.floor and not b.explore_min_lot
    for gates in (["paper_to_tiny_live"], ["paper_to_tiny_live", "tiny_live_to_full_size"]):
        phase_file.write_text(json.dumps({"phase": 4, "gates_passed": gates}))
        _save(tmp_path, UP, account="icm-live")
        defaults = engine_limits(S, live)
        forced = LimitsPolicy(live, defaults, AdaptiveLimitsController(tmp_path, "icm-live", bounds_for(S, _acc(), [_acc()])))
        lim, cur = forced.resolve(_ev(T0))                             # even with demo bounds forced onto it
        assert lim.daily_cap <= defaults.daily_cap and lim.weekly_cap <= defaults.weekly_cap
        assert lim.min_lot_risk_cap is None and cur.profile == DEFAULT_PROFILE


def test_the_combined_caps_follow_a_lower_adaptive_supervisor_cap(tmp_path):
    s = S.risk
    cur = {"profile": PROFILE, "daily_cap": 0.05, "weekly_cap": 0.10, "min_lot_risk_cap": 0.01,
           "project_open_risk": True, "reason": "adaptive", "evidence_ids": [], "supervisor_daily_cap": 0.0225,
           "supervisor_weekly_cap": 0.05}
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(
        {"account": "icm-demo", "mode": "demo", "ts": time.time(), "currency": "GBP", "equity": 1_000.0,
         "day_start_equity": 1_000.0, "week_start_equity": 1_000.0, "balance_closed_hwm": 1_000.0,
         "limits_current": cur}))
    ceilings = RiskCeilings(daily=0.0375, weekly=0.075)
    st = Supervisor(tmp_path, SupervisorLimits.from_settings(s, ceilings)).evaluate()
    assert (st["daily_cap"], st["weekly_cap"]) == (0.0225, 0.05)


# ---------------------------------------------------------------------------------------------- the engine end to end
class _Equity(PaperBroker):
    def __init__(self, equity: float = 1_000.0):
        super().__init__(equity=equity)
        self.eq = equity

    def account(self) -> AccountInfo:
        return AccountInfo(login=0, equity=self.eq, balance=self.eq, margin=0.0, margin_free=self.eq, leverage=20,
                           currency="GBP", server="paper")


def _engine(tmp_path: Path, pb: _Equity) -> Engine:
    pol = engine_limits_policy(S, _acc(), state_dir=tmp_path)
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), mode="demo"), pb,
                  [], {}, ApprovalCenter({1}, bus=ApprovalBus(tmp_path)), limits=pol.defaults, limits_policy=pol)


def _tick(s: float = 0.0) -> Tick:
    return Tick(ts_utc=T0 + pd.Timedelta(seconds=s), bid=2400.0, ask=2400.2)


def test_the_engine_trades_todays_decision_and_publishes_the_supervisor_caps(tmp_path, phase_file):
    _save(tmp_path, UP)
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb)
    eng._refresh_account(_tick())
    assert eng.gate.limits.daily_cap == 0.035 and eng.limits_current.profile == PROFILE
    eng._write_state()
    d = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert d["limits_current"]["supervisor_daily_cap"] == 0.0225


def test_a_restart_the_same_day_never_loosens_past_what_was_applied(tmp_path, phase_file):
    _save(tmp_path, UP)
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb)
    eng._refresh_account(_tick())
    pb.eq = 955.0                                                      # 4.5% below the mark: floors for the day
    eng._refresh_account(_tick(1))
    assert eng.gate.limits.daily_cap == 0.02
    pb.eq = 1_000.0
    again = _engine(tmp_path, pb)                                      # a restart, drawdown recovered
    again._refresh_account(_tick(2))
    assert again.gate.limits.daily_cap == 0.02 and LimitsStore(tmp_path).changes("icm-demo")[-1].current.daily_cap == 0.02


def test_exits_pass_while_the_adaptive_floors_and_caps_bind(tmp_path, phase_file):
    from goldbot.specialists.trend import TrendSpecialist
    from goldbot.telegram.approvals import Proposal
    from tests.test_engine_clock import WED
    from tests.test_engine_clock import _engine as clock_engine
    from tests.test_engine_clock import _tick as clock_tick
    eng, pb, _ = clock_engine(tmp_path)
    agent = TrendSpecialist()
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_400.2, stop=2_390.0, target=2_430.0, p=0.6, ev_r=0.1, spread_points=20.0, top_features=[])
    eng._execute(prop, agent, 0.1, 2_390.0, 2_430.0, requested=2_400.2, atr_usd=10.0)
    (pid,) = eng.open
    eng._limits = EngineLimits(_policy(tmp_path), tmp_path, "icm-demo", eng.gate.limits)   # no decision: the floors
    eng._refresh_account(clock_tick(WED))
    assert eng.gate.limits.daily_cap == 0.02 and "adaptive" in eng.limits_current.reason
    eng.state.day_start_equity = eng.state.week_start_equity = eng.state.equity / 0.85   # 15% down today
    assert {"daily_cap", "weekly_cap"} <= set(eng.gate.account_reasons(eng.state))
    calls: list[Any] = []
    real = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real(*a, **k)

    eng.gate.check = spy                                               # type: ignore[method-assign]
    later = clock_tick(WED + pd.Timedelta(minutes=1), bid=2_389.0)     # the long's stop is hit
    pb.on_tick(later)
    eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and pid not in {p.position_id for p in pb.positions()} and calls == []


# ---------------------------------------------------------------------------------------------- exception trades
@pytest.mark.parametrize("card, sent, recorded", [(False, ["min_lot_exception"], True), (True, [], False),
                                                   (True, None, True)])
def test_the_order_records_the_exception_of_the_decision_it_was_sent_on(tmp_path, card, sent, recorded):
    # the gate re-runs at the click: an entry that became an exception there counts toward the 60 and the -10R stop
    from goldbot.risk.gate import GateDecision
    from goldbot.specialists.trend import TrendSpecialist
    from goldbot.telegram.approvals import Proposal
    from tests.test_engine_clock import _engine as clock_engine
    assert "min_lot_exception" in EntryAudit.model_fields and "min_lot_exception" in ClosedTrade.model_fields
    eng, pb, _ = clock_engine(tmp_path)
    agent = TrendSpecialist()
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.01,
                    entry=2_400.2, stop=2_390.0, target=2_430.0, p=0.6, ev_r=0.1, spread_points=20.0, top_features=[],
                    min_lot_exception=card)
    gd = None if sent is None else GateDecision(allowed=True, lots=0.01, reasons=sent)
    eng._execute(prop, agent, 0.01, 2_390.0, 2_430.0, requested=2_400.2, atr_usd=10.0, gd=gd)
    (tr,) = eng.open.values()
    assert tr.audit is not None and tr.audit.min_lot_exception is recorded


def test_an_exception_trades_stop_risk_counts_against_the_daily_limit(phase_file, tmp_path):
    _save(tmp_path, UP)
    lim, _ = _policy(tmp_path).resolve(_ev(T0))                        # daily 3.5%, min-lot 1%, the £50 limit
    intent = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=8.0, cost_atr=0.05,
                    multiplier=1.0, price=4_150.0, account_currency="GBP", acct_per_usd=1 / 1.33, timeframe="15m",
                    family="trend")

    def state(equity: float) -> AccountState:
        return AccountState(equity=equity, balance_closed_hwm=1_000.0, day_start_equity=1_000.0,
                            week_start_equity=1_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                            spread_points=25.0, open_risk=0.0)

    ok = RiskGate(lim).check(intent, state(1_000.0))
    assert ok.allowed and ok.reasons == ["min_lot_exception"]
    assert RiskGate(lim).daily_loss_limit(state(970.0)) == pytest.approx(35.0)   # the 3.5% cap binds, not the £50
    # £30 lost today (3% < the 3.5% cap, under the £35 limit): the ~£6 minimum-lot stop risk would pass the limit
    late = RiskGate(lim).check(intent, state(970.0))
    assert not late.allowed and late.reasons == ["daily_loss_limit_abs"] and late.risk_amount > 5.0
    wider = RiskGate(lim.model_copy(update={"daily_cap": 0.05})).check(intent, state(970.0))   # 5%: £50 room
    assert wider.allowed and wider.reasons == ["min_lot_exception"]


# ---------------------------------------------------------------------------------------------- the replay
def test_the_replay_is_deterministic_writes_nothing_and_prints_one_line_per_day(tmp_path, phase_file):
    from goldbot.ops.limits_replay import render, replay
    start = pd.Timestamp("2026-09-01 10:00", tz="UTC")
    for i in range(70):
        append_closed_trade(tmp_path, _closed(start + pd.Timedelta(days=i // 2, hours=i % 2), (1.5, -0.5)[i % 3 == 0]))
    before = sorted(p.name for p in tmp_path.iterdir())
    a = _acc()
    runs = [replay(S, a, [a], state_dir=tmp_path, models=MODELS, start=date(2026, 10, 1), end=date(2026, 10, 20))
            for _ in range(2)]
    assert runs[0] == runs[1] and len(runs[0]) == 14                  # 20 days less 3 weekends' 6 days
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "shadow_book.json") == before   # no core.db
    lines = render(runs[0], "not given")
    assert "never a tuning step" in lines[0] and len(lines) == 3 + 14 and lines[3].startswith("2026-10-01")
    assert any(d.limits.daily_cap > 0.02 for d in runs[0])            # the replay does move on good demo fills


def _year_of_bars(recent_range: float) -> pd.DataFrame:
    end = pd.Timestamp(DAY, tz="UTC")
    ts = pd.date_range(end - pd.Timedelta(days=366), end, freq="15min", inclusive="left")
    rng = np.where(ts < end - pd.Timedelta(days=30), 6.0, recent_range)
    return pd.DataFrame({"ts_utc": ts, "high": 2400 + rng / 2, "low": 2400 - rng / 2, "close": 2400.0})


@pytest.mark.parametrize("recent, cap", [(3.0, 0.01), (9.0, None)])
def test_the_first_night_opens_the_minimum_lot_exploration_unless_atr_is_at_its_1_year_high(tmp_path, phase_file,
                                                                                           recent, cap):
    _engine_file(tmp_path)
    a = _acc()
    bars = _year_of_bars(recent)
    out = decide_account(S, a, [a], state_dir=tmp_path, models=MODELS, decision_day=DAY, now_ns=time.time_ns(),
                         dq=_Sink(), bars_15m=lambda s, e: bars)
    assert out["limits"]["min_lot_risk_cap"] == cap and out["limits"]["daily_cap"] == 0.02
    notices = _notices(tmp_path)
    if cap:
        assert notices[0].startswith("Limit change (demo, icm-demo): smallest-trade cap off -> 1%")
    else:
        assert notices == []


def test_a_weekend_night_makes_no_decision_and_keeps_the_loosening_count(tmp_path, phase_file):
    fri = date(2026, 10, 30)
    AdaptiveStore(tmp_path).save(AdaptiveState(account_id="icm-demo", day=fri, limits=FLOOR,
                                               up_streak={"daily_cap": 2}, decided_ns=1), [])
    ctx = SimpleNamespace(settings=S, accounts=[_acc()], state_dir=tmp_path, models=MODELS, clock=None,
                          store=SimpleNamespace(append=lambda *a, **k: 0))
    for slot in ("2026-10-30 23:05", "2026-10-31 23:05"):            # Friday's and Saturday's nights, engine stale
        out = limits_job(ctx, pd.Timestamp(slot, tz="UTC"))           # type: ignore[arg-type]
        assert "skipped" in out
    st = AdaptiveStore(tmp_path).load("icm-demo")
    assert st is not None and st.day == fri and st.up_streak == {"daily_cap": 2} and st.tightened_day is None


def test_tomorrows_decision_written_before_the_roll_waits_for_it(tmp_path, phase_file):
    _save(tmp_path, UP)
    pol = _policy(tmp_path)
    pol.controller.RELOAD_S = 0.0                                      # type: ignore[attr-defined]
    assert pol.resolve(_ev(T0))[0].daily_cap == 0.035
    top = FLOOR.model_copy(update={"daily_cap": 0.05, "weekly_cap": 0.10})
    _save(tmp_path, top, DAY + timedelta(days=1))                      # the 23:05 run decides tomorrow
    late = pd.Timestamp("2026-10-28 23:30", tz="UTC")
    assert pol.resolve(_ev(late))[0].daily_cap == 0.035                 # the last hour of today: today's, not floors
    assert pol.resolve(_ev(late + pd.Timedelta(minutes=31)))[0].daily_cap == 0.05   # from the roll: tomorrow's
    fresh = _policy(tmp_path)                                          # a restart in the last hour, nothing logged
    assert fresh.resolve(_ev(late))[0].daily_cap == 0.02               # today's is gone: the floors, never tomorrow's
