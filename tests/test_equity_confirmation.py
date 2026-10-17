"""BACKLOG 44 (ADR 0016): a zero or collapsed equity read is confirmed before the 12% kill switch. A stage-2 read
trips at once when the balance plus the open P&L agrees with it, else on the next account read (forced on the next
tick); one disagreeing read never trips the kill switch, two consecutive stage-2 reads always do."""
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import AccountInfo, OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import Intent, RiskGate
from goldbot.risk.gate import AccountState, RiskLimits, Stage
from goldbot.telegram.approvals import ApprovalCenter

T0 = pd.Timestamp("2025-03-05 10:01", tz="UTC")


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _engine(tmp_path: Path) -> tuple[Engine, PaperBroker]:
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                              halt_checks=False), pb, [], {}, ApprovalCenter({111}))
    eng._refresh_account(_tick(T0))               # high-water mark 10,000
    assert eng._account_currency() == "USD"       # read once and kept: every later account() read is a refresh's
    return eng, pb


def _halted(eng: Engine) -> bool:
    """The stage read fresh (a helper, so mypy does not narrow it across the refreshes that change it)."""
    return eng.state.stage == Stage.HALTED


def _open(pb: PaperBroker, cid: str, lots: float = 1.0) -> int:
    r = pb.place_order(OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=lots, sl=2300.0, tp=2500.0,
                                   magic=260150, comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


class _Reads:
    """`account()` reads scripted by equity (None = the broker's own, consistent read); the balance stays the broker's."""

    def __init__(self, pb: PaperBroker, *equities: float | None):
        self.real, self.queue = pb.account, list(equities)

    def __call__(self) -> AccountInfo:
        acc = self.real()
        eq = self.queue.pop(0) if self.queue else None
        return acc if eq is None else acc.model_copy(update={"equity": eq})


def _script(monkeypatch: Any, pb: PaperBroker, *equities: float | None) -> None:
    monkeypatch.setattr(pb, "account", _Reads(pb, *equities))


def _intent() -> Intent:
    return Intent(agent_id="session_open-x", side=1, p=0.62, target_atr=1.5, stop_atr=1.0, atr_usd=4.0, cost_atr=0.1,
                  multiplier=1.0, price=2400.2)


# ---------------------------------------------------------------------------------------------- one bad read
def _read(eng: Engine, s: float) -> None:
    """One account read, `s` seconds after T0 on the tick clock (the price stays 2400.0)."""
    eng._refresh_account(_tick(T0 + pd.Timedelta(seconds=s)))


@pytest.mark.parametrize("equity", [0.0, 50.0, 8_700.0])
def test_a_single_zero_or_collapsed_equity_read_never_closes_a_position(tmp_path, monkeypatch, equity):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, equity)                                  # balance 10,000 + open P&L says ~10,000
    _read(eng, 1)
    assert not _halted(eng) and not eng._kill_pending
    assert [p.position_id for p in pb.positions()] == [pid]
    assert eng.state.equity_unconfirmed
    _read(eng, 2)                                                     # the next read is the broker's real one
    assert eng.state.stage == Stage.NORMAL and [p.position_id for p in pb.positions()] == [pid]


def test_an_unconfirmed_equity_read_refuses_entries_and_does_not_move_the_stage(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 50.0)
    _read(eng, 1)
    assert eng.state.stage == Stage.NORMAL                            # not even size-down on one bad read
    d = eng.gate.check(_intent(), eng.state)
    assert not d.allowed and "equity_unconfirmed" in d.reasons
    assert eng.state.stage == Stage.NORMAL                            # the gate's own stage run respects it too


# ---------------------------------------------------------------------------------------------- sticky pending
def test_zone_then_normal_then_zone_trips_the_kill_switch(tmp_path, monkeypatch):
    """Review HIGH-1: alternating readings must not hold the kill switch off. A pending 12% read stays pending
    through normal reads until the recovery is sustained, so the next 12% read confirms it."""
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 0.0, None, 0.0)
    _read(eng, 1)
    _read(eng, 11)
    assert not _halted(eng) and eng.state.equity_unconfirmed          # entries stay refused while pending
    _read(eng, 21)
    assert _halted(eng) and pb.positions() == []


def test_alternating_readings_cannot_hold_the_kill_switch_off(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, *[0.0, None] * 10)
    reads = 0
    while not _halted(eng) and reads < 20:
        _read(eng, 1 + 10 * reads)
        reads += 1
    assert _halted(eng) and reads == 3 and pb.positions() == []


