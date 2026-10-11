"""Non-finite and type-corrupt state fails closed (October safety batch, test engineer's probes).

Every comparison with NaN is False, so a limit written as `if x > limit: refuse` lets a NaN through. The RiskGate
refuses an entry when any number it reads is not finite (`non_finite_state`), the engine drops a quote with a
non-finite price as a data-quality error (entries blocked, the next valid tick still runs the exits), and the
supervisor treats a non-finite or wrongly typed engine file as unreadable (halt) instead of failing open or crashing.
"""
import json
import math
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.execution.broker import Tick
from goldbot.ops import run as ops_run
from goldbot.risk import AccountState, Intent, RiskGate
from goldbot.risk.sizing import account_fx
from goldbot.risk.supervisor import Supervisor, SupervisorLimits
from tests.test_engine_clock import WED, _engine, _intent, _open, _StopStuck, _tick

STATE_FIELDS = ("equity", "balance_closed_hwm", "day_start_equity", "week_start_equity", "margin_used",
                "last_tick_age_s", "spread_points", "open_lots", "open_notional", "other_lots", "other_notional",
                "other_equity", "tick_ahead_s")
INTENT_FIELDS = ("p", "target_atr", "stop_atr", "atr_usd", "cost_atr", "multiplier", "price", "contract_oz",
                 "volume_step", "volume_min", "volume_max", "stops_level_points", "acct_per_usd")
BAD = (math.nan, math.inf, -math.inf)


def _state(**kw: Any) -> AccountState:
    base: dict[str, Any] = dict(equity=10_000.0, balance_closed_hwm=10_000.0, day_start_equity=10_000.0,
                                week_start_equity=10_000.0, open_positions=0, margin_used=0.0, last_tick_age_s=1.0,
                                spread_points=25.0)
    base.update(kw)
    return AccountState(**base)


def _gate_intent(**kw: Any) -> Intent:
    base: dict[str, Any] = dict(agent_id="x", side=1, p=0.7, target_atr=3.0, stop_atr=1.0, atr_usd=4.0, cost_atr=0.01,
                                multiplier=1.0, price=2400.0)
    base.update(kw)
    return Intent(**base)


# ---------------------------------------------------------------------------------------------- 1. RiskGate
def test_the_finite_baseline_is_allowed_so_the_refusals_below_are_caused_by_the_bad_value():
    assert RiskGate().check(_gate_intent(), _state()).allowed


