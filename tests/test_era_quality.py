"""D1 source map, D2 HistData parsing/holdout cut, D4 R11 tiers and the clock check (lane W1-E). Synthetic data only."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import era_quality as eq
from goldbot.instruments import load_instruments

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("histdata_m1", ROOT / "scripts" / "histdata_m1.py")
assert _spec and _spec.loader
hd = importlib.util.module_from_spec(_spec)
sys.modules["histdata_m1"] = hd
_spec.loader.exec_module(hd)

HS = eq.load_history_sources()
MONTHS = pd.period_range("2003-05", "2025-09", freq="M")


def test_every_owner_instrument_is_mapped_and_nothing_else():
    owner = {k for k, s in load_instruments().instruments.items() if s.enabled}
    assert set(HS.instruments) == owner and len(owner) == 25


def test_histdata_covers_23_instruments_with_its_code_reuse_split_by_era():
    """The plan said 22 with UDX = USINDEX; the first pull's era bands showed UDXUSD is the Dow to 2018 and the dollar
    index from 2019, and GRXEUR leaves DAX levels after 2020-06 (2026-11-25)."""
    with_hd = {k for k, s in HS.instruments.items() if s.histdata}
    assert set(HS.instruments) - with_hd == {"BTCUSD", "XAUJPY"}
    assert all(HS.instruments[k].source_tz.get("histdata") == "America/New_York" for k in with_hd)
    us30, usdx, de40 = (HS.instruments[k] for k in ("US30", "USINDEX", "DE40"))
    h18, h19, h20 = us30.histdata_for(2018), usdx.histdata_for(2019), de40.histdata_for(2020)
    assert h18 is not None and h19 is not None and h20 is not None
    assert h18.code == "UDXUSD" and us30.histdata_for(2019) is None
    assert h19.code == "UDXUSD" and usdx.histdata_for(2018) is None
    assert h20.end() == pd.Timestamp("2020-07-01", tz="UTC") and de40.histdata_for(2021) is None
    assert de40.histdata_years(2003, 2025) == [*range(2010, 2021), 2024, 2025]
    assert HS.instruments["XTIUSD"].histdata_years(2003, 2025)[-1] == 2023
    assert HS.instruments["XAUEUR"].histdata_years(2003, 2025) == list(range(2009, 2019))


@pytest.mark.parametrize("key", sorted(HS.instruments))
def test_era_bands_catch_seeded_scale_errors_in_every_month(key):
    src = HS.instruments[key]
    start = max(pd.Period(e.from_, freq="M") for e in src.reference[-1:])   # the oldest reference era
    for p in MONTHS[MONTHS >= start]:
        band = src.band(p)
        assert band is not None, f"{key} {p}: no band (fail closed would refuse real data)"
        lo, hi = band
        for true in (lo, (lo * hi) ** 0.5, hi):
            assert src.price_ok(true, p)
            for wrong in (true * 10, true * 100, true / 10, true / 100):
                assert not src.price_ok(wrong, p), (key, str(p), true, wrong)


def test_a_band_that_cannot_catch_x10_is_refused():
    raw = eq.load_yaml(eq.DEFAULT_HISTORY_SOURCES)
    raw["instruments"]["EURUSD"]["price_bands"] = [[2000, 2026, 0.5, 6.0]]
    with pytest.raises(ValueError, match="x10"):
        eq.HistorySources.model_validate(raw)


def test_holdout_cut_is_the_quant_r10_date_and_cannot_move_in_yaml():
    assert eq.HOLDOUT_CUT == pd.Timestamp("2025-10-01", tz="UTC")
    raw = eq.load_yaml(eq.DEFAULT_HISTORY_SOURCES)
    raw["holdout_cut"] = "2026-10-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="R10"):
        eq.HistorySources.model_validate(raw)


def _ref(key: str, month: str) -> eq.ReferenceEra:
    e = HS.instruments[key].reference_for(month)
    assert e is not None
    return e


def test_synthetic_references_are_not_native():
    assert _ref("EURUSD", "2010-01").native
    assert _ref("XAUUSD", "2010-01").native
    assert not _ref("XAUEUR", "2010-01").native
    assert _ref("USINDEX", "2018-01").native
    assert not _ref("USINDEX", "2017-11").native
    assert _ref("EURGBP", "2004-05").legs == {"EURGBP": 1.0}
    assert not _ref("EURGBP", "2010-05").native


def test_r11_thresholds_are_frozen():
    """Quant R11, frozen 2026-11-25 before any grade: a change needs a new R11_ID and the quant's sign-off."""
    assert eq.R11_ID == "r11-v1-2026-11-25"
    assert eq.R11 == {
        "coverage_median_min": 0.85, "thin_day_coverage": 0.60, "thin_day_share_max": 0.05, "flat_share_max": 0.20,
        "stale_minutes": 30.0, "stale_runs_max": 0.0, "spike_mad_k": 15.0, "spike_revert_frac": 0.5,
        "spike_rate_max": 1e-4, "range_ratio_lo": 0.90, "range_ratio_hi": 1.02, "range_day_share_min": 0.95,
        "close_bp": 10.0, "close_day_share_min": 0.98, "clock_offset_max_min": 1.0, "clock_search_min": 120.0,
        "h1_agree_spread_mult": 2.0, "h1_agree_share_min": 0.99}


