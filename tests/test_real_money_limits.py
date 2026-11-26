"""ADR 0106 decision 2, the owner's real-money limits in the RiskGate: "risk per trade <= 1 % of equity, daily loss limit
2 % of equity then stop for the day, max drawdown halt at 15 % ..., and order size <= about 1 % of the asset's daily
volume. They are enforced in the RiskGate as plain code the models cannot override."

Each test states one rule of goldbot/risk/real_money.py and shows it holds on a real-money account (`RiskLimits.
real_money`, set by ops.services.engine.engine_limits for a live account's real terminal) whatever the profile,
override, dynamic term or pilot block says, and that a demo account is decided exactly as before."""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.engine.store import JsonEngineStore, RiskSnapshot
from goldbot.ops import accounts as acc_mod
from goldbot.ops.services.engine import engine_limits
from goldbot.risk import real_money as rm
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage, new_day
from goldbot.risk.pilot import PilotRules, PilotState

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-11-26 10:00", tz="UTC")
TODAY = dt.date(2026, 11, 26)
# a reference so large it never binds: the volume rule has its own tests
BIG_ADV = rm.AdvReference(adv_notional=1e12, currency="USD", source="test", as_of=TODAY)


def _limits(real_money: bool = True, **kw: Any) -> RiskLimits:
    base: dict[str, Any] = dict(real_money=real_money, adv_reference={"XAUUSD": BIG_ADV} if real_money else {})
    return RiskLimits(**{**base, **kw})


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                                week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0, open_risk=0.0)
    return AccountState(**{**base, **kw})


def _intent(**kw: Any) -> Intent:
    # a $4 stop on 100 oz: $400 a lot; a cost_atr of 0.01 on a $4 ATR is $0.04 an oz round trip
    base: dict[str, Any] = dict(agent_id="trend-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0,
                                cost_atr=0.0, multiplier=1.0, price=1500.0)
    return Intent(**{**base, **kw})


def _check(limits: RiskLimits, intent: Intent | None = None, st: AccountState | None = None):
    return RiskGate(limits).check(intent or _intent(), st or _state(), NOW)


# ---------------------------------------------------------------- 1. risk per trade, the round trip included
PILOT = PilotRules(enabled=True, adr=55, pilot_id="icm-live-adr55", start_utc=pd.Timestamp("2026-10-29", tz="UTC"),
                   leverage_cap=500)
GBP_PER_USD = 0.7508

# each profile sizes a trade above 1 % of equity at the stop plus its round trip; the gate's own fields allow it
OVER_1PCT: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {
    # 1.01 %: 0.25 lot x $400 = $100 at the stop + $1 round trip (0.25 x 100 oz x $0.04)
    "base_rate": (dict(risk_per_trade=0.0101, max_risk_per_trade=0.05), dict(cost_atr=0.01), {}),
    "multiplier": (dict(risk_per_trade=0.01, max_risk_per_trade=0.05), dict(multiplier=1.5), {}),
    "strategy_risk": (dict(strategy_risk={"m1": 0.012}, max_risk_per_trade=0.05), dict(model_version="m1"), {}),
    # tiny-live: a dynamic term clamped to the 1 % per-trade maximum, then the round trip on top
    "tiny_live": (dict(risk_per_trade=0.001, strategy_risk={"m1": 0.0101}, min_lot_risk_cap=None),
                  dict(model_version="m1", cost_atr=0.01), {}),
    # the minimum-lot exception: 0.30 lot is the smallest the broker takes, 1.2 % within a 5 % cap
    "min_lot_risk_cap": (dict(min_lot_risk_cap=0.05, max_risk_per_trade=0.05), dict(volume_min=0.3), {}),
    # the ADR 0055 pilot's own rails as they stood (12.5 % a trade, stages off): £2.63 + £0.13 on £50
    "pilot": (dict(pilot=PILOT, max_positions=1, max_risk_per_trade=0.125, daily_cap=0.25, weekly_cap=0.25,
                   leverage_cap=500, dd_stage1=1.0, dd_stage2=1.0, dd_stage1_clear=1.0, risk_per_trade=0.001,
                   min_lot_risk_cap=None, daily_loss_limit_abs=6.25),
              dict(agent_id="scalp_15m", p=0.55, target_atr=2.0, atr_usd=3.5, cost_atr=0.05, price=4014.84,
                   timeframe="15m", family="box_break", account_currency="GBP", acct_per_usd=GBP_PER_USD),
              dict(equity=50.0, balance_closed_hwm=50.0, day_start_equity=50.0, week_start_equity=50.0,
                   spread_points=20.0, open_risk=0.0, broker_leverage=5000.0,
                   pilot=PilotState(account_id="icm-live", pilot_id="icm-live-adr55", realised_loss=0.0, entries=0))),
}


