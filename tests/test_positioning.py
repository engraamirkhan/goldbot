"""Positioning release (data-positioning workflow, release positioning-v1): CFTC COT and GLD holdings parsing, the
conservative release stamps (DST, holiday weeks, shutdowns), vintages, and the opt-in positioning features."""
import importlib.util
import urllib.error
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data.macro import US_BDAY
from goldbot.data.positioning import (
    COT_GOLD_CODE,
    GldSourceUnavailable,
    cot_available_utc,
    cot_frame,
    gld_available_utc,
    gld_frame,
    merge_releases,
    parse_cot,
    parse_gld_csv,
    read_release,
    write_release,
)
from goldbot.data.release import read_positioning_files
from goldbot.data.store import Store
from goldbot.features import build_features, registry
from goldbot.features import positioning as pf


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


@pytest.fixture
def enabled():
    """Register the opt-in feature for one test and restore the registry afterwards (other tests see the default)."""
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    yield pf.enable()
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def _cot(n: int = 70, start: str = "2024-01-02", vintage: str = "2025-06-01", net0: float = 100_000.0) -> pd.DataFrame:
    days = pd.date_range(start, periods=n, freq="7D")                      # Tuesdays
    i = np.arange(n, dtype=float)
    parsed = pd.DataFrame({"report_date": days, "mm_long": net0 + 150_000 + 1_000 * i + 5_000 * np.sin(i),
                           "mm_short": np.full(n, 150_000.0), "open_interest": 500_000 + 100 * i,
                           "comm_net": -(net0 + 1_000 * i)})
    return cot_frame(parsed, pd.Timestamp(vintage))


def _gld(n: int = 120, start: str = "2024-01-02", vintage: str = "2025-06-01") -> pd.DataFrame:
    days = pd.date_range(start, periods=n, freq=US_BDAY)
    return gld_frame(pd.DataFrame({"value_date": days, "tonnes": 800.0 + np.arange(n) + 3 * np.cos(np.arange(n))}),
                     pd.Timestamp(vintage))


# --------------------------------------------------------------------------- COT release timing
def test_cot_is_public_friday_1530_new_york_time_in_both_seasons():
    got = cot_available_utc(pd.DatetimeIndex(["2025-01-14", "2025-07-08", "2025-03-04", "2025-03-11", "2025-10-28",
                                              "2025-11-04"]))
    assert list(got) == [_utc("2025-01-17 20:30"),     # EST: 15:30 ET = 20:30 UTC
                         _utc("2025-07-11 19:30"),     # EDT: 19:30 UTC
                         _utc("2025-03-07 20:30"),     # DST starts Sunday 9 March: the Friday before is still EST
                         _utc("2025-03-14 19:30"),
                         _utc("2026-01-31 00:00"),     # 2025 shutdown floor (see the next test)
                         _utc("2026-01-31 00:00")]
    winter_summer = cot_available_utc(pd.DatetimeIndex(["2024-10-29", "2024-11-05"]))
    assert list(winter_summer) == [_utc("2024-11-01 19:30"), _utc("2024-11-08 20:30")]   # DST ends 3 Nov 2024
    assert all(t.tz_convert("America/New_York").strftime("%H:%M") == "15:30" for t in winter_summer)


def test_cot_holiday_weeks_move_to_the_next_business_day_after_friday():
    got = cot_available_utc(pd.DatetimeIndex(["2024-11-26", "2024-09-03", "2024-12-31", "2024-07-02", "2024-02-20",
                                              "2024-06-18"]))
    assert list(got) == [_utc("2024-12-02 20:30"),     # Thanksgiving Thursday -> Monday
                         _utc("2024-09-09 19:30"),     # Labor Day Monday of the report week -> Monday after
                         _utc("2025-01-06 20:30"),     # New Year Wednesday -> Monday
                         _utc("2024-07-08 19:30"),     # 4 July Thursday -> Monday
                         _utc("2024-02-26 20:30"),     # Presidents Day Monday 19 Feb -> Monday 26
                         _utc("2024-06-24 19:30")]     # Juneteenth Wednesday -> Monday
    # the report week's own Monday counts: MLK Monday 20 Jan 2025, report Tuesday 21 Jan -> Monday 27 Jan
    assert cot_available_utc(pd.DatetimeIndex(["2025-01-21"]))[0] == _utc("2025-01-27 20:30")
    # a Monday as-of date (the Tuesday was a holiday) uses that week's Friday and the holiday rule
    assert cot_available_utc(pd.DatetimeIndex(["2023-07-03"]))[0] == _utc("2023-07-10 19:30")


