"""BACKLOG 29 / ADR 0004 section 3: the owner's absolute daily loss limit, `risk.daily_loss_limit_abs` (account currency).

Entries only, projected: an entry is refused (`daily_loss_limit_abs`) when the day's loss so far plus this trade's full
stop risk in the account currency would exceed min(daily cap x day-start equity, the absolute limit). Null keeps every
decision as before. Exits never consult it. The supervisor applies the absolute limit to the combined day loss."""
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import RiskSettings, load_settings
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal
from tests.test_health import by_name, engine, make_ctx

GBP = 1 / 1.33


def _state(equity: float, day0: float, **kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=max(equity, day0), day_start_equity=day0,
                                week_start_equity=day0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _usd(stop: float, mult: float) -> Intent:
    return Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=stop, cost_atr=0.01,
                  multiplier=mult, price=100.0, account_currency="USD")


def _gate(abs_limit: float | None = 50.0, **kw: Any) -> RiskGate:
    return RiskGate(RiskLimits(daily_loss_limit_abs=abs_limit, **kw))


# ---------------------------------------------------------------------------------------------- the projected check
def test_5000_equity_40_lost_a_15_risk_entry_is_refused_and_a_9_risk_entry_allowed():
    st = _state(5_000.0, 5_040.0)                                   # £40 lost today (0.79%, inside the 2% cap)
    big = _gate().check(_usd(5.0, 0.6), st)                         # 0.3% of 5,000 = 15 -> 0.03 lot x $5 x 100 = 15
    assert not big.allowed and big.reasons == ["daily_loss_limit_abs"]
    small = _gate().check(_usd(3.0, 0.36), _state(5_000.0, 5_040.0))   # 9 -> 0.03 lot x $3 x 100 = 9; 40 + 9 = 49
    assert small.allowed and small.risk_amount == pytest.approx(9.0)


def test_null_keeps_the_decision_as_before():
    d = _gate(None).check(_usd(5.0, 0.6), _state(5_000.0, 5_040.0))
    assert d.allowed and d.risk_amount == pytest.approx(15.0)


def test_the_percentage_cap_binds_first_below_2500():
    # £1,000 GBP account, two full 15m losses (£16.90) today; a third 0.01 lot (£8.20) would take the day to £25.10,
    # past min(2% x 1,000 = 20, 50) = 20, so it is refused although the absolute 50 is far away
    i = Intent(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=10.90, cost_atr=0.01, multiplier=1.5,
               price=4_150.0, account_currency="GBP", acct_per_usd=GBP)
    assert _gate().check(i, _state(983.10, 1_000.0)).reasons == ["daily_loss_limit_abs"]
    assert _gate().check(i, _state(1_000.0, 1_000.0)).allowed                 # first trade of the day: 8.20 <= 20


def test_the_effective_limit_is_the_smaller_of_the_percentage_and_the_absolute():
    g = _gate()
    assert g.daily_loss_limit(_state(1_000.0, 1_000.0)) == pytest.approx(20.0)     # 2% binds below 2,500
    assert g.daily_loss_limit(_state(5_000.0, 5_000.0)) == pytest.approx(50.0)     # the absolute binds above
    assert g.daily_loss_limit(_state(5_000.0, 0.0)) == pytest.approx(50.0)         # no day start yet: the absolute
    assert _gate(None).daily_loss_limit(_state(5_000.0, 5_000.0)) == pytest.approx(100.0)   # pct only when off


def test_the_boundary_is_inclusive():
    # 39.90 lost + exactly 10 risk + the 0.10 round trip (0.01 ATR x $5 x 0.02 lot x 100; BACKLOG 36) = 50: allowed;
    # 40 lost is now 50.10, refused; a gain today counts as no loss
    assert _gate().check(_usd(5.0, 0.4), _state(5_000.0, 5_039.90)).allowed      # 0.2% = 10 -> 0.02 x 5 x 100 = 10
    assert _gate().check(_usd(5.0, 0.4), _state(5_000.0, 5_040.0)).reasons == ["daily_loss_limit_abs"]
    assert _gate().check(_usd(5.0, 0.6), _state(5_100.0, 5_000.0)).allowed       # up 100 today, 15.3 risk


def test_the_limit_is_per_account_and_validated():
    # review MEDIUM-3: not a global setting (one number read in each account's currency); accounts.yaml per account
    assert "daily_loss_limit_abs" not in RiskSettings.model_fields
    assert RiskLimits.from_settings(load_settings().risk, tiny_live=False).daily_loss_limit_abs is None
    assert SupervisorLimits.from_settings(load_settings().risk).daily_loss_limit_abs is None
    from goldbot.ops import accounts
    assert accounts.load_accounts()["icm-demo"].daily_loss_limit_abs == 50
    for bad in (0, -5):
        with pytest.raises(ValidationError):
            accounts.Account(account_id="a", broker="icm", mode="demo", server="s", login=None, terminal_path="t",
                             server_tz="UTC", symbol="XAUUSD", magic_base=1, enabled=True, daily_loss_limit_abs=bad)