# ------------------------------------------------------------------------------------------------ synthetic market
def _walk(start: str, end: str, seed: int = 1, vol: float = 2e-4) -> pd.DataFrame:
    """Weekday 1m bid bars (FX hours: Sunday 22:00 to Friday 21:00 UTC) of a log random walk."""
    ts = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    wd, h = ts.dayofweek, ts.hour
    open_ = (wd < 4) | ((wd == 4) & (h < 21)) | ((wd == 6) & (h >= 22))
    ts = ts[open_]
    rng = np.random.default_rng(seed)
    c = 1.2 * np.exp(np.cumsum(rng.normal(0, vol, len(ts))))
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 3, len(ts))))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 3, len(ts))))
    return pd.DataFrame({"ts_utc": ts, "bid_open": o, "bid_high": hi, "bid_low": lo, "bid_close": c})


def _hourly(bars: pd.DataFrame, shift_min: int = 0) -> pd.DataFrame:
    b = bars.assign(ts_utc=bars["ts_utc"] + pd.Timedelta(minutes=shift_min))
    g = b.groupby(b["ts_utc"].dt.floor("h"))
    return pd.DataFrame({"open": g["bid_open"].first(), "high": g["bid_high"].max(), "low": g["bid_low"].min(),
                         "close": g["bid_close"].last(), "spread": 1e-4}).rename_axis("ts_utc").reset_index()


def test_clean_month_is_tier_a_against_a_native_reference():
    bars = _walk("2010-03-01", "2010-04-01")
    g = eq.grade_month("EURUSD", "2010-03", bars, eq.daily_from_hourly(_hourly(bars)), 1440, True)
    assert g.tier == "A", g.reasons


def test_same_month_on_a_synthetic_reference_is_at_most_b():
    bars = _walk("2010-03-01", "2010-04-01")
    g = eq.grade_month("XAUEUR", "2010-03", bars, eq.daily_from_hourly(_hourly(bars)), 1440, False)
    assert g.tier == "B" and "range_not_evaluable" in g.reasons


def test_seeded_bad_month_lands_in_c_and_quarantine_in_f():
    bars = _walk("2010-03-01", "2010-04-01")
    ref = eq.daily_from_hourly(_hourly(bars))
    bad = bars.copy()
    bad.loc[bad.index % 7 == 0, "bid_close"] *= 1.01          # closes 100 bp off the reference on most days
    g = eq.grade_month("EURUSD", "2010-03", bad, ref, 1440, True)
    assert g.tier == "C" and any(r.startswith("close_vs_ref") or r.startswith("spikes") for r in g.reasons)
    thin = bars.iloc[::3]                                      # a third of the minutes: thin, close still right
    g2 = eq.grade_month("EURUSD", "2010-03", thin, ref, 1440, True)
    assert g2.tier in ("B", "C") and any(r.startswith("coverage_median") for r in g2.reasons)
    g3 = eq.grade_month("EURUSD", "2010-03", bars, ref, 1440, True, quarantined=True)
    assert g3.tier == "F"


