"""Trading-safety finding F1 (16 Oct): the kill switch's close-everything choice is state, not one read's argument.

Design (Drawdown kill switch): at 12% drawdown every position on the account is closed at market. A failure on the
tripping read (the approval-mode write raising on a full disk, a positions() read timing out) must not narrow the
retries to this engine's own magic range: `_kill_everything` is set before any I/O, saved with the risk state, used by
every retry and after a restart, and cleared only once the whole account has been seen flat.

BACKLOG 62 (ADR 0016 amendment): "close everything" is every position on the account, every symbol and every magic.
While `_kill_everything` is set the switch and the close retries read `account_positions()`; an unreadable account
book falls back to `positions()` (the engine's symbol, every magic: never fewer closes than before) and never clears
the flag. The flag clears only on a successful `account_positions()` read that returns nothing (trading-safety C1, C2).
Fake tickets only; EURUSD stands for any other symbol (a manual trade)."""
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.execution.broker import OrderIntent, OrderResult, Tick
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
    """The bridge times out on the kill switch's first read: account_positions() and the positions() fallback both
    fail once, then read normally."""
    real, real_all, fails = pb.positions, pb.account_positions, [1]

    def flaky_all() -> Any:
        if eng.state.stage == Stage.HALTED and fails:
            raise TimeoutError("bridge timeout")
        return real_all()

    def flaky() -> Any:
        if eng.state.stage == Stage.HALTED and fails:
            fails.pop()
            raise TimeoutError("bridge timeout")
        return real()

    monkeypatch.setattr(pb, "account_positions", flaky_all)
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
    assert eng._kill_pending and eng._kill_everything                 # C2: every close ok, the account not read flat yet
    eng._refresh_account(_crash(pb, 3))
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
    assert pb.positions() == [] and eng._kill_everything              # C2: closes ok is not a flat read
    eng.on_tick(_crash(pb, 1.5))                                       # the next read sees the account flat
    assert not eng._kill_everything and not eng._kill_pending
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
    eng.on_tick(_crash(pb, 1.5))                                               # C2: the next read sees the account flat
    assert eng.state.stage == Stage.HALTED and pb.positions() == [] and not eng._kill_everything
    assert ApprovalBus(tmp_path / "bus").control().approval_mode == "auto"     # the write failed
    again = _engine(tmp_path, pb, ApprovalBus(tmp_path / "bus"))               # restart; the disk has room again
    again._refresh_account(_crash(pb, 2))
    assert ApprovalBus(tmp_path / "bus").control().approval_mode == "propose"


# ------------------------------------------------------------------ BACKLOG 62: every symbol on the account
def _eur(t: pd.Timestamp, bid: float = 1.0800) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.0001)


def _open_eur(pb: PaperBroker, cid: str = "manual-eur", magic: int = OTHER_MAGIC, lots: float = 0.3) -> int:
    r = pb.place_order(OrderIntent(client_order_id=cid, symbol="EURUSD", side=1, lots=lots, sl=1.0, tp=1.2,
                                   magic=magic, comment=cid))
    assert r.ok and r.position_id is not None
    return r.position_id


def _everything(tmp_path: Path, bus: ApprovalBus | None = None) -> tuple[Engine, PaperBroker, int, int, int]:
    """An engine at its 10,000 high-water mark; on the account: its own XAUUSD trade, an XAUUSD trade outside its range
    and a manual EURUSD trade. Returns (engine, broker, own, other, eur)."""
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(T0))
    pb.set_quote("EURUSD", _eur(T0))
    eng = _engine(tmp_path, pb, bus)
    eng._refresh_account(_tick(T0))
    own = _open(pb, "own", OWN_MAGIC, lots=1.0)
    other = _open(pb, "other", OTHER_MAGIC)
    return eng, pb, own, other, _open_eur(pb)


def _all_ids(pb: PaperBroker) -> list[int]:
    return sorted(p.position_id for p in PaperBroker.account_positions(pb))   # the real book, even when patched


