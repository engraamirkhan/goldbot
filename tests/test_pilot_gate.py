"""The live pilot in the RiskGate (ADR 0055, option (a), icm-live only): "0.01 exactly ... there is no sizing-up";
"Stop distance at most $4.00 from entry. A longer stop is refused, never shrunk"; "Risk per trade at most £3.12 at the
stop, including the round trip"; "£6.25 (two full stops), absolute"; "after £25 cumulative realised loss or 30 filled
entries ... entries halt for good"; "at most 1" open position; "15m families only"; `leverage_cap` 500, the terminal's
own leverage when lower. Trading-safety condition (HIGH): the £25 stop is checked BEFORE each entry with the open
risk and this trade. Every other account sizes and trades exactly as before."""
from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, Stage
from goldbot.risk.pilot import PilotRules, PilotState

NOW = pd.Timestamp("2026-10-30 10:00", tz="UTC")
GBP_PER_USD = 0.7508
PRICE = 4014.84
RULES = PilotRules(enabled=True, adr=55, pilot_id="icm-live-adr55", start_utc=pd.Timestamp("2026-10-29", tz="UTC"),
                   leverage_cap=500, max_risk_frac=0.125, daily_cap_frac=0.25, weekly_cap_frac=0.25,
                   drawdown_stages=False, weekend_flat=True)
STOP_GBP = 0.01 * 100 * 3.5 * GBP_PER_USD            # a $3.50 stop at 0.01 lot: £2.63
TRIP_GBP = 0.05 * 3.5 * 0.01 * 100 * GBP_PER_USD     # its round trip: £0.13


def _limits(pilot: PilotRules | None = RULES, **kw: Any) -> RiskLimits:
    """icm-live's limits as engine_limits builds them from config/accounts.yaml (tiny-live risk per trade underneath)."""
    base: dict[str, Any] = dict(risk_per_trade=0.001, min_lot_risk_cap=None, daily_loss_limit_abs=6.25)
    if pilot is not None:
        base.update(pilot=pilot, max_positions=1, max_risk_per_trade=pilot.max_risk_frac,
                    daily_cap=pilot.daily_cap_frac, weekly_cap=pilot.weekly_cap_frac, leverage_cap=pilot.leverage_cap,
                    dd_stage1=1.0, dd_stage2=1.0, dd_stage1_clear=1.0)
    return RiskLimits(**{**base, **kw})


def _state(loss: float | None = 0.0, entries: int | None = 0, *, equity: float = 50.0, pilot_id: str = "icm-live-adr55",
           stopped: bool = False, **kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=50.0, day_start_equity=equity,
                                week_start_equity=equity, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=20.0, open_risk=0.0, broker_leverage=5000.0,
                                pilot=PilotState(account_id="icm-live", pilot_id=pilot_id, realised_loss=loss,
                                                 entries=entries, stopped_utc=NOW if stopped else None))
    return AccountState(**{**base, **kw})


def _intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="scalp_15m", side=1, p=0.55, target_atr=2.0, stop_atr=1.0, atr_usd=3.5,
                                cost_atr=0.05, multiplier=1.0, price=PRICE, timeframe="15m", family="box_break",
                                account_currency="GBP", acct_per_usd=GBP_PER_USD)
    return Intent(**{**base, **kw})


def _check(intent: Intent | None = None, limits: RiskLimits | None = None, st: AccountState | None = None):
    return RiskGate(limits or _limits()).check(intent or _intent(), st or _state(), NOW)


# ---------------------------------------------------------------- sizing
def test_a_pilot_entry_at_50_gbp_is_exactly_0_01_lot_when_stop_and_round_trip_fit_3_12_gbp():
    d = _check()
    assert d.allowed, d
    assert d.lots == pytest.approx(0.01) and "pilot_min_lot" in d.reasons
    assert d.risk_amount == pytest.approx(STOP_GBP)
    assert d.margin_needed == pytest.approx(0.01 * 100 * PRICE * GBP_PER_USD / 500)       # £6.03 at 1:500


