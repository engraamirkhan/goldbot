"""BACKLOG 36 (trading-safety review LOW-6): the daily £ projection pays the round-trip cost.

A full stop-out also pays the spread, slippage and commission (the engine's `_cost_atr`, from the nightly cost table,
else its configured fallback) and, when a broker rollover falls before the risk day ends, the paid swap for the
trade's side (the cost table's measured rate, else the settings prior; a credit never counts). The absolute daily
projection is therefore loss so far (+ open risk) + this trade's stop risk + its round-trip cost, all in the account
currency: cost_atr x ATR x lots x contract x fx, plus swap per oz x lots x contract x fx. It can only be stricter than
before; nothing else in the gate reads the new number, and exits never consult it.

Hand-worked example (GBP account, acct_per_usd 0.75, 100 oz contract, ATR $10, stop 1 ATR, 0.01 lot):
  stop risk    10.00 x 100 x 0.01 x 0.75                         = £7.50
  round trip   cost_atr 0.04 x 10.00 = $0.40/oz x 100 x 0.01 x 0.75 = £0.30
  swap         1 night at -$60/lot = $0.60/oz x 100 x 0.01 x 0.75   = £0.45   (x3 on the triple day: £1.35)
  projection with £41.75 lost today: before 41.75 + 7.50 = 49.25; now 41.75 + 7.50 + 0.30 + 0.45 = 50.00
"""
from typing import Any

import pandas as pd
import pytest

from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.costs import CostTable, SpreadStat
from goldbot.execution.paper import PaperBroker
from goldbot.labels.triple_barrier import SwapSpec
from goldbot.ops.cost_imputation import account_swap, paid_swap_per_oz
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.telegram.approvals import ApprovalCenter
from tests.test_engine_clock import WED, _intent, _tick

FX = 0.75
MON = pd.Timestamp("2025-03-03 08:00", tz="UTC")      # Monday; Europe/Athens is UTC+2, its rollover is 22:00 UTC
PRIOR = SwapSpec(long_usd_per_lot=-60.0, short_usd_per_lot=0.0, triple_weekday=2, server_tz="Europe/Athens")


def _state(lost: float, equity: float = 5_000.0, **kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=equity, balance_closed_hwm=equity + lost, day_start_equity=equity + lost,
                                week_start_equity=equity + lost, open_positions=0, margin_used=0.0,
                                last_tick_age_s=1.0, spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _gbp(swap: float = 0.60, cost_atr: float = 0.04, **kw: Any) -> Intent:
    """0.3% of £5,000 = £15 target; a $10 stop is £7.50 per 0.01 lot, so the size is exactly 0.01 lot."""
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=10.0,
                                cost_atr=cost_atr, multiplier=0.3, price=2_400.0, account_currency="GBP",
                                acct_per_usd=FX, swap_usd_per_oz=swap)
    base.update(kw)
    return Intent(**base)


def _gate(**kw: Any) -> RiskGate:
    return RiskGate(RiskLimits(daily_loss_limit_abs=50.0, **kw))


# ---------------------------------------------------------------------------------------------- the gate
def test_the_hand_worked_gbp_example_charges_75p_on_top_of_the_750_stop():
    d = _gate().check(_gbp(), _state(40.0))
    assert d.allowed and d.lots == 0.01
    assert d.risk_amount == pytest.approx(7.50)
    assert d.cost_amount == pytest.approx(0.30 + 0.45)          # spread+slippage+commission £0.30, one night swap £0.45


def test_the_projection_boundary_counts_the_cost_and_stays_inclusive():
    # 41.75 + 7.50 + 0.75 = 50.00: exactly at the limit is allowed (review LOW-3)
    assert _gate().check(_gbp(), _state(41.75)).allowed
    # 42.00 + 7.50 = 49.50 passed before; with the £0.75 cost it is 50.25 > 50: refused
    d = _gate().check(_gbp(), _state(42.00))
    assert not d.allowed and d.reasons == ["daily_loss_limit_abs"] and d.cost_amount == pytest.approx(0.75)
    assert _gate().check(_gbp(swap=0.0, cost_atr=0.0), _state(42.00)).allowed        # the old figure, for contrast


def test_the_triple_swap_night_costs_three_times_the_swap():
    d = _gate().check(_gbp(swap=1.80), _state(41.75))            # 41.75 + 7.50 + 0.30 + 1.35 = 50.90
    assert not d.allowed and d.reasons == ["daily_loss_limit_abs"] and d.cost_amount == pytest.approx(1.65)


