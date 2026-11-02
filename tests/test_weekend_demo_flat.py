"""The Friday cut on a demo learning host with `demo_weekend_flat` (owner 2026-11-02: "fix the limits of our system to
trade ... for the system to be not bound to test, learn and improve signals"; the demo-limits ADR). Entries may hold up
to the cut, so at the cut every own position closes, winners too (`weekend_flat`, a censored label), a refused close
is retried every tick, and the gate's account-level `weekend` reason still blocks entries until the week rolls. Every
account without the flag keeps the standing rule (losers closed, winners tightened)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from goldbot.engine import exploration
from goldbot.execution.broker import Tick
from tests.test_invalid_feed import CUT, _open_ids, _winner_before_the_cut


def _at_the_cut(tmp_path: Any, flat: bool, fail: int = 0) -> tuple[Any, Any, list[pd.Timestamp], int]:
    eng, pb, host, pid = _winner_before_the_cut(tmp_path / ("flat" if flat else "plain"))
    eng.cfg = eng.cfg.model_copy(update={"demo_weekend_flat": flat})
    pb.fail = fail
    host[0] = CUT + pd.Timedelta(seconds=2)
    pb.on_tick(Tick(ts_utc=host[0], bid=2410.0, ask=2410.3))
    eng._refresh_account(pb.last_tick("XAUUSD"))
    return eng, pb, host, pid


def test_with_demo_weekend_flat_a_winner_is_closed_at_the_cut_and_the_gate_blocks_the_weekend(tmp_path) -> None:
    eng, pb, _, pid = _at_the_cut(tmp_path, flat=True)
    assert pid not in _open_ids(pb) and eng.state.weekend
    assert not any(d.get("action") == "weekend_tighten" for d in eng.decisions)
    assert "weekend" in eng.gate.account_reasons(eng.state)


def test_without_demo_weekend_flat_a_winner_is_kept_and_tightened(tmp_path) -> None:
    eng, pb, _, pid = _at_the_cut(tmp_path, flat=False)
    assert pid in _open_ids(pb)
    assert any(d.get("action") == "weekend_tighten" for d in eng.decisions)


def test_a_refused_flat_close_is_retried_every_tick_until_it_fills(tmp_path) -> None:
    eng, pb, host, pid = _at_the_cut(tmp_path, flat=True, fail=2)
    assert pid in _open_ids(pb) and pid in eng._close_due                  # refused once, decided
    for i in range(3):
        host[0] = host[0] + pd.Timedelta(seconds=1)
        tick = Tick(ts_utc=host[0], bid=2410.0, ask=2410.3)
        pb.on_tick(tick)
        eng.on_tick(tick)
    assert pid not in _open_ids(pb) and pb.calls >= 3


def test_the_flat_close_censors_an_exploration_label() -> None:
    arm = exploration.Arm(spec=exploration.SpecArm(id="X1", rule="random_bracket", timeframe="15m", role="control",
                                                   params={"stop_usd": 5.0, "target_r": 2.0, "hold_bars": 4}),
                          style="intraday", control_share=0.1)
    tr = type("T", (), {"initial_sl": 2395.0, "sl": 2395.0, "side": 1})()
    assert exploration._censored(arm, "weekend_flat", tr)