@pytest.mark.parametrize("name", sorted(OVER_1PCT))
def test_a_real_money_entry_risking_more_than_1_percent_with_its_round_trip_is_refused_under_every_profile(name):
    lim, it, st = OVER_1PCT[name]
    d = _check(_limits(**lim), _intent(**it), _state(**st))
    assert not d.allowed and d.reasons == ["rm_risk_per_trade"], d
    # the same trade on demo is decided exactly as before: allowed, at more than 1 %
    demo = _check(_limits(real_money=False, **lim), _intent(**it), _state(**st))
    assert demo.allowed and demo.risk_amount + demo.cost_amount > 0.01 * (st.get("equity") or 10_000.0)


def test_exactly_1_percent_at_the_stop_with_no_round_trip_is_allowed_and_1_01_percent_is_not():
    lim: dict[str, Any] = dict(risk_per_trade=0.0101, max_risk_per_trade=0.05)
    at = _check(_limits(**lim))
    assert at.allowed and at.lots == 0.25 and at.risk_amount == pytest.approx(100.0) and at.cost_amount == 0.0
    over = _check(_limits(**lim), _intent(cost_atr=0.01))
    assert not over.allowed and over.reasons == ["rm_risk_per_trade"]


def test_the_default_real_money_profile_still_trades_well_inside_1_percent():
    d = _check(_limits(), _intent(cost_atr=0.05))
    assert d.allowed and d.risk_amount + d.cost_amount <= 0.01 * 10_000


# ---------------------------------------------------------------- 2. the daily stop, latched to the risk-day roll
def test_a_2_percent_day_refuses_every_entry_until_the_next_risk_day_even_after_equity_recovers():
    gate = RiskGate(_limits(daily_cap=0.05))                # a profile that raised the daily cap to 5 %
    st = _state(equity=9_800.0)                             # realised + open loss = 2 % of the day start
    d = gate.check(_intent(), st, NOW)
    assert not d.allowed and "rm_daily_stop" in d.reasons and st.rm_daily_stop
    st.equity = 10_150.0                                    # the open positions recover the same day
    assert "rm_daily_stop" in gate.account_reasons(st, NOW)
    assert gate.check(_intent(), st, NOW).reasons == ["rm_daily_stop"]
    assert new_day(st, NOW + pd.Timedelta(days=1), NOW)    # the 00:00 UTC roll ends it
    assert not st.rm_daily_stop and gate.check(_intent(), st, NOW + pd.Timedelta(days=1)).allowed


