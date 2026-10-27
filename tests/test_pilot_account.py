"""The live pilot's configuration and engine side (ADR 0055, icm-live only): the `pilot:` block is accepted on a live
account with owner-confirmed entries and a daily limit of at most £6.25 only, its fields are bounded by the ADR's
terms, engine_limits turns its four changed rails into that account's limits and nobody else's, the bridge's notional
ceiling follows its leverage cap, and the engine counts the pilot's realised loss and filled entries from the broker's
deals, keeps them and the sticky stop in core.db, and refuses entries when the record cannot be read. icm-live itself
stays disabled and its login null."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.engine import pilot as engine_pilot
from goldbot.execution.bridge import MAX_NOTIONAL_EQUITY_MULT, GuardedBroker
from goldbot.ops import accounts as acc_mod
from goldbot.ops.services.engine import engine_limits
from goldbot.risk.gate import AccountState, RiskLimits
from goldbot.risk.pilot import PilotRules, PilotState

NOW = pd.Timestamp("2026-10-30 10:00", tz="UTC")
START = pd.Timestamp("2026-10-29", tz="UTC")


def _rules(**kw: Any) -> PilotRules:
    base: dict[str, Any] = dict(enabled=True, adr=55, pilot_id="icm-live-adr55", start_utc=START, leverage_cap=500,
                                max_risk_frac=0.125, daily_cap_frac=0.25, drawdown_stages=False)
    return PilotRules(**{**base, **kw})


def _account(**kw: Any) -> acc_mod.Account:
    base: dict[str, Any] = dict(account_id="icm-live", broker="icm", mode="live", server="ICMarketsSC-MT5-3",
                                login=None, terminal_path="t", server_tz="NY+7", symbol="XAUUSD", magic_base=1,
                                enabled=False, daily_loss_limit_abs=6.25, currency="GBP", pilot=_rules())
    return acc_mod.Account(**{**base, **kw})


# ---------------------------------------------------------------- config
def test_the_pilot_is_accepted_on_a_live_account_with_confirmed_entries_and_a_daily_limit():
    a = _account()
    assert a.pilot.enabled and a.approval_mode == "propose"


@pytest.mark.parametrize("kw, match", [
    (dict(mode="demo"), "live accounts only"),
    (dict(daily_loss_limit_abs=None), "daily_loss_limit_abs"),
    (dict(daily_loss_limit_abs=6.5), "daily_loss_limit_abs"),
    (dict(dynamic_limits=True), "dynamic_limits"),
])
def test_a_pilot_outside_its_rails_fails_at_load(kw, match):
    with pytest.raises(ValidationError, match=match):
        _account(**kw)


@pytest.mark.parametrize("kw", [dict(stop_loss_abs=25.5), dict(max_risk_per_trade_abs=3.2), dict(lot=0.02),
                                dict(max_stop_usd=4.5), dict(max_entries=31), dict(max_positions=2),
                                dict(leverage_cap=600), dict(max_risk_frac=0.2), dict(daily_cap_frac=0.3),
                                dict(pilot_id=""), dict(start_utc=None)])
def test_the_pilot_rails_cannot_be_widened_past_the_adr_terms_by_config(kw):
    with pytest.raises(ValidationError):
        _rules(**kw)


def test_a_disabled_pilot_block_is_inert_and_not_judged():
    assert not _account(mode="demo", daily_loss_limit_abs=None, pilot=PilotRules()).pilot.enabled


def test_the_committed_icm_live_is_a_disabled_pilot_with_the_adr_0055_terms_and_no_login():
    raw = yaml.safe_load(acc_mod.ACCOUNTS_FILE.read_text())["accounts"]
    live = raw["icm-live"]
    assert live["enabled"] is False and live["login"] is None and live["mode"] == "live"
    accs = acc_mod.load_accounts(logins=False)
    a = accs["icm-live"]
    assert a.server == "ICMarketsSC-MT5-3" and a.currency == "GBP" and a.login is None and not a.enabled
    p = a.pilot
    assert p.enabled and p.adr == 55 and p.lot == 0.01 and p.max_stop_usd == 4.0
    assert p.max_risk_per_trade_abs == 3.12 and p.stop_loss_abs == 25.0 and p.max_entries == 30
    assert p.timeframes == ("15m",) and p.max_positions == 1 and p.leverage_cap == 500
    assert not p.drawdown_stages and p.start_utc is not None and p.start_utc < pd.Timestamp("2026-10-30", tz="UTC")
    assert a.daily_loss_limit_abs == 6.25 and a.approval_mode == "propose" and not a.dynamic_limits
    # the same broker's measured server clock (icm-demo and icm-probe, 2026-10-26 gap week)
    assert a.server_tz == accs["icm-demo"].server_tz == accs["icm-probe"].server_tz == "NY+7"
    assert not any(x.pilot.enabled for x in accs.values() if x.account_id != "icm-live")


def test_a_terminal_reporting_another_currency_than_the_declared_one_is_refused():
    a = _account(login=1)
    info = SimpleNamespace(login=1, server="ICMarketsSC-MT5-3", trade_mode="real", margin_mode="hedging",
                           currency="GBP")
    assert acc_mod.verify_terminal_account(a, info) is None
    usd = SimpleNamespace(**{**vars(info), "currency": "USD"})
    assert "currency is USD" in (acc_mod.verify_terminal_account(a, usd) or "")
    assert acc_mod.verify_terminal_account(_account(login=1, currency=None), usd) is None


# ---------------------------------------------------------------- engine_limits
def test_engine_limits_apply_the_four_changed_rails_to_the_pilot_account_only():
    s = load_settings()
    on = engine_limits(s, _account())
    assert on.pilot is not None and on.pilot.pilot_id == "icm-live-adr55"
    assert on.max_positions == 1 and on.daily_loss_limit_abs == 6.25 and on.leverage_cap == 500
    assert on.max_risk_per_trade == 0.125 and on.daily_cap == 0.25 and on.dd_stage2 == 1.0 and on.dd_stage1 == 1.0
    assert engine_limits(s, _account(), paper=True).pilot is None
    plain = RiskLimits.from_settings(s.risk, tiny_live=False)
    demo = engine_limits(s, _account(mode="demo", daily_loss_limit_abs=220.0, currency=None, pilot=PilotRules()))
    assert demo.pilot is None
    for f in ("max_positions", "max_risk_per_trade", "daily_cap", "weekly_cap", "leverage_cap", "dd_stage1",
              "dd_stage2", "risk_per_trade", "min_lot_risk_cap"):
        assert getattr(demo, f) == getattr(plain, f), f


def test_the_limits_policy_keeps_the_pilot_rails_on_a_live_account():
    from goldbot.ops.services.engine import engine_limits_policy
    pol = engine_limits_policy(load_settings(), _account())
    assert pol.defaults.pilot is not None and pol.defaults.leverage_cap == 500


# ---------------------------------------------------------------- the bridge's notional ceiling
def test_the_bridge_notional_ceiling_follows_the_pilot_leverage_cap_and_stays_6x_elsewhere():
    info: Any = SimpleNamespace(leverage=5000, equity=50.0)
    low: Any = SimpleNamespace(leverage=100)
    unknown: Any = SimpleNamespace(leverage=0)
    assert GuardedBroker(object(), magic_base=1).notional_mult(info) == MAX_NOTIONAL_EQUITY_MULT
    pilot = GuardedBroker(object(), magic_base=1, leverage_cap=500.0)
    assert pilot.notional_mult(info) == pytest.approx(150.0)              # 0.30 x 500: £7,500 at £50
    assert pilot.notional_mult(low) == pytest.approx(30.0)                # the terminal's lower leverage
    assert pilot.notional_mult(unknown) == pytest.approx(150.0)
    notional = 0.01 * 100 * 4014.84 * 0.7508                                # £3,014 for one 0.01 lot
    assert notional <= pilot.notional_mult(info) * 25.0                     # fits down to £25
    assert notional > MAX_NOTIONAL_EQUITY_MULT * 50.0                       # the standing 6x refuses it


def test_the_bridge_service_passes_the_pilot_leverage_cap_for_the_pilot_account_only():
    import inspect

    from goldbot.ops.services import bridge as svc
    assert "leverage_cap=acc.pilot.leverage_cap if acc.pilot.enabled and acc.is_live else None" in \
        inspect.getsource(svc)


# ---------------------------------------------------------------- the engine's pilot record
def _deals(rows: list[tuple[str, int, int, float]]) -> pd.DataFrame:
    """(ts, type, entry, profit) rows as MT5's deal history (type 0 buy, 1 sell, 2 balance; entry 0 in, 1 out)."""
    return pd.DataFrame([dict(ticket=i, ts_utc=pd.Timestamp(ts, tz="UTC"), type=t, entry=e, position_id=i,
                              volume=0.01, price=4000.0, magic=1, profit=p, commission=-0.05 if t < 2 else 0.0,
                              swap=0.0, fee=0.0) for i, (ts, t, e, p) in enumerate(rows)])


