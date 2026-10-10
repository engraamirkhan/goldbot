"""Acting on the account class (design: Account classifier, row X14): on Standard the 15m specialists are disabled
(session-open excepted) and the rest run only when the expected edge exceeds 1.5x the measured round-trip cost; on
Unknown the engine trades nothing (paper only) and the health check alerts. A paper broker has no class."""
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_health import engine as engine_file
from tests.test_health import make_ctx

T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                                open_positions=0, margin_used=0.0, last_tick_age_s=1.0, spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _intent(**kw: Any) -> Intent:
    # gross edge p*T - (1-p)*S = 0.62*1.5 - 0.38*1.0 = 0.55 ATR; cost 0.1 ATR
    base: dict[str, Any] = dict(agent_id="trend-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0,
                                cost_atr=0.1, multiplier=1.0, price=2400.0, timeframe="1h", family="trend")
    base.update(kw)
    return Intent(**base)


# ---------------------------------------------------------------------------------------------- the gate
def test_raw_accounts_are_not_restricted():
    assert RiskGate().check(_intent(timeframe="15m", family="mean_reversion"), _state(account_class="raw")).allowed


def test_unknown_class_blocks_every_entry():
    d = RiskGate().check(_intent(), _state(account_class="unknown"))
    assert not d.allowed and d.reasons == ["account_class_unknown"]


@pytest.mark.parametrize("family, allowed", [("mean_reversion", False), ("intraday_momentum", False),
                                             ("session_open", True)])
def test_standard_disables_15m_families_except_session_open(family, allowed):
    d = RiskGate().check(_intent(timeframe="15m", family=family), _state(account_class="standard"))
    assert d.allowed is allowed
    assert ("standard_account_15m" in d.reasons) is (not allowed)


def test_standard_needs_an_edge_above_one_and_a_half_times_cost():
    gate, st = RiskGate(), _state(account_class="standard")
    assert gate.check(_intent(cost_atr=0.36), st).allowed                     # 0.55 > 1.5 x 0.36 = 0.54
    d = gate.check(_intent(cost_atr=0.37), st)                                # 0.55 < 0.555
    assert not d.allowed and "standard_account_edge" in d.reasons
    assert gate.check(_intent(cost_atr=0.37), _state(account_class="raw")).allowed   # the same trade on Raw


# ---------------------------------------------------------------------------------------------- the engine
def _engine(tmp_path: Path, mode: str) -> Engine:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(Tick(ts_utc=T0, bid=2400.0, ask=2400.2))
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", mode=mode, state_dir=str(tmp_path)), pb, [], {},
                  ApprovalCenter({1}))


def test_engine_reads_the_class_for_a_broker_account_and_ignores_it_on_paper(tmp_path):
    tick = Tick(ts_utc=T0, bid=2400.0, ask=2400.2)
    demo = _engine(tmp_path, "demo")
    demo._refresh_account(tick)
    assert demo.state.account_class == "unknown"                             # not classified yet: fail closed
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps({"class": "standard", "pending": None, "runs": []}))
    demo._refresh_account(tick)
    assert demo.state.account_class == "standard"
    (tmp_path / "classifier_icm-demo.json").write_text("{torn")
    demo._class_cache = (-2.0, "standard")                                   # force a re-read of the torn file
    demo._refresh_account(tick)
    assert demo.state.account_class == "unknown"
    paper = _engine(tmp_path, "paper")
    paper._refresh_account(tick)
    assert paper.state.account_class == "raw"


# ---------------------------------------------------------------------------------------------- the alert
def test_health_warns_while_a_broker_account_is_unclassified(tmp_path):
    ctx = make_ctx(tmp_path)
    engine_file(tmp_path, 60, mode="demo", account_class="unknown")
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "warn" and "account class unknown" in c.reason
    engine_file(tmp_path, 60, mode="demo", account_class="standard")
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "ok" and "standard" in c.reason
    engine_file(tmp_path, 60, mode="paper", account_class="unknown")
    assert health.check_engine(ctx, "icm-demo").status == "ok"
