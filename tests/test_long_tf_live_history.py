"""BACKLOG 254 (ADR 0081): 1d/1w live == history. The common filter judged at the entry close on every research path
(enforced in `arm_rules.long_tf_context`, quant condition 2026-11-14), the hold of a 1d/1w position counted from its
decision close (order id, time exit, `crosses_friday`), and the CONFIRM of record re-filled at the live entry close
with Friday 1d signals dropped (`research/long_confirm.py`)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import tf_seconds
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.engine import exploration, long_bars
from goldbot.research import arm_rules, long_confirm
from tests.test_engine_long_golive import EET_FRI, EET_TUE, _engine, _run
from tests.test_engine_long_tf import _arm, _history_frame

NO = np.zeros(0, dtype=np.int64)


@pytest.fixture(scope="module")
def eet() -> pd.DataFrame:
    """Winter minutes (EET), as tests/test_engine_long_golive.py's."""
    from goldbot.data.resample import ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    return ticks_to_1m(synthetic_ticks("2025-01-06", "2025-02-20", ticks_per_minute=1, seed=3)).reset_index(drop=True)


@pytest.fixture()
def hist(tmp_path: Path) -> Path:
    (tmp_path / "bars").mkdir()
    _history_frame("2023-01-02", pd.Timestamp("2025-01-10 21:00", tz="UTC")).to_parquet(
        tmp_path / "bars" / "xauusd_1d.parquet")
    return tmp_path


def _daily(closes: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"visible_at": closes, "bid_close": 1.0, "ask_close": 1.0})


# ------------------------------------------------------------------------------------- (a) the filter, enforced
def test_a_research_caller_that_omits_the_entry_filter_still_gets_a_1d_candidate_judged_at_its_entry() -> None:
    """A Tuesday 21:00 UTC 1d close sits in the rollover filter (20:45-22:15); its nominal entry (22:30) does not. A
    research path that builds its RuleContext without `filter_close` (run_arm_backtest, autodisc) must not drop it."""
    closes = pd.DatetimeIndex([EET_TUE])
    ctx = arm_rules.RuleContext(bars=_daily(closes), b1=_daily(closes), blackout_lo=NO, blackout_hi=NO)
    assert not arm_rules.common_filter_ok(ctx.filter_ts, NO, NO)[0]         # judged at the close: dropped
    got = arm_rules.long_tf_context(_arm("D1", "1d"), ctx)
    assert got.filter_ts.equals(long_bars.entry_after(closes, "1d")) and arm_rules.common_filter_ok(got.filter_ts, NO, NO)[0]
    cands = pd.DataFrame({"idx": [0], "side": [1], "risk_usd": [10.0], "target_r": [2.0], "max_bars": [1]})
    assert len(arm_rules.screened_frame(_arm("D1", "1d"), ctx, cands)[0]) == 1


def test_a_research_caller_that_passes_another_filter_for_a_1d_arm_is_refused() -> None:
    closes = pd.DatetimeIndex([EET_TUE])
    ctx = arm_rules.RuleContext(bars=_daily(closes), b1=_daily(closes), blackout_lo=NO, blackout_hi=NO,
                                filter_close=closes)
    with pytest.raises(ValueError, match="ADR 0081"):
        arm_rules.screened(_arm("D1", "1d"), ctx)


def test_an_intraday_arm_keeps_its_filter_at_the_close() -> None:
    closes = pd.DatetimeIndex([EET_TUE])
    ctx = arm_rules.RuleContext(bars=_daily(closes), b1=_daily(closes), blackout_lo=NO, blackout_hi=NO)
    assert arm_rules.long_tf_context(_arm("H1", "1h"), ctx) is ctx


# ------------------------------------------------------------------------------------- (b) the hold origin
def test_a_1d_hold_runs_to_the_history_exit_from_its_decision_close_not_from_the_send() -> None:
    send = pd.Timestamp("2025-02-11 23:30", tz="UTC")
    assert long_bars.hold_s("1d", 2, send, EET_TUE, send) == int((EET_TUE + pd.Timedelta(days=2) - send).total_seconds())
    assert long_bars.hold_s("4h", 2, send, EET_TUE, send) == 2 * tf_seconds("4h")      # intraday: unchanged


