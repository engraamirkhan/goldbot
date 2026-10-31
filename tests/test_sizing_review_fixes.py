"""Trading-safety review of BACKLOG 28/25/29 (2026-10-10): the fixes, each with the behaviour it pins.

MEDIUM-2 no minimum-lot exception in tiny-live; MEDIUM-3 the absolute daily limit is per account (accounts.yaml, in
that account's currency), so the $10k USD paper broker is unaffected; MEDIUM-1 a currency rollout that cannot halt
on a stale file and says what it halted on; LOW-1 an unconverted P&L is flagged USD and kept out of money sums;
LOW-2 open notional at the terminal's contract size; LOW-4 the 1.2x minimum-lot tolerance capped at the 1% maximum;
LOW-5 the terminal read once per decision cycle and the conversion re-read at approval (fail closed)."""
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import RiskSettings, load_settings
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops import health
from goldbot.ops.gates_phase import load_closed_trades
from goldbot.ops.run import engine_limits
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.supervisor import Supervisor
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import ApprovalCenter, Proposal
from goldbot.telegram.digest import trades_line
from tests.test_currency_sizing import GBP, GbpPaper
from tests.test_health import make_ctx

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")
OWNER = 111


def _state(equity: float, day0: float | None = None, **kw: Any) -> AccountState:
    d0 = equity if day0 is None else day0
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=max(equity, d0), day_start_equity=d0,
                                week_start_equity=d0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _gbp_intent(stop: float = 10.90, mult: float = 1.5, **kw: Any) -> Intent:
    return Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=stop, cost_atr=0.01,
                  multiplier=mult, price=4_150.0, account_currency="GBP", acct_per_usd=GBP, **kw)


def _acc(mode: str = "demo", **kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id=f"icm-{mode}", broker="icm", mode=mode, server="s", login=1,
                                terminal_path="t", server_tz="UTC", symbol="XAUUSD", magic_base=260100, enabled=True)
    base.update(kw)
    return acc_mod.Account(**base)


@pytest.fixture
def phase_file(tmp_path, monkeypatch):
    f = tmp_path / "phase_state.json"
    monkeypatch.setattr(acc_mod, "PHASE_FILE", f)
    return f


def _settings_with_cap() -> Any:
    s = load_settings()
    return s.model_copy(update={"risk": s.risk.model_copy(update={"min_lot_risk_cap": 0.01})})


# ---------------------------------------------------------------------------------------------- MEDIUM-2
def test_tiny_live_limits_never_carry_the_minimum_lot_exception():
    risk = _settings_with_cap().risk
    assert RiskLimits.from_settings(risk, tiny_live=False).min_lot_risk_cap == 0.01
    assert RiskLimits.from_settings(risk, tiny_live=True).min_lot_risk_cap is None


def test_a_tiny_live_engine_never_takes_the_exception(tmp_path, phase_file):
    phase_file.write_text(json.dumps({"phase": 3, "gates_passed": ["backtest_to_paper", "paper_to_tiny_live"]}))
    s = _settings_with_cap()
    live = engine_limits(s, _acc("live"))
    assert live.risk_per_trade == s.risk.risk_per_trade_tiny_live and live.min_lot_risk_cap is None
    eng = Engine(EngineConfig(account_id="icm-live", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(), [], {},
                 limits=live)
    # £10,000 at tiny-live: the 0.01 lot (0.08%) is above 1.2 x 0.1% x 0.25 but inside a 1% cap; still refused
    d = eng.gate.check(_gbp_intent(mult=0.25), _state(10_000.0))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]
    assert engine_limits(s, _acc("demo")).min_lot_risk_cap == 0.01        # the demo (normal risk) keeps it