def test_zone_then_sustained_recovery_below_the_hysteresis_then_zone_needs_fresh_confirmation(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 0.0, *[None] * 9, 0.0)
    _read(eng, 0)                                                     # pending
    for s in range(1, 82, 10):                                        # clean reads from 1 s to 81 s: 80 s >= 60 s
        _read(eng, s)
    assert not eng.state.equity_unconfirmed                           # recovered: entries allowed again
    _read(eng, 90)                                                    # a new glitch: one read, unconfirmed again
    assert not _halted(eng) and eng.state.equity_unconfirmed and [p.position_id for p in pb.positions()] == [pid]


def test_a_reading_inside_the_hysteresis_band_restarts_the_recovery_clock(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    band = 10_000 * (1 - 0.11)                                        # 11%: below 12%, above 12% - 2%
    _script(monkeypatch, pb, 0.0, None, None, None, band, None, None, None, None, 0.0)
    for s in (0, 10, 30, 50, 55, 60, 80, 100, 114, 115):              # 40 s clean, band at 55 s, then 54 s clean
        _read(eng, s)
    assert _halted(eng) and pb.positions() == []


def test_a_pending_twelve_percent_read_survives_a_restart(tmp_path, monkeypatch):
    """Review LOW-4: a crash loop between the two reads must not defeat confirmation."""
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 0.0)
    _read(eng, 1)
    again = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                                halt_checks=False), pb, [], {}, ApprovalCenter({111}))
    assert again._account_currency() == "USD"
    _script(monkeypatch, pb, 0.0)
    again._refresh_account(_tick(T0 + pd.Timedelta(seconds=30)))
    assert _halted(again) and pb.positions() == []


# ---------------------------------------------------------------------------------------------- confirmed reads
def test_two_consecutive_real_twelve_percent_reads_close_everything(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a")
    _open(pb, "b", lots=0.5)
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=1), 2392.0))           # -$1,200 on $10k... and swap the read can't see
    real = pb.account().equity
    assert real <= 8_800
    _script(monkeypatch, pb, real - 300.0, real - 300.0)              # disagree with balance + open P&L by $300
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert not _halted(eng) and len(pb.positions()) == 2   # first read: awaiting the second
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert _halted(eng) and pb.positions() == []


def test_two_consecutive_zero_equity_reads_trip_the_kill_switch(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 0.0, 0.0)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert _halted(eng) and pb.positions() == []


def test_a_crash_read_that_balance_plus_open_pnl_corroborates_trips_on_the_first_read(tmp_path):
    eng, pb = _engine(tmp_path)
    _open(pb, "a")
    _open(pb, "b", lots=0.5)
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=1), 2392.0))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert _halted(eng) and pb.positions() == []


def test_an_unreadable_account_book_cannot_corroborate_so_the_second_read_confirms(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a")
    _open(pb, "b", lots=0.5)
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=1), 2392.0))
    real_positions = pb.account_positions

    def broken() -> Any:
        raise RuntimeError("positions_get failed")

    monkeypatch.setattr(pb, "account_positions", broken)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert not _halted(eng) and eng.state.equity_unconfirmed
    monkeypatch.setattr(pb, "account_positions", real_positions)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert _halted(eng) and pb.positions() == []


def test_a_genuine_fast_crash_is_confirmed_on_the_very_next_tick_not_after_the_state_interval(tmp_path, monkeypatch):
    """ADR 0016 bound: an uncorroborated stage-2 read forces the account read on the next tick (normally one every
    state_every_s = 10 s), so the confirmation costs at most one tick."""
    eng, pb = _engine(tmp_path)
    t = T0.floor("15min") + pd.Timedelta(minutes=1)
    eng.on_tick(_tick(t, 2400.0))
    _open(pb, "big")
    _open(pb, "more", lots=0.5)
    crash = t + pd.Timedelta(seconds=11)                              # the state interval is due: account read
    pb.on_tick(_tick(crash, 2391.0))
    real = pb.account().equity
    _script(monkeypatch, pb, real - 300.0)                            # swap or another symbol: no corroboration
    eng.on_tick(_tick(crash, 2391.0))
    assert not _halted(eng) and len(pb.positions()) == 2
    eng.on_tick(_tick(crash + pd.Timedelta(seconds=1), 2390.9))       # 1 s later, well inside state_every_s
    assert _halted(eng) and pb.positions() == []


# ---------------------------------------------------------------------------------------------- gate and settings
def test_the_stage_machine_does_not_run_on_an_unconfirmed_equity_read():
    st = AccountState(equity=1.0, balance_closed_hwm=10_000, day_start_equity=10_000, week_start_equity=10_000,
                      open_positions=0, margin_used=0, last_tick_age_s=0, spread_points=20, equity_unconfirmed=True)
    assert RiskGate().update_stage(st) == Stage.NORMAL
    st.equity_unconfirmed = False
    assert RiskGate().update_stage(st) == Stage.HALTED


