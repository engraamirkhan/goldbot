"""Multi-instrument demo plan (2026-11-02, lane L2): one AccountState and one serial RiskGate per account cover every
symbol. Design rules pinned here, quoted from the plan:

* "one AccountState per account and a serial gate so per-account loss limits cover all symbols": two symbols deciding
  on the same bar cannot together take the day past its loss limit (the engine books each allowed entry on the shared
  state, `RiskGate.book`, before the next symbol's check; a multi-symbol account always projects its open risk).
* "account-wide heat": the open risk counts every symbol's positions (`open_risk.account_open_risk`).
* "max_combined_lots ... becomes notional in account currency per correlation group (gold, metals, equity, FX, energy,
  rates, ags)": `group_exposure_cap`; gold keeps its lots cap.
* "leverage per asset class (FX 30, gold/major idx 20, other commodities 10, bonds 5)".
* "XAUUSD behaviour byte-identical": the decision grid captured before the split (tests/data/
  risk_gate_decision_grid.json) is reproduced with the XAUUSD registry terms passed explicitly.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd
import pytest

from goldbot.config import HeatSettings
from goldbot.execution.broker import Position
from goldbot.instruments import LEVERAGE_BY_CLASS, load_instruments
from goldbot.risk import AccountState, Intent, RiskGate, RiskLimits
from goldbot.risk.gate import PER_TRADE_REFUSALS, intent_terms, profit_fx
from goldbot.risk.open_risk import SymbolQuote, account_open_risk, group_exposure
from tests import test_risk_gate_split as grid

NOW = pd.Timestamp("2026-11-04 10:00", tz="UTC")


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                                week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=20.0, open_risk=0.0)
    if "group_notional" in kw:                     # a multi-symbol state: the other accounts' groups are known (none)
        base["other_group_notional"] = {}
    base.update(kw)
    return AccountState(**base)


def _gold(**kw: Any) -> Intent:
    """At 10,000 equity, 0.5% risk and m = 1 a $5 stop sizes 0.10 lot = 50 at the stop (100 oz)."""
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=5.0, cost_atr=0.01,
                                multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


def _eurusd(**kw: Any) -> Intent:
    """EURUSD, 100,000 per lot, quoted in USD: a 0.0050 stop sizes 0.10 lot = 50 at the stop."""
    base: dict[str, Any] = dict(agent_id="y", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=0.005,
                                cost_atr=0.01, multiplier=1.0, price=1.08, contract_oz=100_000.0, volume_max=50.0,
                                correlation_group="fx", leverage_class="fx", point=0.00001, symbol="EURUSD",
                                currency_profit="USD")
    base.update(kw)
    return Intent(**base)


def _other(symbol: str, group: str, cls: str, contract: float, price: float, atr: float, **kw: Any) -> Intent:
    """A non-gold intent with its full registry terms: 0.5% of 10,000 = 50 at the stop."""
    base: dict[str, Any] = dict(agent_id=symbol, side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=atr,
                                cost_atr=0.01, multiplier=1.0, price=price, contract_oz=contract, volume_max=50.0,
                                symbol=symbol, correlation_group=group, leverage_class=cls, currency_profit="USD",
                                point=0.001)
    base.update(kw)
    return Intent(**base)


# ---------------------------------------------------------------------------------------------- daily limit
def test_two_symbols_same_bar_cannot_exceed_daily_limit():
    gate = RiskGate(RiskLimits(daily_loss_limit_abs=80.0, multi_symbol=True))
    st = _state()
    first = gate.check(_gold(), st, NOW)
    assert first.allowed and first.risk_amount == pytest.approx(50.0)
    gate.book(st, _gold(), first)                       # the engine reserves it before the next symbol decides
    second = gate.check(_eurusd(), st, NOW)
    assert not second.allowed and second.reasons == ["daily_loss_limit_abs"]
    # without the booking the second symbol would have passed: the serial check plus the booking is the rule
    assert gate.check(_eurusd(), _state(), NOW).allowed


def test_multi_symbol_projects_open_risk_even_without_the_owner_projection_flag():
    gate = RiskGate(RiskLimits(daily_loss_limit_abs=80.0, multi_symbol=True, project_open_risk=False))
    assert gate.check(_eurusd(), _state(open_risk=40.0), NOW).reasons == ["daily_loss_limit_abs"]
    legacy = RiskGate(RiskLimits(daily_loss_limit_abs=80.0))         # one symbol: as before, the flag decides
    assert legacy.check(_eurusd(), _state(open_risk=40.0), NOW).allowed


def test_multi_symbol_projects_against_the_percentage_limit_when_no_absolute_is_set():
    gate = RiskGate(RiskLimits(multi_symbol=True))                    # 2% of 10,000 = 200
    assert gate.check(_gold(), _state(open_risk=140.0), NOW).allowed is True
    d = gate.check(_gold(), _state(open_risk=160.0), NOW)
    assert d.reasons == ["daily_limit_projected"]           # no absolute limit: not labelled as one (review M1)
    assert "daily_limit_projected" in PER_TRADE_REFUSALS


def test_multi_symbol_refuses_while_the_day_start_equity_is_unknown():
    """Review M1: with no day-start equity the percentage limit would be infinite; a multi-symbol account refuses."""
    gate = RiskGate(RiskLimits(multi_symbol=True))
    assert "day_start_unknown" in gate.check(_gold(), _state(day_start_equity=0.0), NOW).reasons
    assert "day_start_unknown" in gate.account_reasons(_state(day_start_equity=0.0))
    assert "day_start_unknown" not in RiskGate().account_reasons(_state(day_start_equity=0.0))   # one symbol: as before


def test_three_groups_on_one_bar_the_third_is_refused_at_the_margin_floor():
    """Review H1: `book` reserves the decision's margin (the stricter of the broker's and the class figure), so the
    third group's entry on the same bar meets the 300% level floor with the first two counted."""
    gate = RiskGate(RiskLimits(multi_symbol=True, max_positions=5))
    st = _state(group_notional={})
    gold = _gold()                                                                    # 24,000 / 20 = 1,200
    silver = _other("XAGUSD", "metals", "commodity", contract=5_000.0, price=24.0, atr=0.1)       # 12,000 / 10
    oil = _other("XTIUSD", "energy", "commodity", contract=1_000.0, price=120.0, atr=0.5)          # 12,000 / 10
    for intent in (gold, silver):
        d = gate.check(intent, st, NOW)
        assert d.allowed and d.margin_needed == pytest.approx(1_200.0), d
        gate.book(st, intent, d)
    assert st.margin_used == pytest.approx(2_400.0)
    third = gate.check(oil, st, NOW)
    assert third.reasons == ["margin_level_floor"]                  # 10,000 / 3,600 < 300%
    assert gate.check(oil, _state(group_notional={}), NOW).allowed  # alone it fits