def test_cot_shutdown_backlogs_are_not_public_before_the_catch_up():
    got = cot_available_utc(pd.DatetimeIndex(["2018-12-24", "2019-01-08", "2019-03-12", "2025-09-23", "2025-10-14"]))
    assert got[0] == got[1] == _utc("2019-03-11 00:00")
    assert got[2] == _utc("2019-03-15 19:30")          # after the floor window: the normal rule (DST from 10 March)
    assert got[3] == _utc("2025-09-26 19:30")          # before the shutdown: normal
    assert got[4] == _utc("2026-01-31 00:00")


# --------------------------------------------------------------------------- parsing
COT_CSV = (
    "Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,CFTC_Contract_Market_Code,Open_Interest_All,"
    "Prod_Merc_Positions_Long_All,Prod_Merc_Positions_Short_All,Swap_Positions_Long_All,Swap__Positions_Short_All,"
    "M_Money_Positions_Long_All,M_Money_Positions_Short_All\n"
    '"GOLD - COMMODITY EXCHANGE INC.",2025-06-03,088691,500000,20000,120000,30000,180000,200000,50000\n'
    '"SILVER - COMMODITY EXCHANGE INC.",2025-06-03,084691,150000,1,2,3,4,5,6\n'
    '"GOLD - COMMODITY EXCHANGE INC.",2025-06-10,88691,510000,21000,121000,30000,181000,210000,40000\n'
)


def test_parse_cot_keeps_gold_and_derives_commercials_net():
    df = parse_cot(COT_CSV)
    assert list(df["report_date"]) == [pd.Timestamp("2025-06-03"), pd.Timestamp("2025-06-10")]
    assert list(df["mm_long"] - df["mm_short"]) == [150_000, 170_000]
    assert list(df["open_interest"]) == [500_000, 510_000]
    assert list(df["comm_net"]) == [20000 - 120000 + 30000 - 180000, 21000 - 121000 + 30000 - 181000]
    rows = cot_frame(df, pd.Timestamp("2025-06-14"))
    assert set(rows["series"]) == {"cot_mm_long", "cot_mm_short", "cot_mm_net", "cot_open_interest", "cot_comm_net"}
    assert (rows["series_id"] == COT_GOLD_CODE).all() and (rows["source"] == "cftc").all()
    assert (rows["available_utc"] == rows["ts_utc"]).all() and (rows["vintage"] == pd.Timestamp("2025-06-14")).all()
    assert rows.loc[rows["value_date"] == pd.Timestamp("2025-06-03"), "available_utc"].iloc[0] == _utc("2025-06-06 19:30")


def test_parse_cot_refuses_a_changed_format():
    with pytest.raises(ValueError, match="lacks"):
        parse_cot(COT_CSV.replace("M_Money_Positions_Short_All", "MM_Short"))


GLD_CSV = (
    "SPDR Gold Shares historical data\n\n"
    "Date,GLD Close,Total Net Asset Value Ounces in the Trust as at 4.15 p.m. NYT,"
    "Total Net Asset Value Tonnes in the Trust as at 4.15 p.m. NYT\n"
    "03-Jul-2025, 309.10, \"27,876,000\", 867.08\n"
    "04-Jul-2025, HOLIDAY, HOLIDAY, HOLIDAY\n"
    "07-Jul-2025, 307.50, \"27,900,000\", \"867.83\"\n"
)


def test_parse_gld_reads_the_archive_and_drops_holiday_rows():
    df = parse_gld_csv(GLD_CSV)
    assert list(df["value_date"]) == [pd.Timestamp("2025-07-03"), pd.Timestamp("2025-07-07")]
    assert list(df["tonnes"]) == [867.08, 867.83]
    rows = gld_frame(df, pd.Timestamp("2025-07-12"))
    # Thursday 3 July -> Monday 7 July 14:00 UTC (Friday 4 July is a federal holiday); Monday -> Tuesday
    assert list(rows["available_utc"]) == [_utc("2025-07-07 14:00"), _utc("2025-07-08 14:00")]
    assert (rows["series"] == "gld_tonnes").all() and (rows["source"] == "spdr").all()


