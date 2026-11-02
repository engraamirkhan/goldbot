"""The trading-safety review of #307 set the engine lane's wiring requirements E1-E7 (each a merge blocker). One test
(or more) per requirement, named for the behaviour it holds."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.calendar import session_profile
from goldbot.engine import Engine, EngineConfig, books
from goldbot.engine.books import SYMBOL_STATE, BookSpec, using
from goldbot.execution.broker import OrderResult, Position, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.instruments import load_instruments
from goldbot.risk import Intent
from goldbot.risk.gate import GateDecision, intent_terms

REG = load_instruments()
T0 = pd.Timestamp("2025-03-05 10:00", tz="UTC")            # a Wednesday
FRIDAY = pd.Timestamp("2025-03-07 17:30", tz="UTC")        # 19:30 Athens: past ags' 19:00 cut, before gold's 21:30


def _spec(key: str, block: int, **kw: Any) -> BookSpec:
    s = REG.get(key)
    sym = next(iter(s.broker_symbols.values()))
    return BookSpec(key=key, symbol=sym, store_symbol=s.store_symbol, magic_block=block, digits=2, point=0.01,
                    contract_size=100.0, instrument=s, **kw)


SILVER = _spec("XAGUSD", 1)
WHEAT = _spec("WHEAT", 26)


class MultiPaper(PaperBroker):
    def on_tick(self, tick: Tick) -> None:
        return None


def _engine(tmp_path: Path, pb: PaperBroker, specs: tuple[BookSpec, ...] = (SILVER,), **cfg: Any) -> Engine:
    return Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                               state_dir=str(tmp_path), owner_user_id=111, instruments=specs, **cfg), pb, [], {})


def _quote(pb: PaperBroker, sym: str, px: float, at: pd.Timestamp = T0) -> Tick:
    t = Tick(ts_utc=at, bid=px, ask=px + 0.02)
    pb.set_quote(sym, t)
    return t


def _intent(key: str, px: float = 30.0) -> Intent:
    return Intent(agent_id="explore:I0-" + key, side=1, p=0.5, target_atr=2.0, stop_atr=1.0, atr_usd=0.5,
                  cost_atr=0.1, multiplier=1.0, price=px, **intent_terms(REG.get(key)))


def _pos(pid: int, sym: str, magic: int, px: float, sl: float) -> Position:
    return Position(position_id=pid, symbol=sym, side=1, lots=0.1, open_price=px, sl=sl, tp=None, magic=magic,
                    comment="", open_time_utc=T0, profit=0.0)


# ------------------------------------------------------------------------------------------------ E1
def test_e1_bookings_survive_across_serial_checks_on_the_one_canonical_state(tmp_path):
    """A booking made on one book is on the account's one AccountState when the next book is checked; only the
    instrument facts (SYMBOL_STATE) differ per book, and the object the gate reads never changes."""
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    canonical = eng.state
    gold = _quote(pb, "XAUUSD", 2000.0)
    eng._last_tick = gold
    eng._refresh_account(gold)
    before = (canonical.open_positions, canonical.open_risk or 0.0)
    gd = GateDecision(allowed=True, lots=0.1, risk_amount=5.0, cost_amount=0.5, margin_needed=15.0)
    with using(eng, eng.books["XAGUSD"]):
        assert eng.state is canonical
        eng.state.spread_points = 7.0
        books.book(eng, _intent("XAGUSD"), gd, OrderResult(ok=True, retcode=10009, order_id=1, position_id=77,
                                                            filled_lots=0.1, price=30.0, message=""))
    assert eng.state is canonical
    assert canonical.open_positions == before[0] + 1 and canonical.open_risk == pytest.approx(before[1] + 5.5)
    assert canonical.margin_used >= 15.0 and canonical.unconfirmed_order
    assert canonical.spread_points != 7.0                              # gold's own spread back on the gold book
    seen: list[Any] = []

    def fake_check(intent: Any, st: Any, now: Any, **k: Any) -> Any:
        seen.append((st, st.open_positions))
        return GateDecision(allowed=False, reasons=["x"])
    setattr(eng.gate, "check", fake_check)
    books.check(eng, _intent("XAUUSD", 2000.0).model_copy(update={"symbol": "XAUUSD"}), T0)
    assert seen == [(canonical, before[0] + 1)]                        # the next symbol's check sees the booking
    assert "in_rollover" not in SYMBOL_STATE and "tick_error" not in SYMBOL_STATE   # account-wide: stricter


# ------------------------------------------------------------------------------------------------ E2
def test_e2_an_entry_is_booked_only_after_its_send_and_entries_wait_until_a_refresh_sees_it(tmp_path):
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    gd = GateDecision(allowed=True, lots=0.1, risk_amount=5.0)
    books.book(eng, _intent("XAGUSD"), gd, None)                        # not sent: nothing booked
    assert eng.state.open_positions == 0 and not eng.state.unconfirmed_order
    res = OrderResult(ok=True, retcode=10009, order_id=1, position_id=5, filled_lots=0.1, price=30.0, message="")
    books.book(eng, _intent("XAGUSD"), gd, res)
    eng._save_orders()                                                  # a later save keeps it set
    assert eng.state.unconfirmed_order and eng.state.open_positions == 1
    gold = _quote(pb, "XAUUSD", 2000.0)
    eng._last_tick = gold
    eng._refresh_account(gold)                                          # the broker does not list it yet
    assert eng.state.unconfirmed_order
    pb._positions[5] = _pos(5, "XAGUSD", 3_050_100 + 95, 30.0, 29.0)
    with using(eng, eng.books["XAGUSD"]):
        eng._last_tick = _quote(pb, "XAGUSD", 30.0)
    eng._refresh_account(gold)                                          # seen: settled
    assert not eng.state.unconfirmed_order


# ------------------------------------------------------------------------------------------------ E3
def test_e3_exits_ignore_the_profile_blackouts_and_group_caps(tmp_path):
    """Silver in a blackout, past its profile's hours, with the account's exposure unknown: its engine stop still
    closes at market, and the book's exits read the broker's hours, not the profile's."""
    pb = MultiPaper(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb)
    pb._positions[9] = _pos(9, "XAGUSD", 3_050_100 + 95, 30.0, 29.0)
    with using(eng, eng.books["XAGUSD"]):
        eng._reconcile(T0)
        tr = eng.open[9]
        tr.sl = 29.9                                                    # the engine's stop, tighter than the broker's
        st = eng.state
        st.in_blackout, st.market_closed, st.exposure_unknown, st.weekend = True, True, True, True
        t = _quote(pb, "XAGUSD", 29.85)
        eng._stop_check(t)
        assert 9 not in eng.open and not pb.positions(symbol="XAGUSD")
    profile = session_profile("metals", "Europe/Athens").model_copy(update={"intersect_broker": True})
    with using(eng, eng.books["XAGUSD"]):
        eng.set_sessions(profile)
        assert not eng.sessions.intersect_broker and eng.book.entry_sessions is profile