def test_multi_symbol_refuses_entries_while_the_open_risk_is_unknown():
    d = RiskGate(RiskLimits(multi_symbol=True)).check(_gold(), _state(open_risk=None), NOW)
    assert "open_risk_unknown" in d.reasons


def test_book_reserves_lots_risk_positions_and_the_group_notional():
    gate = RiskGate(RiskLimits(multi_symbol=True))
    st = _state(group_notional={})
    d = gate.check(_eurusd(), st, NOW)
    gate.book(st, _eurusd(), d)
    assert st.open_positions == 1 and st.open_long == 1 and st.open_short == 0
    assert st.open_risk == pytest.approx(d.risk_amount + d.cost_amount)
    assert st.group_notional == pytest.approx({"fx": 0.1 * 100_000 * 1.08})
    assert st.open_lots == 0.0                       # lots are counted for gold only (the gold lots cap)
    g = gate.check(_gold(side=-1), st, NOW)
    gate.book(st, _gold(side=-1), g)
    assert st.open_lots == pytest.approx(g.lots) and st.open_short == 1
    assert st.group_notional is not None and st.group_notional["gold"] == pytest.approx(g.lots * 100 * 2400.0)
    before = st.model_copy(deep=True)
    gate.book(st, _gold(), gate.check(_gold(p=0.1), st, NOW))      # a refusal books nothing
    assert st == before


