"""The spread against the stop (demo non-gold fill audit 2026-11-03): an entry whose spread is more than
`risk.max_spread_stop_frac` (0.3) of its stop distance is refused `spread_over_stop` on every entry path, exploration
included, in the instrument's own points. S0-SUGAR traded a 0.00055 stop with a 0.00055 spread (12/12 stopped);
BUND10Y's spread was 0.52 R, UST10Y's 0.89 R. The absolute `max_spread_points` cap could not see it.

The cost counts the spread and the commission (no table: the configured fallback alone said 0.10 R, BUND's cost read
0.00), and the planned risk the gate sizes and caps on includes the round-trip commission (FX min-lot scalps: £0.06
commission on £0.08-0.25 planned risk)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import RiskSettings, load_settings
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.engine.books import BookSpec
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.costs import CostTable, SlippageStat, SpreadStat
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import load_instruments
from goldbot.risk.gate import AccountState, Intent, RiskGate, RiskLimits, intent_terms

NOW = pd.Timestamp("2026-11-03 10:00", tz="UTC")
ARM = "S0-SUGAR"


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                                week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=20.0)
    return AccountState(**{**base, **kw})


def _gold(**kw: Any) -> Intent:
    """At 10,000 equity, 0.5% risk and m = 1 a $5 stop sizes 0.10 lot = 50 at the stop (100 oz)."""
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=5.0, cost_atr=0.01,
                                multiplier=1.0, price=2400.0)
    return Intent(**{**base, **kw})


def _registry(key: str) -> dict[str, Any]:
    return intent_terms(load_instruments().get(key))


def _arm(tmp_path: Path, key: str, stop: float, **kw: Any) -> Intent:
    """A minimum-lot exploration entry on `key`'s registry terms, the stop `stop` in its price units."""
    phase = tmp_path / "phase_state.json"
    phase.write_text(json.dumps({"gates_passed": []}))
    spec = load_instruments().get(key)
    base: dict[str, Any] = dict(agent_id="explore:" + key, side=1, p=0.5, target_atr=1.0, stop_atr=1.0, atr_usd=stop,
                                cost_atr=0.1, multiplier=1.0, price=0.2, timeframe="1m", family="random_bracket",
                                contract_oz=spec.contract_size, volume_min=spec.min_lot, volume_step=spec.lot_step,
                                volume_max=spec.min_lot, account_mode="demo", phase_file=phase, purpose="exploration",
                                terminal_trade_mode="demo", arm_id=ARM, learning_probe=True, **_registry(key))
    return Intent(**{**base, **kw})


def _explorer() -> RiskGate:
    return RiskGate(RiskLimits(probe_only=True, exploration_arms={ARM: 50.0}, max_spread_points=1e6))


# ---------------------------------------------------------------- the guard
def test_a_signal_whose_spread_is_over_three_tenths_of_its_stop_is_refused() -> None:
    # $1 stop, 45 points = $0.45 spread: 0.45 of the stop
    d = RiskGate(RiskLimits()).check(_gold(atr_usd=1.0), _state(spread_points=45.0), NOW)
    assert not d.allowed and "spread_over_stop" in d.reasons
    # the same spread on a $5 stop is 0.09 of it
    assert RiskGate(RiskLimits()).check(_gold(), _state(spread_points=45.0), NOW).allowed


def test_the_sugar_exploration_entry_with_its_stop_inside_the_spread_is_refused(tmp_path: Path) -> None:
    # S0-SUGAR 2026-11-03: stop 0.00055, spread 55 points of 0.00001 = 0.00055 (the gate floored the stop to it)
    d = _explorer().check(_arm(tmp_path, "SUGAR", 0.00055), _state(spread_points=55.0), NOW)
    assert not d.allowed and "spread_over_stop" in d.reasons
    # a stop the spread is 0.22 of trades (0.1 lot x 112,000 x 0.0025 = 28.00 at the stop)
    assert _explorer().check(_arm(tmp_path, "SUGAR", 0.0025), _state(spread_points=55.0), NOW).allowed