def _refuse(monkeypatch: Any, pb: PaperBroker, pid: int, retcode: int, times: int = 1) -> list[int]:
    """The first `times` closes of `pid` are refused with `retcode`; returns the list of attempts on `pid`."""
    real, attempts = pb.close, []

    def close(position_id: int, lots: float | None = None) -> OrderResult:
        if position_id == pid:
            attempts.append(position_id)
            if len(attempts) <= times:
                return OrderResult(ok=False, retcode=retcode, order_id=None, position_id=pid, filled_lots=0.0,
                                   price=None, message="refused")
        return real(position_id, lots)

    monkeypatch.setattr(pb, "close", close)
    return attempts


def _account_book_fails_when_halted(monkeypatch: Any, eng: Engine, pb: PaperBroker, times: int | None) -> None:
    """account_positions() raises while halted (an older bridge, a terminal fault), `times` times or always (None)."""
    left = [times]

    def flaky() -> Any:
        if eng.state.stage == Stage.HALTED and (left[0] is None or left[0] > 0):
            if left[0] is not None:
                left[0] -= 1
            raise TimeoutError("bridge timeout")
        return PaperBroker.account_positions(pb)

    monkeypatch.setattr(pb, "account_positions", flaky)


def test_the_trip_closes_a_eurusd_and_an_xauusd_position(tmp_path):
    eng, pb, own, other, eur = _everything(tmp_path)
    eng._refresh_account(_crash(pb))
    assert eng.state.stage == Stage.HALTED
    assert _all_ids(pb) == []


def test_a_refused_eurusd_close_is_retried_and_does_not_block_the_xauusd_close(tmp_path, monkeypatch):
    eng, pb, own, other, eur = _everything(tmp_path)
    attempts = _refuse(monkeypatch, pb, eur, 10004)                    # a requote: retried CLOSE_RETRY_MIN_S later
    eng._refresh_account(_crash(pb))
    assert _all_ids(pb) == [eur] and eur in eng._close_due and eng._kill_everything
    eng.on_tick(_crash(pb, 3))
    assert attempts == [eur, eur] and _all_ids(pb) == []
    assert not eng._kill_everything and not eng._kill_pending          # cleared on the flat account read


def test_a_due_eurusd_close_is_not_dropped_by_a_gold_only_retry_read(tmp_path, monkeypatch):
    """Trading-safety C3: `_retry_closes` read `positions()` (the engine's symbol only) and dropped every due close it
    could not see, so a refused EURUSD close was forgotten. While the flag is set it reads the account; a close is
    dropped only when a whole-account read no longer lists it, never on the gold-only fallback."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, eur, 10004)
    eng._refresh_account(_crash(pb))
    # the EURUSD close is due: refused on the trip (the base engine never tried it, so it is set up as due here)
    eng._close_due.setdefault(eur, ("kill_switch_close", {}, T0))
    _account_book_fails_when_halted(monkeypatch, eng, pb, times=1)
    eng._retry_closes(T0 + pd.Timedelta(seconds=5))                    # the account read fails: positions() only
    assert eur in eng._close_due and eur in _all_ids(pb)
    eng._retry_closes(T0 + pd.Timedelta(seconds=7))
    assert _all_ids(pb) == [] and eur not in eng._close_due


def test_a_due_xauusd_close_gone_from_the_fallback_read_is_dropped(tmp_path, monkeypatch):
    """The fallback (positions()) lists every position of the engine's symbol, so a due XAUUSD close it no longer
    lists (closed at the broker meanwhile) is left to `_sweep_closed` as before, not retried for ever."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, other, 10004)
    _account_book_fails_when_halted(monkeypatch, eng, pb, times=None)
    eng._refresh_account(_crash(pb))
    assert other in eng._close_due
    PaperBroker.close(pb, other)                                       # its stop filled at the broker
    eng._retry_closes(T0 + pd.Timedelta(seconds=5))
    assert other not in eng._close_due