def test_book_on_a_single_instrument_state_keeps_the_usd_notional():
    gate, st = RiskGate(), _state()
    d = gate.check(_gold(), st, NOW)
    gate.book(st, _gold(), d)
    assert st.group_notional is None and st.open_notional == pytest.approx(d.lots * 100 * 2400.0)
    assert st.open_lots == pytest.approx(d.lots) and st.open_positions == 1


# ---------------------------------------------------------------------------------------------- heat
def _pos(pid: int, symbol: str, side: int, lots: float, sl: float | None) -> Position:
    return Position(position_id=pid, symbol=symbol, side=side, lots=lots, open_price=0.0, sl=sl, tp=None, magic=0,
                    comment="", open_time_utc=NOW, profit=0.0)


QUOTES = {"XAUUSD": SymbolQuote(bid=2400.0, ask=2400.2, contract=100.0, fx=1.0, group="gold"),
          "EURUSD": SymbolQuote(bid=1.0800, ask=1.0801, contract=100_000.0, fx=1.0, group="fx")}


def test_heat_counts_every_symbol():
    positions = [_pos(1, "XAUUSD", 1, 0.10, 2395.0), _pos(2, "EURUSD", -1, 0.10, 1.0851)]
    total, rows = account_open_risk(positions, QUOTES)
    assert total == pytest.approx(0.10 * 100 * 5.0 + 0.10 * 100_000 * 0.0050)        # 50 + 50
    assert [r["symbol"] for r in rows] == ["XAUUSD", "EURUSD"]
    gate = RiskGate(RiskLimits(heat=HeatSettings(enabled=True, max_open_risk=0.012, max_same_direction=5)))
    assert gate.check(_gold(), _state(open_risk=50.0), NOW).allowed                 # gold alone: 50 + 50 <= 120
    assert gate.check(_gold(), _state(open_risk=total), NOW).reasons == ["heat_cap"]   # every symbol: 150 > 120


def test_open_risk_of_a_symbol_without_a_quote_a_stop_or_a_conversion_is_unknown():
    assert account_open_risk([_pos(1, "USDJPY", 1, 0.1, 150.0)], QUOTES)[0] is None
    no_fx = {**QUOTES, "EURUSD": QUOTES["EURUSD"].model_copy(update={"fx": None})}
    assert account_open_risk([_pos(1, "EURUSD", 1, 0.1, 1.07)], no_fx)[0] is None
    assert account_open_risk([_pos(1, "XAUUSD", 1, 0.1, None)], QUOTES)[0] is None
    assert account_open_risk([], QUOTES) == (0.0, [])


def test_group_exposure_sums_notional_per_group_in_the_account_currency():
    gbp = {k: v.model_copy(update={"fx": 0.8}) for k, v in QUOTES.items()}
    groups, gold_lots = group_exposure([_pos(1, "XAUUSD", 1, 0.2, 2390.0), _pos(2, "EURUSD", -1, 0.5, 1.09)], gbp)
    assert gold_lots == pytest.approx(0.2)
    assert groups == pytest.approx({"gold": 0.2 * 100 * 2400.1 * 0.8,
                                    "fx": 0.5 * 100_000 * 1.08005 * 0.8})
    assert group_exposure([_pos(1, "USDJPY", 1, 0.1, 150.0)], gbp) == (None, 0.0)


# ---------------------------------------------------------------------------------------------- group notional cap
def test_group_notional_cap():
    gate = RiskGate(RiskLimits())
    # fx at 1:30: 30% x 30 x 10,000 = 90,000 of fx notional; 0.10 lot EURUSD is 10,800
    assert gate.check(_eurusd(), _state(group_notional={"fx": 79_000.0}), NOW).allowed
    d = gate.check(_eurusd(), _state(group_notional={"fx": 80_000.0}), NOW)
    assert d.reasons == ["group_exposure_cap"]
    # another group's exposure does not count against fx
    assert gate.check(_eurusd(), _state(group_notional={"energy": 1e6}), NOW).allowed
    # the other accounts' exposure in the group counts, as their equity widens the base
    assert gate.check(_eurusd(), _state(group_notional={}, other_group_notional={"fx": 80_000.0}), NOW).reasons \
        == ["group_exposure_cap"]
    assert gate.check(_eurusd(), _state(group_notional={}, other_group_notional={"fx": 80_000.0},
                                        other_equity=10_000.0), NOW).allowed


