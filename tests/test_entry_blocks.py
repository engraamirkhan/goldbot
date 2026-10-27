"""BACKLOG 43 (ADR 0011): the engine publishes why it refuses entries. `RiskGate.account_reasons` is every reason
`check()` refuses an entry for that does not depend on the intent, and `check()` starts from it, so the two cannot
drift; the engine publishes that list as `entry_blocks` at every state write (the heartbeat's too, on the feed it
publishes) and the last per-trade refusal per agent as `entry_refusals`. Exits are never gated by any of it."""
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import HeatSettings, HolidaySettings, ReopenSettings, ThrottleSettings, Tier2Blackout
from goldbot.engine import Engine
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import PER_TRADE_REFUSALS, GateDecision, Stage
from goldbot.risk.throttle import AgentThrottle
from tests import test_closed_market_state_cadence as cadence
from tests.test_auto_same_close import BASE, _actions, _run, _two
from tests.test_closed_market_state_cadence import FRI_LAST, _closed_loop
from tests.test_engine_clock import _engine
from tests.test_sizing_review_fixes import OWNER, GbpPaper, _approval_engine

frozen = cadence.frozen                    # the BACKLOG 59 closed-market engine fixture
NOW = pd.Timestamp("2026-10-14 14:00", tz="UTC")
LIMITS = RiskLimits(tier2=Tier2Blackout(enabled=True), reopen=ReopenSettings(enabled=True),
                    holidays=HolidaySettings(enabled=True), heat=HeatSettings(enabled=True),
                    throttle=ThrottleSettings(enabled=True))


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000, balance_closed_hwm=10_000, day_start_equity=10_000,
                                week_start_equity=10_000, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0, open_risk=0.0)
    base.update(kw)
    return AccountState(**base)


def _intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="session_open-g0-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0,
                                atr_usd=4.0, cost_atr=0.1, multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


# every account-level condition `check()` refuses for, and the code it is listed as
FLAGS: list[tuple[dict[str, Any], str]] = [
    ({"supervisor_halt": True}, "supervisor_halt"), ({"owner_halt": True}, "owner_halt"),
    ({"stage": Stage.HALTED}, "drawdown_halt"), ({"equity_unconfirmed": True}, "equity_unconfirmed"),
    ({"dq_error": True}, "data_quality_error"), ({"in_blackout": True}, "news_blackout"),
    ({"tier2_blackout": True}, "tier2_blackout"), ({"reopen_window": True}, "reopen_window"),
    ({"holiday_thin": True}, "holiday_thin"), ({"calendar_unavailable": True}, "calendar_unavailable"),
    ({"in_rollover": True}, "rollover"), ({"weekend": True}, "weekend"), ({"drift_halt": True}, "drift_system_halt"),
    ({"last_tick_age_s": 600.0}, "stale_data"), ({"stale_bars": True}, "stale_data"),
    ({"tick_ahead_s": 60.0}, "tick_from_future"), ({"clock_mismatch": True}, "server_clock_mismatch"),
    ({"market_closed": True}, "market_closed"), ({"tick_error": True}, "tick_error"),
    ({"bridge_incompatible": True}, "bridge_incompatible"), ({"data_recovering": True}, "data_recovering"),
    ({"spread_points": 100.0}, "spread_too_wide"), ({"open_positions": 2}, "max_positions"),
    ({"equity": 9_750.0, "balance_closed_hwm": 9_750.0}, "daily_cap"),
    ({"week_start_equity": 10_600.0}, "weekly_cap"), ({"account_class": "unknown"}, "account_class_unknown"),
    ({"open_risk": None}, "open_risk_unknown"), ({"equity": math.nan}, "invalid_state"),
]
# an intent with no reason of its own, one refused for its own EV, and (trading-safety INFO-3) one also refused for its
# side's cap: (intent, the account facts it is judged on besides the flag)
SIDE_FULL = {"open_long": LIMITS.heat.max_same_direction}
INTENTS: dict[str, tuple[Intent, dict[str, Any]]] = {
    "clean": (_intent(), {}), "own": (_intent(p=0.2), {}), "side_cap": (_intent(p=0.2), SIDE_FULL)}


@pytest.mark.parametrize("over, code", FLAGS, ids=[c + ":" + ",".join(o) for o, c in FLAGS])
@pytest.mark.parametrize("which", list(INTENTS))
def test_check_refuses_with_exactly_the_account_reasons_plus_the_intents_own(over, code, which):
    gate, (intent, base) = RiskGate(LIMITS), INTENTS[which]
    own = gate.check(intent, _state(**base)).reasons
    assert ("same_direction_cap" in own) == (which == "side_cap")
    st = _state(**{**base, **over})
    listed = gate.account_reasons(st, NOW)
    assert code in listed
    d = gate.check(intent, st, NOW)
    assert not d.allowed
    # a number the gate cannot compare stops before the intent's checks: nothing of the intent's own is judged
    assert d.reasons == listed + ([] if code == "invalid_state" else own)


