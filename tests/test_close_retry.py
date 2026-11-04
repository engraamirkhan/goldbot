"""Close retries (live 2026-11-04, vantage-demo): 3,087 closes refused with retcode 10013 on five scheduled cost probes,
about 600 tries over 14.5 min each. Each probe had closed at the broker on its own stop or target within 2-18 s, but
its 30 s hold close was sent again on every pass: the retry dropped the due close of a position the broker no longer
listed, and the hold timer re-sent it on the next pass, until the bar close's sweep recorded the trade.

Design rules (engine/close_retry.py): a due close whose position the broker no longer lists is never sent again; the
trade is recorded from the broker's exit deal at once, or (no deal yet) kept for the sweep without a close. A close
refused while the position is still open keeps being sent: every CLOSE_RETRY_MIN_S for CLOSE_FREE_TRIES refusals,
then backed off, doubling up to CLOSE_RETRY_MAX_S, one warning per backed-off refusal. Exits are never stopped."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.engine import close_retry
from goldbot.execution.broker import OrderResult
from tests.test_probe_scheduled import _rows, _sent


def _counted_closes(pb: Any, refuse: bool = False) -> list[int]:
    sent: list[int] = []
    real = pb.close

    def close(pid: int, lots: float | None = None) -> OrderResult:
        sent.append(pid)
        if refuse:
            return OrderResult(ok=False, retcode=10013, order_id=None, position_id=pid, filled_lots=0.0, price=None,
                               message="invalid request")
        return real(pid, lots)
    pb.close = close
    return sent


def _passes(eng: Any, start: pd.Timestamp, seconds: int, step: float = 0.5) -> None:
    for k in range(int(seconds / step)):
        eng._retry_closes(start + pd.Timedelta(seconds=k * step))


def test_a_probe_its_stop_closed_at_the_broker_is_recorded_from_the_deal_and_its_hold_close_is_not_sent_again(
        tmp_path: Path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    pos = pb._positions[pid]
    assert pos.sl is not None
    pb._close_at(pos, pos.sl, "stop")                        # the broker's stop took it 2 s after the fill
    sent = _counted_closes(pb)
    t0 = eng.probe.held[pid] + pd.Timedelta(seconds=30)     # type: ignore[union-attr]
    _passes(eng, t0, 14 * 60)                               # until the next slot, as on vantage-demo
    eng._flush_closed()
    assert len(sent) == 1, f"{len(sent)} closes sent to a position the broker no longer lists"
    assert pid not in eng.open and pid not in eng._close_due
    [rec] = _rows(tmp_path)
    assert rec.exit_utc is not None and rec.pnl is not None   # recorded from the broker's exit deal


def test_a_gone_position_whose_exit_deal_is_not_seen_yet_gets_no_close_and_is_kept_for_the_sweep(tmp_path: Path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    del pb._positions[pid]                                  # gone, its deal not in the history yet
    sent = _counted_closes(pb)
    t0 = eng.probe.held[pid] + pd.Timedelta(seconds=30)     # type: ignore[union-attr]
    _passes(eng, t0, 120)
    assert len(sent) == 1
    assert pid in eng.open and pid in eng._close_due        # the sweep's confirmation count records it, as before


def test_a_close_refused_while_the_position_is_open_keeps_being_sent_backed_off_and_logged(tmp_path: Path, caplog):
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    sent = _counted_closes(pb, refuse=True)
    t0 = eng.probe.held[pid] + pd.Timedelta(seconds=30)     # type: ignore[union-attr]
    at: list[pd.Timestamp] = []
    with caplog.at_level(logging.WARNING, logger="goldbot.engine"):
        for k in range(600):                                # 5 minutes of passes, every 0.5 s
            now = t0 + pd.Timedelta(seconds=k * 0.5)
            n = len(sent)
            eng._retry_closes(now)
            if len(sent) > n:
                at.append(now)
    gaps = [(b - a).total_seconds() for a, b in zip(at, at[1:])]
    assert close_retry.CLOSE_FREE_TRIES < len(sent) < 60, len(sent)
    assert max(gaps) <= close_retry.CLOSE_RETRY_MAX_S + 0.5            # never stopped, never further apart
    assert pid in pb._positions and pid in eng.open and pid in eng._close_due
    assert any("refused" in r.getMessage() and str(pid) in r.getMessage() for r in caplog.records)
    pb.close = type(pb).close.__get__(pb)                   # type: ignore[method-assign]  # accepts again
    _passes(eng, at[-1] + pd.Timedelta(seconds=close_retry.CLOSE_RETRY_MAX_S + 1), 2)
    assert pid not in pb._positions and pid not in eng.open


def _refusing(pb: Any, codes: list[int]) -> list[int]:
    """The broker refuses each close with the next retcode of `codes` (the last one repeated)."""
    sent: list[int] = []

    def close(pid: int, lots: float | None = None) -> OrderResult:
        sent.append(pid)
        code = codes[min(len(sent), len(codes)) - 1]
        return OrderResult(ok=False, retcode=code, order_id=None, position_id=pid, filled_lots=0.0, price=None,
                           message="refused")
    pb.close = close
    return sent


def _send_times(eng: Any, sent: list[int], start: pd.Timestamp, seconds: int) -> list[pd.Timestamp]:
    at: list[pd.Timestamp] = []
    for k in range(int(seconds / 0.5)):
        now = start + pd.Timedelta(seconds=k * 0.5)
        n = len(sent)
        eng._retry_closes(now)
        if len(sent) > n:
            at.append(now)
    return at


def test_a_refused_kill_close_is_never_more_than_the_protective_cap_apart_however_often_it_is_refused(tmp_path: Path):
    """Trading-safety review (MEDIUM): a kill, Friday cut, hard flat or engine stop close protects an open position;
    past 30 refusals it is backed off to at most CLOSE_PROTECT_MAX_S, never 30 s."""
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    sent = _refusing(pb, [10013])
    t0 = tick.ts_utc
    eng._try_close(pid, "kill_switch_close", t0)
    at = [t0, *_send_times(eng, sent, t0 + pd.Timedelta(seconds=1), 180)]
    gaps = [(b - a).total_seconds() for a, b in zip(at, at[1:])]
    assert len(sent) > close_retry.CLOSE_FREE_TRIES + 5
    assert max(gaps) <= close_retry.CLOSE_PROTECT_MAX_S + 0.5, max(gaps)
    assert pid in eng._close_due and pid in pb._positions


def test_a_refused_hold_close_of_a_position_through_its_stop_is_held_to_the_protective_cap(tmp_path: Path):
    """A scheduled exit backs off to 30 s only while the price is short of the engine's stop."""
    eng, pb, tick, pid = _sent(tmp_path)
    tr = eng.open[pid]
    assert tr.sl is not None
    through = tr.sl - tr.side * 1.0
    eng._last_tick = tick.model_copy(update={"bid": through, "ask": through + 0.3})
    sent = _refusing(pb, [10013])
    t0 = eng.probe.held[pid] + pd.Timedelta(seconds=30)     # type: ignore[union-attr]
    at = _send_times(eng, sent, t0, 180)
    gaps = [(b - a).total_seconds() for a, b in zip(at, at[1:])]
    assert len(sent) > close_retry.CLOSE_FREE_TRIES + 5
    assert max(gaps) <= close_retry.CLOSE_PROTECT_MAX_S + 0.5, max(gaps)


