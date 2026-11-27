"""Sub-day broker closures and old holes (vantage-demo 2026-10-26..11-27: a 61-open-minute hole 20:58-23:02 UTC in the
US/EU DST-gap week, the broker's daily break ~2 h, refused every 1h/4h close for weeks).

Design rules (engine/history.py): a clean broker-bar pull that reaches past a span and has no minute inside it marks
that sub-day span a broker closure (`covered`); covered spans are recorded in aux.db (`history_closures`) so a restart
keeps them; a hole wholly older than the fill floor (backfill_max_days) that no pull covered, with at most
history_stale_gap_max_min open minutes, is a closure for the refusal (logged once). A hole inside the fill window is
judged as before: a recent hole is never waved through."""
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import ConstantModel, Engine, EngineConfig, history
from goldbot.engine.history import open_gaps
from goldbot.execution.paper import PaperBroker
from goldbot.specialists import SPECIALISTS

POINT = 0.01
HOLE = (pd.Timestamp("2026-10-26 20:58", tz="UTC"), pd.Timestamp("2026-10-26 23:02", tz="UTC"))
TICKS = synthetic_ticks("2026-10-19", "2026-10-27 12:00", ticks_per_minute=1, seed=3)
HOLED = TICKS[(TICKS["ts_utc"] < HOLE[0]) | (TICKS["ts_utc"] >= HOLE[1])]
NOW = pd.Timestamp("2026-10-27 12:00", tz="UTC")


def _rates(ticks: pd.DataFrame) -> pd.DataFrame:
    b = ticks_to_1m(ticks, DEFAULT_SESSIONS)
    return pd.DataFrame({"ts_utc": b["ts_utc"], "open": b["bid_open"], "high": b["bid_high"], "low": b["bid_low"],
                         "close": b["bid_close"], "tick_count": b["tick_count"].astype(int),
                         "spread": ((b["ask_close"] - b["bid_close"]) / POINT).round().astype(int)})


class Broker:
    """No tick history (the terminal's ticks are short); copy_rates 1m holds `rates` (the latest n)."""

    def __init__(self, rates: pd.DataFrame | None):
        self.rates = rates

    def copy_ticks(self, symbol: str, since_utc: pd.Timestamp, n: int = 100_000) -> pd.DataFrame:
        return pd.DataFrame({"ts_utc": pd.DatetimeIndex([], tz="UTC"), "bid": [], "ask": []})

    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame:
        if self.rates is None:
            raise ConnectionError("bridge down")
        return self.rates.tail(n).reset_index(drop=True)

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(point=POINT)


def _pace(now: pd.Timestamp = NOW) -> history._Deadline:
    return history._Deadline(lambda: now, lambda s: None, pause_s=0, page_limit_s=30, budget_s=60)


def _pull(broker: Broker, span: tuple[pd.Timestamp, pd.Timestamp], keep: bool = True,
          now: pd.Timestamp = NOW) -> tuple[list[pd.DataFrame], list[tuple[pd.Timestamp, pd.Timestamp]], str | None]:
    pace = _pace(now)
    return history.pull_bars(broker, "XAUUSD", span, page_ticks=2000, pace=pace, keep=keep,
                             rates=history.BrokerRates(broker, "XAUUSD", pace))


def _engine(tmp_path: Path, **cfg: Any) -> Engine:
    spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    return Engine(EngineConfig(account_id="vantage-demo", broker_name="vantage", approval_mode="auto",
                               state_dir=str(tmp_path), data_root=str(tmp_path / "data"), backfill_page_ticks=2000,
                               **cfg),
                  PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)})