# ---------------------------------------------------------------------------------------------- MEDIUM-3
def test_the_absolute_daily_limit_lives_per_account_in_accounts_yaml_not_in_settings():
    assert "daily_loss_limit_abs" not in RiskSettings.model_fields
    assert "daily_loss_limit_abs" not in yaml.safe_load(Path("config/settings.yaml").read_text())["risk"]
    raw = yaml.safe_load(Path("config/accounts.yaml").read_text())["accounts"]
    assert raw["icm-demo"]["daily_loss_limit_abs"] == 220         # ADR 0042: 2% of the ~£11,000 demo balance
    assert raw["icm-probe"]["daily_loss_limit_abs"] == 220        # ADR 0042 (was 30, ADR 0037 am. 4)
    assert raw["vantage-demo"]["daily_loss_limit_abs"] == 200     # CFO 2026-10-26: 2% of the £10,000 demo deposit
    assert raw["vantage-247"]["daily_loss_limit_abs"] == 20       # ADR 0062: the weekend lane's own small day
    assert all("daily_loss_limit_abs" not in a for k, a in raw.items()
               if k not in ("icm-demo", "icm-probe", "vantage-demo", "vantage-247", "icm-live"))
    assert raw["icm-live"]["daily_loss_limit_abs"] == 6.25       # ADR 0055: the live pilot's £6.25 a day
    with pytest.raises(ValidationError):
        _acc(daily_loss_limit_abs=0)


def test_icm_demo_is_limited_at_50_and_the_paper_broker_at_10k_is_not(tmp_path):
    s = load_settings()
    icm = engine_limits(s, _acc(daily_loss_limit_abs=50.0))
    paper = engine_limits(s, _acc(daily_loss_limit_abs=50.0), paper=True)
    assert icm.daily_loss_limit_abs == 50.0 and paper.daily_loss_limit_abs is None
    usd = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=5.0, cost_atr=0.01, multiplier=1.0,
                 price=100.0)
    st = _state(10_000.0, 10_060.0)                      # $60 lost today; a $50-risk entry (0.5% of $10k)
    assert RiskGate(paper).check(usd, st).allowed
    assert RiskGate(icm).check(usd, _state(10_000.0, 10_060.0)).reasons == ["daily_loss_limit_abs"]


def test_the_engine_publishes_its_accounts_absolute_limit_and_the_supervisor_applies_the_smallest(tmp_path):
    pb = PaperBroker()
    pb.on_tick(Tick(ts_utc=T0, bid=2_000.0, ask=2_000.2))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [], {},
                 limits=RiskLimits(daily_loss_limit_abs=50.0))
    eng.state.day_start_equity, eng.state.equity = 10_000.0, 9_950.0
    eng._write_state()
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["daily_loss_limit_abs"] == 50.0
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and "combined_daily_loss_abs" in s["reasons"] and s["daily_loss_limit_abs"] == 50.0
    # an engine without an absolute limit (paper) publishes None: no absolute halt
    eng2 = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [], {})
    eng2.state.day_start_equity, eng2.state.equity = 10_000.0, 9_950.0
    eng2._write_state()
    s = Supervisor(tmp_path).evaluate()
    assert not s["halt"] and s["daily_loss_limit_abs"] is None


def test_the_shipped_registry_runs_vantage_demo_only_as_a_probe_only_host_outside_the_combined_currency_check():
    accs = acc_mod.load_accounts(Path("config/accounts.yaml"))
    assert accs["icm-demo"].daily_loss_limit_abs == 220 and accs["icm-demo"].enabled      # ADR 0042
    # a probe_only engine file is kept out of the supervisor's currency check (ADR 0037 am. 4), so a second deposit
    # currency could never halt the book; vantage-demo is configured as a learning host only (PD 2026-10-26)
    assert accs["vantage-demo"].probe_only


# ---------------------------------------------------------------------------------------------- MEDIUM-1
def _engine_file(d: Path, account: str, currency: str | None, age_s: float = 1.0) -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "ts": time.time() - age_s, "equity": 1_000.0, "day_start_equity": 1_000.0,
        "week_start_equity": 1_000.0, "balance_closed_hwm": 1_000.0, "currency": currency}))