def test_a_hidden_eurusd_close_is_backed_off_one_row_per_step_not_per_tick(tmp_path, monkeypatch):
    """Trading-safety LOW 1: a symbol the terminal cannot quote (hidden, delisted) is refused 10021 on every attempt.
    For another symbol that is backed off like a market-closed refusal (same table), so the journal gets one row per
    backoff step, not one per tick; the XAUUSD closes are not held back by it."""
    eng, pb, own, other, eur = _everything(tmp_path)
    attempts = _refuse(monkeypatch, pb, eur, 10021, times=10_000)
    eng.on_tick(_crash(pb))                                            # the trip, on the tick clock
    assert _all_ids(pb) == [eur] and eur in eng._close_backoff         # both XAUUSD positions closed
    for s in range(2, 102, 2):                                         # 100 s of ticks, 2 s apart
        eng.on_tick(_crash(pb, s))
    rows = [d for d in eng.decisions if d.get("action") == "kill_switch_close" and d["position"] == eur]
    # attempts at 1 s, 32 s (after 30 s of backoff) and 92 s (after 60 s): three steps, not 50 ticks
    assert len(rows) == len(attempts) == 3 and eng._kill_everything


def test_an_xauusd_close_refused_10021_is_not_backed_off(tmp_path, monkeypatch):
    """The engine's own symbol keeps the transient retry (CLOSE_RETRY_MIN_S): a gold quote gap is seconds long."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, own, 10021)
    eng.on_tick(_crash(pb))
    assert own in eng._close_due and own not in eng._close_backoff
    eng.on_tick(_crash(pb, 3))
    assert own not in _all_ids(pb)


def test_every_due_close_records_its_symbol(tmp_path, monkeypatch):
    """Trading-safety LOW 4: every `_close_due` entry names its symbol, not only the kill switch's."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, own, 10004)
    eng._try_close(own, "time_exit", T0)
    assert eng._close_due[own][1]["symbol"] == "XAUUSD"


def test_a_due_close_without_a_recorded_symbol_is_kept_on_the_fallback_read(tmp_path, monkeypatch):
    """The fallback drop rule never assumes the engine's symbol: an entry that names none waits for an account read."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _account_book_fails_when_halted(monkeypatch, eng, pb, times=None)
    eng._refresh_account(_crash(pb))
    eng._close_due[eur] = ("kill_switch_close", {}, T0)
    eng._retry_closes(T0 + pd.Timedelta(seconds=5))
    assert eur in eng._close_due


def test_the_flag_stays_set_until_account_positions_reads_flat(tmp_path, monkeypatch):
    """Trading-safety C2: positions() flat is not the account flat, and every close returning ok is not a flat read."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, eur, 10018)                               # market closed: backed off
    eng.on_tick(_crash(pb))                                            # the trip, on the tick clock
    assert eng.state.stage == Stage.HALTED and pb.positions() == [] and _all_ids(pb) == [eur]
    eng.on_tick(_crash(pb, 2))
    assert eng._kill_everything and eng._kill_pending
    eng.on_tick(_crash(pb, 2 + eng.cfg.reconcile_every_s + 1))         # after the backoff the close goes through
    assert _all_ids(pb) == []
    assert not eng._kill_everything and not eng._kill_pending


def test_an_all_ok_run_keeps_the_switch_pending_for_one_more_read(tmp_path):
    eng, pb, *_ = _everything(tmp_path)
    eng._refresh_account(_crash(pb))
    assert _all_ids(pb) == [] and eng._kill_everything and eng._kill_pending
    eng.on_tick(_crash(pb, 2))
    assert not eng._kill_everything and not eng._kill_pending