def test_a_stop_asked_inside_the_spread_is_refused_even_though_the_floor_would_widen_it(tmp_path: Path) -> None:
    """The rule's stop is what its R is measured in: a stop the gate floors out to the spread still has its target
    inside the noise, so the guard judges the stop asked for (never looser than the floored one)."""
    d = _explorer().check(_arm(tmp_path, "SUGAR", 0.0001), _state(spread_points=55.0), NOW)
    assert not d.allowed and "spread_over_stop" in d.reasons


@pytest.mark.parametrize("key, spread_points, stop", [("BUND10Y", 52.0, 1.0), ("UST10Y", 8.9, 0.10)])
def test_the_vantage_bond_entries_of_the_audit_are_refused(tmp_path: Path, key: str, spread_points: float,
                                                           stop: float) -> None:
    # the audit's spreads as a share of the stop: BUND10Y 0.52, UST10Y 0.89 (the absolute cap is lifted here, so only
    # the stop guard can refuse)
    d = _explorer().check(_arm(tmp_path, key, stop), _state(spread_points=spread_points), NOW)
    assert not d.allowed and "spread_over_stop" in d.reasons


def test_the_spread_is_read_in_the_instruments_own_points(tmp_path: Path) -> None:
    """EURUSD: 20 points of 0.00001 = 0.0002 on a 0.0050 stop is 0.04 of it; read in gold's 0.01 points it would be
    0.2 on a 0.005 stop (40x) and refused."""
    intent = _gold(atr_usd=0.005, price=1.08, contract_oz=100_000.0, volume_max=50.0, **_registry("EURUSD"))
    d = RiskGate(RiskLimits(max_spread_points=1e6)).check(intent, _state(spread_points=20.0), NOW)
    assert d.allowed, d.reasons


def test_a_spread_of_exactly_the_fraction_is_allowed_and_one_point_more_is_refused() -> None:
    gate = RiskGate(RiskLimits(max_spread_stop_frac=0.3, max_spread_points=1e6))
    assert gate.check(_gold(atr_usd=2.0), _state(spread_points=60.0), NOW).allowed          # 0.60 of a $2 stop: 0.3
    d = gate.check(_gold(atr_usd=2.0), _state(spread_points=61.0), NOW)
    assert not d.allowed and "spread_over_stop" in d.reasons


def test_the_fraction_is_a_setting_carried_into_the_gate_and_only_tightens() -> None:
    s = load_settings()
    assert s.risk.max_spread_stop_frac == 0.3
    assert RiskLimits.from_settings(s.risk, tiny_live=False).max_spread_stop_frac == 0.3
    raw = s.risk.model_dump()
    for bad in (0.0, -0.1, 1.01):
        with pytest.raises(ValidationError):
            RiskSettings(**{**raw, "max_spread_stop_frac": bad})


def test_a_cost_measurement_probe_is_held_to_the_guard_too(tmp_path: Path) -> None:
    phase = tmp_path / "phase_state.json"
    phase.write_text(json.dumps({"gates_passed": []}))
    probe = _gold(atr_usd=1.0, volume_max=0.01, learning_probe=True, account_mode="demo", phase_file=phase,
                  purpose="cost_measurement", terminal_trade_mode="demo", hold_s=30.0, probe_headroom=5.0,
                  probe_max_loss=5.0, has_target=False)
    d = RiskGate(RiskLimits()).check(probe, _state(spread_points=40.0), NOW)
    assert not d.allowed and "spread_over_stop" in d.reasons


# ---------------------------------------------------------------- commission in the planned risk
def test_the_planned_risk_and_the_sizing_include_the_round_trip_commission() -> None:
    gate = RiskGate(RiskLimits())
    plain = gate.check(_gold(), _state(), NOW)
    assert plain.lots == pytest.approx(0.10) and plain.risk_amount == pytest.approx(50.0)
    # $7 a lot round trip = 0.07 per oz: one lot risks 507, so 50 buys 0.09 lot, which risks 45.63 with commission
    d = gate.check(_gold(commission_usd_per_oz=0.07), _state(), NOW)
    assert d.allowed and d.lots == pytest.approx(0.09)
    assert d.risk_amount == pytest.approx(0.09 * 507.0)
    assert d.risk_fraction == pytest.approx(0.09 * 507.0 / 10_000.0)


