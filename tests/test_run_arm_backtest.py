"""scripts/run_arm_backtest.py on synthetic 1m bars: every runnable arm runs end to end, R is the candidate's own stop
distance priced with the frozen cost model and reported at both overshoots, trades stay inside the block, and the loader
refuses a block that reaches the holdout."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import arms as A
from tests.test_count_owed_events import _bars_1m

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_arm_backtest", ROOT / "scripts" / "run_arm_backtest.py")
assert spec and spec.loader
rab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rab)
COSTS = A.load_costs(ROOT / "config" / "arm_costs.yaml")
ARMS = {a.id: a for a in A.load_arms(ROOT / "config" / "exploration_arms.yaml")}
START, END = pd.Timestamp("2023-02-01", tz="UTC"), pd.Timestamp("2023-04-15", tz="UTC")


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return _bars_1m("2023-01-02", "2023-05-01")


@pytest.mark.parametrize("arm_id", ["S0", "S0R", "I0", "S1B", "S2A", "S3A", "S5A", "I2A", "MSA15", "W0"])
def test_arm_runs_and_prices_net_r_twice(b1: pd.DataFrame, arm_id: str) -> None:
    trades, s = rab.run_arm(b1, ARMS[arm_id], START, END, COSTS)
    assert s["n_trades"] == len(trades) and s["config_hash"] == ARMS[arm_id].config_hash
    if len(trades):
        assert trades["ts_utc"].min() >= START and trades["ts_utc"].max() < END + pd.Timedelta(days=1)
        # net = (move - 0.23 - overshoot on stop exits) / the candidate's own stop distance
        stop = (trades["barrier_hit"] == "stop").to_numpy()
        expect0 = (trades["move_usd"] - 0.23) / trades["risk_usd"]
        expect1 = (trades["move_usd"] - 0.23 - 0.70 * stop) / trades["risk_usd"]
        assert trades["net_r"].to_numpy() == pytest.approx(expect0.to_numpy())
        assert trades["net_r_os"].to_numpy() == pytest.approx(expect1.to_numpy())
        assert (trades["net_r_os"] <= trades["net_r"] + 1e-12).all()
    assert set(s) >= {"net_r_overshoot_0", "net_r_overshoot_0.70", "gross_r"}


def test_random_control_is_reproducible_and_loses_the_cost(b1: pd.DataFrame) -> None:
    a, _ = rab.run_arm(b1, ARMS["S0"], START, END, COSTS)
    b, _ = rab.run_arm(b1, ARMS["S0"], START, END, COSTS)
    pd.testing.assert_frame_equal(a, b)
    assert len(a) > 50 and (a["gross_r"] - a["net_r"]).mean() > 0.23 / 3.0 * 0.9   # the round trip over a $3 stop is 0.077 R


def test_max_open_limits_concurrent_positions() -> None:
    lab = pd.DataFrame({"t_entry": [1, 2, 3, 4, 20], "t_exit": [10, 11, 12, 13, 25]})
    assert list(rab.limit_open(lab, 1)["t_entry"]) == [1, 20]
    assert list(rab.limit_open(lab, 3)["t_entry"]) == [1, 2, 3, 20]


def test_blackout_removes_entries(b1: pd.DataFrame) -> None:
    base, _ = rab.run_arm(b1, ARMS["S0"], START, END, COSTS)
    day = pd.Timestamp("2023-03-08", tz="UTC")
    lo, hi = np.array([(day + pd.Timedelta(hours=7)).value]), np.array([(day + pd.Timedelta(hours=17)).value])
    cut, s = rab.run_arm(b1, ARMS["S0"], START, END, COSTS, (lo, hi))
    assert len(cut) < len(base) and s["blackout_windows"] == 1
    t = pd.DatetimeIndex(cut["ts_utc"])
    assert not ((t >= day + pd.Timedelta(hours=7)) & (t < day + pd.Timedelta(hours=17))).any()


def test_loader_refuses_a_block_in_the_holdout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="holdout"):
        rab.load_block_bars(tmp_path, pd.Timestamp("2025-06-01", tz="UTC"), pd.Timestamp("2025-10-31", tz="UTC"))


def test_run_arm_asserts_on_holdout_bars() -> None:
    b1 = _bars_1m("2025-09-28", "2025-10-03")
    with pytest.raises(AssertionError, match="holdout"):
        rab.run_arm(b1, ARMS["I0"], pd.Timestamp("2025-09-28", tz="UTC"), pd.Timestamp("2025-09-30", tz="UTC"), COSTS)


def test_warmup_is_at_least_400_days() -> None:
    assert rab.WARMUP_DAYS >= 400


def _mid_open(b1: pd.DataFrame, ts: pd.Series) -> np.ndarray:
    m = b1.assign(_t=pd.to_datetime(b1["ts_utc"], utc=True)).set_index("_t")
    return ((m["bid_open"] + m["ask_open"]) / 2).reindex(pd.DatetimeIndex(ts)).to_numpy()


def test_entry_is_after_the_decision_close_at_the_next_1m_open(b1: pd.DataFrame) -> None:
    for arm_id in ("S0", "I0", "S2A"):
        trades, _ = rab.run_arm(b1, ARMS[arm_id], START, END, COSTS)
        assert len(trades) > 5
        # the fill minute starts at or after the decision bar's close, and the first minute searched is that one
        assert (pd.DatetimeIndex(trades["entry_ts"]) >= pd.DatetimeIndex(trades["ts_utc"])).all()
        assert (pd.DatetimeIndex(trades["ts_exit"]) >= pd.DatetimeIndex(trades["entry_ts"])).all()
        assert trades["entry"].to_numpy() == pytest.approx(_mid_open(b1, trades["entry_ts"]))


def test_breakout_arm_enters_at_the_next_open_not_the_close(b1: pd.DataFrame) -> None:
    b1 = b1.copy()
    for side in ("bid", "ask"):
        b1[f"{side}_open"] = b1[f"{side}_open"] + 0.5        # every minute opens 0.5 away from the previous close
    trades, _ = rab.run_arm(b1, ARMS["S2A"], START, END, COSTS)
    bars = rab.resample_bars(b1, "5m")
    mid_close = ((bars["bid_close"] + bars["ask_close"]) / 2).set_axis(pd.DatetimeIndex(bars["visible_at"]))
    decision_close = mid_close.reindex(pd.DatetimeIndex(trades["ts_utc"])).to_numpy()
    assert not np.allclose(trades["entry"].to_numpy(), decision_close)         # the close is not the fill
    assert np.allclose(trades["entry"].to_numpy(), _mid_open(b1, trades["entry_ts"]))


def _write_calendar(folder: Path) -> None:
    from goldbot.data.us_calendar import COLUMNS
    ev = pd.Timestamp("2025-03-07 13:30", tz="UTC")
    row = {"event_id": "nfp-2025-03", "event_type": "NFP", "tier": 1, "release_id": "x", "event_utc": ev,
           "status": "scheduled", "available_utc": ev - pd.Timedelta(days=30), "ts_utc": ev - pd.Timedelta(days=30),
           "vintage": "", "source": "test"}
    folder.mkdir(parents=True)
    pd.DataFrame([row], columns=list(COLUMNS)).to_parquet(folder / "us_calendar.parquet")


def test_blackout_loads_from_the_fetched_calendar_layout(tmp_path: Path) -> None:
    """raw-calendar/us_calendar.parquet, the layout `fetch_data_release.py --us-calendar` leaves, gives blackout windows."""
    _write_calendar(tmp_path / "raw-calendar")
    lo, hi = rab.load_blackout(tmp_path / "raw-calendar")
    assert len(lo) == 1 and hi[0] - lo[0] == pd.Timedelta(minutes=45).value


def test_missing_calendar_fails_loudly_and_says_what_to_run(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="calendar-v1"):
        rab.load_blackout(tmp_path / "raw-calendar")
