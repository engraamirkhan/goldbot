"""ADR 0032: demo risk limits under the owner's delegation (2026-10-18: "approve demo risk limits: daily cap £50,
min-lot cap 1%, weekly 10%, demo only").

* `risk_overrides.ceilings` in config/accounts.yaml, accepted on a demo account only and never above the approval;
* the gate reads `LimitsPolicy.resolve` every account refresh: [default .. ceiling] on a demo account with ceilings,
  never above the defaults on any other account, whatever the controller returns;
* the supervisor's combined caps rise in lockstep only when every enabled account is demo with ceilings;
* the 8% size-down reverts to the defaults, sticky across restarts until the owner's re-arm at a normal stage;
* every change of the current limits is logged in core.db; trades, decisions and shadow trades carry the profile."""
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.engine import Engine, EngineConfig
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops.gates_phase import ClosedTrade, EntryAudit
from goldbot.ops.run import engine_limits
from goldbot.ops.services.engine import engine_limits_policy
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import Stage
from goldbot.risk.limits_profile import (
    DEFAULT_PROFILE,
    SUPERVISOR_DEMO_ENVELOPE,
    LimitsEvidence,
    LimitsPolicy,
    LimitsStore,
    RiskCeilings,
    RiskLimitsCurrent,
    RiskOverrides,
    counts_as_default_evidence,
    supervisor_ceilings,
)
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus

PROFILE = "demo-2026-10-18"
OVERRIDES = RiskOverrides(profile=PROFILE, ceilings=RiskCeilings(daily=0.05, weekly=0.10, min_lot_risk_cap=0.01))
GBP = 1 / 1.33
T0 = pd.Timestamp("2026-10-28 12:00", tz="UTC")


@pytest.fixture
def phase_file(tmp_path, monkeypatch):
    f = tmp_path / "phase_state.json"
    monkeypatch.setattr(acc_mod, "PHASE_FILE", f)
    return f


def _acc(mode: Literal["demo", "live"] = "demo", overrides: RiskOverrides | None = OVERRIDES,
         enabled: bool = True, name: str | None = None) -> acc_mod.Account:
    return acc_mod.Account(account_id=name or f"icm-{mode}", broker="icm", mode=mode, server="s", login=1,
                           terminal_path="t", server_tz="UTC", symbol="XAUUSD", magic_base=260100, enabled=enabled,
                           daily_loss_limit_abs=50.0, risk_overrides=overrides)


def _yaml(tmp_path: Path, mode: str, overrides: dict | None, abs_limit: float | None = 50) -> Path:
    a: dict[str, Any] = {"broker": "icm", "mode": mode, "server": "s", "login": None, "terminal_path": "t",
                         "server_tz": "UTC", "symbol": "XAUUSD", "magic_base": 260100, "enabled": True,
                         "daily_loss_limit_abs": abs_limit}
    if overrides is not None:
        a["risk_overrides"] = overrides
    p = tmp_path / "accounts.yaml"
    p.write_text(yaml.safe_dump({"accounts": {"acct-x": a}}))
    return p


OV_YAML = {"profile": PROFILE, "ceilings": {"daily": 0.05, "weekly": 0.10, "min_lot_risk_cap": 0.01}}


# ---------------------------------------------------------------------------------------------- config validation
def test_risk_overrides_on_a_live_account_fail_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    with pytest.raises(ValueError, match="demo"):
        acc_mod.load_accounts(_yaml(tmp_path, "live", OV_YAML))
    with pytest.raises(ValidationError):
        _acc("live")


def test_the_shipped_icm_demo_account_carries_exactly_the_approved_ceilings(monkeypatch):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    accs = acc_mod.load_accounts()
    demo = accs["icm-demo"]
    assert demo.risk_overrides is not None and demo.risk_overrides.profile == PROFILE
    assert demo.risk_overrides.ceilings == OVERRIDES.ceilings and demo.daily_loss_limit_abs == 50
    assert all(a.risk_overrides is None for a in accs.values() if a.mode != "demo")