def test_group_cap_for_gold_keeps_the_lots_cap():
    gate = RiskGate(RiskLimits())
    d = gate.check(_gold(), _state(group_notional={}, open_lots=2.95), NOW)
    assert d.reasons == ["combined_exposure_cap"]
    assert gate.check(_gold(), _state(group_notional={}, open_lots=2.85), NOW).allowed


def test_group_cap_rates_at_one_to_five():
    gate = RiskGate(RiskLimits())
    bund = _eurusd(correlation_group="rates", leverage_class="bond", price=130.0, contract_oz=1000.0, atr_usd=0.5,
                   point=0.01)
    # 30% x 5 x 10,000 = 15,000; 0.10 lot is 13,000 (a 0.10 spread on the 0.5 stop, within the spread-over-stop guard)
    assert gate.check(bund, _state(group_notional={"rates": 1_000.0}, spread_points=10.0), NOW).allowed
    assert gate.check(bund, _state(group_notional={"rates": 3_000.0}, spread_points=10.0), NOW).reasons == \
        ["group_exposure_cap"]


def test_unknown_or_invalid_group_exposure_refuses_entries():
    gate = RiskGate(RiskLimits())
    assert "exposure_unknown" in gate.check(_eurusd(), _state(group_notional={}, exposure_unknown=True), NOW).reasons
    d = gate.check(_eurusd(), _state(group_notional={"fx": math.nan}), NOW)
    assert not d.allowed and "invalid:group_notional" in d.reasons
    d = gate.check(_eurusd(), _state(group_notional={}, other_group_notional={"fx": -1.0}), NOW)
    assert not d.allowed and "invalid:other_group_notional" in d.reasons
    assert {"group_exposure_cap"} <= PER_TRADE_REFUSALS


# ---------------------------------------------------------------------------------------------- leverage per class
@pytest.mark.parametrize("cls, lev", [("fx", 30.0), ("gold_index", 20.0), ("commodity", 10.0), ("bond", 5.0)])
def test_leverage_per_asset_class(cls, lev):
    gate = RiskGate()
    assert gate.leverage(None, cls) == lev
    assert gate.leverage(_state(broker_leverage=4.0), cls) == 4.0              # the terminal's own only tightens
    d = gate.check(_eurusd(leverage_class=cls, correlation_group="metals"), _state(), NOW)
    assert d.allowed and d.margin_needed == pytest.approx(d.lots * 100_000 * 1.08 / lev)
    assert d.margin_note == f"fallback 1:{lev:g}: no broker margin source"


def test_a_lowered_leverage_cap_scales_every_class():
    """Review M2: class leverage = min(class, leverage_cap x class / 20), so lowering the cap tightens every class."""
    gate = RiskGate(RiskLimits(leverage_cap=5.0))
    assert {c: gate.leverage(None, c) for c in LEVERAGE_BY_CLASS} == \
        {"fx": 7.5, "gold_index": 5.0, "commodity": 2.5, "bond": 1.25, "crypto": 0.5}
    assert RiskGate(RiskLimits(leverage_cap=40.0)).leverage(None, "fx") == 30.0        # never above the class
    d = gate.check(_eurusd(), _state(), NOW)
    assert d.margin_needed == pytest.approx(d.lots * 100_000 * 1.08 / 7.5)


def test_gold_group_uses_the_leverage_cap_whatever_its_class():
    """Review M3 (b): every gold symbol is the gold leverage cap, even if an intent names another class."""
    gate = RiskGate()
    assert gate.leverage(None, "commodity", "gold") == 20.0
    xaueur = _gold(symbol="XAUEUR", correlation_group="gold", currency_profit="EUR", acct_per_profit=1.08,
                   point=0.01, leverage_class="fx")
    d = gate.check(xaueur, _state(), NOW)
    assert d.margin_note.startswith("fallback 1:20")