def test_a_market_closed_refusal_resets_the_refusals_in_a_row_and_keeps_its_own_count(tmp_path: Path):
    """Trading-safety review (LOW): the transient count and the market-closed backoff count are kept apart."""
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    _refusing(pb, [10013] * 10 + [10018, 10013])
    t = tick.ts_utc
    for k in range(10):
        eng._try_close(pid, "time_exit", t + pd.Timedelta(seconds=k))
    assert close_retry.refusals(eng, pid) == 10
    eng._try_close(pid, "time_exit", t + pd.Timedelta(seconds=10))     # market closed
    assert close_retry.refusals(eng, pid) == 0 and eng._close_backoff[pid][0] == 1
    eng._try_close(pid, "time_exit", t + pd.Timedelta(seconds=11))
    assert close_retry.refusals(eng, pid) == 1


def test_looking_again_at_a_gone_position_leaves_the_market_closed_count_alone(tmp_path: Path):
    eng, pb, tick, pid = _sent(tmp_path)
    eng._last_tick = tick
    del pb._positions[pid]
    eng._close_backoff[pid] = (3, tick.ts_utc)                # three market-closed refusals before it went
    eng._close_due[pid] = ("time_exit", {"symbol": eng.cfg.symbol}, tick.ts_utc)
    for k in range(1, 4):
        close_retry.gone(eng, pid, "time_exit", tick.ts_utc + pd.Timedelta(seconds=60 * k))
    assert eng._close_backoff[pid][0] == 3