@pytest.mark.parametrize("ceilings", [{"daily": 0.051}, {"weekly": 0.11}, {"min_lot_risk_cap": 0.0101},
                                      {"daily": 0}, {"daily": -0.01}, {"surprise": 0.01}])
def test_ceilings_above_the_owners_approval_or_unknown_keys_fail_validation(tmp_path, monkeypatch, ceilings):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    with pytest.raises(ValueError):
        acc_mod.load_accounts(_yaml(tmp_path, "demo", {"profile": PROFILE, "ceilings": ceilings}))


@pytest.mark.parametrize("profile", ["default", "live-2026-10-18", "demo", "demo-2026-10-18; rm"])
def test_a_profile_name_that_is_not_demo_and_a_date_fails_validation(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    with pytest.raises(ValueError):
        acc_mod.load_accounts(_yaml(tmp_path, "demo", {**OV_YAML, "profile": profile}))


def test_overrides_without_the_absolute_daily_limit_fail_validation(tmp_path, monkeypatch):
    # the 5% cap is safe only because ADR 0004's £50 still binds below it (min of the two)
    monkeypatch.setattr(acc_mod, "get_secret", lambda k: None)
    with pytest.raises(ValueError, match="daily_loss_limit_abs"):
        acc_mod.load_accounts(_yaml(tmp_path, "demo", OV_YAML, abs_limit=None))


def test_an_account_with_an_unknown_mode_gets_the_default_limits():
    acc = acc_mod.Account.model_construct(**{**_acc().model_dump(), "mode": "paper", "risk_overrides": OVERRIDES})
    defaults = RiskLimits(daily_cap=0.02, weekly_cap=0.05)
    lim, cur = LimitsPolicy(acc, defaults).resolve(LimitsEvidence())
    assert cur.profile == DEFAULT_PROFILE and (lim.daily_cap, lim.weekly_cap, lim.min_lot_risk_cap) == (0.02, 0.05, None)


def test_a_live_terminal_behind_a_demo_labelled_account_is_refused():
    info = SimpleNamespace(login=1, server="s", trade_mode="real", margin_mode="hedging")
    why = acc_mod.verify_terminal_account(_acc("demo"), info)
    assert why is not None and "real" in why and "demo" in why


# ---------------------------------------------------------------------------------------------- the gate's limits
def test_the_demo_account_gets_the_ceilings_and_projects_open_risk(phase_file):
    s = load_settings()
    pol = engine_limits_policy(s, _acc("demo"))
    lim, cur = pol.resolve(LimitsEvidence())
    assert cur.profile == PROFILE and cur.reason.startswith("static")
    assert (lim.daily_cap, lim.weekly_cap, lim.min_lot_risk_cap) == (0.05, 0.10, 0.01)
    assert lim.project_open_risk is True and lim.daily_loss_limit_abs == 50.0
    # everything else is the defaults' (engine_limits stays the defaults)
    assert lim.model_copy(update={"daily_cap": s.risk.daily_cap, "weekly_cap": s.risk.weekly_cap,
                                  "min_lot_risk_cap": s.risk.min_lot_risk_cap,
                                  "project_open_risk": s.risk.project_open_risk}) == engine_limits(s, _acc("demo"))


def test_the_paper_stand_in_keeps_the_default_limits(phase_file):
    lim, cur = engine_limits_policy(load_settings(), _acc("demo"), paper=True).resolve(LimitsEvidence())
    assert cur.profile == DEFAULT_PROFILE and lim.daily_loss_limit_abs is None


class _Widening:
    """A controller that tries to widen everything (the adaptive seam must never let it)."""

    def current(self, account: Any, evidence: LimitsEvidence) -> RiskLimitsCurrent:
        return RiskLimitsCurrent(profile="demo-2099-01-01", daily_cap=0.5, weekly_cap=0.9, min_lot_risk_cap=0.5,
                                 project_open_risk=False, reason="greedy")


class _NotANumber:
    def current(self, account: Any, evidence: LimitsEvidence) -> RiskLimitsCurrent:
        return RiskLimitsCurrent(profile=PROFILE, daily_cap=float("nan"), weekly_cap=float("inf"),
                                 min_lot_risk_cap=float("nan"), reason="nan")


@pytest.mark.parametrize("mode", ["demo", "live"])
def test_a_controller_answer_that_is_not_a_finite_number_gives_the_defaults(phase_file, mode):
    acc = _acc("demo") if mode == "demo" else _acc("live", overrides=None)
    pol = engine_limits_policy(load_settings(), acc)
    pol.controller = _NotANumber()
    lim, cur = pol.resolve(LimitsEvidence())
    assert (lim.daily_cap, lim.weekly_cap) == (pol.defaults.daily_cap, pol.defaults.weekly_cap)
    assert lim.min_lot_risk_cap == pol.defaults.min_lot_risk_cap and cur.profile == DEFAULT_PROFILE


class _Broken:
    def current(self, account: Any, evidence: LimitsEvidence) -> RiskLimitsCurrent:
        raise RuntimeError("boom")


@pytest.mark.parametrize("gates", [["backtest_to_paper", "paper_to_tiny_live"],
                                   ["paper_to_tiny_live", "tiny_live_to_full_size"]])
def test_live_and_tiny_live_never_get_more_than_the_defaults_even_from_a_widening_controller(phase_file, gates):
    phase_file.write_text(json.dumps({"phase": 4, "gates_passed": gates}))
    s = load_settings()
    live = _acc("live", overrides=None)
    pol = engine_limits_policy(s, live)
    pol.controller = _Widening()
    lim, cur = pol.resolve(LimitsEvidence())
    defaults = engine_limits(s, live)
    assert cur.profile == DEFAULT_PROFILE and lim.daily_cap <= defaults.daily_cap
    assert lim.weekly_cap <= defaults.weekly_cap and lim.min_lot_risk_cap == defaults.min_lot_risk_cap


def test_a_widening_controller_on_the_demo_is_clamped_to_the_ceilings_and_a_broken_one_gives_defaults(phase_file):
    pol = engine_limits_policy(load_settings(), _acc("demo"))
    pol.controller = _Widening()
    lim, cur = pol.resolve(LimitsEvidence())
    assert (lim.daily_cap, lim.weekly_cap, lim.min_lot_risk_cap, lim.project_open_risk) == (0.05, 0.10, 0.01, True)
    assert cur.profile == PROFILE                                    # derived, never the controller's own label
    pol.controller = _Broken()
    lim, cur = pol.resolve(LimitsEvidence())
    assert cur.profile == DEFAULT_PROFILE and lim.daily_cap == load_settings().risk.daily_cap


def test_reverted_forces_the_defaults_whatever_the_controller_says(phase_file):
    pol = engine_limits_policy(load_settings(), _acc("demo"))
    pol.controller = _Widening()
    lim, cur = pol.resolve(LimitsEvidence(reverted=True))
    assert cur.profile == DEFAULT_PROFILE and lim == pol.defaults


def _gbp_intent(atr: float = 8.0) -> Intent:
    return Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=atr, cost_atr=0.05,
                  multiplier=1.0, price=4_150.0, account_currency="GBP", acct_per_usd=GBP, timeframe="15m",
                  family="trend")


