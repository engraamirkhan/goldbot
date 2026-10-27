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


# ---------------------------------------------------------------- engine lane E3: BXR's runner, its twins, T's spread
def _spec(arm_id: str, rule: str, params: dict | None = None) -> A.Arm:
    return A.Arm(id=arm_id, rule=rule, timeframe="15m", params=params or {})


def test_bxr_in_history_exits_on_the_runner_walk_and_scores_its_twins_on_the_same_entries(b1: pd.DataFrame) -> None:
    """H-80: BXR's exit is `walk_runner` (the function the engine trails with); BXT/BXF are virtual twins on BXR's own
    entries: the same entry price and 1m path, the STRUCT or FIXED target, the fixed 1 R stop."""
    from goldbot.research import arm_rules_clock_box as K
    arm = _spec("BXR15", "box_next", {"exit": "runner"})
    trades, s = rab.run_arm(b1, arm, START, END, COSTS)
    assert len(trades) > 5
    bars = rab.resample_bars(b1, "15m").reset_index(drop=True)
    close = pd.DatetimeIndex(bars["visible_at"])
    for _, t in trades.iterrows():
        i = int(np.searchsorted(close, t["ts_utc"]))                                 # ts_utc: the decision close
        closes = close[i + 1:i + 1 + 96]
        path = K.mid_path(b1, close[i])
        entry, side, r = float(t["entry"]), int(t["side"]), float(t["risk_usd"])
        assert entry == pytest.approx(float(path["open"].iloc[0]))
        w = K.walk_runner(path, closes, entry, side, r, r / K.BOX_STOP_ATR)
        assert t["barrier_hit"] == w["kind"] and t["exit_px"] == pytest.approx(w["exit"])
        for mode, tgt in (("fixed", 2.0), ("struct", float(t["twin_struct_target_r"]))):
            v = K.walk_runner(path, closes, entry, side, r, r / K.BOX_STOP_ATR, target_r=tgt)
            assert t[f"twin_{mode}_hit"] == v["kind"] and t[f"twin_{mode}_exit_px"] == pytest.approx(v["exit"])
    assert (trades["twin_struct_target_r"] >= 2.0 - 1e-9).all()
    assert set(s["twins"]) == {"BXT15", "BXF15"} and s["twins"]["BXF15"]["n_trades"] == len(trades)
    assert set(s["twins"]["BXT15"]) >= {"gross_r", "net_r_overshoot_0", "net_r_overshoot_0.70"}


def test_a_fixed_target_box_arm_has_no_twins(b1: pd.DataFrame) -> None:
    trades, s = rab.run_arm(b1, _spec("BXF-X", "box_next", {"exit": "fixed"}), START, END, COSTS)
    assert "twins" not in s and not any(c.startswith("twin_") for c in trades.columns)


def test_ka15_stratum_t_pays_its_signal_time_spread_on_both_legs_and_the_family_is_also_read_without_t(b1) -> None:
    """Arms 8.13 condition 2: "T is charged at the spread measured at signal time on both legs: the 23:00 UTC entry and
    the 23:30 UTC exit" (in place of the p90 spread inside the frozen round trip, never below it); condition 3: the
    family net with T excluded and each stratum's share of fills."""
    trades, s = rab.run_arm(b1, _spec("KA15", "clock_anchor"), START, END, COSTS)
    t_rows = trades[trades["stratum"] == "T"]
    others = trades[trades["stratum"] != "T"]
    assert len(t_rows) > 3 and len(others) > 3
    dec = pd.DatetimeIndex(t_rows["ts_utc"])                                  # the decision close
    assert (dec.hour == 23).all() and (dec.minute == 0).all()
    m = b1.assign(_t=pd.to_datetime(b1["ts_utc"], utc=True)).set_index("_t")
    for (_, t), at in zip(t_rows.iterrows(), dec, strict=True):
        s_in = float(m["ask_open"].loc[t["entry_ts"]] - m["bid_open"].loc[t["entry_ts"]])
        last = at + pd.Timedelta(minutes=29)                                  # the minute closing at 23:30
        s_out = float(m["ask_close"].loc[last] - m["bid_close"].loc[last])
        extra = max(0.0, s_in + s_out - COSTS.spread_both_legs_usd)
        assert t["spread_extra_usd"] == pytest.approx(extra) and extra > 0
        stop = float(t["barrier_hit"] == "stop")
        assert t["net_r"] == pytest.approx((t["move_usd"] - 0.23 - extra) / t["risk_usd"])
        assert t["net_r_os"] == pytest.approx((t["move_usd"] - 0.23 - extra - 0.70 * stop) / t["risk_usd"])
    assert others["net_r"].to_numpy() == pytest.approx(((others["move_usd"] - 0.23) / others["risk_usd"]).to_numpy())
    assert s["net_r_without_T"]["mean"] == pytest.approx(float(others["net_r"].mean()))
    assert s["strata"]["T"] == pytest.approx(len(t_rows) / len(trades))


@pytest.mark.parametrize(("rule", "tf", "params", "end"), [
    ("pdc_break", "4h", {"stop_atr": 1.0, "target_r": 2.0, "hold_bars": 32}, "2023-05-01"),
    ("donchian_break", "1d", {"hold_bars": 10}, "2024-01-01")])           # DOND: 55 daily bars of warm-up, ~1 a month
def test_pdc4_and_dond_in_history_exit_on_the_cut_walk_and_never_hold_into_the_weekend(rule, tf, params, end) -> None:
    """ARM-SPEC 8.15: history replays the engine's Friday cut on the 1m path (`arm_rules_weekcut.walk_cut`, the rule
    the engine applies live): losers close at the cut, winners hold under its tightened stop to the week-capped time
    exit; every exit is before the server's Saturday 00:00."""
    from goldbot.research import arm_rules as R
    from goldbot.research import arm_rules_clock_box as K
    from goldbot.research import arm_rules_weekcut as W
    b1 = _bars_1m("2023-01-02", end)
    arm = A.Arm(id="WEEKX", rule=rule, timeframe=tf, params=params)
    trades, _ = rab.run_arm(b1, arm, START, pd.Timestamp(end, tz="UTC") - pd.Timedelta(days=14), COSTS)
    assert len(trades) > 3, "the fixture must give trades, else the checks are vacuous"
    bars = rab.resample_bars(b1, tf).reset_index(drop=True)
    no = np.zeros(0, dtype=np.int64)
    cands = R.candidates(arm, R.RuleContext(bars=bars, b1=b1, blackout_lo=no, blackout_hi=no)).set_index("idx")
    close = pd.DatetimeIndex(bars["visible_at"])
    target_r = float(params.get("target_r", 20.0))
    for _, t in trades.iterrows():
        i = int(np.searchsorted(close, t["ts_utc"]))
        exit_at = close[i + 1 + int(cands.at[i, "max_bars"])]  # type: ignore[arg-type]
        path = K.mid_path(b1, close[i])
        cut = pd.Timestamp(cands.at[i, "cut_at"])  # type: ignore[arg-type]
        w = W.walk_cut(path, float(t["entry"]), int(t["side"]), float(t["risk_usd"]), cut, exit_at, target_r)
        assert t["barrier_hit"] == w["kind"] and t["exit_px"] == pytest.approx(w["exit"])
        _, saturday = W.week_bounds(pd.DatetimeIndex([close[i]]))
        assert pd.Timestamp(w["at"]) <= saturday[0] - W.WEEK_END_SLACK
