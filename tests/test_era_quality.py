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


def test_histdata_covers_the_22_instruments_of_the_plan():
    with_hd = {k for k, s in HS.instruments.items() if s.histdata is not None}
    assert len(with_hd) == 22
    assert set(HS.instruments) - with_hd == {"US30", "BTCUSD", "XAUJPY"}
    assert all(HS.instruments[k].source_tz.get("histdata") == "America/New_York" for k in with_hd)


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


def test_synthetic_references_are_not_native():
    assert HS.instruments["EURUSD"].reference_for("2010-01").native
    assert HS.instruments["XAUUSD"].reference_for("2010-01").native
    assert not HS.instruments["XAUEUR"].reference_for("2010-01").native
    assert HS.instruments["USINDEX"].reference_for("2018-01").native
    assert not HS.instruments["USINDEX"].reference_for("2017-11").native
    assert HS.instruments["EURGBP"].reference_for("2004-05").legs == {"EURGBP": 1.0}
    assert not HS.instruments["EURGBP"].reference_for("2010-05").native


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
    bars.loc[10, "bid_low"] = bars.loc[10, "bid_high"] * 1.01        # low above high
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
    monkeypatch.setattr(hd, "_get", lambda *a, **k: throttled)
    assert hd.download_zip("XAUEUR", 2020, tmp_path) == (None, "throttled")
    monkeypatch.setattr(hd, "_get", lambda *a, **k: b'<input name="tk" id="tk" value="" />')
    assert hd.download_zip("XAUEUR", 2001, tmp_path) == (None, "absent")