def _gbp_state(equity: float = 1_000.0) -> AccountState:
    return AccountState(equity=equity, balance_closed_hwm=equity, day_start_equity=equity, week_start_equity=equity,
                        open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0, open_risk=0.0)


def test_demo_places_a_0_01_lot_at_1x_atr15_of_8_dollars_on_a_gbp_account_while_live_and_tiny_live_refuse(
        phase_file):
    s = load_settings()
    demo, _ = engine_limits_policy(s, _acc("demo")).resolve(LimitsEvidence())
    d = RiskGate(demo).check(_gbp_intent(), _gbp_state())
    assert d.allowed and d.lots == 0.01 and d.reasons == ["min_lot_exception"] and d.risk_fraction < 0.01
    live = _acc("live", overrides=None)
    for gates in (["paper_to_tiny_live"], ["paper_to_tiny_live", "tiny_live_to_full_size"]):
        phase_file.write_text(json.dumps({"phase": 4, "gates_passed": gates}))
        pol = engine_limits_policy(s, live)
        pol.controller = _Widening()
        lim, _ = pol.resolve(LimitsEvidence())
        r = RiskGate(lim).check(_gbp_intent(), _gbp_state())
        assert not r.allowed and r.reasons == ["min_lot_exceeds_risk"]


def test_the_absolute_50_pound_limit_still_binds_under_the_5_percent_cap(phase_file):
    lim, _ = engine_limits_policy(load_settings(), _acc("demo")).resolve(LimitsEvidence())
    st = _gbp_state(2_000.0)                       # 5% of 2,000 = 100 > 50: the £50 wins
    assert RiskGate(lim).daily_loss_limit(st) == 50.0