def test_a_stale_engine_file_is_left_out_of_the_currency_check(tmp_path):
    _engine_file(tmp_path, "icm-demo", "GBP")
    _engine_file(tmp_path, "vantage-demo", "USD", age_s=600)        # an engine that no longer runs
    s = Supervisor(tmp_path).evaluate()
    assert "mixed_account_currencies" not in s["reasons"] and s["currencies"] == {"icm-demo": "GBP"}
    _engine_file(tmp_path, "vantage-demo", "USD")                    # running again: mixed
    s = Supervisor(tmp_path).evaluate()
    assert "mixed_account_currencies" in s["reasons"]
    assert s["currency_files"] == {"icm-demo": "engine_icm-demo.json", "vantage-demo": "engine_vantage-demo.json"}


def _money_file(d: Path, account: str, currency: str | None, *, equity: float, day0: float, hwm: float,
                age_s: float = 1.0, lots: float = 0.0) -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "ts": time.time() - age_s, "equity": equity, "day_start_equity": day0,
        "week_start_equity": day0, "balance_closed_hwm": hwm, "currency": currency, "open_lots": lots,
        "open_notional": lots * 100 * 4_150.0}))


def test_a_stale_file_in_another_currency_never_dilutes_the_combined_money(tmp_path):
    """Re-verification MEDIUM: a fresh GBP engine 1.6% down plus a stale USD file must not read as 0.8% down."""
    _money_file(tmp_path, "icm-demo", "GBP", equity=984.0, day0=1_000.0, hwm=1_000.0)
    alone = Supervisor(tmp_path).evaluate()
    _money_file(tmp_path, "vantage-demo", "USD", equity=10_000.0, day0=10_000.0, hwm=10_000.0, age_s=600, lots=0.02)
    s = Supervisor(tmp_path).evaluate()
    for k in ("combined_equity", "day_loss", "week_loss", "drawdown"):
        assert s[k] == pytest.approx(alone[k]), k
    assert s["halt"] and s["reasons"] == ["combined_daily_cap"]            # 1.6% >= 1.5%: still caught
    assert s["exposure"]["vantage-demo"]["equity"] == 0.0                  # no money from the other currency ...
    assert s["exposure"]["vantage-demo"]["lots"] == 0.02                   # ... but its exposure stays (strict)
    assert s["money_excluded"] == ["engine_vantage-demo.json"]
    # the engine's view: other_equity unchanged, other lots counted
    pb = PaperBroker()
    pb.on_tick(Tick(ts_utc=pd.Timestamp.now("UTC"), bid=4_150.0, ask=4_150.2))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), halt_checks=True),
                 pb, [], {})
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.other_equity == 0.0 and eng.state.other_lots == 0.02


def test_a_stale_file_without_a_currency_stays_out_of_the_money_and_one_in_the_same_currency_counts(tmp_path):
    _money_file(tmp_path, "icm-demo", "GBP", equity=984.0, day0=1_000.0, hwm=1_000.0)
    _money_file(tmp_path, "old", None, equity=5_000.0, day0=5_000.0, hwm=5_000.0, age_s=600)
    s = Supervisor(tmp_path).evaluate()
    assert s["combined_equity"] == pytest.approx(984.0) and s["money_excluded"] == ["engine_old.json"]
    (tmp_path / "engine_old.json").unlink()
    _money_file(tmp_path, "icm-demo-2", "GBP", equity=1_000.0, day0=1_000.0, hwm=1_000.0, age_s=600)
    s = Supervisor(tmp_path).evaluate()
    assert s["combined_equity"] == pytest.approx(1_984.0) and s["money_excluded"] == []   # same currency: as before