class _Broker:
    def __init__(self, deals: pd.DataFrame | None = None, fail: bool = False):
        self.deals, self.fail, self.reads = deals, fail, 0

    def deals_since(self, since: pd.Timestamp) -> pd.DataFrame:
        self.reads += 1
        if self.fail:
            raise ConnectionError("bridge down")
        return self.deals if self.deals is not None else _deals([])


class _Eng:
    def __init__(self, tmp_path, broker: _Broker, rules: PilotRules | None = None):
        self.cfg = SimpleNamespace(state_dir=str(tmp_path), account_id="icm-live")
        self.gate = SimpleNamespace(limits=RiskLimits(pilot=rules if rules is not None else _rules()))
        self.broker = broker
        self.state = AccountState(equity=50.0, balance_closed_hwm=50.0, day_start_equity=50.0,
                                  week_start_equity=50.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                  spread_points=20.0)
        self.decisions: list[dict[str, Any]] = []


def _acc(balance: float = 50.0, leverage: int = 5000) -> SimpleNamespace:
    return SimpleNamespace(balance=balance, equity=balance, leverage=leverage)


LOSSES = [("2026-10-28 09:00", 2, 0, 50.0),                     # the deposit, before the pilot: never counted
          ("2026-10-30 09:00", 0, 0, 0.0), ("2026-10-30 09:30", 1, 1, -3.0),
          ("2026-10-30 10:00", 1, 0, 0.0), ("2026-10-30 10:20", 0, 1, 1.0),
          ("2026-10-30 11:00", 2, 0, -5.0)]                      # a withdrawal: never P&L