# ------------------------------------------------------------------------------------------------ E4
def test_e4_the_account_file_publishes_group_notional_and_null_when_unknown(tmp_path):
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    gold = _quote(pb, "XAUUSD", 2000.0)
    pb._positions[1] = _pos(1, "XAUUSD", 260595, 2000.0, 1990.0)
    eng._last_tick = gold
    eng._refresh_account(gold)
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert st["group_notional"] == {"gold": pytest.approx(0.1 * 100 * 2000.01)} and st["exposure_unknown"] is False
    assert st["open_lots"] == pytest.approx(0.1) and st["open_notional"] == pytest.approx(0.1 * 100 * 2000.01)
    assert eng.gate.limits.multi_symbol and eng.state.other_group_notional == {}
    with using(eng, eng.books["XAGUSD"]):
        pb._positions[2] = _pos(2, "XAGUSD", 3_050_195, 30.0, 29.0)    # silver has no quote yet: cannot be valued
    eng._refresh_account(gold)
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert st["group_notional"] is None and st["exposure_unknown"] is True
    assert "exposure_unknown" in eng.gate.account_reasons(eng.state)


# ------------------------------------------------------------------------------------------------ E5
def test_e5_an_intent_for_another_instrument_than_the_book_sends_is_refused(tmp_path):
    eng = _engine(tmp_path, MultiPaper(equity=10_000))
    called: list[Any] = []
    def fake_check(*a: Any, **k: Any) -> GateDecision:
        called.append(a)
        return GateDecision(allowed=True)
    setattr(eng.gate, "check", fake_check)
    with using(eng, eng.books["XAGUSD"]):
        bare = _intent("XAGUSD").model_copy(update={"symbol": "XAUUSD"})   # no terms: reads as gold
        assert books.check(eng, bare, T0).reasons == ["symbol_mismatch"] and not called
        assert books.check(eng, _intent("XAGUSD"), T0).allowed
    assert books.check(eng, _intent("XAGUSD"), T0).reasons == ["symbol_mismatch"]   # silver's intent on gold's book


def test_e5_a_books_session_fallback_is_its_own_profile_never_the_default(tmp_path):
    eng = _engine(tmp_path, MultiPaper(equity=10_000), specs=(WHEAT,))
    with using(eng, eng.books["Wheat-C"]):
        assert eng.sessions.friday_cut == session_profile("ags").friday_cut
        assert eng.cfg.weekend_cut == "19:00"


