"""Daily research set (goldbot/data/daily.py, scripts/daily_fetch.py): the 21:00 UTC session clock, mid aggregation,
the 2025-10-01 cutoff, point-in-time joins and the USD-broad splice. Synthetic data only (no market data in the repo)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import lzma
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import daily
from goldbot.data.daily import (
    BI5_CANDLE,
    CUTOFF,
    assert_before_cutoff,
    bars_to_daily,
    check_daily,
    decode_hourly,
    hourly_mid,
    load_daily,
    load_fred,
    minutes_to_daily,
    months,
    session_date,
    usd_broad,
)
from goldbot.data.macro import fetch_fred_csv, fred_available_utc, fred_frame
from goldbot.data.store import asof_join

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("daily_fetch", ROOT / "scripts" / "daily_fetch.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["daily_fetch"] = mod
    spec.loader.exec_module(mod)
    return mod


def _u(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.mark.parametrize(("start", "label"), [
    ("2024-01-08 20:00", "2024-01-08"),   # Monday's last hour closes Monday's bar at 21:00
    ("2024-01-08 21:00", "2024-01-09"),   # the first hour after the close opens Tuesday
    ("2024-01-12 21:00", "2024-01-15"),   # winter Friday 21:00-22:00 FX hour belongs to Monday
    ("2024-01-13 10:00", "2024-01-15"),   # anything on Saturday rolls to Monday
    ("2024-01-14 22:00", "2024-01-15"),   # the Sunday reopen is Monday's
])
def test_session_date(start: str, label: str) -> None:
    assert session_date(pd.DatetimeIndex([_u(start)]))[0] == pd.Timestamp(label)


def _hours(start: str, n: int, price0: float = 100.0) -> pd.DataFrame:
    ts = pd.date_range(_u(start), periods=n, freq="h")
    p = price0 + np.arange(n, dtype=float)
    return pd.DataFrame({"ts_utc": ts, "open": p, "high": p + 0.5, "low": p - 0.5, "close": p + 0.25,
                         "spread": 0.1})


def test_bars_to_daily_ohlc_count_and_visible_at() -> None:
    h = _hours("2024-01-08 21:00", 24)        # exactly Tuesday's session
    d = bars_to_daily(h, "n_hours", "test")
    assert list(d["date"]) == [pd.Timestamp("2024-01-09")]
    r = d.iloc[0]
    assert (r["open"], r["high"], r["low"], r["close"]) == (100.0, 123.5, 99.5, 123.25)
    assert r["n_hours"] == 24 and r["visible_at"] == _u("2024-01-09 21:00")
    check_daily(d, "T")


def test_bars_to_daily_drops_session_after_cutoff() -> None:
    h = _hours("2025-09-29 21:00", 30)        # Tuesday 09-30 full, then 6 hours that would open 10-01
    d = bars_to_daily(h, "n_hours", "test")
    assert d["date"].max() == pd.Timestamp("2025-09-30")
    assert (d["visible_at"] < CUTOFF).all()


def test_check_daily_rejects_cutoff_and_wrong_visibility() -> None:
    d = bars_to_daily(_hours("2024-01-08 21:00", 24), "n_hours", "t")
    late = d.assign(date=pd.Timestamp("2025-10-01"), visible_at=_u("2025-10-01 21:00"))
    with pytest.raises(ValueError, match="cutoff"):
        check_daily(late, "T")
    with pytest.raises(ValueError, match="visible_at"):
        check_daily(d.assign(visible_at=_u("2024-01-09 00:00")), "T")


def test_load_daily_round_trip_and_refuses_future_rows(tmp_path: Path) -> None:
    d = bars_to_daily(_hours("2024-01-08 21:00", 48), "n_hours", "t")
    d.to_parquet(tmp_path / "eurusd.parquet", index=False)
    got = load_daily("EURUSD", tmp_path)
    assert len(got) == 2 and str(got["visible_at"].dtype) == "datetime64[ns, UTC]"
    bad = pd.concat([d, d.tail(1).assign(date=pd.Timestamp("2025-10-02"), visible_at=_u("2025-10-02 21:00"))])
    bad.to_parquet(tmp_path / "gbpusd.parquet", index=False)
    with pytest.raises(ValueError, match="cutoff"):
        load_daily("GBPUSD", tmp_path)


def test_asof_join_on_visible_at_never_sees_a_bar_before_its_close() -> None:
    """Seeds a daily bar whose close is in the future of the probe minute: the join must not return it."""
    d = bars_to_daily(_hours("2024-01-08 21:00", 48), "n_hours", "t")   # Tue 01-09 and Wed 01-10
    probe = pd.DataFrame({"ts_utc": [_u("2024-01-10 20:59"), _u("2024-01-10 21:00")]})
    j = asof_join(probe, d[["visible_at", "close", "date"]], available_col="visible_at", prefix="d_")
    assert j.loc[0, "d_date"] == pd.Timestamp("2024-01-09")    # Wednesday's bar is not closed at 20:59
    assert j.loc[1, "d_date"] == pd.Timestamp("2024-01-10")


def _bi5(rows: list[tuple[int, int, int, int, int, float]]) -> bytes:
    a = np.array(rows, dtype=BI5_CANDLE)
    return a.tobytes()


def test_decode_hourly_scales_drops_closed_hours_and_cutoff() -> None:
    raw = _bi5([(0, 1_100_00, 1_100_50, 1_099_00, 1_101_00, 10.0),
                (3600, 1_100_50, 1_100_50, 1_100_50, 1_100_50, 0.0),          # closed hour: no volume
                (30 * 86400 + 3600, 1_2, 1_2, 1_2, 1_2, 5.0)])                  # 2025-10-01 01:00: past the cutoff
    f = decode_hourly(raw, dt.date(2025, 9, 1), 1e5)
    assert len(f) == 1
    assert f.loc[0, "ts_utc"] == _u("2025-09-01 00:00")
    assert f.loc[0, "open"] == pytest.approx(1.1) and f.loc[0, "high"] == pytest.approx(1.101)
    # the cached files are lzma bi5; decode works on the decompressed body
    assert lzma.decompress(lzma.compress(raw)) == raw


def test_hourly_mid_and_bracket() -> None:
    bid = _hours("2024-01-08 21:00", 3).drop(columns="spread")
    ask = bid.assign(**{c: bid[c] + 0.2 for c in ("open", "high", "low", "close")})
    ask.loc[2, "high"] = ask.loc[2, "close"]          # ask high at its close; mid high must still bracket
    m = hourly_mid(bid, ask)
    assert m["spread"].round(9).eq(0.2).all()
    assert (m["high"] >= m[["open", "close"]].max(axis=1)).all()
    assert (m["low"] <= m[["open", "close"]].min(axis=1)).all()


def test_minutes_to_daily_counts_minutes() -> None:
    ts = pd.date_range(_u("2024-01-08 21:00"), periods=120, freq="min")
    p = np.linspace(2000, 2010, 120)
    bars = pd.DataFrame({"ts_utc": ts, **{f"bid_{c}": p for c in ("open", "high", "low", "close")},
                         **{f"ask_{c}": p + 0.3 for c in ("open", "high", "low", "close")}})
    d = minutes_to_daily(bars)
    assert d.loc[0, "n_minutes"] == 120 and d.loc[0, "date"] == pd.Timestamp("2024-01-09")
    assert d.loc[0, "open"] == pytest.approx(2000.15)


def test_cutoff_guards() -> None:
    assert months(dt.date(2025, 7, 1)) == [dt.date(2025, 7, 1), dt.date(2025, 8, 1), dt.date(2025, 9, 1)]
    assert months(dt.date(2025, 1, 1), last=dt.date(2026, 6, 1))[-1] == dt.date(2025, 9, 1)
    assert_before_cutoff(_u("2025-09-30 23:59:59"))
    with pytest.raises(ValueError):
        assert_before_cutoff(dt.date(2025, 10, 1))
    mod = _script()
    with pytest.raises(ValueError):
        mod.hourly_url("EURUSD", dt.date(2025, 10, 1), "BID")
    assert mod.hourly_url("EURUSD", dt.date(2025, 9, 1), "BID").endswith("/EURUSD/2025/08/BID_candles_hour_1.bi5")


def test_fetch_one_is_resumable_without_network(tmp_path: Path) -> None:
    mod = _script()
    p = mod.cache_path(tmp_path, "EURUSD", dt.date(2010, 1, 1), "BID")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    assert mod.fetch_one(tmp_path, "EURUSD", dt.date(2010, 1, 1), "BID", pause=0) == "cached"
    p.with_suffix(".404").write_bytes(b"")
    p.unlink()
    assert mod.fetch_one(tmp_path, "EURUSD", dt.date(2010, 1, 1), "BID", pause=0) == "cached"


def test_quality_counts_missing_weekdays_and_gaps() -> None:
    mod = _script()
    dates = pd.to_datetime(["2024-01-08", "2024-01-09", "2024-01-11", "2024-01-22"])
    q = mod.quality(pd.DataFrame({"date": dates, "n_hours": [23, 23, 3, 23], "open": [10.0, 10.0, 10.0, 11.0],
                                  "close": [10.0, 10.0, 10.0, 11.0]}))
    assert q["days"] == 4 and q["short_days"] == 1
    assert q["missing_weekdays"] == 7 and q["max_gap_days"] == 11 and q["max_gap_at"] == "2024-01-11..2024-01-22"
    assert q["open_jumps_gt_3pct"] == 1          # a 10% close -> open gap (a futures roll) is counted, not hidden


# --------------------------------------------------------------------------- FRED


def _fred(sid: str, dates: list[str], values: list[float]) -> pd.DataFrame:
    return fred_frame(sid, pd.DataFrame({"value_date": pd.to_datetime(dates), "value": values}),
                      pd.Timestamp("2026-11-02"))


def test_dtwexb_takes_the_weekly_h10_rule() -> None:
    d = pd.DatetimeIndex(["2005-12-28"])          # a Wednesday; the week's release is Monday 01-02 (holiday) -> Tue
    assert fred_available_utc("DTWEXB", d)[0] == fred_available_utc("DTWEXBGS", d)[0]
    assert fred_available_utc("DTWEXB", d)[0] > fred_available_utc("DFII10", d)[0]


def test_usd_broad_is_ratio_chained(tmp_path: Path) -> None:
    old_dates = ["2005-12-28", "2005-12-29", "2005-12-30", "2006-01-02", "2006-01-03"]
    _fred("DTWEXB", old_dates, [100.0, 101.0, 102.0, 102.0, 103.0]).to_parquet(tmp_path / "fred_DTWEXB.parquet")
    _fred("DTWEXBGS", ["2006-01-02", "2006-01-03"], [51.0, 51.5]).to_parquet(tmp_path / "fred_DTWEXBGS.parquet")
    u = usd_broad(tmp_path)
    assert list(u["series_id"]) == ["DTWEXB"] * 3 + ["DTWEXBGS"] * 2
    assert u["value"].iloc[:3].tolist() == pytest.approx([50.0, 50.5, 51.0])     # x 51/102
    r = u["value"].pct_change()
    assert r.iloc[1] == pytest.approx(0.01) and r.iloc[3] == pytest.approx(0.0)   # no fake jump at the splice


def test_load_fred_refuses_rows_past_the_cutoff(tmp_path: Path) -> None:
    f = _fred("DTB3", ["2025-09-29", "2025-09-30"], [3.86, 3.86])
    assert f["available_utc"].max() >= CUTOFF      # 09-30's value is published 10-01: must not be stored
    f.to_parquet(tmp_path / "fred_DTB3.parquet")
    with pytest.raises(ValueError, match="cutoff"):
        load_fred("DTB3", tmp_path)
    f[f["available_utc"] < CUTOFF].to_parquet(tmp_path / "fred_DTB3.parquet")
    assert len(load_fred("DTB3", tmp_path)) == 1


def test_fetch_fred_csv_passes_the_end_date() -> None:
    seen: dict[str, object] = {}

    class _R:
        text = "observation_date,DTB3\n2025-09-30,3.86\n"

        def raise_for_status(self) -> None:
            return None

    class _S:
        def get(self, url: str, params: dict[str, str], timeout: int) -> _R:
            seen.update(params)
            return _R()

    out = fetch_fred_csv("DTB3", "2002-01-01", end="2025-09-30", session=_S())  # type: ignore[arg-type]
    assert seen["coed"] == "2025-09-30" and len(out) == 1


def test_specs_cover_pos1_universe() -> None:
    assert set(daily.POS1_SYMBOLS) <= set(daily.SPECS)
    assert len(daily.POS1_SYMBOLS) == 15     # U_16 minus XAUUSD (built from the 1m lane's files)
    for s in daily.SPECS.values():
        assert s.price_band[0] < s.price_band[1]
