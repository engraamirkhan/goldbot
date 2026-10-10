"""Macro release (data-macro workflow, release macro-v1): FRED CSV parsing, conservative availability stamps,
vintages, the derived driver features and how research picks them up."""
import urllib.error

import numpy as np
import pandas as pd
import pytest

from goldbot.data.macro import (
    US_BDAY,
    driver_frames,
    first_release,
    fred_available_utc,
    fred_frame,
    merge_vintages,
    parse_fredgraph_csv,
)
from goldbot.data.release import read_macro_files
from goldbot.data.store import Store
from goldbot.features import build_features


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _release(days: pd.DatetimeIndex, vintage: str = "2025-07-01") -> pd.DataFrame:
    n = len(days)
    return pd.concat([
        fred_frame("DFII10", pd.DataFrame({"value_date": days, "value": 1.0 + 0.01 * np.arange(n)}), pd.Timestamp(vintage)),
        fred_frame("DTWEXBGS", pd.DataFrame({"value_date": days, "value": 120.0 + 0.1 * np.arange(n)}), pd.Timestamp(vintage)),
        fred_frame("GVZCLS", pd.DataFrame({"value_date": days, "value": 15.0 + np.sin(np.arange(n))}), pd.Timestamp(vintage)),
    ], ignore_index=True)


def test_daily_h15_values_are_available_the_next_us_business_day_at_23_utc():
    got = fred_available_utc("DFII10", pd.DatetimeIndex(["2025-03-05", "2025-03-07", "2025-05-23", "2025-01-03"]))
    assert list(got) == [_utc("2025-03-06 23:00"),      # Wednesday -> Thursday
                         _utc("2025-03-10 23:00"),      # Friday -> Monday
                         _utc("2025-05-27 23:00"),      # Friday before Memorial Day -> Tuesday
                         _utc("2025-01-06 23:00")]
    # 23:00 UTC is after the 16:15 ET release in both seasons (21:15 UTC in winter, 20:15 in summer)
    assert all(t.tz_convert("America/New_York").time() > pd.Timestamp("16:15").time() for t in got)


def test_weekly_dollar_waits_for_the_h10_release_after_its_week():
    got = fred_available_utc("DTWEXBGS", pd.DatetimeIndex(["2025-03-03", "2025-03-07", "2025-01-17"]))
    assert list(got) == [_utc("2025-03-11 23:00"),      # Monday's value: H.10 next Monday, stamped Tuesday
                         _utc("2025-03-11 23:00"),      # Friday's value: same release
                         _utc("2025-01-21 23:00")]      # Monday 20 Jan is MLK day: release Tuesday, stamp Tuesday 23:00