def test_gld_source_unavailable_is_explicit_not_scraped():
    for text in ("<!DOCTYPE html><html>Just a moment...</html>", "a,b\n1,2\n"):
        with pytest.raises(GldSourceUnavailable):
            parse_gld_csv(text)
    assert gld_available_utc(pd.DatetimeIndex(["2025-01-17"]))[0] == _utc("2025-01-21 14:00")   # MLK Monday skipped


# --------------------------------------------------------------------------- vintages
def test_revisions_add_rows_and_the_first_release_is_kept():
    first = _cot(n=5)
    t1 = _utc("2025-06-01 05:00")
    pub = merge_releases(None, first, t1, {"cftc": t1})
    fresh = _cot(n=6, vintage="2025-06-08")
    rev_day = pd.Timestamp("2024-01-09")
    mask = (fresh["series"] == "cot_mm_net") & (fresh["value_date"] == rev_day)
    fresh.loc[mask, "value"] += 1.0
    t2 = _utc("2025-06-08 05:00")
    out = merge_releases(pub, fresh, t2, {"cftc": t1})
    net = out[(out["series"] == "cot_mm_net") & (out["value_date"] == rev_day)]
    assert len(net) == 2 and net["available_utc"].iloc[0] < net["available_utc"].iloc[1] == t2
    # the 6th report is new: before the previous download (t1), so it was late -> stamped at t2
    sixth = out[(out["series"] == "cot_mm_net") & (out["value_date"] == pd.Timestamp("2024-02-06"))]
    assert len(sixth) == 1 and sixth["available_utc"].iloc[0] == t2
    # unchanged values add nothing
    assert len(merge_releases(out, fresh, t2, {"cftc": t2})) == len(out)


def test_a_new_report_after_the_previous_download_keeps_its_rule_stamp():
    t1 = _utc("2025-06-07 05:00")
    pub = merge_releases(None, _cot(n=3, start="2025-05-20"), t1, {"cftc": t1})
    fresh = _cot(n=4, start="2025-05-20")                                 # adds report 2025-06-10, released 13 June
    out = merge_releases(pub, fresh, _utc("2025-06-14 05:00"), {"cftc": t1})
    row = out[(out["series"] == "cot_mm_net") & (out["value_date"] == pd.Timestamp("2025-06-10"))]
    assert row["available_utc"].iloc[0] == _utc("2025-06-13 19:30")


def test_release_file_round_trips_rows_and_last_download_times(tmp_path):
    rel = pd.concat([_cot(n=3), _gld(n=5)], ignore_index=True)
    ok = {"cftc": _utc("2025-06-07 05:00"), "spdr": _utc("2025-06-07 05:01")}
    write_release(rel, str(tmp_path / "positioning.parquet"), ok)
    back, ok2 = read_release(str(tmp_path / "positioning.parquet"))
    assert len(back) == len(rel) and ok2 == ok
    files = read_positioning_files(tmp_path)
    assert len(files) == len(rel) and str(files["available_utc"].dt.tz) == "UTC"
    rel.drop(columns=["available_utc"]).to_parquet(tmp_path / "bad.parquet", index=False)
    with pytest.raises(ValueError, match="available_utc"):
        read_positioning_files(tmp_path / "bad.parquet")


# --------------------------------------------------------------------------- features
def test_features_match_their_definitions():
    rel = pd.concat([_cot(), _gld()], ignore_index=True)
    fr = pf.positioning_frames(rel)
    cot = rel[rel["series"] == "cot_mm_net"].sort_values("value_date")
    oi = rel[rel["series"] == "cot_open_interest"].sort_values("value_date")
    pct = 100 * cot["value"].to_numpy() / oi["value"].to_numpy()
    assert np.allclose(fr["pos_cot_mm_net_pct_oi"]["pos_cot_mm_net_pct_oi"], pct)
    assert np.isclose(fr["pos_cot_mm_net_pct_oi_chg4"]["pos_cot_mm_net_pct_oi_chg4"].iloc[-1], pct[-1] - pct[-5])
    last = pct[-52:]
    assert np.isclose(fr["pos_cot_mm_net_pct_oi_z52"]["pos_cot_mm_net_pct_oi_z52"].iloc[-1],
                      (last[-1] - last.mean()) / last.std(ddof=1))
    assert len(fr["pos_cot_mm_net_pct_oi_z52"]) == 70 - 40 + 1                     # needs 40 reports
    t = rel[rel["series"] == "gld_tonnes"].sort_values("value_date")["value"].to_numpy()
    assert np.isclose(fr["pos_gld_tonnes_chg5_pct"]["pos_gld_tonnes_chg5_pct"].iloc[-1], 100 * (t[-1] / t[-6] - 1))
    assert np.isclose(fr["pos_gld_tonnes_chg20_pct"]["pos_gld_tonnes_chg20_pct"].iloc[-1], 100 * (t[-1] / t[-21] - 1))