def test_no_reference_is_c_never_a():
    bars = _walk("2010-03-01", "2010-04-01")
    g = eq.grade_month("UK100", "2010-03", bars, None, 1320, False)
    assert g.tier == "C" and "no_reference" in g.reasons


def test_spike_mask_flags_a_reverting_spike_only():
    bars = _walk("2010-03-01", "2010-03-03")
    c = bars["bid_close"].copy()
    c.iloc[500] *= 1.02
    m = eq.spike_mask(c)
    assert m.iloc[500] and m.sum() == 1


def test_stale_run_is_counted():
    bars = _walk("2010-03-02", "2010-03-03")
    c = bars["bid_close"].copy()
    c.iloc[100:140] = c.iloc[100]
    assert eq.stale_runs(bars["ts_utc"], c) == 1
    assert eq.stale_runs(bars["ts_utc"], bars["bid_close"]) == 0


@pytest.mark.parametrize("shift", [0, 1, -1, 60, -60, 7])
def test_clock_check_recovers_the_offset(shift):
    bars = _walk("2010-03-01", "2010-05-01", seed=3)
    ref = _hourly(bars, shift_min=shift)
    cc = eq.clock_check("EURUSD", 2010, eq.epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(), ref)
    assert cc.offset_min == shift
    assert cc.passed == (abs(shift) <= 1)


def test_clock_check_without_reference_fails_closed():
    bars = _walk("2010-03-01", "2010-03-05")
    cc = eq.clock_check("UK100", 2010, eq.epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(), bars.iloc[:0])
    assert not cc.passed and cc.reason == "no_reference"


# --------------------------------------------------------------------------------------------- HistData parse + cut
def test_histdata_stamps_are_new_york_with_dst_and_carry_visible_at_and_source():
    text = ("20100115 120000;1.4300;1.4302;1.4299;1.4301;0\n"     # winter: EST = UTC-5
            "20100715 120000;1.2700;1.2702;1.2699;1.2701;0\n")    # summer: EDT = UTC-4
    f = hd.parse_histdata(text, "EURUSD")
    assert list(f["ts_utc"]) == [pd.Timestamp("2010-01-15 17:00", tz="UTC"), pd.Timestamp("2010-07-15 16:00", tz="UTC")]
    assert (f["visible_at"] - f["ts_utc"] == pd.Timedelta(seconds=60)).all()
    assert (f["source"] == "histdata:EURUSD").all()


def test_minutes_at_or_after_the_holdout_cut_are_never_kept():
    text = ("20250930 195800;1.17;1.17;1.17;1.17;0\n"   # 23:58 UTC 09-30: research
            "20250930 195900;1.17;1.17;1.17;1.17;0\n"   # 23:59 UTC: research
            "20250930 200000;1.17;1.17;1.17;1.17;0\n"   # 00:00 UTC 10-01: sealed
            "20251215 120000;1.17;1.17;1.17;1.17;0\n")
    kept, dropped = eq.drop_holdout(hd.parse_histdata(text, "EURUSD"))
    assert dropped == 2 and kept["ts_utc"].max() < eq.HOLDOUT_CUT


def test_split_errors_quarantines_broken_rows_and_keeps_the_rest():
    bars = _walk("2010-03-01", "2010-03-03")
    bars.loc[10, "bid_low"] = float(bars["bid_high"].iloc[10]) * 1.01   # low above high
    bars = pd.concat([bars, bars.iloc[[20]]], ignore_index=True).sort_values("ts_utc", kind="stable")
    i = bars.index[600]
    bars.loc[i, "bid_close"] *= 1.02                                   # a reverting spike: flagged, kept
    bars.loc[i, "bid_high"] = bars.loc[i, "bid_close"]
    clean, err = hd.split_errors(bars.reset_index(drop=True))
    assert set(err["dq_reason"]) == {"ohlc", "duplicate_minute"}
    assert len(clean) + len(err) == len(bars) and not clean["ts_utc"].duplicated().any()
    assert (clean["dq_flag"] == "warning:spike_15mad").sum() == 1