def test_a_non_gold_symbol_without_its_registry_terms_is_refused():
    """Review M3 (c): a symbol other than XAUUSD whose intent left the registry terms at their (gold) defaults."""
    bare = Intent(agent_id="z", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=0.005, cost_atr=0.01,
                  multiplier=1.0, price=1.08, contract_oz=100_000.0, volume_max=50.0, symbol="EURUSD")
    assert "instrument_terms_missing" in RiskGate().check(bare, _state(), NOW).reasons
    partial = bare.model_copy(update={"correlation_group": "fx", "leverage_class": "fx", "point": 0.00001})
    assert "instrument_terms_missing" in RiskGate().check(partial, _state(), NOW).reasons     # currency not given
    assert "instrument_terms_missing" not in RiskGate().check(_eurusd(), _state(), NOW).reasons
    assert "instrument_terms_missing" not in RiskGate().check(_gold(), _state(), NOW).reasons  # XAUUSD: defaults


def test_unknown_other_accounts_groups_refuse_on_a_multi_symbol_state():
    """Review M4: on a multi-symbol state the other accounts' groups must be known (an empty map is known)."""
    d = RiskGate().check(_eurusd(), _state(group_notional={}, other_group_notional=None), NOW)
    assert "exposure_unknown" in d.reasons
    assert RiskGate().check(_gold(), _state(other_group_notional=None), NOW).allowed      # single symbol: not read


def test_class_leverage_matches_the_registry():
    assert {c: RiskGate().leverage(None, c) for c in LEVERAGE_BY_CLASS} == \
        {c: float(v) for c, v in LEVERAGE_BY_CLASS.items()}


# ---------------------------------------------------------------------------------------------- currencies, point
def test_profit_currency_conversion_is_never_guessed():
    jpy = _eurusd(currency_profit="JPY", price=150.0, atr_usd=0.5, point=0.001)
    assert profit_fx(jpy) is None
    assert RiskGate().check(jpy, _state(), NOW).reasons == ["fx_conversion_unavailable"]
    ok = jpy.model_copy(update={"acct_per_profit": 1 / 150})
    d = RiskGate().check(ok, _state(), NOW)
    assert d.allowed and d.risk_amount == pytest.approx(d.lots * 100_000 * 0.5 / 150)
    # the account's own currency is 1, and a broker figure far from 1 means the terms are wrong
    gbp = _eurusd(currency_profit="GBP", account_currency="GBP")
    assert profit_fx(gbp) == 1.0
    assert profit_fx(gbp.model_copy(update={"acct_per_profit": 1.3})) is None
    # a USD quote keeps the USD conversion rule exactly
    assert profit_fx(_gold(account_currency="GBP", acct_per_usd=0.78)) == 0.78
    assert profit_fx(_gold(account_currency="GBP")) is None


def test_the_stop_floor_uses_the_instruments_point():
    d = RiskGate().check(_eurusd(stops_level_points=100.0, atr_usd=0.0001), _state(spread_points=1.0), NOW)
    assert d.stop_distance == pytest.approx((100 + 1) * 0.00001)       # the gold point (0.01) would make it 1.01


# ---------------------------------------------------------------------------------------------- XAUUSD unchanged
def test_xauusd_gate_decisions_byte_identical_with_the_registry_terms(monkeypatch):
    spec = load_instruments().get("XAUUSD")
    terms = intent_terms(spec)
    assert terms == {"symbol": "XAUUSD", "correlation_group": "gold", "leverage_class": "gold_index",
                     "currency_profit": "USD", "point": 0.01}
    want = json.loads(grid.GRID.read_text())
    original = grid._intent

    def explicit(mod: Any = grid.gate_module, **kw: Any) -> Any:
        return original(mod, **{**terms, **kw})
    monkeypatch.setattr(grid, "_intent", explicit)
    monkeypatch.setattr(grid.gate_module, "spread_over_stop", lambda *a: False)   # the golden predates that guard
    got = grid._grid()
    diff = {k: (want[k], got[k]) for k in want if want[k] != got[k]}
    assert set(got) == set(want) and not diff, diff


# ---------------------------------------------------------------------------------------------- supervisor
def _engine_file(tmp_path: Any, account: str, **kw: Any) -> None:
    import time
    body = {"account": account, "ts": time.time(), "equity": 10_000.0, "day_start_equity": 10_000.0,
            "week_start_equity": 10_000.0, "balance_closed_hwm": 10_000.0, "currency": "GBP", **kw}
    (tmp_path / f"engine_{account}.json").write_text(json.dumps(body))