def test_a_usd_account_pays_the_same_cost_in_dollars():
    d = _gate().check(_gbp(account_currency="USD", acct_per_usd=None, multiplier=0.4), _state(30.0))
    assert d.allowed and d.lots == 0.01 and d.risk_amount == pytest.approx(10.0)
    assert d.cost_amount == pytest.approx(0.40 + 0.60)


def test_the_cost_scales_with_the_lots():
    d = _gate().check(_gbp(multiplier=0.6), _state(0.0))         # £15 target: 0.02 lot
    assert d.lots == 0.02 and d.cost_amount == pytest.approx(1.50)


def test_open_risk_and_the_cost_both_count_when_the_projection_counts_open_risk():
    st = _state(30.0, open_risk=11.75)                           # 30 + 11.75 + 7.50 + 0.75 = 50.00
    assert _gate(project_open_risk=True).check(_gbp(), st).allowed
    st = _state(30.25, open_risk=11.75)
    assert _gate(project_open_risk=True).check(_gbp(), st).reasons == ["daily_loss_limit_abs"]


def test_a_negative_cost_or_a_swap_credit_never_loosens_the_projection():
    # a credit (or a nonsense negative cost) counts as zero: the projection is never below loss + stop risk
    d = _gate().check(_gbp(swap=-5.0, cost_atr=-1.0), _state(42.50))   # 42.50 + 7.50 = 50.00
    assert d.allowed and d.cost_amount == 0.0
    assert _gate().check(_gbp(swap=-5.0, cost_atr=-1.0), _state(42.51)).reasons == ["daily_loss_limit_abs"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_swap_refuses_the_entry(bad):
    d = _gate().check(_gbp(swap=bad), _state(0.0))
    assert not d.allowed and "invalid:swap_usd_per_oz" in d.reasons


def test_without_an_absolute_limit_the_cost_changes_no_decision():
    d = RiskGate(RiskLimits()).check(_gbp(swap=100.0), _state(40.0))
    assert d.allowed and d.lots == 0.01 and d.cost_amount == pytest.approx(0.30 + 75.0)   # reported, not gated


# ---------------------------------------------------------------------------------------------- the swap (ADR 0017 helper)
@pytest.mark.parametrize(("now", "side", "per_oz"), [
    (MON, 1, 0.60),                                             # Monday's rollover (22:00 UTC) is before 24:00 UTC
    (MON + pd.Timedelta(hours=14, minutes=30), 1, 0.0),         # 22:30 UTC: past it, the next is tomorrow's risk day
    (MON + pd.Timedelta(days=2), 1, 1.80),                      # Wednesday: the triple night
    (MON + pd.Timedelta(days=4), 1, 0.60),                      # Friday's rollover pays one weekday night
    (MON, -1, 0.0),                                             # the short side pays nothing at the prior
])
def test_the_swap_before_the_risk_day_ends_reuses_the_closed_trade_rule(now, side, per_oz):
    end = now.normalize() + pd.Timedelta(days=1)
    assert paid_swap_per_oz(PRIOR, side, now, end) == pytest.approx(per_oz)


def test_a_swap_credit_never_counts_and_an_unknown_side_pays_the_larger():
    credit = SwapSpec(long_usd_per_lot=-45.0, short_usd_per_lot=10.0, server_tz="Europe/Athens")
    end = MON.normalize() + pd.Timedelta(days=1)
    assert paid_swap_per_oz(credit, -1, MON, end) == 0.0
    assert paid_swap_per_oz(credit, None, MON, end) == pytest.approx(0.45)


def _table(**kw: Any) -> CostTable:
    """London: spread $0.25 + 2 x $0.05 slippage + 2 x $3 / 100 commission = $0.41 per oz round trip."""
    base: dict[str, Any] = dict(account_id="icm-demo", built_utc=MON, spread={"london": SpreadStat(median=0.25, p90=0.4,
                                n=100)}, slippage={}, commission_per_lot_side_usd=3.0, slippage_prior_usd=0.05)
    base.update(kw)
    return CostTable(**base)


def test_the_account_swap_is_the_measured_table_else_the_prior():
    assert account_swap(None, PRIOR) == PRIOR
    assert account_swap(_table(), PRIOR) == PRIOR                                 # the table has no swap measured
    measured = account_swap(_table(swap_long_usd_per_lot=-45.0, swap_short_usd_per_lot=10.0), PRIOR)
    assert (measured.long_usd_per_lot, measured.short_usd_per_lot, measured.server_tz) == (-45.0, 10.0, "Europe/Athens")


# ---------------------------------------------------------------------------------------------- the engine
def _eng(tmp_path: Any, **cfg: Any) -> Engine:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(MON))
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                               server_tz="Europe/Athens", **cfg), pb, [], {}, ApprovalCenter({111}))