@pytest.mark.parametrize("bad", BAD, ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("field", STATE_FIELDS)
def test_a_non_finite_account_state_field_refuses_the_entry_and_names_the_field(field, bad):
    d = RiskGate().check(_gate_intent(), _state(**{field: bad}))
    assert not d.allowed and d.lots == 0.0
    assert d.reasons[0] == "non_finite_state" and f"non_finite:{field}" in d.reasons


@pytest.mark.parametrize("bad", BAD, ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("field", INTENT_FIELDS)
def test_a_non_finite_intent_field_refuses_the_entry_and_names_the_field(field, bad):
    d = RiskGate().check(_gate_intent(**{field: bad}), _state())
    assert not d.allowed and d.lots == 0.0
    assert d.reasons[0] == "non_finite_state" and f"non_finite:{field}" in d.reasons


def test_a_nan_equity_is_refused_by_name_not_by_a_crash_in_the_sizing():
    d = RiskGate().check(_gate_intent(), _state(equity=math.nan))
    assert d.reasons[:2] == ["non_finite_state", "non_finite:equity"]


def test_an_unknown_conversion_stays_none_and_is_not_a_non_finite_refusal():
    d = RiskGate().check(_gate_intent(acct_per_usd=None), _state())       # USD account: None means "not needed"
    assert d.allowed
    d = RiskGate().check(_gate_intent(acct_per_usd=None, account_currency="GBP"), _state())
    assert d.reasons == ["fx_conversion_unavailable"]                       # the existing explicit handling


def test_every_bad_field_is_named_and_the_state_is_left_untouched():
    st = _state(equity=math.nan, spread_points=math.inf)
    d = RiskGate().check(_gate_intent(stop_atr=math.nan), st)
    assert d.reasons == ["non_finite_state", "non_finite:equity", "non_finite:spread_points", "non_finite:stop_atr"]
    assert st.stage.value == "normal"


# ---------------------------------------------------------------------------------------------- 4. account_fx
def test_a_blank_account_currency_is_never_sized():
    assert account_fx("", 1.0) is None
    assert account_fx("   ", 1.0) is None
    assert account_fx("USD", None) == 1.0


def test_a_blank_account_currency_refuses_the_entry_at_the_gate():
    d = RiskGate().check(_gate_intent(account_currency="", acct_per_usd=1.0), _state())
    assert d.reasons == ["fx_conversion_unavailable"]


# ---------------------------------------------------------------------------------------------- 2. engine ticks
@pytest.mark.parametrize("bid,ask", [(math.nan, math.nan), (2400.0, math.inf), (math.nan, 2400.2), (math.inf, math.inf),
                                     (-math.inf, 2400.2), (2400.0, math.nan)],
                         ids=["nan-nan", "inf-ask", "nan-bid", "inf-inf", "-inf-bid", "nan-ask"])
def test_a_quote_with_a_non_finite_price_is_a_data_quality_error_that_blocks_entries(tmp_path, bid, ask):
    eng, _, _ = _engine(tmp_path)
    assert eng._tick_ok(Tick(ts_utc=WED, bid=bid, ask=ask)) is False
    assert any(e.check == "non_finite_quote" and e.severity == "error" for e in eng._dq_errors())
    eng._refresh_account(_tick(WED))
    assert eng.state.dq_error and "data_quality_error" in eng.gate.check(_intent(), eng.state).reasons


def test_a_garbage_quote_never_enters_the_bars_and_the_next_valid_tick_runs_the_engine_stop(tmp_path):
    pb = _StopStuck(equity=10_000)                                    # the broker never takes the engine's stop
    pb.on_tick(_tick(WED))
    eng, _, host = _engine(tmp_path, pb=pb)
    pid = _open(pb, 1, 2300.0)                                        # the broker's stop is far away
    eng.open[pid] = eng._adopt(next(p for p in pb.positions() if p.position_id == pid))
    eng.open[pid].sl = 2399.0                                         # the engine's own stop is tight
    host[0] = WED + pd.Timedelta(seconds=1)
    eng.on_tick(Tick(ts_utc=host[0], bid=math.nan, ask=math.nan))
    assert all(math.isfinite(t.bid) for t in eng.ticks)
    host[0] = WED + pd.Timedelta(seconds=2)
    pb.on_tick(_tick(host[0], 2398.0))                                 # through the engine stop, far above the broker's
    assert pid in {p.position_id for p in pb.positions()}             # the broker did not close it
    eng.on_tick(_tick(host[0], 2398.0))
    assert pid not in {p.position_id for p in pb.positions()}         # the engine stop did


# ---------------------------------------------------------------------------------------------- 3. supervisor
BASE = {"account": "a", "equity": 900.0, "day_start_equity": 1000.0, "week_start_equity": 1000.0,
        "balance_closed_hwm": 1000.0, "currency": "GBP"}
CORRUPT = {
    "equity null": {"equity": None}, "equity str": {"equity": "900"}, "equity bool": {"equity": True},
    "equity nan": {"equity": math.nan}, "equity inf": {"equity": math.inf},
    "day_start nan": {"day_start_equity": math.nan}, "week_start str": {"week_start_equity": "1000"},
    "hwm null": {"balance_closed_hwm": None},
    "abs limit junk": {"daily_loss_limit_abs": "x"}, "abs limit nan": {"daily_loss_limit_abs": math.nan},
    "ts null": {"ts": None}, "ts str": {"ts": "now"}, "ts nan": {"ts": math.nan},
    "open_lots junk": {"open_lots": "x"}, "open_notional nan": {"open_notional": math.nan},
}


def _write(d: Path, name: str, payload: dict) -> None:
    (d / f"engine_{name}.json").write_text(json.dumps(payload))      # json.dumps writes NaN/Infinity, as a bug would


@pytest.mark.parametrize("patch", CORRUPT.values(), ids=CORRUPT.keys())
def test_a_corrupt_engine_file_halts_the_supervisor_by_name_and_never_crashes_it(tmp_path, patch):
    _write(tmp_path, "a", {**BASE, "ts": time.time(), "equity": 1000.0, **patch})
    s = Supervisor(tmp_path, SupervisorLimits(daily_loss_limit_abs=50.0)).evaluate()
    assert s["halt"] is True and "engine_state_unreadable" in s["reasons"]
    assert s["unreadable_engines"] == ["engine_a.json"]
    assert json.loads((tmp_path / "supervisor.json").read_text())["halt"] is True


def test_a_corrupt_engine_beside_a_good_one_halts_and_its_money_is_never_summed(tmp_path):
    now = time.time()
    _write(tmp_path, "a", {**BASE, "ts": now, "equity": 1000.0})
    _write(tmp_path, "b", {**BASE, "account": "b", "ts": now, "equity": math.nan, "open_lots": 0.5})
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] and "engine_state_unreadable" in s["reasons"] and s["unreadable_engines"] == ["engine_b.json"]
    assert s["combined_equity"] == 1000.0 and math.isfinite(s["drawdown"])


def test_optional_engine_fields_may_still_be_absent(tmp_path):
    _write(tmp_path, "a", {"account": "a", "ts": time.time(), "equity": 1000.0, "day_start_equity": 1000.0,
                           "week_start_equity": 1000.0, "balance_closed_hwm": 1000.0, "currency": "GBP",
                           "daily_loss_limit_abs": None})
    s = Supervisor(tmp_path).evaluate()
    assert s["halt"] is False and s["reasons"] == []


def test_an_unexpected_supervisor_error_writes_a_named_halt_and_the_loop_lives_on(tmp_path):
    sup = Supervisor(tmp_path)
    beats: list[int] = []

    class HB:
        def beat(self) -> None:
            beats.append(1)

    def boom() -> dict:
        raise RuntimeError("bug")
    sup.evaluate = boom                                                # type: ignore[method-assign]
    s = ops_run.supervisor_pass(sup, HB())
    assert s["halt"] is True and s["reasons"] == ["supervisor_error"]
    assert Supervisor.engine_should_halt(tmp_path) == (True, "supervisor_error")
    assert beats == [1]


def test_a_healthy_supervisor_pass_returns_the_evaluation(tmp_path):
    _write(tmp_path, "a", {**BASE, "ts": time.time(), "equity": 1000.0})
    s = ops_run.supervisor_pass(Supervisor(tmp_path), type("HB", (), {"beat": lambda self: None})())
    assert s["halt"] is False
