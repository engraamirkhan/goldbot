"""research/history_replay.py (H-101 card section 6 item 2): point-in-time features through asof_join on
available_utc, outcome-blind candidates, the W2 bps label convention. Synthetic bars only."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research import history_replay as H
from goldbot.research import model_zoo as Z


def bars(n: int = 6 * 1440, seed: int = 0, start: str = "2012-03-05") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    mid = 1700 + np.cumsum(rng.normal(0, 0.3, n))
    hi, lo = mid + rng.uniform(0, 0.5, n), mid - rng.uniform(0, 0.5, n)
    out = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for side, sh in (("bid", -0.15), ("ask", 0.15)):
        out[f"{side}_open"], out[f"{side}_high"] = mid + sh, hi + sh
        out[f"{side}_low"], out[f"{side}_close"] = lo + sh, mid + sh
    out["tick_count"] = rng.integers(20, 200, n)
    out["spread_mean"], out["spread_max"] = 0.3, 0.6
    return out


def test_history_minutes_are_available_at_their_nominal_close() -> None:
    m = H.history_minutes(bars(100))
    assert (m["available_utc"] == pd.to_datetime(m["visible_at"], utc=True)).all()


def test_every_feature_input_was_available_at_its_decision() -> None:
    m = H.history_minutes(bars())
    dec = pd.DatetimeIndex(["2012-03-08 10:00", "2012-03-09 14:35", "2012-03-10 23:55"], tz="UTC")
    f = H.features_at(m, "5m", dec)
    assert len(f) == 3
    assert (pd.to_datetime(f["available_utc_max"], utc=True) <= f["decision_utc"]).all()
    assert {"ret_4", "rsi14", "vol_tercile", "hour_utc"} <= set(f.columns)


def test_a_minute_after_the_decision_never_changes_its_features() -> None:
    b = bars()
    dec = pd.DatetimeIndex(["2012-03-08 10:00"], tz="UTC")
    f0 = H.features_at(H.history_minutes(b), "5m", dec)
    later = pd.to_datetime(b["visible_at"], utc=True) > dec[0]
    b.loc[later, ["bid_close", "ask_close", "bid_high", "ask_high"]] += 50.0
    f1 = H.features_at(H.history_minutes(b), "5m", dec)
    pd.testing.assert_frame_equal(f0, f1)


def test_candidates_are_outcome_blind(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_label() -> None:
        raise AssertionError("a count must never reach the barrier search")
    monkeypatch.setattr(H, "_run_arm_module", no_label)
    arm = H.load_arm("Z3D")
    b = bars(20 * 1440, start="2012-02-01")
    lo, hi = pd.Timestamp("2012-02-10", tz="UTC"), pd.Timestamp("2012-02-21", tz="UTC")
    c = H.candidates("Z3D", arm, b, lo, hi, (np.zeros(0, np.int64), np.zeros(0, np.int64)))
    assert list(c.columns) == ["arm", "decision_utc", "side", "max_hold_s", "session_day"]
    assert not ({"r", "r_mid", "label_exit_utc"} & set(c.columns))
    if len(c):
        assert ((c["decision_utc"] >= lo) & (c["decision_utc"] < hi)).all()
        assert (c["max_hold_s"] == (6 + 1) * 300).all()


def test_the_card_arm_rbr15_replays_rb15s_rule_with_no_slot_limit() -> None:
    a = H.load_arm("RBR15", replay_as=Z.REPLAY_AS["RBR15"])
    assert a.id == "RB15" and a.rule == "redbox_break" and a.max_open == H.MAX_OPEN_EVERY


def test_bps_net_charges_the_round_trip_and_the_stop_overshoot() -> None:
    t = pd.DataFrame({"entry": [4000.0, 4000.0], "move_usd": [2.0, -4.0], "risk_usd": [4.0, 4.0],
                      "barrier_hit": ["target", "stop"]})
    np.testing.assert_allclose(H.bps_net(t), [(2.0 - 0.23) / 4.0, (-4.0 - 0.23 - 0.70) / 4.0])


def test_session_day_rolls_at_five_pm_new_york() -> None:
    d = H.session_day(pd.DatetimeIndex(["2012-03-07 21:59", "2012-03-07 22:01"], tz="UTC"))   # EST: 17:00 = 22:00 UTC
    assert [str(x) for x in d] == ["2012-03-07", "2012-03-08"]