# ---------------------------------------------------------------------------------------------- the supervisor
def _engine_file(d: Path, account: str, cur: dict | None, mode: str = "demo", eq: float = 1_000.0,
                 day0: float = 1_000.0) -> None:
    payload: dict[str, Any] = {"account": account, "mode": mode, "ts": time.time(), "currency": "GBP", "equity": eq,
                               "day_start_equity": day0, "week_start_equity": day0, "balance_closed_hwm": day0}
    if cur is not None:
        payload["limits_current"] = cur
    (d / f"engine_{account}.json").write_text(json.dumps(payload))


DEMO_CUR = {"profile": PROFILE, "daily_cap": 0.05, "weekly_cap": 0.10, "min_lot_risk_cap": 0.01,
            "project_open_risk": True, "reason": "static", "evidence_ids": []}
DEF_CUR = {**DEMO_CUR, "profile": "default", "daily_cap": 0.02, "weekly_cap": 0.05, "min_lot_risk_cap": None}


def test_supervisor_ceilings_rise_only_when_every_enabled_account_is_demo_with_ceilings():
    # the combined caps' ceiling is the CFO envelope, below the account ceilings (trading-safety HIGH on 156cfbf)
    assert supervisor_ceilings([_acc("demo"), _acc("live", overrides=None, enabled=False)]) == RiskCeilings(
        daily=0.0375, weekly=0.075)
    tight = RiskOverrides(profile=PROFILE, ceilings=RiskCeilings(daily=0.03, weekly=0.10, min_lot_risk_cap=0.01))
    assert supervisor_ceilings([_acc("demo", overrides=tight)]) == RiskCeilings(daily=0.03, weekly=0.075)
    assert supervisor_ceilings([_acc("demo"), _acc("live", overrides=None)]) is None
    assert supervisor_ceilings([_acc("demo"), _acc("demo", overrides=None, name="vantage-demo")]) is None
    assert supervisor_ceilings([]) is None


def test_supervisor_caps_follow_the_engines_current_limits_in_lockstep(tmp_path):
    s = load_settings().risk
    lim = SupervisorLimits.from_settings(s, ceilings=supervisor_ceilings([_acc("demo")]))
    _engine_file(tmp_path, "icm-demo", DEMO_CUR, eq=970.0)           # 3% down: under 5%, over the default 1.5%
    st = Supervisor(tmp_path, lim).evaluate()
    assert (st["daily_cap"], st["weekly_cap"]) == SUPERVISOR_DEMO_ENVELOPE and "combined_daily_cap" not in st["reasons"]
    _engine_file(tmp_path, "icm-demo", DEMO_CUR, eq=960.0)           # 4% down: over the 3.75% combined envelope
    assert "combined_daily_cap" in Supervisor(tmp_path, lim).evaluate()["reasons"]
    _engine_file(tmp_path, "icm-demo", DEF_CUR, eq=970.0)            # the engine reverted: defaults at once
    st = Supervisor(tmp_path, lim).evaluate()
    assert st["daily_cap"] == s.supervisor_daily_cap and "combined_daily_cap" in st["reasons"]