def test_a_1d_order_id_carries_its_decision_close_and_its_time_exit_is_the_history_exit() -> None:
    eng: Any = type("E", (), {"cfg": type("C", (), {"account_id": "t"})()})()
    arm = exploration.Arm(spec=_arm("D1", "1d"), style="swing", control_share=0.1)
    send = pd.Timestamp("2025-02-11 23:30", tz="UTC")
    p = exploration.Pending(arm_id="D1", side=1, bar_ts=send, stop_usd=10.0, target_usd=20.0, hold_bars=2,
                            decision_utc=EET_TUE)
    cid = exploration.pending_id(eng, arm, p)
    assert exploration.signal_close(cid) == EET_TUE
    max_bars = 2 * tf_seconds("1d") // tf_seconds("15m")                   # the engine's base-bar barrier
    end = pd.Timestamp("2025-02-13 21:00", tz="UTC")                          # Thursday: bar idx + 2's close
    assert not long_bars.time_exit_due("1d", cid, max_bars, tf_seconds("15m"), end - pd.Timedelta(minutes=1))
    assert long_bars.time_exit_due("1d", cid, max_bars, tf_seconds("15m"), end)
    assert not long_bars.time_exit_due("4h", cid, max_bars, tf_seconds("15m"), end + pd.Timedelta(days=9))


# ------------------------------------------------------------------------------------- live == history
@pytest.mark.parametrize(("tf", "start", "end"), [
    ("1d", EET_TUE - pd.Timedelta(days=1), EET_FRI + pd.Timedelta(hours=3)),            # Mon .. Fri
    ("1w", EET_FRI, EET_FRI + pd.Timedelta(days=3)),
])
def test_live_equals_history_for_a_1d_and_a_1w_arm(tf, start, end, eet, hist, db_dir) -> None:
    """The engine's signals (decision close, side, send close, time exit) equal the CONFIRM of record's on the same
    minutes and the same rule: decisions on the 21:00 UTC roll, the fill at the first 1m open at or after the live
    entry close, a Friday 1d signal never traded, the exit at the hold's close counted from the decision close."""
    arm = _arm("X", tf)
    eng = _engine(eet, [arm], hist, exploration.ledger_db(str(db_dir)))
    live = _run(eng, start, end)
    assert live
    assert all(p.decision_utc is not None for p in live)
    got_live = [(p.decision_utc, p.side, p.bar_ts, long_bars.hold_end(tf, p.decision_utc, p.hold_bars))  # type: ignore[arg-type]
                for p in live]

    bars = long_confirm.decision_bars(eet, tf)
    cands = arm_rules.candidates(arm, arm_rules.RuleContext(bars=bars, b1=eet, blackout_lo=NO, blackout_hi=NO))
    close = pd.DatetimeIndex(pd.to_datetime(bars["visible_at"], utc=True))
    at = close[cands["idx"].to_numpy()]
    cands = cands[np.asarray((at >= start) & (at <= end))].reset_index(drop=True)
    delays = long_confirm.entry_delays(tf, close[cands["idx"].to_numpy()], DEFAULT_SESSIONS)
    idx, side, mb = (cands[c].to_numpy(dtype=int) for c in ("idx", "side", "max_bars"))
    hist_rows = [(close[i], int(sd), close[i] + pd.Timedelta(seconds=float(dl)),
                  close[i + 1 + m] if i + 1 + m < len(close) else long_bars.hold_end(tf, close[i], m + 1))
                 for i, sd, m, dl in zip(idx, side, mb, delays) if np.isfinite(dl)]
    assert got_live == hist_rows
    if tf == "1d":                     # Monday to Thursday traded; Friday's close (its entry in the Friday filter) never
        assert [d.weekday() for d, *_ in hist_rows] == [0, 1, 2, 3]


@pytest.fixture()
def db_dir(tmp_path):
    return tmp_path / "state"


def test_the_confirm_of_record_drops_a_friday_1d_and_fills_after_the_reopen_in_eet_and_eest() -> None:
    closes = pd.DatetimeIndex([EET_TUE, EET_FRI, pd.Timestamp("2025-06-10 21:00", tz="UTC")])
    d = long_confirm.entry_delays("1d", closes)
    assert np.isnan(d[1])
    assert closes[0] + pd.Timedelta(seconds=d[0]) == pd.Timestamp("2025-02-11 23:30", tz="UTC")   # EET
    assert closes[2] + pd.Timedelta(seconds=d[2]) == pd.Timestamp("2025-06-10 22:30", tz="UTC")   # EEST
    w = long_confirm.entry_delays("1w", pd.DatetimeIndex([EET_FRI]))
    assert EET_FRI + pd.Timedelta(seconds=w[0]) == pd.Timestamp("2025-02-17 00:15", tz="UTC")