# ------------------------------------------------------------------------------------------------ E6
def test_e6_a_symbols_friday_cut_closes_its_losers_at_its_own_time_and_retries_a_refused_close(tmp_path):
    """Wheat's cut is 19:00 server (gold's 21:30): at 19:30 its loser is closed with no approval; a close the broker
    refuses stays due and is sent again on the next pass (`_retry_closes`)."""
    pb = MultiPaper(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = _engine(tmp_path, pb, specs=(WHEAT,))
    pb._positions[4] = _pos(4, "Wheat-C", 3_050_000 + 26 * 100 + 95, 30.0, 25.0)
    refusals = [OrderResult(ok=False, retcode=10004, order_id=None, position_id=4, filled_lots=0.0, price=None,
                            message="requote")]
    real_close = pb.close

    def close(pid: int, lots: float | None = None) -> OrderResult:
        return refusals.pop() if refusals else real_close(pid, lots)
    setattr(pb, "close", close)
    with using(eng, eng.books["Wheat-C"]):
        eng._reconcile(FRIDAY)
        t = _quote(pb, "Wheat-C", 29.0, FRIDAY)                         # a loser
        eng._last_tick = t
        eng._server_clock(FRIDAY, t)
        assert 4 in eng._close_due and pb.positions(symbol="Wheat-C")   # refused once: still due
        eng._retry_closes(FRIDAY + pd.Timedelta(seconds=5))
        assert not pb.positions(symbol="Wheat-C") and 4 not in eng._close_due
    assert eng.cfg.weekend_cut == "21:30"                               # gold's cut is its own


# ------------------------------------------------------------------------------------------------ E7
def test_e7_live_and_pilot_accounts_stay_xauusd_only(tmp_path):
    with pytest.raises(ValueError, match="demo accounts only"):
        Engine(EngineConfig(account_id="icm-live", broker_name="icm", mode="live", magic_base=260300,
                            state_dir=str(tmp_path), instruments=(SILVER,)), MultiPaper(), [], {})
    live = Engine(EngineConfig(account_id="icm-live", broker_name="icm", mode="live", magic_base=260300,
                               state_dir=str(tmp_path / "l")), MultiPaper(), [], {})
    assert list(live.books) == ["XAUUSD"] and not live.gate.limits.multi_symbol
    from goldbot.ops import accounts as acc_mod
    from goldbot.ops.services import engine as svc
    for acc in acc_mod.load_accounts(logins=False).values():
        if acc.mode != "demo" or acc.pilot.enabled:
            assert not acc.instruments and svc.account_instruments(acc) == [], acc.account_id


# ------------------------------------------------------------------------------------------------ L4's contract
def test_the_account_file_carries_symbols_groups_and_heat_for_the_dashboard(tmp_path):
    pb = MultiPaper(equity=10_000)
    eng = _engine(tmp_path, pb)
    gold = _quote(pb, "XAUUSD", 2000.0)
    pb._positions[1] = _pos(1, "XAUUSD", 260595, 2000.0, 1990.0)
    eng._last_tick = gold
    eng._refresh_account(gold)
    eng._write_state()
    st = json.loads((tmp_path / "engine_icm-probe.json").read_text())
    assert set(st["symbols"]) == {"XAUUSD", "XAGUSD"}
    assert st["symbols"]["XAGUSD"]["state"] in {"market_closed", "stale", "terms_missing"}
    assert set(st["symbols"]["XAUUSD"]) == {"state", "text", "clears_at", "price", "lot_value"}
    assert st["groups"]["gold"]["lots_by_symbol"] == {"XAUUSD": 0.1} and st["groups"]["gold"]["open_n"] == 1
    assert st["groups"]["gold"]["cap"] > 0 and "heat_used" in st and "heat_limit" in st


def test_an_entry_past_the_bridges_account_wide_ceiling_is_refused_before_it_is_sent(tmp_path):
    """The gate's per-group caps add up to far more than the bridge's 6x equity: the engine refuses past 0.95 of it
    (`account_notional_cap`), so the gate never approves an order the bridge then refuses."""
    eng = _engine(tmp_path, MultiPaper(equity=10_000))
    eng.state.equity = 10_000.0
    eng.state.group_notional = {"fx": 50_000.0}
    intent = _intent("XAGUSD").model_copy(update={"contract_oz": 5000.0, "account_currency": "USD",
                                                  "acct_per_usd": 1.0})
    assert books.ceiling(eng, intent, GateDecision(allowed=True, lots=1.0)).reasons == ["account_notional_cap"]
    assert books.ceiling(eng, intent, GateDecision(allowed=True, lots=0.01)).allowed