def test_an_fx_min_lot_scalps_planned_risk_counts_its_commission() -> None:
    # EURUSD 0.01 lot, a 2-pip stop: 0.01 x 100,000 x 0.0002 = 0.20 at the stop, + $7 a lot round trip x 0.01 = 0.07
    intent = _gold(atr_usd=0.0002, price=1.08, contract_oz=100_000.0, volume_max=50.0,
                   commission_usd_per_oz=7.0 / 100_000.0, **_registry("EURUSD"))
    gate = RiskGate(RiskLimits(min_lot_risk_cap=0.001, max_spread_points=1e6))
    d = gate.check(intent.model_copy(update={"volume_max": 0.01}), _state(equity=1_000.0, day_start_equity=1_000.0,
                                                                          week_start_equity=1_000.0,
                                                                          balance_closed_hwm=1_000.0,
                                                                          spread_points=5.0), NOW)
    assert d.allowed, d.reasons
    assert d.lots == pytest.approx(0.01) and d.risk_amount == pytest.approx(0.27)


def test_the_commission_counts_against_an_exploration_arms_per_trade_cap(tmp_path: Path) -> None:
    """The loss at the stop at the arm's cap exactly is allowed; the same trade paying its commission is over it."""
    gate = RiskGate(RiskLimits(probe_only=True, exploration_arms={"A": 3.0}))
    arm = _gold(p=0.3, target_atr=1.0, atr_usd=3.0, volume_max=0.01, learning_probe=True, account_mode="demo",
                phase_file=tmp_path / "phase_state.json", purpose="exploration", terminal_trade_mode="demo",
                arm_id="A")
    assert gate.check(arm, _state(), NOW).allowed
    d = gate.check(arm.model_copy(update={"commission_usd_per_oz": 0.07}), _state(), NOW)
    assert not d.allowed and d.reasons == ["exploration_arm_cap"]


def test_a_non_finite_or_negative_commission_refuses_the_entry() -> None:
    for bad in (float("nan"), float("inf"), -0.01):
        d = RiskGate(RiskLimits()).check(_gold(commission_usd_per_oz=bad), _state(), NOW)
        assert not d.allowed and "invalid:commission_usd_per_oz" in d.reasons


# ---------------------------------------------------------------- the engine's cost and commission
def _engine(tmp_path: Path, **cfg: Any) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), cost_atr=0.10,
                               **cfg), PaperBroker(), [], {"session_open": ConstantModel(p=0.6)})


def _table(**kw: Any) -> CostTable:
    base: dict[str, Any] = dict(account_id="icm-demo", built_utc=pd.Timestamp("2025-03-03", tz="UTC"),
                                spread={"london": SpreadStat(median=0.12, p90=0.2, n=1000)},
                                slippage={"london:market": SlippageStat(mean=0.05, n=60, from_prior=False)},
                                commission_per_lot_side_usd=3.5, slippage_prior_usd=0.15)
    return CostTable(**{**base, **kw})


def test_without_a_cost_table_the_cost_still_counts_the_live_spread_and_the_commission(tmp_path: Path) -> None:
    eng = _engine(tmp_path, commission_per_lot_side_usd=3.5)
    ts = pd.Timestamp("2026-11-03 09:00", tz="UTC")
    eng.state.spread_points = 0.0
    # 2 x 3.5 / 100 oz = 0.07 per oz over a 2.0 stop = 0.035 R: under the 0.10 fallback, which stays
    assert eng._cost_atr(2.0, ts) == pytest.approx(0.10)
    # a 0.52 spread (52 points of 0.01) + 0.07 commission over 1.0 = 0.59 R: the fallback alone said 0.10
    eng.state.spread_points = 52.0
    assert eng._cost_atr(1.0, ts) == pytest.approx(0.59)
    # the threshold's cost leaves the spread out (the labels pay it), never the commission
    assert eng._cost_atr(0.5, ts, ex_spread=True) == pytest.approx(0.14)