def test_a_healthy_account_has_no_account_reasons_and_the_clean_intent_is_allowed():
    gate = RiskGate(LIMITS)
    assert gate.account_reasons(_state(), NOW) == []
    assert gate.check(_intent(), _state(), NOW).allowed
    assert gate.check(_intent(p=0.2), _state(), NOW).reasons == ["negative_ev"]


def test_every_flag_at_once_is_listed_in_the_order_check_refuses_with():
    gate = RiskGate(LIMITS)
    st = _state(**{k: v for over, _ in FLAGS if "equity" not in over for k, v in over.items()})
    listed = gate.account_reasons(st, NOW)
    assert gate.check(_intent(), st, NOW).reasons == listed
    assert set(listed) == {c for over, c in FLAGS if "equity" not in over}


def test_account_reasons_only_reads_the_stage_machine_runs_in_check():
    gate = RiskGate(LIMITS)
    st = _state(equity=8_700.0, day_start_equity=8_700.0, week_start_equity=8_700.0)   # 13% below the high
    assert gate.account_reasons(st, NOW) == [] and st.stage == Stage.NORMAL
    assert "drawdown_halt" in gate.check(_intent(), st, NOW).reasons


def test_the_throttle_is_an_account_reason_only_for_the_agent_it_is_given_for():
    gate = RiskGate(LIMITS)
    t = AgentThrottle(agent_id="a", timeframe="1h", loss_streak=9, entries_today=0,
                      last_loss_utc=NOW - pd.Timedelta(minutes=5))
    assert gate.account_reasons(_state(), NOW) == []
    assert gate.account_reasons(_state(), NOW, t) == ["throttle_loss_streak"]
    assert gate.check(_intent(), _state(), NOW, throttle=t).reasons == ["throttle_loss_streak"]


def test_a_bad_intent_number_keeps_the_account_reasons_and_names_its_field():
    gate = RiskGate(LIMITS)
    st = _state(weekend=True)
    assert gate.check(_intent(stop_atr=math.nan), st, NOW).reasons == ["weekend", "invalid_state", "invalid:stop_atr"]


def test_the_per_trade_refusals_are_the_sizing_refusals_of_the_gate():
    assert PER_TRADE_REFUSALS == {"fx_conversion_unavailable", "min_lot_exceeds_risk", "margin_level_floor",
                                  "combined_exposure_cap", "daily_loss_limit_abs", "heat_cap",
                                  "pilot_stop_above_cap", "pilot_trade_risk_above_cap",
                                  "pilot_stop_projected"}
    assert not PER_TRADE_REFUSALS & set(RiskGate(LIMITS).account_reasons(
        _state(**{k: v for over, _ in FLAGS if "equity" not in over for k, v in over.items()}), NOW))


# ---------------------------------------------------------------------------------------------- the engine publishes
def _file(eng: Engine) -> dict[str, Any]:
    return json.loads(Path(eng.cfg.state_dir, f"engine_{eng.cfg.account_id}.json").read_text())


@pytest.mark.parametrize("over", [{}, {"in_rollover": True}, {"weekend": True, "owner_halt": True},
                                  {"spread_points": 999.0}])
def test_the_engine_publishes_the_gates_account_reasons_at_every_state_write(tmp_path, over):
    eng, _, _ = _engine(tmp_path)
    for k, v in over.items():
        setattr(eng.state, k, v)
    eng._write_state()
    assert _file(eng)["entry_blocks"] == eng.gate.account_reasons(eng.state)
    assert ("rollover" in _file(eng)["entry_blocks"]) == ("in_rollover" in over)


def test_the_heartbeat_lists_the_reasons_of_the_feed_it_publishes(tmp_path, frozen):
    """BACKLOG 59: a closed market's heartbeat publishes the host clock's tick age and market_closed; its entry_blocks
    are the gate's reasons on that feed, never the last account refresh's (which saw a fresh tick and an open market)."""
    eng, pb, host, _ = frozen(FRI_LAST)
    _closed_loop(eng, pb, host, 20 * 60)
    st = _file(eng)
    assert {"market_closed", "stale_data"} <= set(st["entry_blocks"])
    # the in-memory state is left as the last refresh saw it (a fresh tick), so stale_data came from the published feed
    assert eng.state.last_tick_age_s < 1.0 and "stale_data" not in eng.gate.account_reasons(eng.state)