def test_scale_error_year_is_reported():
    bars = _walk("2010-03-01", "2010-03-03")
    assert hd.scale_errors(bars, HS.instruments["EURUSD"]) == []
    assert hd.scale_errors(bars.assign(bid_close=bars["bid_close"] * 10), HS.instruments["EURUSD"])


def test_pull_is_a_no_op_once_done(tmp_path):
    base = tmp_path / "histdata_m1"
    p = hd.year_path(base, "EURUSD", 2010)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    hd.append_manifest(base, {"key": "EURUSD", "year": 2010, "status": "ok"})
    hd.append_manifest(base, {"key": "EURUSD", "year": 2011, "status": "failed"})
    hd.append_manifest(base, {"key": "EURUSD", "year": 2002, "status": "absent"})
    todo = {(k, y) for k, y, _ in hd.tasks_for(["EURUSD"], base)}
    assert ("EURUSD", 2010) not in todo and ("EURUSD", 2011) in todo
    assert min(y for _, y in todo) == 2003 and max(y for _, y in todo) == 2025


def test_a_daily_break_is_not_a_stale_run():
    bars = _walk("2010-03-02", "2010-03-03")
    c = bars["bid_close"].copy()
    keep = ~bars["ts_utc"].between(pd.Timestamp("2010-03-02 10:00", tz="UTC"), pd.Timestamp("2010-03-02 10:59", tz="UTC"))
    c.loc[bars["ts_utc"] == pd.Timestamp("2010-03-02 11:00", tz="UTC")] = c.loc[
        bars["ts_utc"] == pd.Timestamp("2010-03-02 09:59", tz="UTC")].iloc[0]
    assert eq.stale_runs(bars.loc[keep, "ts_utc"], c[keep]) == 0


def test_spike_threshold_uses_mad_of_signed_returns():
    """A 1m random walk has no 15-MAD reverting spikes (the |r| MAD would flag hundreds a month)."""
    bars = _walk("2010-03-01", "2010-04-01", seed=5)
    assert eq.spike_mask(bars["bid_close"]).sum() == 0


def test_an_empty_form_token_is_a_throttle_not_an_absent_year(tmp_path, monkeypatch):
    """HistData answers its per-IP throttle with the year page and an empty token; that year must be retried, not
    recorded as absent (absent years are never asked again)."""
    monkeypatch.setattr(hd, "PAUSE_S", 0.0)
    monkeypatch.setattr(hd, "THROTTLE_WAIT_S", 0.0)
    throttled = b'<a>HISTDATA_COM_ASCII_XAUEUR_M1_2020.zip</a><input name="tk" id="tk" value="" />'
    monkeypatch.setattr(hd, "_get", lambda *a, **k: throttled)          # the control page is empty too
    assert hd.download_zip("XAUEUR", 2020, tmp_path) == (None, "throttled")
    control = b'<input name="tk" id="tk" value="c79e36fd" />'
    monkeypatch.setattr(hd, "_get", lambda url, *a, **k: control if url == hd.CONTROL_PAGE else throttled)
    assert hd.download_zip("XAUEUR", 2020, tmp_path) == (None, "absent")   # only this pair-year has no token
    monkeypatch.setattr(hd, "_get", lambda *a, **k: b'<input name="tk" id="tk" value="" />')
    assert hd.download_zip("XAUEUR", 2001, tmp_path) == (None, "absent")