def test_a_cost_table_whose_spread_is_narrower_than_the_live_one_counts_the_live_spread(tmp_path: Path) -> None:
    eng = _engine(tmp_path)
    _table().save(tmp_path / "costs_icm-demo.json")
    ts = pd.Timestamp("2025-03-04 09:00", tz="UTC")
    eng.state.spread_points = 0.0
    assert eng._cost_atr(2.0, ts) == pytest.approx(0.29 / 2.0)              # 0.12 + 0.10 + 0.07: the table
    eng.state.spread_points = 50.0                                          # 0.50 now, the table's median 0.12
    assert eng._cost_atr(2.0, ts) == pytest.approx((0.50 + 0.07) / 2.0)


def test_the_commission_per_oz_is_the_tables_else_the_brokers_prior(tmp_path: Path) -> None:
    eng = _engine(tmp_path, commission_per_lot_side_usd=3.0)
    assert eng._commission_usd_per_oz(2400.0) == pytest.approx(0.06)        # 2 x 3.0 / 100 oz: the prior
    _table().save(tmp_path / "costs_icm-demo.json")
    assert eng._commission_usd_per_oz(2400.0) == pytest.approx(0.07)        # 2 x 3.5 / 100 oz: measured
    assert _engine(tmp_path / "none")._commission_usd_per_oz(2400.0) == 0.0    # no prior configured: none


@pytest.mark.parametrize("key, price, per_unit", [
    ("EURUSD", 1.08, 7.0 / 100_000.0),                     # USD-quoted: per unit of the 100,000 contract
    ("USDJPY", 150.0, 7.0 / 100_000.0 * 150.0),            # quoted in JPY: the USD commission at the USDJPY price
    ("XAGUSD", 30.0, None),                                # a metal: the prior per its own contract
    ("JP225", 50_000.0, 0.0),                              # index, bond, energy and soft CFDs: spread-priced
    ("BUND10Y", 130.0, 0.0),
])
def test_a_non_gold_books_commission_follows_its_asset_class_and_quote(tmp_path: Path, key: str, price: float,
                                                                       per_unit: float | None) -> None:
    spec = load_instruments().get(key)
    book = BookSpec(key=key, symbol=key, store_symbol=key, magic_block=spec.magic_block, digits=spec.digits,
                    point=spec.point, contract_size=spec.contract_size, instrument=spec)
    eng = _engine(tmp_path, commission_per_lot_side_usd=3.5, mode="demo", instruments=(book,))
    from goldbot.engine import books
    with books.using(eng, eng.books[key]):
        got = eng._commission_usd_per_oz(price)
    want = 7.0 / spec.contract_size if per_unit is None else per_unit
    assert got == pytest.approx(want)


# ---------------------------------------------------------------- the commission in the quote currency (#327 review L1)
class _FxBroker(PaperBroker):
    """A paper terminal in `currency` whose loss on one lot converts each symbol's profit currency at `acct_per`."""

    def __init__(self, currency: str, acct_per: dict[str, float]) -> None:
        super().__init__()
        self.currency, self.acct_per = currency, acct_per

    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"currency": self.currency})

    def loss_per_lot(self, symbol: str, side: int, price: float, distance: float) -> float | None:
        fx = self.acct_per.get(symbol)
        return None if fx is None else abs(distance) * self.contract * fx


def _aud_engine(tmp_path: Path, broker: PaperBroker) -> Engine:
    spec = load_instruments().get("XAUAUD")
    book = BookSpec(key="XAUAUD", symbol="XAUAUD", store_symbol="XAUAUD", magic_block=spec.magic_block,
                    digits=spec.digits, point=spec.point, contract_size=spec.contract_size, instrument=spec)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), cost_atr=0.10,
                              commission_per_lot_side_usd=3.5, mode="demo", instruments=(book,)),
                 broker, [], {"session_open": ConstantModel(p=0.6)})
    eng.primary.__dict__["_last_tick"] = Tick(ts_utc=NOW, bid=2400.0, ask=2400.2)
    eng.primary.__dict__["_contract"] = 100.0
    return eng