@pytest.mark.parametrize("files", [
    [("icm-demo", DEMO_CUR, "demo"), ("icm-live", DEMO_CUR, "live")],     # a non-demo engine in the book
    [("icm-demo", DEMO_CUR, "demo"), ("other-demo", None, "demo")],       # an engine that publishes nothing
    [("icm-demo", {**DEMO_CUR, "daily_cap": 0.9, "weekly_cap": 0.9}, "demo")],   # a file above the ceiling
])
def test_supervisor_caps_stay_at_the_defaults_or_ceilings_when_any_engine_is_not_a_demo_under_ceilings(tmp_path, files):
    s = load_settings().risk
    lim = SupervisorLimits.from_settings(s, ceilings=supervisor_ceilings([_acc("demo")]))
    for a, cur, mode in files:
        _engine_file(tmp_path, a, cur, mode=mode)
    st = Supervisor(tmp_path, lim).evaluate()
    if len(files) == 1:
        assert (st["daily_cap"], st["weekly_cap"]) == SUPERVISOR_DEMO_ENVELOPE
    else:
        assert (st["daily_cap"], st["weekly_cap"]) == (s.supervisor_daily_cap, s.supervisor_weekly_cap)


def test_supervisor_without_ceilings_ignores_what_engines_publish(tmp_path):
    s = load_settings().risk
    _engine_file(tmp_path, "icm-demo", DEMO_CUR)
    st = Supervisor(tmp_path, SupervisorLimits.from_settings(s)).evaluate()
    assert (st["daily_cap"], st["weekly_cap"]) == (s.supervisor_daily_cap, s.supervisor_weekly_cap)


# ---------------------------------------------------------------------------------------------- the engine
class _Equity(PaperBroker):
    def __init__(self, equity: float = 1_000.0):
        super().__init__(equity=equity)
        self.eq, self.bal = equity, equity

    def account(self) -> AccountInfo:
        return AccountInfo(login=0, equity=self.eq, balance=self.bal, margin=0.0, margin_free=self.eq, leverage=20,
                           currency="GBP", server="paper")


def _engine(tmp_path: Path, pb: _Equity, phase_file: Path) -> Engine:
    pol = engine_limits_policy(load_settings(), _acc("demo", name="icm-demo"))
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), mode="demo"), pb,
                  [], {}, center, limits=pol.defaults, limits_policy=pol)


def _tick(s: float = 0.0) -> Tick:
    return Tick(ts_utc=T0 + pd.Timedelta(seconds=s), bid=2400.0, ask=2400.2)


def test_the_engine_trades_under_the_demo_ceilings(tmp_path, phase_file):
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    assert eng.limits_current.profile == PROFILE and eng.gate.limits.daily_cap == 0.05
    assert eng.gate.limits.min_lot_risk_cap == 0.01 and eng.gate.limits.project_open_risk is True


def test_the_8_percent_size_down_reverts_to_defaults_and_the_revert_survives_a_restart(tmp_path, phase_file):
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    pb.eq = 915.0                                               # 8.5% below the 1,000 mark: size-down
    eng._refresh_account(_tick(1))
    assert eng.state.stage == Stage.SIZE_DOWN
    assert eng.limits_current.profile == DEFAULT_PROFILE and eng.gate.limits.daily_cap == load_settings().risk.daily_cap
    assert eng.gate.limits.min_lot_risk_cap is None
    pb.eq = 1_000.0                                             # recovered: the stage clears, the revert does not
    eng._refresh_account(_tick(2))
    assert Stage(eng.state.stage) is Stage.NORMAL and eng.limits_current.profile == DEFAULT_PROFILE
    again = _engine(tmp_path, pb, phase_file)                   # a restart keeps it (core.db)
    again._refresh_account(_tick(3))
    assert again.limits_current.profile == DEFAULT_PROFILE and again.gate.limits.daily_cap == 0.02
    assert not (tmp_path / "limits_icm-demo.json").exists()     # no new JSON state file
    assert LimitsStore(tmp_path).load("icm-demo").reverted


