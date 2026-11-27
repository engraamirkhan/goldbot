"""scripts/research/sr_v3_training_sets.py: the outcome-blind checks behind the sr-v3 training-set manifest."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("sr_v3_training_sets", ROOT / "scripts/research/sr_v3_training_sets.py")
assert _spec and _spec.loader
S = importlib.util.module_from_spec(_spec)
sys.modules["sr_v3_training_sets"] = S
_spec.loader.exec_module(S)

T0 = pd.Timestamp("2012-03-06 10:00", tz="UTC")


def _frames(n: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    dec = pd.date_range(T0, periods=n, freq="15min")
    fs = pd.DataFrame({"decision_utc": dec, "available_utc": dec, "window_from_utc": dec - pd.Timedelta("1D")})
    sk = pd.DataFrame({"decision_utc": dec, "entry_utc": dec + pd.Timedelta("1min"),
                       "horizon_end_utc": dec + pd.Timedelta("8h"), "label_available_utc": dec + pd.Timedelta("8h"),
                       "label_ok": True})
    return fs, sk


def test_clean_rows_have_no_look_ahead() -> None:
    fs, sk = _frames()
    v = S.look_ahead(fs, sk, pd.Timestamp("2018-01-01", tz="UTC"))
    assert not any(v.values()), v


def test_a_future_release_is_flagged() -> None:
    """A seeded row whose feature became available after its decision (a revised or late value) is a violation."""
    fs, sk = _frames()
    fs.loc[2, "available_utc"] = T0 + pd.Timedelta("30min") + pd.Timedelta("1s")
    v = S.look_ahead(fs, sk, pd.Timestamp("2018-01-01", tz="UTC"))
    assert v["fs_available_after_decision"] == 1


def test_a_label_horizon_past_learn_end_is_flagged() -> None:
    fs, sk = _frames()
    v = S.look_ahead(fs, sk, T0 + pd.Timedelta("8h") + pd.Timedelta("30min"))
    assert v["label_ok_horizon_past_learn_end"] == 2


def test_missing_minutes_counts_only_in_session_gaps() -> None:
    ts = pd.date_range("2012-03-06 10:00", "2012-03-06 12:00", freq="1min", tz="UTC")
    ts = ts.delete([30, 31, 32])                                 # 3 in-session minutes missing (Tuesday)
    r = S.missing_minutes(pd.DataFrame({"ts_utc": ts}), [2012])
    assert r["missing_in_session"] == 3
    assert r["present"] == len(ts)
    assert not r["pass"]                                          # 3 / 121 > 0.1 %


def test_train_cut_is_the_trainer_gap() -> None:
    cut = S.train_cut("15m", 2014)
    assert cut == (pd.Timestamp("2014-01-01", tz="UTC") - pd.Timedelta("1D")).value   # max(1 d, H = 8 h)
    assert S.train_cut("1w", 2014) < cut                                                # H = 20 d
    assert S.train_cut("1m", 2018) > pd.Timestamp("2017-12-31", tz="UTC").value         # v0: every learn row


def test_norm_acc_uses_only_rows_before_the_cut() -> None:
    acc = S.NormAcc(["a"], [2014])
    hz = np.array([pd.Timestamp("2013-06-01", tz="UTC").value, pd.Timestamp("2014-06-01", tz="UTC").value])
    acc.add(np.array([[1.0], [100.0]]), hz, "15m")
    f = acc.frame()
    assert f.loc[0, "n"] == 1 and f.loc[0, "mean"] == 1.0