def test_the_same_entry_on_an_account_without_the_pilot_is_refused_as_before():
    d = _check(limits=_limits(pilot=None), st=_state(pilot=None, broker_leverage=None))
    assert not d.allowed and d.reasons == ["min_lot_exceeds_risk"]


def test_a_stop_wider_than_4_usd_is_refused_never_shrunk():
    d = _check(_intent(atr_usd=6.6, stop_atr=1.5))                 # the 15m natural stop of $9.90
    assert not d.allowed and d.reasons == ["pilot_stop_above_cap"] and d.lots == 0.0
    assert _check(_intent(atr_usd=4.0, cost_atr=0.03)).allowed     # exactly $4.00 is allowed


def test_the_round_trip_counts_toward_the_3_12_gbp_per_trade_cap():
    # a $4 stop costs £3.00; a round trip above £0.12 takes it over £3.12
    d = _check(_intent(atr_usd=4.0, cost_atr=0.05))                # trip 0.2 $/oz = £0.15
    assert not d.allowed and d.reasons == ["pilot_trade_risk_above_cap"]
    assert _check(_intent(atr_usd=4.0, cost_atr=0.03)).allowed     # trip 0.12 $/oz = £0.09


def test_a_size_down_stage_halves_the_pilot_per_trade_cap():
    d = _check(st=_state(combined_size_down=True))                 # £2.76 > £1.56
    assert not d.allowed and d.reasons == ["pilot_trade_risk_above_cap"]


def test_the_pilot_never_sends_more_than_its_lot_whatever_the_multiplier_or_equity():
    d = _check(_intent(multiplier=1.5), limits=_limits(risk_per_trade=0.01), st=_state(equity=50_000.0))
    assert d.allowed and d.lots == pytest.approx(0.01)


def test_the_pilot_sends_its_own_lot_not_a_smaller_broker_minimum():
    d = _check(_intent(volume_min=0.001, volume_step=0.001))
    assert d.allowed and d.lots == pytest.approx(0.01)
    assert "pilot_lot_unavailable" in _check(_intent(volume_min=0.001, volume_step=0.003)).reasons   # not a step
    assert "pilot_lot_unavailable" in _check(_intent(volume_max=0.005, volume_min=0.001,
                                                     volume_step=0.001)).reasons


def test_a_broker_whose_smallest_lot_is_above_the_pilot_lot_refuses_the_entry():
    d = _check(_intent(volume_min=0.1, volume_step=0.1))
    assert not d.allowed and "pilot_lot_unavailable" in d.reasons


# ---------------------------------------------------------------- leverage
def test_the_gate_uses_leverage_500_not_the_brokers_5000_and_not_the_standing_1_to_20():
    assert RiskGate(_limits()).leverage(_state()) == 500.0
    at20 = _check(limits=_limits(leverage_cap=20.0))               # £150.7 margin on £50: refused as before
    assert not at20.allowed and at20.reasons == ["margin_level_floor"]


def test_a_terminal_leverage_below_the_cap_tightens_the_margin_figure():
    d = _check(st=_state(broker_leverage=100.0))                   # £30.1 margin on £50: level 166% < 300%
    assert not d.allowed and d.reasons == ["margin_level_floor"]
    assert RiskGate(_limits()).leverage(_state(broker_leverage=None)) == 500.0


# ---------------------------------------------------------------- scope
def test_the_pilot_trades_15m_signals_only():
    assert "pilot_timeframe" in _check(_intent(timeframe="1h")).reasons
    assert "pilot_timeframe" in _check(_intent(timeframe="")).reasons


def test_the_pilot_refuses_probes_and_exploration_entries():
    assert "pilot_signal_only" in _check(_intent(purpose="exploration", arm_id="a")).reasons
    assert "pilot_signal_only" in _check(_intent(learning_probe=True)).reasons


def test_one_position_at_a_time_on_the_pilot_account():
    d = _check(st=_state(open_positions=1))
    assert not d.allowed and "max_positions" in d.reasons