def test_the_pilot_counts_realised_loss_and_filled_entries_from_the_broker_deals_since_its_start(tmp_path):
    eng = _Eng(tmp_path, _Broker(_deals(LOSSES)))
    engine_pilot.refresh(eng, _acc(), NOW)
    ps = eng.state.pilot
    assert ps is not None and ps.entries == 2
    assert ps.realised_loss == pytest.approx(3.0 - 1.0 + 4 * 0.05)
    assert eng.state.broker_leverage == 5000.0


def test_the_pilot_record_survives_a_restart_in_core_db(tmp_path):
    eng = _Eng(tmp_path, _Broker(_deals(LOSSES)))
    engine_pilot.refresh(eng, _acc(), NOW)
    again = _Eng(tmp_path, _Broker(fail=True))                     # a restart whose first deal read fails
    engine_pilot.refresh(again, _acc(), NOW)
    assert again.state.pilot is not None and not again.state.pilot.known       # unknown: entries refused
    row = engine_pilot.PilotStore(tmp_path).load("icm-live")
    assert row is not None and row.entries == 2


def test_the_stop_after_25_gbp_realised_is_recorded_once_kept_in_core_db_and_survives_a_restart(tmp_path):
    rows = [("2026-10-30 09:00", 0, 0, 0.0), ("2026-10-30 09:30", 1, 1, -25.0)]
    eng = _Eng(tmp_path, _Broker(_deals(rows)))
    engine_pilot.refresh(eng, _acc(25.0), NOW)
    ps = eng.state.pilot
    assert ps is not None and ps.stopped_utc == NOW and ps.stop_reason == "stop_loss"
    assert [d["action"] for d in eng.decisions] == ["pilot_stop"]
    engine_pilot.refresh(eng, _acc(26.0), NOW + pd.Timedelta(minutes=5))
    assert len(eng.decisions) == 1
    fresh = _Eng(tmp_path, _Broker(_deals([])))                    # the history now shows nothing: still stopped
    engine_pilot.refresh(fresh, _acc(), NOW + pd.Timedelta(hours=1))
    assert fresh.state.pilot is not None and fresh.state.pilot.stopped_utc == NOW


def test_the_30th_filled_entry_stops_the_pilot(tmp_path):
    rows = [(f"2026-10-30 09:{i:02d}", 0, 0, 0.0) for i in range(30)]
    eng = _Eng(tmp_path, _Broker(_deals(rows)))
    engine_pilot.refresh(eng, _acc(), NOW)
    assert eng.state.pilot is not None and eng.state.pilot.stop_reason == "max_entries"


def test_an_unreadable_core_db_leaves_the_record_unknown(tmp_path, monkeypatch):
    eng = _Eng(tmp_path, _Broker())

    def boom(self, account_id):
        raise OSError("disk")
    monkeypatch.setattr(engine_pilot.PilotStore, "load", boom)
    engine_pilot.refresh(eng, _acc(), NOW)
    assert eng.state.pilot is None


def test_the_deal_history_is_read_again_when_the_balance_or_the_positions_change_or_a_minute_passed(tmp_path):
    b = _Broker()
    eng = _Eng(tmp_path, b)
    engine_pilot.refresh(eng, _acc(), NOW)
    engine_pilot.refresh(eng, _acc(), NOW + pd.Timedelta(seconds=10))
    assert b.reads == 1
    eng.state.open_positions = 1
    engine_pilot.refresh(eng, _acc(), NOW + pd.Timedelta(seconds=20))
    engine_pilot.refresh(eng, _acc(49.0), NOW + pd.Timedelta(seconds=30))
    engine_pilot.refresh(eng, _acc(49.0), NOW + pd.Timedelta(seconds=100))
    assert b.reads == 4


def test_a_new_pilot_id_starts_a_new_record_and_no_pilot_clears_it(tmp_path):
    engine_pilot.PilotStore(tmp_path).save(PilotState(account_id="icm-live", pilot_id="old", realised_loss=30.0,
                                                      entries=3, stopped_utc=NOW))
    eng = _Eng(tmp_path, _Broker())
    engine_pilot.refresh(eng, _acc(), NOW)
    assert eng.state.pilot is not None and eng.state.pilot.pilot_id == "icm-live-adr55"
    assert eng.state.pilot.stopped_utc is None
    off = _Eng(tmp_path, _Broker())
    off.gate.limits = RiskLimits()
    off.state.pilot = eng.state.pilot
    engine_pilot.refresh(off, _acc(), NOW)
    assert off.state.pilot is None and off.state.broker_leverage is None