def test_an_entry_is_sized_down_to_what_is_left_of_the_2_percent_day_with_open_risk_counted():
    """ "2 % then stop": a full 1 % entry at 1.9 % down would let the day reach about 2.9 %. Each entry's loss at the stop
    plus its round trip is capped at min(1 %, 2 % - the day's loss so far - the open positions' risk to their stops),
    in whole lot steps; below the minimum lot it is refused `rm_daily_projected`."""
    lim = _limits(risk_per_trade=0.01, max_risk_per_trade=0.01)
    full = _check(lim)
    assert full.allowed and full.lots == 0.25                                   # $100 at the stop, 1 %
    # 1.5 % lost + 0.2 % open risk: $30 left of $200 -> 0.07 lot ($28)
    d = _check(lim, st=_state(equity=9_850.0, open_risk=20.0))
    assert d.allowed and d.lots == 0.07 and d.risk_amount + d.cost_amount <= 30.0 + 1e-9
    # $3 left: below one 0.01 lot ($4) -> refused
    d = _check(lim, st=_state(equity=9_830.0, open_risk=27.0))
    assert not d.allowed and d.reasons == ["rm_daily_projected"]
    # the open risk must be known on real money (fail closed); demo is unchanged
    assert "open_risk_unknown" in _check(lim, st=_state(open_risk=None)).reasons
    demo = _check(_limits(real_money=False, risk_per_trade=0.01, max_risk_per_trade=0.01),
                  st=_state(equity=9_850.0, open_risk=20.0))
    assert demo.allowed and demo.lots == 0.24                                   # 1 % of 9,850, not capped


def test_the_daily_stop_is_set_by_the_stage_step_the_engine_runs_at_every_account_refresh():
    gate = RiskGate(_limits())
    st = _state(equity=9_799.0)
    gate.stage_step(st)
    assert st.rm_daily_stop
    st2 = _state(equity=9_801.0)                            # 1.99 %: not reached
    gate.stage_step(st2)
    assert not st2.rm_daily_stop and "rm_daily_stop" not in gate.account_reasons(st2)


def test_a_demo_account_has_no_latch_and_its_daily_cap_is_unchanged():
    gate = RiskGate(_limits(real_money=False, daily_cap=0.05))
    st = _state(equity=9_700.0)                             # 3 % down, inside the demo's 5 % cap
    d = gate.check(_intent(), st, NOW)
    assert d.allowed and not st.rm_daily_stop


def test_the_daily_stop_survives_a_restart_and_an_older_risk_file_reads_as_not_latched(tmp_path):
    store = JsonEngineStore(tmp_path, "icm-live")
    snap = RiskSnapshot(last_reset=NOW, day_start_equity=10_000.0, week_start_equity=10_000.0,
                        balance_closed_hwm=10_000.0, stage=Stage.NORMAL, rearm_seen=None, weekend_done=None,
                        halted_at=None, propose_only_until=None, rearm_refused=None, kill_everything=False,
                        stage2_pending_since=None, rm_daily_stop=True)
    store.save_risk(snap)
    loaded = store.load_risk()
    assert loaded is not None and loaded.rm_daily_stop
    store.save_risk(snap.model_copy(update={"rm_daily_stop": False}))
    raw = (tmp_path / "risk_icm-live.json").read_text()
    assert "rm_daily_stop" not in raw                       # written only while latched: other files read as before
    loaded = store.load_risk()
    assert loaded is not None and not loaded.rm_daily_stop


def test_the_engine_restores_the_latch_on_restart(tmp_path):
    from goldbot.engine import Engine, EngineConfig
    from goldbot.execution.paper import PaperBroker
    from goldbot.telegram.approvals import ApprovalCenter
    from goldbot.telegram.bus import ApprovalBus

    def engine() -> Engine:
        return Engine(EngineConfig(account_id="icm-live", broker_name="icm", state_dir=str(tmp_path), mode="demo"),
                      PaperBroker(equity=10_000.0), [], {}, ApprovalCenter({1}, bus=ApprovalBus(tmp_path)),
                      limits=_limits())
    eng = engine()
    eng.state.rm_daily_stop = True
    eng._save_risk_state()
    assert engine().state.rm_daily_stop