def test_a_seeded_future_release_is_never_seen_before_its_available_utc(enabled):
    """Fake release: the last COT report carries an absurd value. Bars before its Friday stamp must not see it; the
    bar at the stamp must."""
    rel = _cot(n=50)
    last_day = rel["value_date"].max()
    rel.loc[(rel["series"] == "cot_mm_net") & (rel["value_date"] == last_day), "value"] = 9e9
    stamp = rel.loc[rel["value_date"] == last_day, "available_utc"].iloc[0]
    bars = pd.DataFrame({"ts_utc": pd.date_range(stamp - pd.Timedelta(days=10), stamp + pd.Timedelta(days=2),
                                                 freq="15min", tz="UTC")})
    X = build_features(bars, [enabled], {"positioning": rel})
    before = (bars["ts_utc"] < stamp).to_numpy()
    assert X.loc[before, "pos_cot_mm_net_pct_oi"].max() < 100
    assert (X.loc[~before, "pos_cot_mm_net_pct_oi"] > 1e5).all()
    # a Tuesday as-of value is still unseen on Thursday
    tue = (bars["ts_utc"] == pd.Timestamp(last_day, tz="UTC") + pd.Timedelta(days=2, hours=12)).to_numpy()
    assert X.loc[tue, "pos_cot_mm_net_pct_oi"].item() < 100


def test_positioning_features_on_a_truncated_release_match_the_full_one_before_the_cut(enabled):
    rel = pd.concat([_cot(n=80), _gld(n=380)], ignore_index=True)
    bars = pd.DataFrame({"ts_utc": pd.date_range("2024-11-01", "2025-06-01", freq="1h", tz="UTC")})
    cut = _utc("2025-02-12 12:00")
    full = build_features(bars, [enabled], {"positioning": rel})
    part = build_features(bars, [enabled], {"positioning": rel[rel["available_utc"] <= cut]})
    before = (bars["ts_utc"] < cut).to_numpy()
    for c in pf.COLUMNS:
        assert full.loc[before, c].notna().any(), c
        assert np.allclose(full.loc[before, c], part.loc[before, c], equal_nan=True), c


def test_a_revision_never_changes_what_an_earlier_bar_saw(enabled):
    t1 = _utc("2025-06-01")
    pub = merge_releases(None, _cot(n=50), t1, {"cftc": t1})
    fresh = _cot(n=50)
    fresh.loc[fresh["series"] == "cot_mm_net", "value"] += 50_000                  # every report revised
    out = merge_releases(pub, fresh, _utc("2025-06-08 05:00"), {"cftc": t1})
    bars = pd.DataFrame({"ts_utc": pd.date_range("2024-06-01", "2025-07-01", freq="6h", tz="UTC")})
    a = build_features(bars, [enabled], {"positioning": pub})
    b = build_features(bars, [enabled], {"positioning": out})
    assert np.allclose(a["pos_cot_mm_net_pct_oi"], b["pos_cot_mm_net_pct_oi"], equal_nan=True)


def test_without_positioning_data_the_columns_are_nan_and_fixed(enabled):
    bars = pd.DataFrame({"ts_utc": pd.date_range("2025-01-01", periods=10, freq="15min", tz="UTC")})
    X = build_features(bars, [enabled], {})
    assert list(X.columns.drop("ts_utc")) == list(pf.COLUMNS) and X.drop(columns=["ts_utc"]).isna().all().all()


def test_opt_in_leaves_the_default_feature_version_unchanged():
    from goldbot.features.registry import FEATURES, feature_version
    from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES, research_feature_names
    assert "positioning" not in FEATURES                      # importing the module registers nothing
    base = feature_version(DEFAULT_FEATURE_NAMES)
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    try:
        name = pf.enable()
        assert pf.enable() == name                            # idempotent
        assert "positioning" not in DEFAULT_FEATURE_NAMES and feature_version(DEFAULT_FEATURE_NAMES) == base
        rel = _cot(n=3)
        assert research_feature_names({"positioning": rel}) == DEFAULT_FEATURE_NAMES
        assert feature_version([*DEFAULT_FEATURE_NAMES, name]) != base
    finally:
        registry.FEATURES.clear()
        registry.FEATURES.update(feats)
        registry.SIGNED.clear()
        registry.SIGNED.update(signed)