def test_fredgraph_csv_parses_both_header_styles_and_drops_missing_days():
    new = "observation_date,DFII10\n2025-01-02,2.10\n2025-01-03,.\n2025-01-06,2.15\n"
    old = "DATE,DFII10\n2025-01-02,2.10\n2025-01-03,\n2025-01-06,2.15\n"
    for text in (new, old):
        df = parse_fredgraph_csv(text, "DFII10")
        assert list(df["value_date"]) == [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-06")]
        assert list(df["value"]) == [2.10, 2.15]
    with pytest.raises(ValueError, match="columns"):
        parse_fredgraph_csv("observation_date,OTHER\n2025-01-02,1\n", "DFII10")


def test_release_rows_carry_value_date_available_utc_and_vintage():
    rel = _release(pd.bdate_range("2025-01-01", periods=5))
    assert {"series", "series_id", "value_date", "value", "vintage", "available_utc", "ts_utc"} <= set(rel.columns)
    assert (rel["available_utc"] > pd.to_datetime(rel["value_date"]).dt.tz_localize("UTC")).all()
    assert (rel["ts_utc"] == rel["available_utc"]).all()
    assert set(rel["series"]) == {"real_yield_10y", "broad_dollar", "gold_vix"}


def test_a_weekly_refresh_adds_new_dates_and_dates_revisions_from_the_day_they_were_seen():
    days = pd.bdate_range("2025-01-01", periods=10)
    prev = _release(days[:8], vintage="2025-01-20")
    fresh = _release(days, vintage="2025-02-04")
    fresh.loc[(fresh["series"] == "gold_vix") & (fresh["value_date"] == days[2]), "value"] += 1.0     # a revision
    out = merge_vintages(prev, fresh)
    # unchanged values add nothing; 2 new dates x 3 series + 1 revision
    assert len(out) == len(prev) + 2 * 3 + 1
    rev = out[(out["series"] == "gold_vix") & (out["value_date"] == days[2])].sort_values("available_utc")
    assert len(rev) == 2
    assert rev["available_utc"].iloc[1] == _utc("2025-02-04 23:00")      # not before we could have seen it
    new = out[(out["series"] == "gold_vix") & (out["value_date"] == days[9])]
    assert new["available_utc"].iloc[0] == fred_available_utc("GVZCLS", pd.DatetimeIndex([days[9]]))[0]
    assert merge_vintages(None, fresh).shape[0] == len(fresh)


def test_features_use_the_first_release_so_a_revision_never_changes_an_earlier_bar():
    days = pd.bdate_range("2025-01-01", periods=40)
    base = _release(days, vintage="2025-03-01")
    revised = base[(base["series"] == "gold_vix") & (base["value_date"] == days[5])].copy()
    revised["value"] += 100.0
    revised["vintage"] = pd.Timestamp("2025-03-10")
    revised["available_utc"] = revised["ts_utc"] = _utc("2025-03-10 23:00")
    s = first_release(pd.concat([base, revised]), "gold_vix")
    assert len(s) == 40 and not (s["value"] > 50).any()
    assert s["available_utc"].is_monotonic_increasing


def test_driver_values_match_their_definitions():
    days = pd.date_range("2023-01-03", periods=300, freq=US_BDAY)          # FRED has no holiday rows
    rel = _release(days)
    fr = driver_frames(rel)
    ry = 1.0 + 0.01 * np.arange(300)
    assert np.allclose(fr["macro_real_yield_chg20"]["macro_real_yield_chg20"], 0.2)
    last = ry[-252:]
    assert np.isclose(fr["macro_real_yield_z252"]["macro_real_yield_z252"].iloc[-1], (last[-1] - last.mean()) / last.std(ddof=1))
    dollar = 120.0 + 0.1 * np.arange(300)
    assert np.isclose(fr["macro_dollar_chg20"]["macro_dollar_chg20"].iloc[-1], dollar[-1] / dollar[-21] - 1)
    # a week of dollar values published together is one row: the week's last observation
    assert fr["macro_dollar_chg20"]["available_utc"].is_unique and len(fr["macro_dollar_chg20"]) < 100
    assert len(fr["macro_gvz"]) == 300 and len(fr["macro_gvz_chg20"]) == 280
    # the z-score needs 80% of a year of observations before its first value
    assert len(fr["macro_real_yield_z252"]) == 300 - int(252 * 0.8) + 1


def test_macro_features_built_on_a_truncated_release_match_the_full_one_before_the_cut():
    """No derived value (the 1-year z-score included) depends on an observation published after the bar."""
    rel = _release(pd.bdate_range("2023-01-02", periods=320))
    bars = pd.DataFrame({"ts_utc": pd.date_range("2023-11-01", "2024-03-01", freq="1h", tz="UTC")})
    cut = _utc("2024-01-15 12:00")
    full = build_features(bars, ["macro_drivers"], {"macro": rel})
    part = build_features(bars, ["macro_drivers"], {"macro": rel[rel["available_utc"] <= cut]})
    before = (bars["ts_utc"] < cut).to_numpy()
    for c in full.columns.drop("ts_utc"):
        assert np.allclose(full.loc[before, c], part.loc[before, c], equal_nan=True), c
    assert full.loc[before, "macro_real_yield_z252"].notna().any()


def test_without_macro_data_the_feature_is_all_nan_with_a_fixed_column_set():
    bars = pd.DataFrame({"ts_utc": pd.date_range("2025-01-01", periods=10, freq="15min", tz="UTC")})
    X = build_features(bars, ["macro_drivers"], {})
    assert list(X.columns.drop("ts_utc")) == ["macro_real_yield_chg20", "macro_real_yield_z252", "macro_dollar_chg20",
                                              "macro_gvz", "macro_gvz_chg20"]
    assert X.drop(columns=["ts_utc"]).isna().all().all()


def test_read_macro_files_refuses_rows_without_available_utc(tmp_path):
    assert read_macro_files(tmp_path / "missing").empty
    assert read_macro_files(tmp_path).empty
    rel = _release(pd.bdate_range("2025-01-01", periods=3))
    rel.drop(columns=["available_utc"]).to_parquet(tmp_path / "bad.parquet", index=False)
    with pytest.raises(ValueError, match="available_utc"):
        read_macro_files(tmp_path)


def test_macro_sync_is_a_no_op_until_the_release_exists(tmp_path, monkeypatch):
    from goldbot.data import release

    def no_release(token=None, repo=release.REPO, tag="data-v1"):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(release, "list_assets", no_release)
    assert release.sync_release_macro(Store(tmp_path)) == 0

    # the Saturday refresh keeps its bar counts when the macro sync fails for another reason
    def boom(store, **kw):
        raise OSError("network down")
    monkeypatch.setattr(release, "sync_release_bars", lambda store, **kw: {"1m": 5})
    monkeypatch.setattr(release, "sync_release_macro", boom)
    assert release.sync_release(Store(tmp_path)) == {"1m": 5}


def test_macro_sync_loads_the_release_into_the_store(tmp_path, monkeypatch):
    from goldbot.data import release
    rel = _release(pd.bdate_range("2025-01-01", periods=30))
    src = tmp_path / "asset.parquet"
    rel.to_parquet(src, index=False)
    monkeypatch.setattr(release, "list_assets", lambda token=None, repo=release.REPO, tag="data-v1":
                        [{"name": "macro_fred.parquet", "size": src.stat().st_size, "url": "x"}] if tag == "macro-v1" else [])

    def fake_download(asset, dest_dir, token=None):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / asset["name"]).write_bytes(src.read_bytes())
        return dest_dir / asset["name"]
    monkeypatch.setattr(release, "download", fake_download)
    store = Store(tmp_path / "store")
    assert release.sync_release_macro(store, raw_dir=tmp_path / "raw") == len(rel)
    release.sync_release_macro(store, raw_dir=tmp_path / "raw")                  # idempotent
    assert len(release.store_macro(store)) == len(rel)