def test_the_account_refresh_latches_and_saves_the_daily_stop_and_a_restart_keeps_refusing(tmp_path):
    from goldbot.engine import Engine, EngineConfig
    from goldbot.execution.broker import AccountInfo, Tick
    from goldbot.execution.paper import PaperBroker
    from goldbot.telegram.approvals import ApprovalCenter
    from goldbot.telegram.bus import ApprovalBus

    class _Equity(PaperBroker):
        eq = 10_000.0

        def account(self) -> AccountInfo:
            return AccountInfo(login=0, equity=self.eq, balance=self.eq, margin=0.0, margin_free=self.eq, leverage=20,
                               currency="USD", server="paper")

    def engine(pb: PaperBroker) -> Engine:
        eng = Engine(EngineConfig(account_id="icm-live", broker_name="icm", state_dir=str(tmp_path), mode="demo"),
                     pb, [], {}, ApprovalCenter({1}, bus=ApprovalBus(tmp_path)), limits=_limits())
        eng.clock = lambda: NOW
        return eng

    def tick(s: float) -> Tick:
        return Tick(ts_utc=NOW + pd.Timedelta(seconds=s), bid=2400.0, ask=2400.2)

    pb = _Equity()
    pb.on_tick(tick(0))
    eng = engine(pb)
    eng._refresh_account(tick(0))
    assert not eng.state.rm_daily_stop
    pb.eq = 9_790.0                                         # 2.1 % down on the day
    eng._refresh_account(tick(1))
    assert eng.state.rm_daily_stop and '"rm_daily_stop": true' in (tmp_path / "risk_icm-live.json").read_text()
    pb.eq = 10_050.0                                        # recovered, then a restart the same day
    again = engine(pb)
    again._refresh_account(tick(2))
    assert again.state.rm_daily_stop
    assert "rm_daily_stop" in again.gate.account_reasons(again.state, NOW)


# ---------------------------------------------------------------- 3. the 15 % drawdown halt
@pytest.mark.parametrize("stages", [dict(), dict(dd_stage1=1.0, dd_stage2=1.0, dd_stage1_clear=1.0),
                                    dict(dd_stage2=0.5)])
def test_a_15_percent_drawdown_halts_a_real_money_account_whatever_its_stages_say(stages):
    gate = RiskGate(_limits(**stages))
    st = _state(equity=8_500.0, day_start_equity=8_500.0, week_start_equity=8_500.0)
    assert gate.stage_step(st) == Stage.HALTED
    assert "rm_drawdown_halt" in gate.account_reasons(st)
    assert gate.halt_at() <= rm.RM_DD_HALT


def test_the_12_percent_stage_still_fires_first_on_the_default_profile():
    gate = RiskGate(_limits())
    st = _state(equity=8_800.0, day_start_equity=8_800.0, week_start_equity=8_800.0)
    assert gate.stage_step(st) == Stage.HALTED and gate.halt_at() == 0.12
    assert "rm_drawdown_halt" not in gate.account_reasons(st)


def test_a_demo_account_with_its_stages_off_is_not_halted_at_15_percent():
    gate = RiskGate(_limits(real_money=False, dd_stage1=1.0, dd_stage2=1.0, dd_stage1_clear=1.0))
    st = _state(equity=8_000.0, day_start_equity=8_000.0, week_start_equity=8_000.0)
    assert gate.stage_step(st) == Stage.NORMAL and gate.halt_at() == 1.0


def test_a_real_money_account_is_never_unlimited():
    gate = RiskGate(_limits(demo_unlimited=True, probe_only=True))
    assert not gate.unlimited()


# ---------------------------------------------------------------- 4. order size against the daily volume
def _adv(notional: float, **kw: Any) -> dict[str, rm.AdvReference]:
    return {"XAUUSD": rm.AdvReference(**{**dict(adv_notional=notional, currency="USD", source="test", as_of=TODAY),
                                         **kw})}


def test_an_order_above_1_percent_of_the_reference_daily_volume_is_refused():
    # the default trade: 0.12 lot x 100 oz x $1,500 = $18,000 notional
    assert _check(_limits(adv_reference=_adv(1_800_000.0))).allowed            # exactly 1 %
    d = _check(_limits(adv_reference=_adv(1_799_000.0)))
    assert not d.allowed and d.reasons == ["rm_volume"]