def test_the_equity_cross_check_tolerance_comes_from_settings():
    s = load_settings()
    assert s.risk.equity_check_tolerance == pytest.approx(0.02)
    assert RiskLimits.from_settings(s.risk, tiny_live=False).equity_check_tolerance == pytest.approx(0.02)


# ---------------------------------------------------------------------------------------------- review MEDIUM-2
def _crash(pb: PaperBroker) -> float:
    _open(pb, "a")
    _open(pb, "b", lots=0.5)
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=1), 2392.0))
    return pb.account().equity


def test_swap_on_the_open_positions_corroborates_a_crash_read_on_the_first_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    real = _crash(pb)
    _script(monkeypatch, pb, real - 300.0)                            # the terminal's equity includes -$300 swap
    book = [p.model_copy(update={"swap": -150.0}) for p in pb.positions()]
    monkeypatch.setattr(pb, "account_positions", lambda: book)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert _halted(eng) and pb.positions() == []


def test_a_position_on_another_symbol_corroborates_a_crash_read_on_the_first_read(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    real = _crash(pb)
    _script(monkeypatch, pb, real - 300.0)
    other = pb.positions()[0].model_copy(update={"position_id": 999, "symbol": "EURUSD", "profit": -300.0})
    monkeypatch.setattr(pb, "account_positions", lambda: [*PaperBroker.positions(pb), other])
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert _halted(eng)


def test_an_unconfirmed_read_at_a_bar_close_is_read_again_on_the_next_tick(tmp_path, monkeypatch):
    """Review LOW-3: the bar-close branch does not re-read in the same tick (milliseconds later the terminal returns
    the same glitch); the next tick, which is never another bar close, takes the re-read branch."""
    eng, pb = _engine(tmp_path)
    t = T0.floor("15min") + pd.Timedelta(minutes=1)
    eng.on_tick(_tick(t, 2400.0))
    _open(pb, "a", lots=0.1)
    reads: list[pd.Timestamp] = []
    real_refresh = eng._refresh_account

    def counting(tick: Tick) -> None:
        reads.append(tick.ts_utc)
        real_refresh(tick)

    def bar_close(close: pd.Timestamp, tick: Tick) -> list:
        eng._refresh_account(tick)
        return []

    monkeypatch.setattr(eng, "_refresh_account", counting)
    monkeypatch.setattr(eng, "on_bar_close", bar_close)
    _script(monkeypatch, pb, 0.0)
    close = t.floor("15min") + pd.Timedelta(minutes=15, seconds=1)
    eng.on_tick(_tick(close, 2400.0))                                 # a bar close: the read is unconfirmed
    assert eng.state.equity_unconfirmed and reads == [close]
    eng.on_tick(_tick(close + pd.Timedelta(seconds=1), 2400.0))       # inside state_every_s: re-read anyway
    assert reads == [close, close + pd.Timedelta(seconds=1)]


def test_the_mt5_adapter_reads_swap_and_every_symbol_for_the_account_book(monkeypatch):
    from collections import namedtuple
    from types import SimpleNamespace

    from goldbot.execution import mt5_adapter

    P = namedtuple("P", "ticket symbol type volume price_open sl tp magic comment time_msc profit swap")
    book = [P(1, "XAUUSD", 0, 0.1, 2400.0, 2390.0, 2420.0, 260150, "", 1_741_168_800_000, -12.0, -3.5),
            P(2, "EURUSD", 1, 0.2, 1.08, 0.0, 0.0, 7, "", 1_741_168_800_000, 4.0, -0.5)]
    calls: list[dict] = []

    def positions_get(**kw: Any) -> list:
        calls.append(kw)
        return [p for p in book if "symbol" not in kw or p.symbol == kw["symbol"]]

    fake = SimpleNamespace(positions_get=positions_get, POSITION_TYPE_BUY=0, last_error=lambda: (1, "e"))
    monkeypatch.setattr(mt5_adapter, "mt5", fake)
    b = object.__new__(mt5_adapter.MT5Broker)
    b.symbol, b.server_tz = "XAUUSD", "Europe/Athens"
    assert [(p.symbol, p.swap) for p in b.positions()] == [("XAUUSD", -3.5)]
    assert [(p.symbol, p.profit, p.swap) for p in b.account_positions()] == [("XAUUSD", -12.0, -3.5),
                                                                           ("EURUSD", 4.0, -0.5)]
    assert calls[-1] == {}                                            # no symbol filter


def test_a_position_without_swap_reads_as_none_so_an_older_bridge_still_decodes():
    from goldbot.base import load_record
    from goldbot.execution.broker import Position

    old = {"position_id": 1, "symbol": "XAUUSD", "side": 1, "lots": 0.1, "open_price": 2400.0, "sl": 2390.0,
           "tp": None, "magic": 1, "comment": "", "open_time_utc": T0, "profit": 0.0}
    assert load_record(Position, old, source="test").swap is None


def test_paper_positions_carry_no_swap_and_its_account_book_is_its_positions(tmp_path):
    _, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    assert [p.swap for p in pb.account_positions()] == [None]
    assert pb.account_positions() == pb.positions()


# ---------------------------------------------------------------------------------------------- re-review: failed saves
def _disk_full(monkeypatch: Any) -> None:
    """Every durable state write (risk, orders, engine state) raises, as on a full disk."""
    import goldbot.engine.store as store

    def fail(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "write_atomic", fail)


def _no_book(monkeypatch: Any, pb: PaperBroker) -> None:
    """An account book the cross-check cannot read (a v4 bridge): a crash read is confirmed only by a second read."""
    def missing() -> Any:
        raise RuntimeError("v4 bridge")

    monkeypatch.setattr(pb, "account_positions", missing)


def _spy_kill(monkeypatch: Any, eng: Engine) -> list[bool]:
    runs: list[bool] = []
    real = eng._kill_switch

    def spy(*, everything: bool) -> bool:
        runs.append(everything)
        return real(everything=everything)

    monkeypatch.setattr(eng, "_kill_switch", spy)
    return runs


def _foreign(pb: PaperBroker) -> int:
    """A position outside this engine's magic range (another engine, or the owner by hand)."""
    r = pb.place_order(OrderIntent(client_order_id="other", symbol="XAUUSD", side=1, lots=0.1, sl=2300.0, tp=2500.0,
                                   magic=7, comment="other"))
    assert r.ok and r.position_id is not None
    return r.position_id


def test_a_failed_risk_state_save_never_stops_an_uncorroborated_crash_from_tripping_the_kill_switch(tmp_path,
                                                                                                     monkeypatch):
    """Re-review MEDIUM-1: the pending 12% read is decided in memory; a save that raises (disk full) is logged, blocks
    entries (`tick_error`) and never aborts the refresh before the stage machine runs."""
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _foreign(pb)
    _no_book(monkeypatch, pb)
    _script(monkeypatch, pb, 8_000.0, 8_000.0)                         # a real-looking 20% crash, uncorroborated
    _disk_full(monkeypatch)
    runs = _spy_kill(monkeypatch, eng)
    _read(eng, 1)                                                     # never raises
    assert not _halted(eng) and eng._stage2_pending_since is not None and eng.state.tick_error
    _read(eng, 2)
    assert _halted(eng) and runs == [True] and pb.positions() == []  # the whole account flattened
    # BACKLOG 62 (C1): the account book stays unreadable, so the gold fallback was closed and the flag stays set
    assert eng._kill_pending and eng._kill_everything


def test_a_failed_save_on_the_halt_still_closes_the_whole_account_on_that_read(tmp_path, monkeypatch):
    """Re-review LOW-2: the halt's save raising must neither skip the flatten on the tripping read nor narrow the
    next one to this engine's book."""
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _foreign(pb)
    _no_book(monkeypatch, pb)
    _script(monkeypatch, pb, 8_000.0, 8_000.0)
    _read(eng, 1)                                                     # pending, saved normally
    _disk_full(monkeypatch)
    runs = _spy_kill(monkeypatch, eng)
    _read(eng, 2)                                                     # the trip; its save raises
    assert _halted(eng) and runs == [True] and pb.positions() == [] and eng.state.tick_error


def test_a_failed_save_of_a_confirmed_high_water_mark_rise_never_aborts_the_refresh(tmp_path, monkeypatch):
    """Re-review LOW-2 (ADR 0018): the raise's save raising is logged and blocks entries; the mark is raised in
    memory and the stage machine still runs on the same read."""
    eng, pb = _engine(tmp_path)
    real = pb.account
    monkeypatch.setattr(pb, "account", lambda: real().model_copy(update={"balance": 10_500.0, "equity": 10_500.0}))
    _disk_full(monkeypatch)
    stages: list[Stage] = []
    real_update = eng.gate.update_stage

    def spy(st: AccountState) -> Stage:
        stages.append(st.stage)
        return real_update(st)

    monkeypatch.setattr(eng.gate, "update_stage", spy)
    _read(eng, 1)                                                     # paper reads are exact: one read raises
    assert eng.state.balance_closed_hwm == 10_500 and stages and eng.state.tick_error
