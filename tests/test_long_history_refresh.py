"""BACKLOG 251 (BACKLOG 144 blocker 4): the 1d/1w long daily history kept current, checked before start, and a
held-back arm told to the owner. goldbot/ops/long_history.py, goldbot/ops/checks/long_history.py, engine/long_bars.py."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import GO_LIVE_BLOCKERS_1D_1W, load_settings
from goldbot.data.resample import BAR_COLUMNS
from goldbot.engine import long_bars
from goldbot.ops import health, long_history
from goldbot.ops.health import AccountRef
from goldbot.ops.jobs import JOBS
from tests.test_health import make_ctx, write

SETTINGS = load_settings()
FILE_COLUMNS = ["date", "ts_utc", "visible_at", "bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high",
                "ask_low", "ask_close", "open", "high", "low", "close", "spread_close", "spread_mean", "n_hours",
                "volume", "source"]


def minutes(start: str, end: str) -> pd.DataFrame:
    """Store-schema 1m bars on every weekday minute in [start, end), a steady rise."""
    ts = pd.date_range(start, end, freq="1min", inclusive="left", tz="UTC")
    ts = ts[ts.dayofweek < 5]
    px = 4000.0 + np.arange(len(ts)) * 0.001
    f: dict[str, Any] = {"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)}
    for side in ("bid", "ask"):
        for c in ("open", "high", "low", "close"):
            f[f"{side}_{c}"] = px
    f["tick_count"], f["spread_mean"], f["spread_max"] = 10, 0.2, 0.3
    return pd.DataFrame(f)[BAR_COLUMNS]


def history_file(path: Path, last_close: str, days: int = 300) -> None:
    """A research-shaped daily history: one row per weekday 21:00 UTC close up to `last_close`."""
    vis = pd.date_range(end=pd.Timestamp(last_close, tz="UTC"), periods=days * 2, freq="D")
    vis = vis[vis.dayofweek < 5][-days:]
    n = len(vis)
    d: dict[str, Any] = {"date": vis.tz_localize(None).normalize(), "ts_utc": vis - pd.Timedelta(hours=23),
                         "visible_at": vis, "spread_close": 0.3, "spread_mean": 0.3, "n_hours": 23, "volume": 1.0,
                         "source": "dukascopy_1m:XAUUSD"}
    for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close", "open",
              "high", "low", "close"):
        d[c] = np.linspace(3000.0, 4000.0, n)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(d)[FILE_COLUMNS].to_parquet(path, index=False)


def test_a_stale_history_is_extended_by_the_complete_days_and_an_up_to_date_one_is_left_untouched(tmp_path):
    path = tmp_path / "bars" / "xauusd_1d.parquet"
    history_file(path, "2026-11-02 21:00")                         # Monday's close
    b1 = minutes("2026-11-02 12:00", "2026-11-05 21:30")           # through Thursday's close and a bit
    now = pd.Timestamp("2026-11-05 21:40", tz="UTC")
    got = long_history.refresh(path, b1, now, "engine_1m:icm")
    assert got.status == "appended" and got.appended == 3 and got.holes == []
    d = pd.read_parquet(path)
    assert list(d.columns) == FILE_COLUMNS
    assert [f"{t:%a %H:%M}" for t in d["visible_at"].iloc[-3:]] == ["Tue 21:00", "Wed 21:00", "Thu 21:00"]
    assert set(d["source"].iloc[-3:]) == {"engine_1m:icm"} and set(d["source"].iloc[:-3]) == {"dukascopy_1m:XAUUSD"}
    # the engine's reader sees the same bars the engine builds from its own minutes (point in time, its roll)
    hist = long_bars.read_history(tmp_path, "XAUUSD")
    assert hist is not None
    own = long_bars.daily_from_1m(b1)
    own = own[pd.DatetimeIndex(own["visible_at"]) > pd.Timestamp("2026-11-02 21:00", tz="UTC")]
    assert np.allclose(hist["bid_close"].iloc[-3:].to_numpy(), own["bid_close"].to_numpy()[:3])
    # nothing new: the file (and its mtime, which the engine re-reads on) is untouched
    stamp = path.stat().st_mtime_ns
    again = long_history.refresh(path, b1, now, "engine_1m:icm")
    assert again.status == "up_to_date" and path.stat().st_mtime_ns == stamp


def test_a_day_whose_close_has_not_passed_or_whose_first_minutes_are_missing_is_never_appended(tmp_path):
    path = tmp_path / "bars" / "xauusd_1d.parquet"
    history_file(path, "2026-11-02 21:00")
    # the store starts inside Tuesday's day (after Monday's close + 6 h): Tuesday may be partial, so only Wednesday
    b1 = minutes("2026-11-03 03:00", "2026-11-05 12:00")
    got = long_history.refresh(path, b1, pd.Timestamp("2026-11-05 12:00", tz="UTC"), "engine_1m:icm")
    assert got.status == "appended" and got.appended == 1 and got.holes == ["2026-11-03"]
    assert f"{pd.read_parquet(path)['visible_at'].iloc[-1]:%a %H:%M}" == "Wed 21:00"   # Thursday is not closed yet


def test_the_refresh_is_a_scheduled_job_with_its_settings_entry():
    assert "long_history_refresh" in JOBS
    s = SETTINGS.scheduler.long_history_refresh
    assert (s.kind, s.at) == ("daily", "21:40")


class FakeStore:
    def __init__(self, b1: pd.DataFrame):
        self.b1 = b1

    def read(self, table: str, *, source: str | None = None, symbol: str | None = None, start: Any = None,
             end: Any = None, **_: Any) -> pd.DataFrame:
        if table != "bars_1m_live":
            return pd.DataFrame(columns=BAR_COLUMNS)
        t = pd.DatetimeIndex(self.b1["ts_utc"])
        return self.b1[(t >= start) & (t < end)].reset_index(drop=True)


GOLD: Any = SimpleNamespace(account_id="icm-demo", enabled=True, broker="icm", symbol="XAUUSD", store_symbol=None)


def _live_settings(tmp_path: Path) -> Any:
    eng = SETTINGS.engine.model_copy(update={"timeframes_live": [*SETTINGS.engine.timeframes_live, "1d"],
                                             "long_history_dir": str(tmp_path)})
    return SETTINGS.model_copy(update={"engine": eng, "data_root": str(tmp_path)})


def test_the_preflight_fails_when_a_live_1d_history_does_not_reach_the_engine_window(tmp_path):
    now = pd.Timestamp("2026-11-13 22:00", tz="UTC")
    store: Any = FakeStore(minutes("2026-10-26 00:00", "2026-11-13 21:59"))
    history_file(tmp_path / "bars" / "xauusd_1d.parquet", "2026-10-20 21:00")    # three weeks stale
    status, detail = long_history.preflight_status(_live_settings(tmp_path), store, [GOLD], now)
    assert status == "fail" and "gap" in detail
    history_file(tmp_path / "bars" / "xauusd_1d.parquet", "2026-11-12 21:00")    # refreshed
    assert long_history.preflight_status(_live_settings(tmp_path), store, [GOLD], now)[0] == "ok"


def test_the_preflight_skips_the_long_history_while_1d_and_1w_are_dark(tmp_path):
    assert long_history.preflight_status(SETTINGS, None, [], pd.Timestamp("2026-11-13", tz="UTC")) == \
        ("ok", "skipped: 1d/1w are dark (engine.timeframes_live)")


NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")


def _engine_state(tmp_path: Path, held: dict[str, Any]) -> None:
    write(tmp_path / "engine_icm-demo.json", {"long_history_gap": held})


def test_an_arm_held_back_by_its_long_history_for_more_than_a_day_warns_and_tells_the_owner(tmp_path):
    _engine_state(tmp_path, {"x-1d": {"tf": "1d", "reason": "gap", "since": (NOW - pd.Timedelta(hours=30)).isoformat()}})
    c = health.check_long_history(make_ctx(tmp_path), AccountRef(account_id="icm-demo"))
    assert c is not None and c.status == "warn" and "x-1d" in c.reason
    assert health._alerting(c.name, c.status)                     # NOTIFY_ON_WARN
    _engine_state(tmp_path, {"x-1d": {"tf": "1d", "reason": "gap", "since": (NOW - pd.Timedelta(hours=3)).isoformat()}})
    c = health.check_long_history(make_ctx(tmp_path), AccountRef(account_id="icm-demo"))
    assert c is not None and c.status == "ok"
    _engine_state(tmp_path, {})
    assert health.check_long_history(make_ctx(tmp_path), AccountRef(account_id="icm-demo")) is None


def test_the_engine_publishes_a_held_back_long_arm_with_the_first_close_it_was_held_at():
    state = long_bars.LongHistory(None, "XAUUSD")
    eng = SimpleNamespace(decisions=[])
    arm = SimpleNamespace(arm_id="x-1d", tf="1d", tag="x")
    t0, t1 = pd.Timestamp("2026-11-10 22:30", tz="UTC"), pd.Timestamp("2026-11-11 22:30", tz="UTC")
    long_bars._held(eng, state, arm, long_bars.GAP, t0)            # type: ignore[arg-type]
    long_bars._held(eng, state, arm, long_bars.SHORT, t1)          # type: ignore[arg-type]
    pub = long_bars.published(SimpleNamespace(long=state))         # type: ignore[arg-type]
    assert pub == {"long_history_gap": {"x-1d": {"tf": "1d", "reason": long_bars.SHORT, "since": t0.isoformat()}}}
    assert long_bars.published(None) == {"long_history_gap": {}}


def test_the_long_history_refresh_blocker_has_left_the_go_live_list():
    assert not any("long-history refresh" in b for b in GO_LIVE_BLOCKERS_1D_1W)
