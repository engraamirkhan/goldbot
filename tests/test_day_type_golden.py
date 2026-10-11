"""Golden output of the opt-in `day_type` family, tied to `day_type.VERSION` (quant review of E-4, M1).

H-16 pins what it reads, `feature_version(["day_type"])`, which hashes the family's name and version only. So a change
to the columns' values without a version bump would leave the pinned version unchanged while the pre-registered test
read different inputs. This file holds day_type v<VERSION>'s columns on fixed synthetic bars, GVZ and calendar; any
change to the outputs fails here until `day_type.VERSION` is bumped and a new golden file is written for it
(`python tests/test_day_type_golden.py` writes the file for the current version)."""
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.macro import fred_frame
from goldbot.features import day_type as dt

GOLDEN = Path(__file__).parent / "data" / f"day_type_v{dt.VERSION}_golden.parquet"
EVERY = 7                     # every 7th bar is kept in the file (all columns), to keep it small


def golden_inputs() -> tuple[pd.DataFrame, dict]:
    """Eight months of hourly bars (weekend closure removed, persistent daily volatility), GVZ on business days with a
    two-week gap, and a weekly tier-1 calendar with an FOMC statement + press conference pair; the HAR fit needs 60
    rows (ctx day_type_min_fit_rows), so every column has values."""
    ts = pd.date_range("2024-01-01", "2024-09-01", freq="1h", tz="UTC", inclusive="left")
    wd, hr = ts.dayofweek, ts.hour
    ts = ts[~((wd == 5) | ((wd == 4) & (hr >= 22)) | ((wd == 6) & (hr < 22)))]
    rng = np.random.default_rng(20261011)
    day = np.asarray(dt.opportunity_day(ts).factorize()[0])
    lv = np.zeros(day.max() + 1)
    for i in range(1, len(lv)):
        lv[i] = 0.85 * lv[i - 1] + 0.3 * rng.standard_normal()
    sig = 0.0015 * np.exp(lv[day])
    close = 2000 * np.exp(np.cumsum(sig * rng.standard_normal(len(ts))))
    open_ = np.concatenate(([2000.0], close[:-1])) * np.exp(sig * 0.2 * rng.standard_normal(len(ts)))
    high = np.maximum(open_, close) + np.abs(sig * rng.standard_normal(len(ts))) * close
    low = np.minimum(open_, close) - np.abs(sig * rng.standard_normal(len(ts))) * close
    bars = pd.DataFrame({"ts_utc": ts, "open": open_, "high": high, "low": low, "close": close,
                         "spread": 0.2, "tick_count": 10})
    vd = pd.bdate_range("2023-12-01", "2024-09-01")
    vd = vd[(vd < pd.Timestamp("2024-05-06")) | (vd > pd.Timestamp("2024-05-17"))]
    gvz = fred_frame("GVZCLS", pd.DataFrame({"value_date": vd, "value": 16 + 2 * np.sin(np.arange(len(vd)) / 9)}),
                     pd.Timestamp("2025-01-01"))
    cal = []

    def row(eid: str, ev: pd.Timestamp, avail: pd.Timestamp, status: str = "scheduled") -> dict:
        return {"event_id": eid, "event_type": eid.split(":")[0], "tier": 1, "release_id": -1, "event_utc": ev,
                "status": status, "available_utc": avail, "ts_utc": avail, "vintage": pd.Timestamp("2025-01-01"),
                "source": "fred"}
    for d in pd.date_range("2023-12-12", "2024-09-01", freq="7D"):
        ev = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=13, minutes=30)
        cal.append(row(f"cpi:{d.date()}", ev, ev - pd.Timedelta(days=7)))
    fomc = pd.Timestamp("2024-06-12 18:00", tz="UTC")
    cal += [row("fomc_statement:2024-06-12", fomc, fomc - pd.Timedelta(days=30)),
            row("fomc_press_conference:2024-06-12", fomc + pd.Timedelta(minutes=30), fomc - pd.Timedelta(days=30))]
    ctx = {"macro": gvz, "us_calendar": pd.DataFrame(cal), "day_type_min_fit_rows": 60}
    return bars, ctx


def golden_frame() -> pd.DataFrame:
    bars, ctx = golden_inputs()
    return dt.f_day_type(bars, ctx).iloc[::EVERY].reset_index(drop=True)


def test_day_type_outputs_match_the_golden_file_of_their_version_so_a_change_needs_a_version_bump():
    assert GOLDEN.exists(), (f"no golden file for day_type v{dt.VERSION}: a version bump writes one "
                             f"(python tests/test_day_type_golden.py)")
    got, want = golden_frame(), pd.read_parquet(GOLDEN)
    assert list(got.columns) == list(dt.COLUMNS)
    for c in dt.COLUMNS:
        assert got[c].notna().any(), f"{c} has no value on the golden inputs: the file would not pin it"
    # written on macOS; Linux libm differs in the last ulp of a few operations, so compare to 1e-12 relative (NaN
    # positions and every column must still match), as tests/test_sessions_v1_migration.py does
    pd.testing.assert_frame_equal(got, want, check_exact=False, rtol=1e-12, atol=1e-12,
                                  obj=f"day_type v{dt.VERSION} outputs changed: bump day_type.VERSION")


if __name__ == "__main__":
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    golden_frame().to_parquet(GOLDEN)
    print(f"wrote {GOLDEN}")
