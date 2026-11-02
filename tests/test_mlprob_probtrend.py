"""probtrend gate: the forecast in force is the last one available at or before the signal (asof_join), p >= tau."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "mlprob"))
import probtrend_screen as P  # noqa: E402

T0 = pd.Timestamp("2012-03-05 07:00", tz="UTC")


def _fc() -> pd.DataFrame:
    return pd.DataFrame({"available_utc": [T0, T0 + pd.Timedelta(hours=1)], "p": [0.9, 0.1], "tau": [0.5, 0.5],
                         "snap": ["A", "B"]})


def _sig(times: list[pd.Timestamp]) -> pd.DataFrame:
    dec = np.array([t.value for t in times], dtype=np.int64)
    return P.E.frame(dec, np.ones(len(dec), dtype=np.int64), risk=1.0)


def test_gate_uses_forecast_in_force_only() -> None:
    times = [T0 - pd.Timedelta(minutes=1), T0, T0 + pd.Timedelta(minutes=59), T0 + pd.Timedelta(hours=1)]
    out = P.gate_sig(_sig(times), _fc())
    kept = pd.to_datetime(out.dec.to_numpy(), utc=True).tolist()
    # before the first forecast: none in force (no trade); A (p 0.9) passes; B (p 0.1) blocks from its available time
    assert kept == [T0, T0 + pd.Timedelta(minutes=59)]


def test_gate_stale_forecast_expires() -> None:
    out = P.gate_sig(_sig([T0 + pd.Timedelta(hours=26)]), _fc().iloc[:1])
    assert out.empty


def test_nan_forecast_blocks() -> None:
    fc = _fc()
    fc.loc[1, "p"] = np.nan
    out = P.gate_sig(_sig([T0 + pd.Timedelta(hours=2)]), fc)
    assert out.empty