def _script():
    spec = importlib.util.spec_from_file_location(
        "positioning_data", Path(__file__).resolve().parents[1] / "scripts" / "positioning_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_workflow_script_keeps_gld_rows_when_the_source_is_unavailable(tmp_path, monkeypatch, capsys):
    mod = _script()
    prev = tmp_path / "prev.parquet"
    write_release(pd.concat([_cot(n=3), _gld(n=5)], ignore_index=True), str(prev), {"cftc": _utc("2025-06-01")})
    cot = pd.DataFrame({"report_date": pd.date_range("2024-01-02", periods=4, freq="7D"), "mm_long": 1.0,
                        "mm_short": 0.0, "open_interest": 10.0, "comm_net": -1.0})
    monkeypatch.setattr(mod, "fetch_cot", lambda years: cot)

    def blocked():
        raise GldSourceUnavailable("GLD archive returned HTML")
    monkeypatch.setattr(mod, "fetch_gld", blocked)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(prev)]) == 0
    df, ok = read_release(str(out))
    assert (df["series"] == "gld_tonnes").sum() == 5 and "cftc" in ok and "spdr" not in ok
    assert "GLD SOURCE UNAVAILABLE" in capsys.readouterr().out

    def down(years):
        raise RuntimeError("cftc.gov: 503")
    monkeypatch.setattr(mod, "fetch_cot", down)
    assert mod.main(["--out", str(out), "--existing", str(prev), "--skip-gld"]) == 1
    assert len(read_release(str(out))[0]) == len(read_release(str(prev))[0])       # published rows kept


@pytest.mark.integration
def test_positioning_sync_loads_the_release_into_the_store(tmp_path, monkeypatch):
    from goldbot.data import release

    def no_release(token=None, repo=release.REPO, tag="data-v1"):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(release, "list_assets", no_release)
    assert release.sync_release_positioning(Store(tmp_path / "s0")) == 0
    rel = pd.concat([_cot(n=10), _gld(n=30)], ignore_index=True)
    src = tmp_path / "positioning.parquet"
    write_release(rel, str(src), {})
    monkeypatch.setattr(release, "list_assets", lambda token=None, repo=release.REPO, tag="data-v1":
                        [{"name": "positioning.parquet", "size": src.stat().st_size, "url": "x"}]
                        if tag == "positioning-v1" else [])

    def fake_download(asset, dest_dir, token=None):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / asset["name"]).write_bytes(src.read_bytes())
        return dest_dir / asset["name"]
    monkeypatch.setattr(release, "download", fake_download)
    store = Store(tmp_path / "store")
    assert release.sync_release_positioning(store, raw_dir=tmp_path / "raw") == len(rel)
    release.sync_release_positioning(store, raw_dir=tmp_path / "raw")              # idempotent
    got = release.store_positioning(store)
    assert len(got) == len(rel) and set(got["source"]) == {"cftc", "spdr"}


@pytest.mark.integration
def test_research_pass_positioning_frame_passes_the_lookahead_check(enabled):
    """The pipeline's own leakage check (history and the positioning table cut at 70%) on the research frame."""
    from goldbot.data.resample import resample_bars, ticks_to_1m
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.research.pipeline import lookahead_check
    bars = resample_bars(ticks_to_1m(synthetic_ticks("2025-03-03", "2025-04-26", ticks_per_minute=2, seed=7)),
                         "1h").reset_index(drop=True)
    rel = pd.concat([_cot(n=70, start="2024-01-02"), _gld(n=330, start="2024-01-02")], ignore_index=True)
    out = lookahead_check(bars, None, feature_names=[enabled], ctx={"positioning": rel})
    assert out["lookahead_columns"] == [] and out["columns_checked"] == len(pf.COLUMNS)


def test_research_pass_adds_positioning_only_with_the_flag(enabled):
    from goldbot.research.pipeline import research_feature_names
    spec = importlib.util.spec_from_file_location(
        "research_pass", Path(__file__).resolve().parents[1] / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.feature_ctx(None, None) == (None, None)
    assert mod.load_positioning("")[1]["used"] is False
    rel = _cot(n=3)
    ctx, names = mod.feature_ctx(None, rel)
    assert ctx is not None and names == [*research_feature_names(ctx), "positioning"]