def test_a_saved_size_down_stage_without_a_revert_row_starts_on_the_defaults(tmp_path, phase_file):
    # trading-safety LOW on 156cfbf: the risk file says size-down but core.db has no revert (a lost write, or a stage
    # saved before this release): the engine must start on the defaults, not the ceilings, before any refresh
    pb = _Equity()
    pb.on_tick(_tick())
    old = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), mode="demo"), pb,
                 [], {}, ApprovalCenter({1}, bus=ApprovalBus(tmp_path)))
    old.state.stage = Stage.SIZE_DOWN
    old._save_risk_state()
    eng = _engine(tmp_path, pb, phase_file)
    assert eng._limits_reverted and eng.limits_current.profile == DEFAULT_PROFILE
    assert eng.gate.limits.daily_cap == load_settings().risk.daily_cap and eng.gate.limits.min_lot_risk_cap is None
    assert LimitsStore(tmp_path).load("icm-demo").reverted


def test_only_the_owners_rearm_at_a_normal_stage_clears_the_revert(tmp_path, phase_file):
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    pb.eq = 915.0
    eng._refresh_account(_tick(1))
    bus = eng.center.bus
    assert bus is not None
    bus.owner_rearm(by="test")                                  # still in size-down: refused, stays reverted
    eng._poll_rearm(T0)
    assert eng._limits_reverted and any(d["action"] == "limits_reset_refused" for d in eng.decisions)
    pb.eq = 1_000.0
    eng._refresh_account(_tick(2))
    assert eng._limits_reverted                                 # a normal stage alone never clears it
    bus.owner_rearm(by="test")
    eng._poll_rearm(T0)
    eng._refresh_account(_tick(3))
    assert not eng._limits_reverted and eng.limits_current.profile == PROFILE
    assert not LimitsStore(tmp_path).load("icm-demo").reverted


def test_an_unreadable_revert_state_loads_as_reverted(tmp_path, phase_file, monkeypatch):
    from goldbot.risk import limits_profile

    def unreadable(cls: Any, *a: Any, **k: Any) -> Any:
        raise OSError("disk")

    monkeypatch.setattr(limits_profile.Database, "open", classmethod(unreadable))
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    assert eng.limits_current.profile == DEFAULT_PROFILE


def test_every_change_of_the_current_limits_is_logged_in_core_db(tmp_path, phase_file):
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    eng._refresh_account(_tick(1))                              # no change: no row
    pb.eq = 915.0
    eng._refresh_account(_tick(2))
    rows = LimitsStore(tmp_path).changes("icm-demo")
    assert [(r.previous_profile, r.current.profile) for r in rows] == [(None, PROFILE), (PROFILE, DEFAULT_PROFILE)]
    assert rows[1].current.reason == "reverted" and rows[1].current.daily_cap == 0.02
    assert [d["action"] for d in eng.decisions if d["action"] == "limits_change"] == ["limits_change"] * 2


def test_the_engine_file_publishes_the_current_limits_for_the_supervisor(tmp_path, phase_file):
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    eng._write_state()
    d = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert d["limits_current"]["profile"] == PROFILE and d["limits_current"]["daily_cap"] == 0.05


def test_exits_pass_while_the_daily_and_weekly_caps_bind(tmp_path, phase_file):
    from goldbot.specialists.trend import TrendSpecialist
    from goldbot.telegram.approvals import Proposal
    from tests.test_engine_clock import WED
    from tests.test_engine_clock import _engine as clock_engine
    from tests.test_engine_clock import _tick as clock_tick
    eng, pb, _ = clock_engine(tmp_path)
    eng.gate.limits = RiskLimits(daily_cap=0.05, weekly_cap=0.10, min_lot_risk_cap=0.01, project_open_risk=True,
                                 daily_loss_limit_abs=1.0)
    agent = TrendSpecialist()
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_400.2, stop=2_390.0, target=2_430.0, p=0.6, ev_r=0.1, spread_points=20.0, top_features=[])
    eng._execute(prop, agent, 0.1, 2_390.0, 2_430.0, requested=2_400.2, atr_usd=10.0)
    (pid,) = eng.open
    eng._refresh_account(clock_tick(WED))
    eng.state.day_start_equity = eng.state.week_start_equity = eng.state.equity / 0.85   # 15% down today
    assert {"daily_cap", "weekly_cap"} <= set(eng.gate.account_reasons(eng.state))
    calls: list[Any] = []
    real = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real(*a, **k)

    eng.gate.check = spy                                        # type: ignore[method-assign]
    later = clock_tick(WED + pd.Timedelta(minutes=1), bid=2_389.0)   # the long's stop is hit
    pb.on_tick(later)
    eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and pid not in {p.position_id for p in pb.positions()} and calls == []


