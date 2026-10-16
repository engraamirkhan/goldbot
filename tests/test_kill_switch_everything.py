"""Trading-safety finding F1 (16 Oct): the kill switch's close-everything choice is state, not one read's argument.

Design (Drawdown kill switch): at 12% drawdown every position on the account is closed at market. A failure on the
tripping read (the approval-mode write raising on a full disk, a positions() read timing out) must not narrow the
retries to this engine's own magic range: `_kill_everything` is set before any I/O, saved with the risk state, used by
every retry and after a restart, and cleared only once the whole account has been seen flat."""
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk.gate import Stage
from goldbot.telegram.approvals import ApprovalCenter
from goldbot.telegram.bus import ApprovalBus

T0 = pd.Timestamp("2025-03-05 10:01", tz="UTC")
OWN_MAGIC, OTHER_MAGIC = 260150, 7


def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


class _FailingModeBus(ApprovalBus):
    """The owner's control file is readable but the mode write raises, as on a full disk."""

    def set_mode(self, *a: Any, **k: Any) -> Any:
        raise OSError("disk full")


def _engine(tmp_path: Path, pb: PaperBroker, bus: ApprovalBus | None = None) -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111,
                               halt_checks=False), pb, [], {}, ApprovalCenter({111}, bus=bus))


def _open(pb: PaperBroker, cid: str, magic: int, lots: float = 0.5) -> int:
    r = pb.place_order(OrderIntent(client_order_id=cid, symbol="XAUUSD", side=1, lots=lots, sl=2300.0, tp=2500.0,
                                   magic=magic, comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


def _book(tmp_path: Path, bus: ApprovalBus | None = None) -> tuple[Engine, PaperBroker, int]:
    """An engine at its 10,000 high-water mark holding its own trade and one outside its range (a manual trade or
    another engine's); returns the other position's id."""
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _engine(tmp_path, pb, bus)
    eng._refresh_account(_tick(T0))
    _open(pb, "own", OWN_MAGIC, lots=1.0)
    other = _open(pb, "other", OTHER_MAGIC)
    return eng, pb, other


def _crash(pb: PaperBroker, s: float = 1) -> Tick:
    """The price falls far enough that balance plus open P&L corroborates a >12% drawdown: the read trips at once."""
    pb.on_tick(_tick(T0 + pd.Timedelta(seconds=s), 2385.0))
    t = pb.last_tick("XAUUSD")
    assert t is not None
    return t


def _fail_positions_once_when_halted(monkeypatch: Any, eng: Engine, pb: PaperBroker) -> list[int]:
    """positions() times out once, on the kill switch's read (the bridge), then reads normally."""
    real, fails = pb.positions, [1]

    def flaky() -> Any:
        if eng.state.stage == Stage.HALTED and fails:
            fails.pop()
            raise TimeoutError("bridge timeout")
        return real()

    monkeypatch.setattr(pb, "positions", flaky)
    return fails


def _ids(pb: PaperBroker) -> list[int]:
    return [p.position_id for p in pb.positions()]


def test_a_failed_approval_mode_write_on_the_trip_still_closes_the_other_magic_position(tmp_path):
    """Scenario A: auto mode on, the disk full when the 12% drawdown trips: set_mode raises. The flatten runs first,
    the failure is a tick error, and the in-memory mode is propose, so auto entries stay blocked."""
    bus = _FailingModeBus(tmp_path / "bus")
    ApprovalBus(tmp_path / "bus").set_mode("auto", by="dashboard")
    eng, pb, other = _book(tmp_path, bus)
    eng._refresh_account(_crash(pb))                                   # never raises
    assert eng.state.stage == Stage.HALTED
    assert other not in _ids(pb) and pb.positions() == []
    assert eng.cfg.approval_mode == "propose" and eng.state.tick_error


def test_a_failed_approval_mode_write_is_retried_and_never_narrows_later_refreshes(tmp_path, monkeypatch):
    """Scenario A with the flatten also interrupted: the retry on the next refresh still closes the whole account."""
    bus = _FailingModeBus(tmp_path / "bus")
    ApprovalBus(tmp_path / "bus").set_mode("auto", by="dashboard")
    eng, pb, other = _book(tmp_path, bus)
    _fail_positions_once_when_halted(monkeypatch, eng, pb)
    eng._refresh_account(_crash(pb))
    assert eng.state.stage == Stage.HALTED and other in _ids(pb) and eng._kill_pending
    eng._refresh_account(_crash(pb, 2))                                # a later refresh while halted
    assert other not in _ids(pb) and pb.positions() == []


def test_a_failed_positions_read_on_the_trip_still_closes_the_other_magic_position_on_the_next_refresh(tmp_path,
                                                                                                      monkeypatch):
    """Scenario B: one positions() read fails (a bridge timeout) on the tripping read; the retry is not narrowed."""
    eng, pb, other = _book(tmp_path)
    fails = _fail_positions_once_when_halted(monkeypatch, eng, pb)
    eng._refresh_account(_crash(pb))
    assert eng.state.stage == Stage.HALTED and not fails and other in _ids(pb)
    assert eng._kill_pending and eng._kill_everything
    eng._refresh_account(_crash(pb, 2))
    assert other not in _ids(pb) and pb.positions() == []
    assert not eng._kill_pending and not eng._kill_everything         # the whole account seen flat: cleared


def test_a_failed_positions_read_on_the_trip_still_closes_the_other_magic_position_on_the_next_tick(tmp_path,
                                                                                                   monkeypatch):
    """Scenario B through the tick path (`_kill_pending` on every tick), not only the account refresh."""
    eng, pb, other = _book(tmp_path)
    _fail_positions_once_when_halted(monkeypatch, eng, pb)
    eng._refresh_account(_crash(pb))
    assert other in _ids(pb)
    eng.on_tick(_crash(pb, 2))
    assert other not in _ids(pb) and pb.positions() == []


def test_after_a_restart_a_saved_close_everything_still_closes_the_whole_account(tmp_path, monkeypatch):
    """Restart safety: the choice is saved with the risk state, so a new process retries the whole account."""
    eng, pb, other = _book(tmp_path)
    _fail_positions_once_when_halted(monkeypatch, eng, pb)
    eng._refresh_account(_crash(pb))
    assert other in _ids(pb)
    again = _engine(tmp_path, pb)
    assert again.state.stage == Stage.HALTED and again._kill_everything and again._kill_pending
    again._refresh_account(_crash(pb, 2))
    assert other not in _ids(pb) and pb.positions() == []


def test_once_the_whole_account_was_seen_flat_later_halted_refreshes_close_only_this_engines_book(tmp_path):
    """The choice clears once the whole account is flat (and the clear is saved): a trade the owner opens by hand
    while halted is left alone, as before, and a restart does not close it either."""
    eng, pb, _ = _book(tmp_path)
    eng._refresh_account(_crash(pb))
    assert pb.positions() == [] and not eng._kill_everything
    manual = _open(pb, "manual", OTHER_MAGIC, lots=0.1)
    eng._refresh_account(_crash(pb, 2))
    assert _ids(pb) == [manual]
    again = _engine(tmp_path, pb)
    assert not again._kill_everything
    again._refresh_account(_crash(pb, 3))
    assert _ids(pb) == [manual]


def test_a_failed_mode_write_on_a_flat_trip_is_retried_after_a_restart(tmp_path):
    """Re-review LOW (A10): the trip found the account flat (`kill_everything` saved false), the mode write failed and
    the process restarted. The halted engine still owes the write: the bus shows propose after the first refresh, so
    auto never comes back after a re-arm without new evidence."""
    ApprovalBus(tmp_path / "bus").set_mode("auto", by="dashboard")
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    eng = _engine(tmp_path, pb, _FailingModeBus(tmp_path / "bus"))
    eng._refresh_account(_tick(T0))
    _open(pb, "own", OWN_MAGIC, lots=1.0)
    eng._refresh_account(_crash(pb))
    assert eng.state.stage == Stage.HALTED and pb.positions() == [] and not eng._kill_everything
    assert ApprovalBus(tmp_path / "bus").control().approval_mode == "auto"     # the write failed
    again = _engine(tmp_path, pb, ApprovalBus(tmp_path / "bus"))               # restart; the disk has room again
    again._refresh_account(_crash(pb, 2))
    assert ApprovalBus(tmp_path / "bus").control().approval_mode == "propose"
