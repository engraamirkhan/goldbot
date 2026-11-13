"""MFE and MAE on every closed exploration trade (research-director ruling 2026-11-13, lane F item 4): the closed
`exploration_trades` body carries `mfe_r`/`mae_r` (of the initial stop distance) and `mfe_usd`/`mae_usd` (USD per oz,
like `stop_usd`), from the fill to the exit, taken from the tick track the engine already keeps for its exits
(`Engine._track_excursions`, `closed_record.closed_excursion`). Reporting only: no exit changes. Rows written before
it have none of the fields and still load."""
from __future__ import annotations

import pandas as pd
import pytest

from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.execution.broker import Tick
from tests.test_exploration_engine import CLOSE, _at, _fresh_arm, _stopped_at_the_broker


def _closed_row(tmp_path, moves: tuple[float, ...]) -> tuple[ExplorationRecord, float, float]:
    eng, pb = _fresh_arm(tmp_path)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [(pid, tr)] = list(eng.open.items())
    entry, stop = float(tr.open_price or 0.0), float(tr.initial_sl or 0.0)
    for k, d in enumerate(moves):                              # the exit-side price the tick track folds (long: bid)
        eng._track_excursions(Tick(ts_utc=CLOSE + pd.Timedelta(seconds=10 + k), bid=entry + d, ask=entry + d + 0.2))
    _stopped_at_the_broker(eng, pb)
    eng._flush_closed()
    [row] = [r for r in exploration.trades(str(tmp_path)) if r.position_id == pid]
    return row, entry, stop


def test_a_closed_exploration_trade_records_its_mfe_and_mae_in_r_and_usd_from_fill_to_exit(tmp_path) -> None:
    row, entry, stop = _closed_row(tmp_path, (1.5, -0.5, 2.0))
    risk = entry - stop
    assert row.exit_utc is not None and row.exit_price is not None
    assert row.mfe_usd == pytest.approx(2.0)                   # the best tick before the stop
    assert row.mae_usd == pytest.approx(row.exit_price - entry)   # the stop fill is the worst price seen
    assert row.mfe_r == pytest.approx(2.0 / risk)
    assert row.mae_r == pytest.approx((row.exit_price - entry) / risk)
    assert row.mae_r is not None and row.mfe_r is not None and row.mae_r <= -0.9 < 0 < row.mfe_r
    assert row.excursion_approx is False                       # every price came from the tick track


def test_the_excursion_never_changes_the_trades_exit(tmp_path) -> None:
    with_track, _, _ = _closed_row(tmp_path / "a", (1.5, -0.5, 2.0))
    without, _, _ = _closed_row(tmp_path / "b", ())
    for f in ("exit_utc", "exit_price", "exit_reason", "pnl", "censored"):
        assert getattr(with_track, f) == getattr(without, f), f


def test_a_ledger_row_written_before_the_fields_existed_still_loads_with_them_empty() -> None:
    old = {"client_order_id": "x", "account_id": "icm-demo", "arm_id": "S0", "family": "random_bracket", "tf": "1m",
           "side": 1, "lots": 0.01, "signal_utc": "2026-11-01T10:00:00Z", "sent_utc": "2026-11-01T10:00:01Z",
           "atr_usd": 3.0, "stop_usd": 3.0, "target_usd": 3.0, "hold_s": 1800, "exit_utc": "2026-11-01T10:20:00Z"}
    r = ExplorationRecord.model_validate(old)
    assert (r.mfe_r, r.mae_r, r.mfe_usd, r.mae_usd) == (None, None, None, None)