@pytest.mark.parametrize("ref", [{}, {"EURUSD": _adv(1e12)["XAUUSD"]}, _adv(1e12, currency="EUR")])
def test_an_unknown_reference_volume_refuses_on_real_money_and_does_nothing_on_demo(ref):
    d = _check(_limits(adv_reference=ref))
    assert not d.allowed and d.reasons == ["rm_volume_unknown"]
    assert _check(_limits(real_money=False, adv_reference=ref)).allowed


def test_the_committed_reference_file_loads_and_refuses_until_the_data_team_fills_it():
    refs = rm.load_adv_reference(rm.ADV_REFERENCE_FILE, today=TODAY)
    raw = yaml.safe_load(rm.ADV_REFERENCE_FILE.read_text())
    assert "XAUUSD" in raw["instruments"]
    for sym, r in refs.items():                              # a filled entry names its source and date
        assert r.source and r.as_of is not None and r.adv_notional is not None, sym
    if raw["instruments"]["XAUUSD"]["adv_notional"] is None:
        assert "XAUUSD" not in refs


def test_a_reference_without_its_source_or_date_is_refused_at_load():
    with pytest.raises(ValidationError):
        rm.AdvReference(adv_notional=1e9, currency="USD", source="", as_of=TODAY)
    with pytest.raises(ValidationError):
        rm.AdvReference(adv_notional=1e9, currency="USD", source="CME", as_of=None)


def test_a_stale_or_unreadable_reference_file_gives_no_reference(tmp_path):
    f = tmp_path / "adv.yaml"
    f.write_text(yaml.safe_dump({"schema": 1, "instruments": {
        "XAUUSD": {"adv_notional": 5e10, "currency": "USD", "source": "CME GC", "as_of": "2025-01-01"},
        "XAGUSD": {"adv_notional": 1e10, "currency": "USD", "source": "CME SI", "as_of": "2026-11-01"}}}))
    refs = rm.load_adv_reference(f, today=TODAY)
    assert set(refs) == {"XAGUSD"}                           # older than ADV_MAX_AGE_DAYS: dropped (fail closed)
    f.write_text("instruments: [not, a, map")
    assert rm.load_adv_reference(f, today=TODAY) == {}
    assert rm.load_adv_reference(tmp_path / "missing.yaml", today=TODAY) == {}


# ---------------------------------------------------------------- 5. the pilot block
@pytest.mark.parametrize("kw", [dict(max_risk_frac=0.0101), dict(daily_cap_frac=0.0201), dict(drawdown_stages=False),
                                dict(max_risk_frac=0.125, daily_cap_frac=0.25, drawdown_stages=False)])
def test_a_pilot_block_looser_than_the_real_money_limits_is_refused_at_load(kw):
    with pytest.raises(ValidationError):
        PilotRules(**{**PILOT.model_dump(), **kw})
    with pytest.raises(ValidationError):
        PilotRules(**kw)                                     # disabled or not: the block is refused


def test_engine_limits_mark_a_live_account_as_real_money_and_paper_and_demo_as_not():
    s = load_settings()
    accs = acc_mod.load_accounts(logins=False)
    live = engine_limits(s, accs["icm-live"])
    assert live.real_money and live.max_risk_per_trade <= rm.RM_MAX_RISK_PER_TRADE
    assert live.daily_cap <= rm.RM_DAILY_STOP and live.dd_stage2 <= rm.RM_DD_HALT
    assert not engine_limits(s, accs["icm-live"], paper=True).real_money
    for k, a in accs.items():
        if a.mode == "demo":
            lim = engine_limits(s, a)
            assert not lim.real_money and lim.adv_reference == {}, k


# ---------------------------------------------------------------- 6. demo unchanged (the existing suite, plus this)
def test_a_demo_learning_probe_is_decided_exactly_as_before_with_no_reference_volume():
    d_new = _check(_limits(real_money=False), _intent(cost_atr=0.05))
    d_old = RiskGate(RiskLimits()).check(_intent(cost_atr=0.05), _state(), NOW)
    assert d_new == d_old and d_new.allowed