# ------------------------------------------------------------------------------------------- the pull marks it
def test_a_clean_bar_pull_with_no_minutes_in_a_sub_day_span_marks_it_a_broker_closure():
    """The pulled day has minutes, so the whole-day rule never vouched for the hole inside it: the 10-26 DST-gap-week
    break (20:58-23:02) the broker's own bars skip is covered by the pull that reached past it."""
    day = (pd.Timestamp("2026-10-26", tz="UTC"), pd.Timestamp("2026-10-27", tz="UTC"))
    bars, covered, err = _pull(Broker(_rates(HOLED)), day)
    assert err is None and bars and not any(((b["ts_utc"] >= HOLE[0]) & (b["ts_utc"] < HOLE[1])).any() for b in bars)
    assert covered == [HOLE]


def test_a_span_at_the_end_of_the_broker_bars_is_never_a_closure():
    """Recent data: minutes the broker has not delivered yet (its last bar before the span ends) are not a closure."""
    rates = _rates(TICKS[TICKS["ts_utc"] < HOLE[0]])
    _, covered, err = _pull(Broker(rates), (pd.Timestamp("2026-10-26", tz="UTC"), HOLE[0] + pd.Timedelta(hours=1)),
                            now=HOLE[0] + pd.Timedelta(hours=1))
    assert err is None and covered == []


def test_a_mid_session_hole_the_broker_bars_hold_is_filled_not_covered():
    hole = (pd.Timestamp("2026-10-26 13:00", tz="UTC"), pd.Timestamp("2026-10-26 15:00", tz="UTC"))
    bars, covered, err = _pull(Broker(_rates(TICKS)), hole)
    assert err is None and covered == [] and len(bars[0]) == 120


def test_a_failed_bar_pull_never_marks_a_sub_day_span():
    day = (pd.Timestamp("2026-10-26", tz="UTC"), pd.Timestamp("2026-10-27", tz="UTC"))
    for keep in (True, False):
        _, covered, _ = _pull(Broker(None), day, keep=keep)
        assert covered == [], keep


def test_a_run_inside_the_session_table_closure_alone_is_not_recorded():
    """The table's own closures need no cover: only a run with an open minute in it is marked."""
    day = (pd.Timestamp("2026-10-23", tz="UTC"), pd.Timestamp("2026-10-24", tz="UTC"))   # a whole, unholed day
    _, covered, err = _pull(Broker(_rates(HOLED)), day)
    assert err is None and covered == []


# ------------------------------------------------------------------------------------------- persisted
def _holed_store(tmp_path: Path) -> None:
    Store(tmp_path / "data").append("bars_1m", ticks_to_1m(HOLED), source="dukascopy")