def test_health_warns_naming_a_file_kept_out_of_the_money(tmp_path):
    _money_file(tmp_path, "icm-demo", "GBP", equity=1_000.0, day0=1_000.0, hwm=1_000.0)
    _money_file(tmp_path, "vantage-demo", "USD", equity=10_000.0, day0=10_000.0, hwm=10_000.0, age_s=600)
    Supervisor(tmp_path).evaluate()
    c = health.check_supervisor(make_ctx(tmp_path, now=pd.Timestamp.now("UTC")))
    assert c.status == "warn" and "engine_vantage-demo.json" in c.reason and "not summed" in c.reason


def test_the_paper_exemption_goes_by_broker_type_not_its_name():
    from goldbot.ops.run import paper_stand_in

    class Renamed(PaperBroker):
        name = "mt5"

    class Impostor:
        name = "paper"

    assert paper_stand_in(PaperBroker()) and paper_stand_in(Renamed())
    assert not paper_stand_in(Impostor())


def test_health_names_mixed_currencies_and_the_offending_files(tmp_path):
    _engine_file(tmp_path, "icm-demo", "GBP")
    _engine_file(tmp_path, "vantage-demo", "USD")
    Supervisor(tmp_path).evaluate()
    ctx = make_ctx(tmp_path, now=pd.Timestamp.now("UTC"))
    c = health.check_supervisor(ctx)
    assert c.status == "fail"
    assert "mixed_account_currencies" in c.reason
    assert "engine_icm-demo.json (GBP)" in c.reason and "engine_vantage-demo.json (USD)" in c.reason


def test_the_startup_error_payload_carries_the_currency(tmp_path):
    class Dead(GbpPaper):
        def positions(self, magic_prefix: int | None = None) -> list:
            raise RuntimeError("terminal down")
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps({"sent": {}, "open": {}}))
    with pytest.raises(RuntimeError):
        Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), Dead(), [], {})
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["startup_error"] and e["currency"] == "GBP"


# ---------------------------------------------------------------------------------------------- LOW-1
def test_an_unconverted_own_fill_pnl_is_recorded_in_usd_with_a_flag_and_kept_out_of_money_sums(tmp_path):
    pb = GbpPaper(fx=None, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                              owner_user_id=OWNER), pb, [TrendSpecialist()], {})
    agent = TrendSpecialist()
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.0))
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.01,
                    entry=4_150.0, stop=4_139.10, target=4_180.0, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.01, 4_139.10, 4_180.0, requested=4_150.0, atr_usd=10.90)
    (pid,) = eng.open

    def no_deals(since: pd.Timestamp) -> pd.DataFrame:
        raise RuntimeError("history unreadable")

    pb.deals_since = no_deals                                       # type: ignore[method-assign,assignment]
    eng._record_closed(pid, eng.open[pid], "engine_stop_close", 4_139.10, T0 + pd.Timedelta(minutes=30))
    (t,) = load_closed_trades(tmp_path)[0]
    assert t.pnl_currency == "USD" and t.pnl == pytest.approx(-10.90)          # USD, never treated as factor 1 GBP
    assert t.r == pytest.approx(-1.0)                                           # R is currency-free on this path
    # a converted trade the same day: the digest sums only the account-currency P&L and says what it left out
    from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
    append_closed_trade(tmp_path, ClosedTrade(account_id="icm-demo", broker="icm", mode="demo",
                                              exit_utc=T0 + pd.Timedelta(minutes=40), ret=0.001, pnl=5.0,
                                              equity_before=1_000.0, position_id=99, pnl_basis="net"))
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"account": "icm-demo", "currency": "GBP"}))
    line = trades_line(tmp_path, T0, T0 + pd.Timedelta(days=1))[0]
    # the engine's own fills carry no commission or swap: that row is gross only (GAP-05), never in the net
    assert "net +£5.00 · " in line and "1 trade gross only (costs not recorded): -$10.90" in line
    assert "5.90" not in line