def test_an_unreadable_account_book_closes_what_positions_returns_and_stays_pending(tmp_path, monkeypatch):
    """Trading-safety C1: what to close and whether the flag may clear are separate. The fallback book (positions(),
    the engine's symbol) is closed in full, any magic, and never clears the flag; the failure is logged once."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _account_book_fails_when_halted(monkeypatch, eng, pb, times=None)
    eng._refresh_account(_crash(pb))
    assert eng.state.stage == Stage.HALTED
    assert _all_ids(pb) == [eur]                                       # own and the OTHER_MAGIC XAUUSD trade closed
    eng.on_tick(_crash(pb, 2))
    eng.on_tick(_crash(pb, 3))
    assert eng._kill_everything and eng._kill_pending                  # a fallback read never clears the flag
    rows = [d for d in eng.decisions if d.get("action") == "book_unreadable" and d.get("read") == "account_positions"]
    assert len(rows) == 1                                              # once per streak


def test_after_a_restart_a_saved_flag_still_closes_the_eurusd_position(tmp_path, monkeypatch):
    eng, pb, own, other, eur = _everything(tmp_path)
    _account_book_fails_when_halted(monkeypatch, eng, pb, times=1)
    eng._refresh_account(_crash(pb))
    assert _all_ids(pb) == [eur]
    again = _engine(tmp_path, pb)
    assert again._kill_everything and again._kill_pending
    again._refresh_account(_crash(pb, 2))
    assert _all_ids(pb) == []


def test_after_the_account_is_flat_halted_refreshes_close_only_this_engines_magic(tmp_path):
    eng, pb, *_ = _everything(tmp_path)
    eng._refresh_account(_crash(pb))
    eng.on_tick(_crash(pb, 2))
    assert not eng._kill_everything
    manual_eur, manual_gold = _open_eur(pb, "m-eur"), _open(pb, "m-gold", OTHER_MAGIC, lots=0.1)
    _open(pb, "ours-again", OWN_MAGIC, lots=0.1)
    eng._refresh_account(_crash(pb, 3))
    eng.on_tick(_crash(pb, 5))
    assert _all_ids(pb) == sorted([manual_eur, manual_gold])


def test_a_market_closed_eurusd_close_backs_off_while_the_xauusd_close_goes_through(tmp_path, monkeypatch):
    eng, pb, own, other, eur = _everything(tmp_path)
    attempts = _refuse(monkeypatch, pb, eur, 10018)
    eng.on_tick(_crash(pb))                                            # the trip, on the tick clock
    assert _all_ids(pb) == [eur] and eur in eng._close_backoff
    eng.on_tick(_crash(pb, 3))                                         # inside the backoff: not re-sent
    assert attempts == [eur]
    eng.on_tick(_crash(pb, 3 + eng.cfg.reconcile_every_s))             # the backoff over: re-sent and closed
    assert attempts == [eur, eur] and _all_ids(pb) == []


def test_a_due_eurusd_close_is_retried_through_the_no_quote_pass(tmp_path, monkeypatch):
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, eur, 10004)
    eng._refresh_account(_crash(pb))
    assert _all_ids(pb) == [eur]
    eng.cfg.live_clock, eng.clock = True, lambda: T0 + pd.Timedelta(seconds=30)
    eng.no_quote_pass()                                                # our feed is invalid; the close is still sent
    assert _all_ids(pb) == [] and not eng._kill_everything


def test_an_auto_mode_trip_with_account_positions_raising_still_resets_to_propose(tmp_path, monkeypatch):
    ApprovalBus(tmp_path / "bus").set_mode("auto", by="dashboard")
    eng, pb, *_ = _everything(tmp_path, ApprovalBus(tmp_path / "bus"))
    _account_book_fails_when_halted(monkeypatch, eng, pb, times=None)
    eng._refresh_account(_crash(pb))
    assert eng.state.stage == Stage.HALTED and eng._kill_everything
    assert ApprovalBus(tmp_path / "bus").control().approval_mode == "propose" and eng.cfg.approval_mode == "propose"


def test_every_kill_switch_close_row_carries_symbol_magic_and_lots(tmp_path, monkeypatch):
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, eur, 10004)
    eng._refresh_account(_crash(pb))
    eng.on_tick(_crash(pb, 3))                                         # the retry row too
    rows = [d for d in eng.decisions if d.get("action") == "kill_switch_close"]
    assert len(rows) == 4
    assert {(d["position"], d["symbol"], d["magic"], d["lots"]) for d in rows} == {
        (own, "XAUUSD", OWN_MAGIC, 1.0), (other, "XAUUSD", OTHER_MAGIC, 0.5), (eur, "EURUSD", OTHER_MAGIC, 0.3)}


def test_open_positions_counts_the_engine_symbol_of_every_magic_and_not_other_symbols(tmp_path, monkeypatch):
    """Trading-safety C7: the same count as `_refresh_account` (positions(): the engine's symbol, any magic)."""
    eng, pb, own, other, eur = _everything(tmp_path)
    _refuse(monkeypatch, pb, other, 10018)
    _refuse(monkeypatch, pb, eur, 10018)
    eng._refresh_account(_crash(pb))
    assert _all_ids(pb) == sorted([other, eur]) and eng.state.open_positions == 1