def test_a_covered_closure_is_kept_by_a_restart(tmp_path):
    _holed_store(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(NOW, Broker(_rates(HOLED)))
    assert eng.history.fill is not None and eng.history.fill.done.wait(60)
    history.absorb(eng)
    assert history.window_gaps(eng, eng.bars_1m) == []
    again = _engine(tmp_path)
    again.warm_start(NOW, None)                               # no broker: nothing can be pulled again
    assert any(lo <= HOLE[0] and HOLE[1] <= hi for lo, hi in again.history.covered)
    assert history.window_gaps(again, again.bars_1m) == []


def test_without_the_record_the_restart_refuses_the_hole_again(tmp_path):
    """The record is what keeps it: a fresh state dir (no aux.db rows) and no broker judge the hole open."""
    _holed_store(tmp_path)
    eng = _engine(tmp_path)
    eng.warm_start(NOW, None)
    [g] = history.window_gaps(eng, eng.bars_1m)
    assert g.start == HOLE[0] and g.end == HOLE[1]


# ------------------------------------------------------------------------------------------- old holes
LATE = pd.Timestamp("2026-11-27 13:00", tz="UTC")


def _minutes(hole: tuple[pd.Timestamp, pd.Timestamp] = HOLE, end: pd.Timestamp = LATE) -> pd.DatetimeIndex:
    ts = pd.date_range(pd.Timestamp("2026-10-12", tz="UTC"), end, freq="min", inclusive="left")
    ts = ts[DEFAULT_SESSIONS.is_open(ts)]
    return ts[(ts < hole[0]) | (ts >= hole[1])]


def _refuse(eng: Engine, ts: pd.DatetimeIndex, close: pd.Timestamp, tf: str = "1h") -> list[dict] | None:
    gaps = history.window_gaps(eng, pd.DataFrame({"ts_utc": ts}))
    fr = SimpleNamespace(dec=pd.DataFrame({"ts_utc": [ts[0], ts[-1]]}))
    return history.refuse(eng, tf, fr, gaps, [], close)


def test_the_dst_gap_week_hole_older_than_the_fill_floor_no_longer_refuses_1h_closes(tmp_path, caplog):
    eng = _engine(tmp_path)
    ts = _minutes()
    assert open_gaps(ts, DEFAULT_SESSIONS, max_open_min=60)            # still a hole by the gap rule
    with caplog.at_level(logging.WARNING, logger="goldbot.engine.history"):
        assert _refuse(eng, ts, LATE) is None
        assert _refuse(eng, ts, LATE + pd.Timedelta(hours=1)) is None
    assert "1h" not in eng.history.gaps
    assert sum("older than the fill floor" in r.getMessage() for r in caplog.records) == 1     # logged once


def test_the_same_hole_inside_the_fill_window_is_still_refused(tmp_path):
    eng = _engine(tmp_path)
    end = pd.Timestamp("2026-10-28 13:00", tz="UTC")
    assert _refuse(eng, _minutes(end=end), end) is not None
    assert eng.history.gaps["1h"]["from"] == HOLE[0].isoformat()


def test_a_genuine_mid_session_hole_inside_the_fill_window_is_still_refused(tmp_path):
    eng = _engine(tmp_path)
    hole = (pd.Timestamp("2026-11-20 13:00", tz="UTC"), pd.Timestamp("2026-11-20 14:30", tz="UTC"))
    assert _refuse(eng, _minutes(hole), LATE) is not None
    assert eng.history.gaps["1h"]["open_min"] == 90


@pytest.mark.parametrize("cfg", [{"history_stale_gap_max_min": 0}, {"backfill_max_days": 0}])
def test_the_old_hole_rule_is_off_without_a_cap_or_a_fill_window(tmp_path, cfg):
    """backfill_max_days 0 would make every hole 'older than the floor': fail closed, the rule is off."""
    assert _refuse(_engine(tmp_path, **cfg), _minutes(), LATE) is not None


def test_an_old_hole_longer_than_the_cap_is_still_refused(tmp_path):
    eng = _engine(tmp_path)
    hole = (pd.Timestamp("2026-10-20 08:00", tz="UTC"), pd.Timestamp("2026-10-20 14:00", tz="UTC"))   # 360 open min
    assert _refuse(eng, _minutes(hole), LATE) is not None


def _cut(ts: pd.DatetimeIndex, *holes: tuple[pd.Timestamp, pd.Timestamp]) -> pd.DatetimeIndex:
    for lo, hi in holes:
        ts = ts[(ts < lo) | (ts >= hi)]
    return ts


H200A = (pd.Timestamp("2026-10-20 08:00", tz="UTC"), pd.Timestamp("2026-10-20 11:20", tz="UTC"))   # 200 open min
H200B = (pd.Timestamp("2026-10-21 08:00", tz="UTC"), pd.Timestamp("2026-10-21 11:20", tz="UTC"))


def test_one_old_200_minute_hole_in_a_4h_window_is_allowed(tmp_path):
    ts = _cut(_minutes((HOLE[1], HOLE[1])), H200A)
    assert _refuse(_engine(tmp_path), ts, LATE, "4h") is None


def test_old_holes_summing_past_the_cap_inside_one_window_are_refused(tmp_path):
    """Quant hardening on #508: each old hole is under the cap, but the cleared minutes the window decides on add up
    to more than history_stale_gap_max_min (2 x 200 > 240): refused."""
    eng = _engine(tmp_path)
    ts = _cut(_minutes((HOLE[1], HOLE[1])), H200A, H200B)
    assert _refuse(eng, ts, LATE, "4h") is not None
    assert eng.history.gaps["4h"]["open_min"] == 200
