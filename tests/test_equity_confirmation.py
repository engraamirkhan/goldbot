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
@pytest.mark.parametrize("equity", [0.0, 50.0, 8_700.0])
def test_a_single_zero_or_collapsed_equity_read_never_closes_a_position(tmp_path, monkeypatch, equity):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, equity)                                  # balance 10,000 + open P&L says ~10,000
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert not _halted(eng) and not eng._kill_pending
    assert [p.position_id for p in pb.positions()] == [pid]
    assert eng.state.equity_unconfirmed
    eng._refresh_account(pb.last_tick("XAUUSD"))                      # the next read is the broker's real one
    assert eng.state.stage == Stage.NORMAL and not eng.state.equity_unconfirmed
    assert [p.position_id for p in pb.positions()] == [pid]


def test_an_unconfirmed_equity_read_refuses_entries_and_does_not_move_the_stage(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 50.0)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.state.stage == Stage.NORMAL                            # not even size-down on one bad read
    d = eng.gate.check(_intent(), eng.state)
    assert not d.allowed and "equity_unconfirmed" in d.reasons
    assert eng.state.stage == Stage.NORMAL                            # the gate's own stage run respects it too


def test_a_disagreeing_read_between_two_stage_two_reads_resets_the_confirmation(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    pid = _open(pb, "a", lots=0.1)
    _script(monkeypatch, pb, 0.0, None, 0.0)
    for _ in range(3):
        eng._refresh_account(pb.last_tick("XAUUSD"))
    assert not _halted(eng) and [p.position_id for p in pb.positions()] == [pid]


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


def test_an_unreadable_book_cannot_corroborate_so_the_second_read_confirms(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _open(pb, "a")
    _open(pb, "b", lots=0.5)
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=1), 2392.0))
    real_positions = pb.positions

    def broken() -> Any:
        raise RuntimeError("positions_get failed")

    monkeypatch.setattr(pb, "positions", broken)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert not _halted(eng) and eng.state.equity_unconfirmed
    monkeypatch.setattr(pb, "positions", real_positions)
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