def test_one_news_hour_does_not_move_the_clock_peak():
    """Rank correlation: a single huge hourly move (SNB 2015) must not pull the offset off 0."""
    bars = _walk("2015-01-05", "2015-02-28", seed=7)
    i = bars.index[len(bars) // 2]
    bars.loc[i:, ["bid_open", "bid_high", "bid_low", "bid_close"]] *= 0.85        # a 15 % jump, both sources
    cc = eq.clock_check("USDCHF", 2015, eq.epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(), _hourly(bars))
    assert cc.offset_min == 0 and cc.passed


def test_a_shift_in_one_month_is_caught_per_month():
    bars = _walk("2020-01-01", "2020-05-01", seed=9)
    ref = _hourly(bars)
    march = ref["ts_utc"].dt.month == 3
    shifted = _hourly(bars, shift_min=60)
    ref = pd.concat([ref[~march], shifted[shifted["ts_utc"].dt.month == 3]]).sort_values("ts_utc").reset_index(drop=True)
    offs = eq.month_offsets(eq.epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy(), ref)
    assert offs["2020-03"] == 60 and offs["2020-02"] == 0 and offs["2020-04"] == 0
    g = eq.grade_month("EURUSD", "2020-03", bars, eq.daily_from_hourly(_hourly(bars)), 1440, True,
                       clock_offset=offs["2020-03"])
    assert g.tier == "F" and g.reasons[0].startswith("clock_month")


def test_eet_minus_7_rule_switches_on_eu_dates():
    """2019: US DST from 03-10, EU from 03-31. In between, an EET-7 stamp is one hour later in UTC than a NY one."""
    local = pd.Series(pd.to_datetime(["2019-03-04 12:00", "2019-03-20 12:00", "2019-04-10 12:00"]))
    ny, eet = hd.histdata_to_utc(local, "ny"), hd.histdata_to_utc(local, "eet-7")
    assert list((eet - ny).dt.total_seconds() / 60) == [0.0, 60.0, 0.0]


def test_reconvert_round_trips_ny_stamps():
    bars = _walk("2019-03-04", "2019-04-06", seed=2)
    bars["source"] = "histdata:EURUSD"
    same = hd.reconvert(bars, "ny")
    assert same["ts_utc"].equals(bars["ts_utc"].astype(same["ts_utc"].dtype)) or len(same) == len(bars)
    alt = hd.reconvert(bars, "eet-7")
    moved = alt["ts_utc"].between(pd.Timestamp("2019-03-11", tz="UTC"), pd.Timestamp("2019-03-29", tz="UTC"))
    assert moved.any() and (alt["visible_at"] - alt["ts_utc"] == pd.Timedelta(seconds=60)).all()


def test_cached_zip_is_trimmed_to_the_holdout_cut(tmp_path):
    """Review of #453: the 2025 source zips held Oct-Dec 2025. Both stamp rules are UTC-4 in late September."""
    import zipfile
    z = tmp_path / "EURUSD_2025.zip"
    rows = ["20250930 195900;1.17;1.17;1.17;1.17;0", "20250930 200000;1.17;1.17;1.17;1.17;0",
            "20251215 120000;1.17;1.17;1.17;1.17;0"]
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("DAT_ASCII_EURUSD_M1_2025.csv", "\n".join(rows) + "\n")
    assert hd.trim_zip_to_cut(z) == (1, 2)
    assert hd.trim_zip_to_cut(z) == (1, 0)
    kept = hd.parse_histdata(zipfile.ZipFile(z).read("DAT_ASCII_EURUSD_M1_2025.csv"), "EURUSD")
    assert kept["ts_utc"].max() < eq.HOLDOUT_CUT


# ------------------------------------------------------------------------------------- r11-v2 (quant ruling, #453)
def _ref_hourly_hl(bars: pd.DataFrame, spread: float = 1e-4) -> pd.DataFrame:
    h = _hourly(bars)
    return h.assign(spread=spread)


def test_v2_news_spike_inside_the_reference_range_is_not_confirmed():
    bars = _walk("2010-03-01", "2010-03-03", seed=11)
    i = bars.index[700]
    bars.loc[i, ["bid_close", "bid_high"]] = float(bars["bid_close"].iloc[700]) * 1.004   # a 40 bp news minute
    ref = _ref_hourly_hl(bars)                                         # the reference saw the same move
    cand = eq.spike_mask(bars["bid_close"])
    assert cand.loc[i]
    assert not eq.spike_confirmed(bars, cand, ref).loc[i]


def test_v2_off_market_print_outside_range_plus_tol_is_confirmed():
    bars = _walk("2010-03-01", "2010-03-03", seed=11)
    ref = _ref_hourly_hl(bars)                                         # reference built BEFORE the bad print
    i = bars.index[700]
    bars.loc[i, ["bid_close", "bid_high"]] = float(bars["bid_close"].iloc[700]) * 1.004
    cand = eq.spike_mask(bars["bid_close"])
    assert eq.spike_confirmed(bars, cand, ref).loc[i]


def test_v2_synthetic_leg_bounds_bracket_the_cross():
    """XAUEUR from legs: high <= XAU_H / EUR_L, low >= XAU_L / EUR_H; a print above the bound is confirmed."""
    xau = pd.DataFrame({"high": [1210.0], "low": [1190.0], "close": [1200.0], "spread": [0.3]},
                       index=pd.DatetimeIndex([pd.Timestamp("2010-03-02 10:00", tz="UTC")], name="ts_utc"))
    eur = pd.DataFrame({"high": [1.36], "low": [1.34], "close": [1.35], "spread": [1e-4]}, index=xau.index)
    ref = eq.combine_legs({"XAUUSD": xau, "EURUSD": eur}, {"XAUUSD": 1.0, "EURUSD": -1.0})
    assert ref["high"].iloc[0] == pytest.approx(1210 / 1.34) and ref["low"].iloc[0] == pytest.approx(1190 / 1.36)
    t = pd.date_range("2010-03-02 10:00", periods=5, freq="1min", tz="UTC")
    bars = pd.DataFrame({"ts_utc": t, "bid_close": [889.0, 889.2, 905.0, 889.1, 889.0]})
    cand = pd.Series([False, False, True, False, False])
    assert eq.spike_confirmed(bars, cand, ref.reset_index()).tolist() == [False, False, True, False, False]


def test_v2_offsetting_high_and_low_errors_fail_range_v2():
    """A high too high and a low too high keep R/R_ref near 1 (v1 passed it); v2 compares each extreme."""
    bars = _walk("2010-03-01", "2010-04-01", seed=4)
    ref = _ref_hourly_hl(bars)
    d = eq.daily_from_minutes(bars)
    rd = eq.daily_from_hourly(ref)
    shift = (rd["high"] - rd["low"]).median() * 0.5
    bad = d.assign(high=d["high"] + shift, low=d["low"] + shift)
    share, ratio = eq.range_v2(bad, rd, ref)
    assert ratio == pytest.approx(1.0, abs=0.05) and share is not None and share < 0.9
    good_share, _ = eq.range_v2(d, rd, ref)
    assert good_share == 1.0


def test_v2_gap_week_60_min_shift_is_rejected():
    """2021: the spring gap is 2 weeks (03-14..03-28). A year shifted 60 min ONLY in the gap weeks still peaks at 0
    per month; the per-gap-window check sees 60."""
    bars = _walk("2021-03-01", "2021-04-12", seed=21)
    ref = _hourly(bars)
    wins = eq.gap_windows(2021)
    assert wins[0][0] == pd.Timestamp("2021-03-14 07:00", tz="UTC")
    a, b = wins[0]
    in_gap = (ref["ts_utc"] >= a) & (ref["ts_utc"] < b)
    shifted = _hourly(bars, shift_min=60)
    ref2 = pd.concat([ref[~in_gap], shifted[(shifted["ts_utc"] >= a) & (shifted["ts_utc"] < b)]])
    ref2 = ref2.sort_values("ts_utc").reset_index(drop=True)
    ts, c = eq.epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy()
    assert eq.gap_week_offsets(ts, c, ref2, 2021)[str(a.date())] == 60     # the gap-window check is not
    assert eq.gap_week_offsets(ts, c, ref, 2021)[str(a.date())] == 0


def test_v2_same_source_detector():
    bars = _walk("2019-03-01", "2019-04-01", seed=3)
    ts, c = eq.epoch_ns_utc(bars["ts_utc"]), bars["bid_close"].to_numpy()
    same = _hourly(bars)
    assert eq.same_source_share(ts, c, same, 1e5) == 1.0
    other = same.assign(close=same["close"] * (1 + np.random.default_rng(0).normal(0, 3e-5, len(same))))
    share = eq.same_source_share(ts, c, other, 1e5)
    assert share is not None and share < eq.R11_V2["same_source_share"]


def test_v2_same_source_years_cap_at_a_consistent_or_wait_for_the_fix():
    bars = _walk("2019-03-01", "2019-04-01", seed=3)
    ref = _ref_hourly_hl(bars)
    noon = pd.date_range("2019-03-01 17:00", "2019-03-29 17:00", freq="B", tz="UTC")
    fix = pd.DataFrame({"fix_utc": noon,
                        "value": np.interp(eq.epoch_ns_utc(noon), eq.epoch_ns_utc(bars["ts_utc"]),
                                           bars["bid_close"].to_numpy())})
    g = eq.grade_month_v2("EURUSD", "2019-03", bars, ref, 1440, True, same_source=True, fix=fix, fix_status="ok")
    assert g.tier == "A-consistent", g.reasons
    p = eq.grade_month_v2("XAUUSD", "2019-03", bars, ref, 1440, True, same_source=True, fix_status="pending")
    assert p.tier == "P"
    capped = eq.grade_month_v2("US500", "2019-03", bars, ref, 1440, True, same_source=True, fix_status="cap_b")
    assert capped.tier == "B"
    indep = eq.grade_month_v2("EURUSD", "2010-03", bars, ref, 1440, True)
    assert indep.tier == "A" and indep.r11_id == "r11-v2-2026-11-25"


def test_confirmed_spike_crossing_a_barrier_makes_no_touch():
    bars = _walk("2010-03-01", "2010-03-02", seed=6)
    level = float(bars["bid_high"].max()) * 1.002
    i = bars.index[300]
    bars.loc[i, "bid_high"] = level * 1.001                            # the only bar above the barrier
    bars["dq_flag"] = ""
    bars.loc[i, "dq_flag"] = "warning:spike_confirmed"
    hi, _ = eq.touch_extremes(bars)
    assert not (hi >= level).any()
    bars.loc[i, "dq_flag"] = ""
    assert (eq.touch_extremes(bars)[0] >= level).any()


def test_v2_thresholds_are_frozen():
    assert eq.R11_V2_ID == "r11-v2-2026-11-25"
    assert {k: eq.R11_V2[k] for k in ("same_source_share", "fix_bp", "fix_day_share_min", "spike_tol_spread_mult",
                                     "spike_tol_bp", "range2_tol_spread_mult", "range2_tol_range_frac",
                                     "range2_day_share_min", "range2_ratio_lo", "range2_ratio_hi",
                                     "gap_week_offset_max")} == {
        "same_source_share": 0.50, "fix_bp": 10.0, "fix_day_share_min": 0.98, "spike_tol_spread_mult": 2.0,
        "spike_tol_bp": 2.0, "range2_tol_spread_mult": 3.0, "range2_tol_range_frac": 0.05,
        "range2_day_share_min": 0.90, "range2_ratio_lo": 0.90, "range2_ratio_hi": 1.10, "gap_week_offset_max": 1.0}
    assert eq.R11_ID == "r11-v1-2026-11-25"                            # v1 kept for its grades


def test_fix_spec_is_exactly_one_kind():
    with pytest.raises(ValueError):
        eq.FixSpec(series={"DEXUSEU": 1.0})                            # no timestamp
    with pytest.raises(ValueError):
        eq.FixSpec(cap="B", pending="x")
