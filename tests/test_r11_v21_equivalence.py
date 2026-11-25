"""R1 (quant, blocking): the r11-v2.2 module grades exactly like the frozen r11-v2.1 blob when no v2.2 fix result is
passed. tests/fixtures/era_quality_r11_v21.py is the v2.1 module byte for byte (its git blob is checked here, so a
shallow CI clone needs no history); both grade_month_v2 run on the same fixtures (same-source with a fix, partial
share, cap_b, pending, FX fix) and must return equal MonthGradeV2 records."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data import era_quality as current

ROOT = Path(__file__).resolve().parents[1]
V21 = ROOT / "tests" / "fixtures" / "era_quality_r11_v21.py"
V21_BLOB = "c6f283d763718bcf32f1c3971acd812a868fce63"


def _v21() -> ModuleType:
    blob = subprocess.run(["git", "hash-object", str(V21)], capture_output=True, text=True, check=True).stdout.strip()
    assert blob == V21_BLOB, "the fixture is not the frozen r11-v2.1 module"
    spec = importlib.util.spec_from_file_location("era_quality_r11_v21", V21)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["era_quality_r11_v21"] = mod
    spec.loader.exec_module(mod)
    return mod


def _month(seed: int, base: float = 1300.0) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """A 2019-03 gold-like month: 1m bid bars (23 h a day), the reference hours (here the bars' own hours, slightly
    perturbed, with a spread) and a 15:00 London fix per day."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2019-03-01", "2019-03-29")
    ts = pd.DatetimeIndex(np.concatenate([pd.date_range(d - pd.Timedelta(hours=1), periods=1380, freq="min").to_numpy()
                                          for d in days])).tz_localize("UTC")
    close = base * np.exp(np.cumsum(rng.normal(0, 3e-4, len(ts))))
    close = np.round(close, 3)
    op = np.r_[close[0], close[:-1]]
    hi = np.maximum(op, close) + np.round(rng.uniform(0, 0.2, len(ts)), 3)
    lo = np.minimum(op, close) - np.round(rng.uniform(0, 0.2, len(ts)), 3)
    bars = pd.DataFrame({"ts_utc": ts, "bid_open": op, "bid_high": hi, "bid_low": lo, "bid_close": close,
                         "dq_flag": ""})
    h = bars.assign(h=ts.floor("h")).groupby("h")
    ref = pd.DataFrame({"open": h["bid_open"].first(), "high": h["bid_high"].max(), "low": h["bid_low"].min(),
                        "close": h["bid_close"].last() * (1 + rng.normal(0, 2e-5, h.ngroups))}).rename_axis(
        "ts_utc").reset_index()
    ref["spread"] = 0.3
    fts = pd.DatetimeIndex([d + pd.Timedelta(hours=15) for d in days]).tz_localize("Europe/London").tz_convert("UTC")
    j = np.searchsorted(current.epoch_ns_utc(pd.Series(ts)), current.epoch_ns_utc(pd.Series(fts)))
    fix = pd.DataFrame({"fix_utc": fts, "value": close[np.clip(j - 1, 0, len(close) - 1)] * 1.0002})
    return bars, ref, fix


CASES: dict[str, dict[str, Any]] = {
    "same_source_fix": {"same_source": True, "fix_status": "ok", "use_fix": True},
    "partial_share": {"partial_share": True, "fix_status": "ok", "use_fix": True},
    "cap_b": {"same_source": True, "fix_status": "cap_b", "use_fix": False},
    "pending": {"same_source": True, "fix_status": "pending", "use_fix": False},
    "fx_fix": {"fix_status": "ok", "use_fix": True},
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_grade_month_v2_equals_the_frozen_v21_blob(case: str) -> None:
    v21 = _v21()
    kw: dict[str, Any] = dict(CASES[case])
    use_fix = kw.pop("use_fix")
    for seed in (1, 2):
        bars, ref, fix = _month(seed)
        args = ("XAUUSD", "2019-03", bars, ref, 1380, True)
        extra: dict[str, Any] = {**kw, "fix": fix if use_fix else None}
        old = v21.grade_month_v2(*args, **extra)
        new = current.grade_month_v2(*args, **extra, fix_result=None)
        assert old.model_dump() == new.model_dump(), case