def test_supervisor_publishes_exposure_per_group(tmp_path):
    from goldbot.risk.supervisor import Supervisor
    _engine_file(tmp_path, "gold", open_lots=0.5, open_notional=120_000.0)               # an XAUUSD-only engine
    _engine_file(tmp_path, "multi", group_notional={"fx": 5_000.0, "gold": 2_000.0})
    _engine_file(tmp_path, "probe", probe_only=True, group_notional={"energy": 7_000.0})
    s = Supervisor(tmp_path).evaluate()
    assert "groups" not in s["exposure"]["gold"]                                         # its entry is unchanged
    assert s["exposure"]["multi"]["groups"] == {"fx": 5_000.0, "gold": 2_000.0}
    assert s["exposure"]["multi"]["groups_currency"] == "GBP"
    assert s["combined_group_notional"] == {"fx": 5_000.0, "gold": 2_000.0}             # real accounts only
    assert s["group_notional_excluded"] == []
    assert s["probe_engines"]["probe"]["groups"] == {"energy": 7_000.0}                  # reported apart
    assert not s["halt"], s["reasons"]


def test_combined_group_notional_skips_a_map_in_another_currency(tmp_path):
    """Review L3: a group map is summed only in the money currency; another one is listed, never added."""
    from goldbot.risk.supervisor import Supervisor
    _engine_file(tmp_path, "multi", group_notional={"fx": 5_000.0})
    _engine_file(tmp_path, "usd", currency="USD", group_notional={"fx": 9_000.0})
    s = Supervisor(tmp_path).evaluate()
    assert "mixed_account_currencies" in s["reasons"]                       # halted anyway
    assert s["combined_group_notional"] == {} and s["group_notional_excluded"] == ["engine_multi.json",
                                                                                     "engine_usd.json"]


@pytest.mark.parametrize("bad", [{"fx": math.inf}, {"fx": -1.0}, {"fx": "1"}, [1.0], "x"])
def test_an_engine_file_with_an_unusable_group_notional_is_unreadable(tmp_path, bad):
    from goldbot.risk.supervisor import Supervisor
    _engine_file(tmp_path, "multi", group_notional=bad)
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and "engine_state_unreadable" in s["reasons"]


def test_a_failed_engine_start_keeps_the_group_exposure(tmp_path):
    from goldbot.risk.supervisor import Supervisor, write_startup_error
    _engine_file(tmp_path, "multi", group_notional={"fx": 5_000.0})
    write_startup_error(tmp_path, "multi", "terminal refused", dq_checks=["startup"], currency="GBP")
    s = Supervisor(tmp_path).evaluate()
    assert s["exposure"]["multi"]["groups"] == {"fx": 5_000.0} and "engine_startup_refused" in s["reasons"]


def test_others_group_notional_converts_the_gold_engines_usd_and_never_mixes_currencies():
    from goldbot.risk.supervisor import others_group_notional
    sup = {"exposure": {"gold": {"lots": 0.5, "notional": 100_000.0, "equity": 1.0},
                        "multi": {"lots": 0.0, "notional": 0.0, "equity": 1.0, "groups": {"fx": 5_000.0},
                                  "groups_currency": "GBP"},
                        "me": {"lots": 0.0, "notional": 0.0, "groups": {"fx": 9e9}, "groups_currency": "GBP"}}}
    assert others_group_notional(sup, "me", currency="GBP", acct_per_usd=0.8) == \
        {"gold": pytest.approx(80_000.0), "fx": 5_000.0}
    assert others_group_notional(sup, "me", currency="GBP", acct_per_usd=None) is None       # USD with no rate
    assert others_group_notional(sup, "me", currency="USD", acct_per_usd=1.0) is None        # GBP groups, USD reader
    assert others_group_notional({}, "me", currency="GBP", acct_per_usd=None) == {}


def test_xauusd_defaults_are_the_gold_registry_terms():
    i = _gold()
    assert (i.correlation_group, i.leverage_class, i.currency_profit, i.point, i.acct_per_profit) == \
        ("gold", "gold_index", "USD", None, None)
    st = _state()
    assert st.group_notional is None and st.other_group_notional is None and st.exposure_unknown is False
    assert RiskLimits().multi_symbol is False and i.symbol == "XAUUSD"