# ---------------------------------------------------------------------------------------------- the supervisor
def _engine_file(d: Path, account: str, equity: float, day0: float) -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({
        "account": account, "ts": time.time(), "equity": equity, "day_start_equity": day0, "week_start_equity": day0,
        "balance_closed_hwm": day0, "currency": "GBP"}))


def test_the_supervisor_halts_when_the_combined_day_loss_reaches_the_absolute_limit(tmp_path):
    _engine_file(tmp_path, "icm-demo", 4_970.0, 5_000.0)
    _engine_file(tmp_path, "vantage-demo", 4_980.0, 5_000.0)          # 50 lost combined: 0.5%, inside 1.5%
    s = Supervisor(tmp_path, SupervisorLimits(daily_loss_limit_abs=50.0)).evaluate()
    assert s["halt"] and s["reasons"] == ["combined_daily_loss_abs"] and s["day_loss_amount"] == pytest.approx(50.0)
    _engine_file(tmp_path, "vantage-demo", 4_981.0, 5_000.0)          # 49 lost
    assert not Supervisor(tmp_path, SupervisorLimits(daily_loss_limit_abs=50.0)).evaluate()["halt"]
    _engine_file(tmp_path, "vantage-demo", 4_980.0, 5_000.0)
    assert not Supervisor(tmp_path, SupervisorLimits()).evaluate()["halt"]          # off: as before


def test_at_1000_the_supervisors_1_5_percent_cap_stops_entries_first(tmp_path):
    _engine_file(tmp_path, "icm-demo", 983.10, 1_000.0)               # two full losses, £16.90 (1.69%)
    s = Supervisor(tmp_path, SupervisorLimits(daily_loss_limit_abs=50.0)).evaluate()
    assert s["halt"] and s["reasons"] == ["combined_daily_cap"]


# ---------------------------------------------------------------------------------------------- engine state, health, exits
T0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")


def _engine(tmp_path: Path, pb: PaperBroker) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode="demo", state_dir=str(tmp_path),
                               owner_user_id=111), pb, [TrendSpecialist()], {},
                  limits=RiskLimits(daily_loss_limit_abs=50.0))


def test_the_engine_publishes_the_effective_daily_limit_and_what_is_left(tmp_path):
    pb = PaperBroker()
    pb.on_tick(Tick(ts_utc=T0, bid=2_000.0, ask=2_000.2))
    eng = _engine(tmp_path, pb)
    eng.state.day_start_equity, eng.state.equity = 1_000.0, 990.0
    eng._write_state()
    e = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert e["daily_loss_limit"] == pytest.approx(20.0) and e["daily_loss_remaining"] == pytest.approx(10.0)


def test_health_shows_the_daily_limit_and_warns_when_none_is_left(tmp_path):
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 60, equity=4_990.0, day_start_equity=5_000.0, week_start_equity=5_000.0, daily_loss_limit=50.0,
           daily_loss_remaining=40.0)
    c = by_name(health.check_loss_caps(ctx, "icm-demo"))["daily_cap:icm-demo"]
    assert c.status == "ok" and "daily loss limit 50.00 (40.00 left)" in c.reason
    engine(tmp_path, 60, equity=4_950.0, day_start_equity=5_000.0, week_start_equity=5_000.0, daily_loss_limit=50.0,
           daily_loss_remaining=0.0)
    c = by_name(health.check_loss_caps(ctx, "icm-demo"))["daily_cap:icm-demo"]
    assert c.status == "warn" and "DAILY LOSS LIMIT" in c.reason


def test_exits_never_consult_the_daily_limit(tmp_path):
    pb = PaperBroker(adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb)
    agent = TrendSpecialist()
    pb.on_tick(Tick(ts_utc=T0, bid=2_000.0, ask=2_000.0))
    prop = Proposal(proposal_id="icm-demo-1-x", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=0.1,
                    entry=2_000.0, stop=1_990.0, target=2_030.0, p=0.6, ev_r=0.1, spread_points=0.0, top_features=[])
    eng._execute(prop, agent, 0.1, 1_990.0, 2_030.0, requested=2_000.0, atr_usd=10.0)
    (pid,) = eng.open
    eng.state.day_start_equity, eng.state.equity = 10_000.0, 9_000.0          # far past the limit
    calls: list[Any] = []
    real = eng.gate.check

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(a)
        return real(*a, **k)

    eng.gate.check = spy                                                     # type: ignore[method-assign]
    pb.on_tick(Tick(ts_utc=T0 + pd.Timedelta(minutes=5), bid=1_989.0, ask=1_989.0))
    eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and not pb.positions() and calls == []