# ---------------------------------------------------------------- daily and weekly
def test_the_6_25_gbp_daily_limit_refuses_an_entry_that_could_end_the_day_past_it():
    st = _state(equity=46.0, day_start_equity=50.0, week_start_equity=46.0)    # £4 lost today + £2.76 > £6.25
    d = _check(st=st)
    assert not d.allowed and d.reasons == ["daily_loss_limit_abs"]


def test_the_pilot_weekly_cap_is_25_percent_and_the_standing_5_percent_elsewhere():
    """ADR 0055 amendment: `pilot.weekly_cap_frac` 0.25 (£12.50 at £50) replaces the 5% weekly cap on the pilot only."""
    st = _state(equity=47.0, day_start_equity=47.0, week_start_equity=50.0)     # 6% down this week
    assert _check(st=st).allowed
    d = _check(st=_state(equity=37.5, day_start_equity=37.5, week_start_equity=50.0))   # 25% down
    assert not d.allowed and "weekly_cap" in d.reasons
    plain = RiskGate(_limits(pilot=None)).account_reasons(_state(equity=47.0, day_start_equity=47.0,
                                                                 week_start_equity=50.0))
    assert "weekly_cap" in plain


def test_the_drawdown_stages_do_not_run_on_the_pilot_account():
    st = _state(equity=30.0, balance_closed_hwm=50.0)              # 40% below the mark: no 12% kill
    d = _check(st=st)
    assert d.allowed and st.stage == Stage.NORMAL


# ---------------------------------------------------------------- the pilot stop
def test_no_entry_once_the_pilot_realised_loss_reaches_25_gbp():
    d = _check(st=_state(loss=25.0, equity=25.0))
    assert not d.allowed and "pilot_stop" in d.reasons
    assert "pilot_stop" in RiskGate(_limits()).account_reasons(_state(loss=25.0))       # published as entry_blocks


def test_no_entry_after_30_filled_entries():
    d = _check(st=_state(entries=30))
    assert not d.allowed and "pilot_trade_limit" in d.reasons
    assert _check(st=_state(entries=29)).allowed


def test_the_pilot_stop_is_sticky_whatever_the_counts_say():
    d = _check(st=_state(loss=0.0, entries=0, stopped=True))
    assert not d.allowed and "pilot_stop" in d.reasons


def test_the_25_gbp_stop_is_checked_before_each_entry_with_open_risk_and_this_trade():
    st = _state(loss=22.5, equity=27.5)                            # £22.50 + £2.63 + £0.13 > £25
    d = _check(st=st)
    assert not d.allowed and d.reasons == ["pilot_stop_projected"]
    assert _check(st=_state(loss=21.0, equity=29.0)).allowed       # £23.76
    d = _check(st=_state(loss=20.0, equity=30.0, open_risk=3.0))   # £20 + £3 open + £2.76 > £25
    assert not d.allowed and d.reasons == ["pilot_stop_projected"]


def test_an_unreadable_pilot_record_refuses_every_entry():
    for st in (_state(loss=None), _state(entries=None), _state(pilot=None), _state(pilot_id="another-pilot"),
               _state(loss=float("nan"))):
        d = _check(st=st)
        assert not d.allowed and "pilot_record_unknown" in d.reasons, st.pilot


def test_an_unknown_open_risk_refuses_a_pilot_entry():
    d = _check(st=_state(open_risk=None))
    assert not d.allowed and "open_risk_unknown" in d.reasons


def test_the_pilot_record_is_ignored_on_an_account_without_the_pilot():
    d = _check(_intent(atr_usd=6.6, stop_atr=1.5), limits=_limits(pilot=None, risk_per_trade=0.01,
                                                                   daily_loss_limit_abs=None),
               st=_state(loss=500.0, stopped=True, equity=1000.0, broker_leverage=None))
    assert d.allowed and "pilot_min_lot" not in d.reasons