def test_research_adds_macro_features_only_when_macro_data_is_passed():
    from goldbot.features.registry import feature_version
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, research_feature_names
    assert "macro_drivers" not in DEFAULT_FEATURE_NAMES and "macro" not in DEFAULT_FEATURE_NAMES
    assert research_feature_names(None) == DEFAULT_FEATURE_NAMES
    assert research_feature_names({"macro": pd.DataFrame()}) == DEFAULT_FEATURE_NAMES
    rel = _release(pd.bdate_range("2025-01-01", periods=3))
    with_macro = research_feature_names({"macro": rel})
    assert with_macro == [*DEFAULT_FEATURE_NAMES, "macro_drivers"]
    assert feature_version(with_macro) != feature_version(DEFAULT_FEATURE_NAMES)


def test_declared_model_features_ignore_the_macro_columns():
    """Specialists keep their declared inputs; the macro columns are there for trials that ask for them."""
    from goldbot.research.pipeline import model_inputs
    from goldbot.specialists import SPECIALISTS
    for fam, cls in SPECIALISTS.items():
        spec = cls()
        if not spec.model_features:
            continue
        pool = [*spec.model_features, "macro_real_yield_chg20", "macro_gvz"]
        cols = model_inputs(spec, pool)
        assert not any(c.startswith("macro_") for c in cols), fam


def test_the_workflow_script_keeps_published_history_and_reports_a_failed_series(tmp_path, monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("fred_macro", Path(__file__).resolve().parents[1] / "scripts" / "fred_macro.py")
    assert spec is not None and spec.loader is not None
    fm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fm)
    csv = {"DFII10": "observation_date,DFII10\n2025-01-02,2.10\n2025-01-03,.\n2025-01-06,2.15\n",
           "GVZCLS": "observation_date,GVZCLS\n2025-01-02,15.0\n2025-01-03,15.5\n"}

    def fake_fetch(sid, start="2003-01-01"):
        if sid not in csv:
            raise RuntimeError(f"FRED {sid}: 503")
        return parse_fredgraph_csv(csv[sid], sid)
    monkeypatch.setattr(fm, "fetch_fred_csv", fake_fetch)
    out = tmp_path / "macro_fred.parquet"
    monkeypatch.setattr(sys, "argv", ["fred_macro.py", "--out", str(out), "--series", "DFII10", "GVZCLS"])
    assert fm.main() == 0
    first = pd.read_parquet(out)
    assert len(first) == 4 and set(first["series_id"]) == {"DFII10", "GVZCLS"}
    # next week: nothing changed for GVZ, DFII10 adds a day, a third series fails: published rows kept, exit 1
    csv["DFII10"] += "2025-01-07,2.20\n"
    prev = tmp_path / "prev.parquet"
    out.rename(prev)
    monkeypatch.setattr(sys, "argv", ["fred_macro.py", "--out", str(out), "--existing", str(prev),
                                      "--series", "DFII10", "GVZCLS", "DGS2"])
    assert fm.main() == 1
    second = pd.read_parquet(out)
    assert len(second) == 5 and "download failed for ['DGS2']" in capsys.readouterr().out
    assert list(second.columns) == list(first.columns)