@pytest.mark.parametrize("currency, acct_per", [
    ("USD", {"XAUUSD": 1.0, "XAUAUD": 0.65}),             # USD account: 1 AUD = 0.65 USD
    ("GBP", {"XAUUSD": 0.78, "XAUAUD": 0.507}),           # GBP account: 0.78 GBP per USD, 0.507 per AUD
])
def test_an_aud_quoted_books_commission_is_converted_into_aud(tmp_path: Path, currency: str,
                                                              acct_per: dict[str, float]) -> None:
    """XAUAUD's spread, stop and ATR are AUD per oz, so the USD commission ($7 a lot round trip, 0.07 per oz) is
    taken into AUD at the broker's own conversions (account per USD over account per AUD), never added as USD."""
    eng = _aud_engine(tmp_path, _FxBroker(currency, acct_per))
    from goldbot.engine import books
    with books.using(eng, eng.books["XAUAUD"]):
        got = eng._commission_usd_per_oz(3700.0)
        eng._last_tick = Tick(ts_utc=NOW, bid=3699.75, ask=3700.25)        # the cost reads the book's own quote
        eng.state.spread_points = 50.0
        cost = eng._cost_atr(1.0, NOW)
    aud = 0.07 * acct_per["XAUUSD"] / acct_per["XAUAUD"]
    assert got == pytest.approx(aud)
    assert cost == pytest.approx(0.50 + aud)                   # spread and commission both in AUD over a 1.0 ATR


def test_an_aud_quoted_book_without_a_conversion_refuses_rather_than_counting_usd(tmp_path: Path) -> None:
    eng = _aud_engine(tmp_path, _FxBroker("USD", {"XAUUSD": 1.0}))       # the broker cannot convert XAUAUD
    from goldbot.engine import books
    with books.using(eng, eng.books["XAUAUD"]):
        got = eng._commission_usd_per_oz(3700.0)
    assert math.isnan(got)                                     # the gate refuses `invalid:commission_usd_per_oz`
    d = RiskGate(RiskLimits()).check(_gold(commission_usd_per_oz=got), _state(), NOW)
    assert not d.allowed and "invalid:commission_usd_per_oz" in d.reasons


# ---------------------------------------------------------------- tightening only, on the pinned decision grid
def test_on_the_pinned_gate_grid_the_guard_only_ever_adds_a_refusal() -> None:
    """tests/test_risk_gate_split.py's golden (captured before the guard): with the guard on, every case it changes
    is now a refusal naming `spread_over_stop`; nothing else moves, so nothing the gate refused is allowed."""
    from tests import test_risk_gate_split as grid
    want = json.loads(grid.GRID.read_text())
    got = grid._grid()
    changed = [k for k in want if want[k] != got[k]]
    assert changed                                                  # the grid's tight stops do meet the guard
    for k in changed:
        before, after = want[k], got[k]
        assert after[0] is False and "spread_over_stop" in after[2], (k, before, after)


def test_an_unknown_commission_floors_the_cost_on_the_spread_alone() -> None:
    """#327 follow-up L2: max(cost, nan) kept `cost` and dropped the spread too; the floor keeps the spread."""
    from types import SimpleNamespace

    from goldbot.engine import commission

    eng: Any = SimpleNamespace(state=SimpleNamespace(spread_points=50.0), cfg=SimpleNamespace(point=0.01),
                          _commission_usd_per_oz=lambda: math.nan)
    assert commission.floor_cost(eng, 0.05, 2.0, ex_spread=False) == pytest.approx(0.25)   # 0.50 / 2.0
    assert commission.floor_cost(eng, 0.05, 2.0, ex_spread=True) == pytest.approx(0.05)