# ---------------------------------------------------------------- 7. nothing can change the constants
CONSTANTS = ("RM_MAX_RISK_PER_TRADE", "RM_DAILY_STOP", "RM_DD_HALT", "RM_MAX_ADV_SHARE")


def test_the_constants_are_the_owners_figures_and_final_at_module_level():
    assert (rm.RM_MAX_RISK_PER_TRADE, rm.RM_DAILY_STOP, rm.RM_DD_HALT, rm.RM_MAX_ADV_SHARE) == (0.01, 0.02, 0.15, 0.01)
    tree = ast.parse((ROOT / "goldbot/risk/real_money.py").read_text())
    final = {n.target.id for n in tree.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
             and "Final" in ast.unparse(n.annotation)}
    assert set(CONSTANTS) <= final


def _writes(tree: ast.AST) -> list[str]:
    """Every place a module could rebind or shadow a constant: an attribute or name assignment, setattr, a
    `global`, an import that renames one, or a keyword or dict key naming one."""
    out: list[str] = []
    for n in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = list(n.targets) if isinstance(n, ast.Assign) else [n.target]
        for t in targets:
            for e in ast.walk(t):
                if isinstance(e, ast.Attribute) and e.attr in CONSTANTS or isinstance(e, ast.Name) and e.id in CONSTANTS:
                    out.append(ast.unparse(n))
        if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("setattr") and any(
                isinstance(a, ast.Constant) and a.value in CONSTANTS for a in n.args):
            out.append(ast.unparse(n))
        if isinstance(n, ast.Global) and set(n.names) & set(CONSTANTS):
            out.append(ast.unparse(n))
        if isinstance(n, ast.ImportFrom) and any(a.name in CONSTANTS and a.asname for a in n.names):
            out.append(ast.unparse(n))
        if isinstance(n, ast.keyword) and n.arg in CONSTANTS:
            out.append(f"{n.arg}=")
        if isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value in CONSTANTS for k in n.keys):
            out.append(ast.unparse(n))
    return out


def test_no_module_outside_real_money_can_set_the_constants_and_no_setting_names_them():
    bad: dict[str, list[str]] = {}
    for f in sorted((ROOT / "goldbot").rglob("*.py")):
        if f.name == "real_money.py" and f.parent.name == "risk":
            continue
        if hits := _writes(ast.parse(f.read_text())):
            bad[str(f.relative_to(ROOT))] = hits
    assert not bad
    settings = (ROOT / "config/settings.yaml").read_text().lower()
    from goldbot.config import RiskSettings
    assert not [k for k in RiskSettings.model_fields if k.startswith("rm_") or "real_money" in k]
    assert "rm_max" not in settings and "real_money" not in settings and "rm_daily" not in settings


def test_only_engine_limits_sets_the_real_money_flag_and_the_reference_volume():
    """No research, model-serving or dynamic-limits path can turn the flag off or swap the reference: the two
    `RiskLimits` fields are written by name only in ops/services/engine.py (and declared in the gate)."""
    allowed = {"goldbot/ops/services/engine.py", "goldbot/risk/gate.py", "goldbot/risk/real_money.py"}
    bad: dict[str, list[str]] = {}
    for f in sorted((ROOT / "goldbot").rglob("*.py")):
        rel = str(f.relative_to(ROOT))
        if rel in allowed:
            continue
        hits = [ast.unparse(n) for n in ast.walk(ast.parse(f.read_text()))
                if (isinstance(n, ast.keyword) and n.arg in ("real_money", "adv_reference"))
                or (isinstance(n, ast.Constant) and n.value in ("real_money", "adv_reference"))
                or (isinstance(n, ast.Attribute) and n.attr in ("real_money", "adv_reference")
                    and isinstance(n.ctx, ast.Store))]
        if hits:
            bad[rel] = hits
    assert not bad