def test_a_per_trade_refusal_is_published_per_agent_with_its_time(tmp_path):
    eng, _, dec = _run(tmp_path, _two(), RiskLimits(**{**BASE, "max_combined_lots": 0.0}))
    assert _actions(dec) == ["gate:combined_exposure_cap"] * 2
    refusals = _file(eng)["entry_refusals"]
    assert set(refusals) == {a.agent_id for a in eng.agents.values()}
    for d in dec:                                   # dated by the bar close it was decided at
        assert refusals[d["agent"]] == {"code": "combined_exposure_cap", "at": d["ts"]}


def test_an_allowed_check_clears_the_agents_refusal_and_account_reasons_are_never_recorded(tmp_path):
    eng, _, _ = _engine(tmp_path)
    eng._note_refusal("a", GateDecision(allowed=False, reasons=["margin_level_floor"]), NOW)
    eng._note_refusal("b", GateDecision(allowed=False, reasons=["heat_cap"]), NOW)
    eng._note_refusal("b", GateDecision(allowed=False, reasons=["weekend", "max_positions"]), NOW)   # kept
    eng._note_refusal("c", GateDecision(allowed=False, reasons=["negative_ev"]), NOW)                # not per trade
    eng._write_state()
    assert _file(eng)["entry_refusals"] == {"a": {"code": "margin_level_floor", "at": NOW.isoformat()},
                                            "b": {"code": "heat_cap", "at": NOW.isoformat()}}
    eng._note_refusal("a", GateDecision(allowed=True, lots=0.01), NOW + pd.Timedelta(hours=1))
    eng._write_state()
    assert set(_file(eng)["entry_refusals"]) == {"b"}


def test_the_risk_day_rollover_clears_every_agents_last_refusal(tmp_path):
    """Code review LOW-1 (558588e): an agent that stops producing intents is never allowed again, so its refusal would
    read "since <old time>" until a restart; the new risk day starts with none."""
    eng, _, _ = _engine(tmp_path)
    eng._roll_risk_period(NOW)                                       # today's reset
    eng._note_refusal("a", GateDecision(allowed=False, reasons=["margin_level_floor"]), NOW)
    eng._roll_risk_period(NOW + pd.Timedelta(hours=2))               # the same risk day: kept
    assert set(eng._entry_refusals) == {"a"}
    eng._roll_risk_period(NOW + pd.Timedelta(days=1))                # 00:00 UTC passed: cleared
    eng._write_state()
    assert _file(eng)["entry_refusals"] == {}


def test_a_refusal_at_approval_time_is_published_for_its_agent(tmp_path):
    pb = GbpPaper()
    eng, center = _approval_engine(tmp_path, pb)
    pb.fx = None                                                     # the terminal lost the conversion at the click
    center.decide("icm-demo-9-a", OWNER, True)
    agent = eng.decisions[-1]["agent"]
    refusals = _file(eng)["entry_refusals"]
    assert refusals == {agent: {"code": "fx_conversion_unavailable", "at": pb.last_tick("XAUUSD").ts_utc.isoformat()}}
    assert not pb.positions()


# ---------------------------------------------------------------------------------------------- reporting never costs
def test_a_failing_entry_blocks_never_stops_the_state_or_risk_write(tmp_path, monkeypatch):
    """Trading-safety LOW-1 (558588e): entry_blocks is reporting only. If listing the reasons raises, the state file,
    the risk state and the orders are still written, entry_blocks is left out, and the API shows it never OPEN."""
    from goldbot.api import entry_status as es
    eng, _, _ = _engine(tmp_path)

    def boom(*_a: Any, **_k: Any) -> list[str]:
        raise RuntimeError("bug")
    monkeypatch.setattr(eng.gate, "account_reasons", boom)
    risk = eng._risk_path()
    risk.unlink(missing_ok=True)
    eng._write_state()
    st = _file(eng)
    assert "entry_blocks" not in st and risk.exists()
    s = es.account_entry_status(st, now=pd.Timestamp(st["ts"], unit="s", tz="UTC"), owner_halt=None,
                                supervisor_reasons=None, drift_reasons=None, budget=None, limits=RiskLimits())
    assert s.state != "open" and [b.code for b in s.blocks] == ["entry_reasons_missing"]


def test_a_failing_refusal_note_never_changes_a_decision(tmp_path, monkeypatch):
    """Trading-safety LOW-1: noting a refusal is reporting only; if it raises, the bar-close decisions and an approval
    go on exactly as the gate decided."""
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")
    monkeypatch.setattr(Engine, "_record_refusal", boom)
    _, pb, dec = _run(tmp_path / "close", _two(), RiskLimits(**BASE))
    assert _actions(dec) == ["executed:auto", "executed:auto"] and len(pb.positions()) == 2
    gbp = GbpPaper()
    eng, center = _approval_engine(tmp_path / "click", gbp)
    center.decide("icm-demo-9-a", OWNER, True)
    assert len(gbp.positions()) == 1 and _file(eng)["entry_refusals"] == {}