def test_the_engine_swap_uses_the_prior_until_the_cost_table_measures_one(tmp_path):
    eng = _eng(tmp_path)
    assert eng._swap_usd_per_oz(1, MON) == pytest.approx(0.60)                      # settings default prior
    assert eng._swap_usd_per_oz(-1, MON) == 0.0
    _table(swap_long_usd_per_lot=-45.0, swap_short_usd_per_lot=10.0).save(tmp_path / "costs_icm-demo.json")
    assert eng._swap_usd_per_oz(1, MON) == pytest.approx(0.45)                      # measured
    assert eng._swap_usd_per_oz(-1, MON) == 0.0                                     # a credit never counts
    assert eng._cost_atr(10.0, MON) == pytest.approx(0.041)                         # one table for both


def test_the_engine_swap_prior_comes_from_its_config_on_its_own_server_clock(tmp_path):
    eng = _eng(tmp_path, swap_prior=PRIOR.model_copy(update={"long_usd_per_lot": -30.0, "server_tz": "UTC"}))
    assert eng._swap_usd_per_oz(1, MON) == pytest.approx(0.30)
    # Athens (the engine's clock) has rolled at 22:00 UTC; on UTC the next rollover would be at 24:00, outside the day
    assert eng._swap_usd_per_oz(1, MON + pd.Timedelta(hours=14, minutes=30)) == 0.0


def test_an_unreadable_cost_table_keeps_the_prior_swap(tmp_path):
    eng = _eng(tmp_path)
    (tmp_path / "costs_icm-demo.json").write_text("{not json")
    assert eng._swap_usd_per_oz(1, MON) == pytest.approx(0.60)


def test_the_service_passes_the_settings_swap_prior(tmp_path):
    from goldbot.config import load_settings
    from goldbot.execution.costs import settings_swap
    s = settings_swap(load_settings())
    assert EngineConfig.model_fields["swap_prior"].default_factory is not None
    d = EngineConfig(account_id="a", broker_name="b").swap_prior
    assert (d.long_usd_per_lot, d.short_usd_per_lot, d.triple_weekday) == \
        (s.long_usd_per_lot, s.short_usd_per_lot, s.triple_weekday)               # the default is the shipped prior
    import inspect

    from goldbot.ops.services import engine as service
    assert "swap_prior=settings_swap(settings)" in inspect.getsource(service)


def _record_swap(monkeypatch: Any) -> list[tuple[int, pd.Timestamp]]:
    """Engine._swap_usd_per_oz replaced by a recorder that answers $0.123/oz, so a test sees what the intent carries."""
    calls: list[tuple[int, pd.Timestamp]] = []

    def fake(self: Engine, side: int, now: pd.Timestamp) -> float:
        calls.append((side, now))
        return 0.123

    monkeypatch.setattr(Engine, "_swap_usd_per_oz", fake)
    return calls


def test_the_decision_intent_carries_the_swap_at_the_bar_close(tmp_path, monkeypatch):
    from goldbot.engine.runner import RiskGate as EngineGate
    from goldbot.features.registry import feature_version
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
    from tests.test_engine_safety import _decide_once
    seen: list[Intent] = []
    real = EngineGate.check

    def spy(self: Any, intent: Intent, *a: Any, **k: Any) -> Any:
        seen.append(intent)
        return real(self, intent, *a, **k)

    monkeypatch.setattr(EngineGate, "check", spy)
    calls = _record_swap(monkeypatch)
    _decide_once(tmp_path, ConstantModel(p=0.9, feature_version=feature_version(DEFAULT_FEATURE_NAMES)))
    assert seen and seen[-1].swap_usd_per_oz == 0.123
    assert calls[-1][0] == seen[-1].side


def test_the_approval_recheck_prices_the_swap_at_the_click(tmp_path, monkeypatch):
    from tests.test_approved_card_cap import _approve, _card
    from tests.test_engine_clock import _engine
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    intent = _intent().model_copy(update={**eng._sizing_terms(1, 2400.2, 4.0), "swap_usd_per_oz": 0.0})
    prop = _card(eng, intent)
    seen: list[Intent] = []
    real = eng.gate.check

    def spy(i: Intent, *a: Any, **k: Any) -> Any:
        seen.append(i)
        return real(i, *a, **k)

    eng.gate.check = spy                                                  # type: ignore[method-assign,assignment]
    calls = _record_swap(monkeypatch)
    _approve(eng, prop)
    [i] = seen
    assert i.swap_usd_per_oz == 0.123                                # re-priced, not the proposal's 0.0
    assert calls == [(1, pb.last_tick("XAUUSD").ts_utc)]            # at the click's quote time