def test_a_converted_gbp_trade_states_its_currency_is_not_flagged_and_is_summed(tmp_path):
    """Dashboard contract: pnl_currency is the ACCOUNT currency when P&L and R were converted ("USD" only for the
    unconverted fallback, None only on legacy records), so the monthly report's r_understated never flags it."""
    from goldbot.ops.monthly_report import r_understated
    pb = GbpPaper(commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                              owner_user_id=OWNER), pb, [TrendSpecialist()], {})
    agent = TrendSpecialist()
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.0))
    prop = Proposal(proposal_id="icm-demo-2-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.01,
                    entry=4_150.0, stop=4_139.10, target=4_180.0, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.01, 4_139.10, 4_180.0, requested=4_150.0, atr_usd=10.90)
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=30), bid=4_180.0, ask=4_180.0))       # the target
    eng._manage_open(pd.DataFrame())
    eng._save_orders()
    (t,) = load_closed_trades(tmp_path)[0]
    assert t.pnl_currency == "GBP" and t.r == pytest.approx(30.0 / 10.90)
    assert not r_understated(t, "GBP")
    from goldbot.ops.gates_phase import ClosedTrade
    legacy = ClosedTrade(account_id="icm-demo", broker="icm", mode="demo", exit_utc=T0, ret=0.0, pnl=1.0,
                         equity_before=1_000.0, r=0.1)
    assert r_understated(legacy, "GBP")                                         # a pre-fix record keeps its flag
    eng._write_state()                                                          # engine file: currency GBP
    line = trades_line(tmp_path, T0, T0 + pd.Timedelta(days=1))[0]
    # summed in pounds, as gross: the paper fill records no commission or swap (GAP-05), so it is never labelled net
    assert f"1 trade gross only (costs not recorded): +£{30.0 / 1.33:,.2f}" in line and "$" not in line


# ---------------------------------------------------------------------------------------------- LOW-2
def test_open_notional_uses_the_terminals_contract_size(tmp_path):
    pb = PaperBroker(contract=10.0)
    pb.on_tick(Tick(ts_utc=T0, bid=2_000.0, ask=2_000.0))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
                 pb, [], {}, ApprovalCenter({OWNER}))
    from goldbot.execution.broker import OrderIntent
    pb.place_order(OrderIntent(client_order_id="c1", symbol="XAUUSD", side=1, lots=0.5, sl=1_990.0, tp=2_100.0,
                               magic=260150))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.open_lots == 0.5 and eng.state.open_notional == pytest.approx(0.5 * 10.0 * 2_000.0)


# ---------------------------------------------------------------------------------------------- LOW-4
def test_the_minimum_lot_tolerance_never_exceeds_the_one_percent_maximum():
    # 0.8% x 1.5 clamps to a 1% target; 1.2x would tolerate a 1.2% minimum lot. $1,000, $11 stop: 0.01 lot = 1.1%
    i = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=11.0, cost_atr=0.01, multiplier=1.5,
               price=100.0)
    d = RiskGate(RiskLimits(risk_per_trade=0.008)).check(i, _state(1_000.0))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]
    ok = RiskGate(RiskLimits(risk_per_trade=0.008)).check(i.model_copy(update={"atr_usd": 10.0}), _state(1_000.0))
    assert ok.allowed and ok.risk_fraction == pytest.approx(0.01)            # exactly 1%: allowed


# ---------------------------------------------------------------------------------------------- LOW-5
class Counting(GbpPaper):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.calls = {"symbol_info": 0, "loss_per_lot": 0}

    def symbol_info(self, symbol: str) -> Any:
        self.calls["symbol_info"] += 1
        return super().symbol_info(symbol)

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        self.calls["loss_per_lot"] += 1
        return super().loss_per_lot(symbol, side, price, distance)


def test_the_terminal_is_read_once_per_decision_cycle(tmp_path):
    pb = Counting()
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.2))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [], {})
    a = eng._sizing_terms(1, 4_150.2, 10.9, cycle=T0)
    b = eng._sizing_terms(1, 4_150.2, 5.0, cycle=T0)
    c = eng._sizing_terms(-1, 4_150.0, 10.9, cycle=T0)
    assert a["acct_per_usd"] == pytest.approx(GBP) == b["acct_per_usd"] == c["acct_per_usd"]
    assert pb.calls == {"symbol_info": 1, "loss_per_lot": 2}                  # one per side
    eng._sizing_terms(1, 4_150.2, 10.9, cycle=T0 + pd.Timedelta(minutes=15))
    assert pb.calls == {"symbol_info": 2, "loss_per_lot": 3}                  # a new bar close reads again