# ---------------------------------------------------------------------------------------------- tagging
def test_records_carry_the_limits_profile_and_values(tmp_path, phase_file):
    assert "limits_profile" in EntryAudit.model_fields and "limits" in EntryAudit.model_fields
    assert set(EntryAudit.model_fields) <= set(ClosedTrade.model_fields)     # spread into the closed record
    pb = _Equity()
    pb.on_tick(_tick())
    eng = _engine(tmp_path, pb, phase_file)
    eng._refresh_account(_tick())
    audit = eng._entry_limits()
    assert audit == {"limits_profile": PROFILE, "limits": eng.limits_current.tag()}
    eng.decisions.append({"ts": time.time(), "action": "probe"})
    pb.eq = 915.0
    eng._refresh_account(_tick(1))                              # the change tags what was decided before it
    eng.decisions.append({"ts": time.time(), "action": "probe2"})
    eng._tag_decisions()
    probes = {d["action"]: d["limits_profile"] for d in eng.decisions if d["action"].startswith("probe")}
    assert probes == {"probe": PROFILE, "probe2": DEFAULT_PROFILE}


def test_shadow_trades_carry_the_limits_profile(tmp_path):
    book = ShadowBook(tmp_path)
    book.track("v1", T0)
    t = book.open_trade(version="v1", agent_id="a", side=1, bar_ts=T0, entry=2400.0, atr_usd=8.0, target_atr=2.0,
                        stop_atr=1.0, max_bars=8, p=0.6, limits_profile=PROFILE)
    assert t is not None and t.limits_profile == PROFILE


# ---------------------------------------------------------------------------------------------- paper evidence
def _closed(profile: str | None, exit_utc: pd.Timestamp, limits: dict | None = None, mode: str = "demo") -> ClosedTrade:
    return ClosedTrade.model_validate({"account_id": "icm-demo", "broker": "icm", "mode": mode, "exit_utc": exit_utc,
                                       "ret": 0.001, "pnl": 1.0, "equity_before": 1_000.0, "limits_profile": profile,
                                       "limits": limits})


CUT = pd.Timestamp("2026-10-18", tz="UTC")
DEF_TAG = {"daily_cap": 0.02, "weekly_cap": 0.05, "min_lot_risk_cap": None, "project_open_risk": False}


@pytest.mark.parametrize("profile, exit_utc, limits, mode, counts", [
    (PROFILE, CUT + pd.Timedelta(days=1), None, "demo", False),                    # demo profile: excluded
    (None, CUT - pd.Timedelta(days=1), None, "demo", True),                        # untagged before the cutover
    (None, CUT + pd.Timedelta(days=1), None, "demo", False),                       # untagged after it (an orphan)
    (DEFAULT_PROFILE, CUT + pd.Timedelta(days=1), DEF_TAG, "demo", True),          # default profile, default values
    (DEFAULT_PROFILE, CUT + pd.Timedelta(days=1), None, "demo", True),             # default profile, no values
    (DEFAULT_PROFILE, CUT + pd.Timedelta(days=1), {**DEF_TAG, "daily_cap": 0.05}, "demo", False),   # values above
    (DEFAULT_PROFILE, CUT + pd.Timedelta(days=1), {**DEF_TAG, "min_lot_risk_cap": 0.01}, "demo", False),
    (DEFAULT_PROFILE, CUT + pd.Timedelta(days=1), DEF_TAG, "live", False),         # not a paper trade at all
])
def test_paper_evidence_counts_only_default_profile_trades(profile, exit_utc, limits, mode, counts):
    risk = load_settings().risk
    assert counts_as_default_evidence(_closed(profile, exit_utc, limits, mode), risk) is counts