def _approval_engine(tmp_path: Path, pb: GbpPaper) -> tuple[Engine, ApprovalCenter]:
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.2))
    center = ApprovalCenter({OWNER})
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER),
                 pb, [], {}, center)
    eng.bars_1m = pd.DataFrame({"ts_utc": [T0 - pd.Timedelta(minutes=1)], "visible_at": [T0]})   # fresh data
    agent = SPECIALISTS["session_open"]()
    intent = Intent(agent_id=agent.agent_id, side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                    multiplier=1.0, price=4_150.2, account_currency="GBP", acct_per_usd=GBP)
    prop = Proposal(proposal_id="icm-demo-9-a", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.12,
                    entry=4_150.2, stop=4_146.2, target=4_156.2, p=0.62, ev_r=0.2, spread_points=20, top_features=[])
    eng.pending[prop.proposal_id] = (intent, prop, agent)
    center.propose(prop)
    return eng, center


def test_the_conversion_is_read_again_at_approval_and_a_lost_one_refuses_the_entry(tmp_path):
    pb = GbpPaper()
    eng, center = _approval_engine(tmp_path, pb)
    pb.fx = None                                                               # the terminal lost the conversion
    center.decide("icm-demo-9-a", OWNER, True)
    assert not pb.positions()
    assert any(d.get("action") == "gate_at_approval:fx_conversion_unavailable" for d in eng.decisions)


def test_an_approval_with_the_conversion_still_available_places_the_order(tmp_path):
    pb = GbpPaper()
    eng, center = _approval_engine(tmp_path, pb)
    center.decide("icm-demo-9-a", OWNER, True)
    assert len(pb.positions()) == 1


# ---------------------------------------------------------------------------------------------- dashboard contract
def test_the_accounts_absolute_limit_and_the_min_lot_risk_are_published_under_limits(tmp_path):
    """engine_<account>.json `limits.daily_loss_limit_abs` (accounts.yaml, account currency) and
    `limits.min_lot_risk_account` (the minimum lot's stop risk in the account currency at the latest ATR stop)."""
    acc = _acc(account_id="icm-demo", daily_loss_limit_abs=50.0)
    pb = GbpPaper()
    pb.on_tick(Tick(ts_utc=T0, bid=4_150.0, ask=4_150.2))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [], {},
                 limits=engine_limits(load_settings(), acc))
    eng._write_state()
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["limits"]["daily_loss_limit_abs"] == 50.0
    assert e["limits"]["min_lot_risk_account"] is None                                   # no stop seen yet
    assert "min_lot_risk_cap" in e["limits"] and e["limits"]["daily_cap"] == 0.02       # the dashboard's keys too
    eng._note_min_lot_risk(eng._sizing_terms(1, 4_150.2, 10.90, cycle=T0), 10.90)
    eng._write_state()
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["limits"]["min_lot_risk_account"] == pytest.approx(round(0.01 * 10.90 * 100 * GBP, 2))   # £8.20
    pb.fx = None                                                           # conversion lost: unknown, not a guess
    eng._note_min_lot_risk(eng._sizing_terms(1, 4_150.2, 10.90, cycle=T0 + pd.Timedelta(minutes=15)), 10.90)
    eng._write_state()
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["limits"]["min_lot_risk_account"] is None
    paper = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), PaperBroker(), [],
                   {}, limits=engine_limits(load_settings(), acc, paper=True))
    paper._write_state()
    assert json.loads((tmp_path / "engine_icm-demo.json").read_text())["limits"]["daily_loss_limit_abs"] is None
