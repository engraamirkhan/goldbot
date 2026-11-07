"""Long daily history: Goldhub header detection, epoch-unit inference, the stitch cut-over and the row contract
(no duplicate dates, available_utc monotone and never before its date, nothing past the cutoff), on tiny synthetic
files. No network: sources are injected."""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.daily import CUTOFF
from goldbot.data.long_history import (
    LONG_COLUMNS,
    fixing_frame,
    goldhub_frame,
    infer_epoch_unit,
    load_dukascopy_daily,
    load_goldhub,
    local_stamp,
    parse_bbk_csv,
    parse_ecb_csv,
    stitch,
    validate_long,
)
from goldbot.data.long_sources import SPECS, InstrumentSpec, SourceRef, _pair, build_instrument


def _goldhub_xlsx(path: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    about = wb.active
    about.title = "About"
    about.append(["readme"])
    ws = wb.create_sheet("Daily_Indicators")
    for row in (["World Gold Council"], [], ["Notes", "x"], [None, "Name", "USD", "EUR"]):
        ws.append(row)
    for d, v in (("1978-12-29", 226.0), ("1979-01-02", 227.5), ("1979-01-02", 227.5), ("2003-05-02", 340.0),
                 ("2003-05-05", 343.0), ("2003-05-06", 345.0), ("bad", 1.0)):
        ws.append([None, dt.datetime.fromisoformat(d) if d != "bad" else "n/a", v, v * 0.9])
    wb.save(path)
    return path


def _duka(path: Path, unit: str | None = None) -> Path:
    dates = pd.to_datetime(["2003-05-05", "2003-05-06", "2003-05-07", "2003-05-08"])
    df = pd.DataFrame({"open": [342.0, 344.0, 345.0, 346.0], "high": [344.0, 346.0, 347.0, 346.0],
                       "low": [341.0, 343.0, 344.0, 346.0], "close": [343.5, 345.5, 346.0, 346.0]})
    if unit is None:
        df.insert(0, "date", dates)
        df["visible_at"] = (dates + pd.Timedelta(hours=21)).tz_localize("UTC")
        df["source"] = "dukascopy_1m:XAUUSD"
        df.to_parquet(path, index=False)
    else:
        scale = {"s": 1, "ms": 10**3, "us": 10**6, "ns": 10**9}[unit]
        df.insert(0, "Timestamp", (dates - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1) * scale)
        df.to_csv(path, index=False)
    return path


def test_goldhub_header_detection_and_daily_sheet(tmp_path: Path) -> None:
    gh = load_goldhub(_goldhub_xlsx(tmp_path / "gh.xlsx"))
    assert list(gh.columns) == ["date", "close"]
    assert gh["date"].is_unique and gh["date"].is_monotonic_increasing
    assert gh["date"].iloc[0] == pd.Timestamp("1978-12-29") and gh["close"].iloc[0] == 226.0
    assert len(gh) == 5   # the duplicate and the unparseable row are gone
    long = goldhub_frame(gh)
    assert list(long.columns) == list(LONG_COLUMNS) and long["open"].isna().all()
    # 15:30 London: 15:30 UTC in winter
    assert long["available_utc"].iloc[0] == pd.Timestamp("1978-12-29 15:30", tz="UTC")


def test_goldhub_without_usd_header_fails(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    p = tmp_path / "x.xlsx"
    pd.DataFrame([["a", "b"], [1, 2]]).to_excel(p, sheet_name="Daily", header=False, index=False)
    with pytest.raises(ValueError, match="USD"):
        load_goldhub(p)


@pytest.mark.parametrize("unit", ["s", "ms", "us", "ns"])
def test_epoch_unit_inference(tmp_path: Path, unit: str) -> None:
    d = load_dukascopy_daily(_duka(tmp_path / f"d_{unit}.csv", unit))
    assert d["date"].iloc[0] == pd.Timestamp("2003-05-05")
    assert len(d) == 3          # the flat high == low bar is dropped
    assert d["available_utc"].iloc[0] == pd.Timestamp("2003-05-05 21:00", tz="UTC")


def test_infer_epoch_unit_thresholds() -> None:
    t = pd.Timestamp("1978-01-02").value
    assert [infer_epoch_unit(np.array([t // k])) for k in (10**9, 10**6, 10**3, 1)] == ["s", "ms", "us", "ns"]
    with pytest.raises(ValueError):
        infer_epoch_unit(np.array([np.nan]))


def test_stitch_cutover_no_duplicates_and_monotone(tmp_path: Path) -> None:
    duka = load_dukascopy_daily(_duka(tmp_path / "d.parquet"))
    gh = goldhub_frame(load_goldhub(_goldhub_xlsx(tmp_path / "gh.xlsx")))
    out, stats = stitch([duka, gh])
    assert out["date"].is_unique and out["date"].is_monotonic_increasing
    a = pd.DatetimeIndex(out["available_utc"])
    assert (a[1:] > a[:-1]).all()
    # Goldhub rows only before Dukascopy's first date; Dukascopy wins on the overlap
    assert (out.loc[out["source"] == "wgc_goldhub:LBMA_PM_USD", "date"] < pd.Timestamp("2003-05-05")).all()
    assert out.loc[out["date"] == pd.Timestamp("2003-05-05"), "close"].item() == 343.5
    assert stats[0].cutover == "2003-05-05" and stats[0].rows_added == 3
    # overlap 2003-05-05/06: (343/343.5 - 1, 345/345.5 - 1)
    assert stats[0].n_common == 2
    assert stats[0].median_signed_pct == pytest.approx(np.median([343 / 343.5 - 1, 345 / 345.5 - 1]) * 100)


def test_stitch_never_fills_holes_inside_a_better_source() -> None:
    hi = fixing_frame(pd.Series([1.0, 1.1], index=pd.to_datetime(["2001-01-02", "2001-01-10"])), "a:x", "UTC", 21)
    lo = fixing_frame(pd.Series(1.0, index=pd.bdate_range("2000-12-25", "2001-01-12")), "b:y", "UTC", 23)
    out, _ = stitch([hi, lo])
    assert not ((out["source"] == "b:y") & (out["date"] >= pd.Timestamp("2001-01-02"))).any()


def test_validate_rejects_leaks() -> None:
    ok = fixing_frame(pd.Series([1.0, 2.0], index=pd.to_datetime(["2001-01-02", "2001-01-03"])), "a:x", "UTC", 12)
    validate_long(ok)
    bad = ok.copy()
    bad.loc[1, "available_utc"] = pd.Timestamp("2001-01-02 11:00", tz="UTC")   # known before its own date
    with pytest.raises(ValueError):
        validate_long(bad)
    dup = pd.concat([ok, ok.iloc[[0]]]).reset_index(drop=True)
    with pytest.raises(ValueError, match="duplicate|increasing"):
        validate_long(dup)
    late = fixing_frame(pd.Series([1.0], index=[CUTOFF.tz_localize(None)]), "a:x", "UTC", 1)
    assert late.empty   # nothing at or after the cutoff survives to_long


def test_local_stamp_is_dst_aware() -> None:
    s = local_stamp(pd.to_datetime(["2020-01-15", "2020-07-15"]), "Europe/London", 15, 30)
    assert list(s.hour) == [15, 14]


def test_parsers() -> None:
    bbk = parse_bbk_csv('﻿"",BBEX3.D.USD.DEM.AA.AC.000,FLAGS\n"",title,\nunit,DEM,\n'
                        "1990-01-02,1.6905,\n1990-01-06,.,No value available\n1990-01-08,1.70,\n")
    assert list(bbk.round(4)) == [1.6905, 1.70] and bbk.index[0] == pd.Timestamp("1990-01-02")
    ecb = parse_ecb_csv("KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.USD,1999-01-04,1.1789\nEXR.D.USD,1999-01-05,\n")
    assert ecb.to_dict() == {pd.Timestamp("1999-01-04"): 1.1789}


def test_fx_crosses_from_dem_and_eur() -> None:
    idx = pd.to_datetime(["1990-01-02"])
    dem = {"USD": pd.Series([2.0], index=idx), "JPY": pd.Series([0.0125], index=idx)}
    assert _pair("USD", "JPY", dem, "DEM").item() == pytest.approx(160.0)
    assert _pair("EUR", "USD", dem, "DEM").item() == pytest.approx(1.95583 / 2.0)
    eur = {"USD": pd.Series([1.2], index=idx), "GBP": pd.Series([0.8], index=idx)}
    assert _pair("GBP", "USD", eur, "EUR").item() == pytest.approx(1.5)
    assert _pair("EUR", "GBP", eur, "EUR").item() == pytest.approx(0.8)


def test_build_instrument_records_failed_sources(tmp_path: Path) -> None:
    _duka(tmp_path / "anchor.parquet")
    spec = InstrumentSpec(key="T", dukascopy=("anchor.parquet",),
                          older=(SourceRef(kind="yahoo", ref="BAD"), SourceRef(kind="bbk_gold")))
    old = fixing_frame(pd.Series([300.0, 301.0], index=pd.to_datetime(["1990-01-02", "1990-01-03"])),
                       "bbk:gold", "Europe/Berlin", 17)

    def loader(ref: SourceRef) -> pd.DataFrame | None:
        if ref.kind == "yahoo":
            raise OSError("offline")
        return old

    df, res = build_instrument(spec, root=tmp_path, loader=loader)
    assert res.failures and "offline" in res.failures[0]
    assert df["date"].iloc[0] == pd.Timestamp("1990-01-02") and res.overlaps[-1].n_common == 0
    assert "XAUUSD" in SPECS and SPECS["XAUUSD"].older[0].kind == "goldhub"


# ------------------------------------------------------------------ review LOWs: cache, close > 0, ECB end date


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _fake_net(monkeypatch: pytest.MonkeyPatch, bodies: list[bytes]) -> list[str]:
    import goldbot.data.long_sources as ls
    calls: list[str] = []

    def urlopen(req: object, timeout: float = 0) -> _Resp:
        calls.append(getattr(req, "full_url", str(req)))
        return _Resp(bodies[min(len(calls), len(bodies)) - 1])

    monkeypatch.setattr(ls.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    return calls


def test_fetch_cached_never_caches_a_body_that_does_not_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.data.long_sources import _ecb_check, fetch_cached
    good = b"KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.USD,1999-01-04,1.1789\n"
    calls = _fake_net(monkeypatch, [b"<html>rate limited</html>"])
    path = tmp_path / "ecb_USD.csv"
    with pytest.raises(RuntimeError, match="download failed"):
        fetch_cached("https://x/ecb", path, tries=2, check=_ecb_check)
    assert not path.exists() and not list(tmp_path.iterdir()) and len(calls) == 2   # nothing cached, no tmp left
    calls = _fake_net(monkeypatch, [b"KEY,TIME_PERIOD,OBS_VALUE\n", good])          # empty parse retried, then good
    assert fetch_cached("https://x/ecb", path, tries=3, check=_ecb_check) == good and path.read_bytes() == good
    assert len(calls) == 2
    calls = _fake_net(monkeypatch, [b"unused"])
    assert fetch_cached("https://x/ecb", path, check=_ecb_check) == good and calls == []  # served from cache


def test_fetch_cached_replaces_a_bad_cache_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.data.long_sources import _yahoo_check, fetch_cached
    path = tmp_path / "yahoo_x.json"
    path.write_bytes(b'{"chart": {"result": null, "error": {"code": "Too Many Requests"}}}')
    good = b'{"chart": {"result": [{"timestamp": [1]}]}}'
    calls = _fake_net(monkeypatch, [good])
    assert fetch_cached("https://x/y", path, check=_yahoo_check) == good and len(calls) == 1
    assert path.read_bytes() == good


def test_non_positive_close_is_dropped_and_refused() -> None:
    s = pd.Series([18.0, -37.63, 0.0, 10.0], index=pd.to_datetime(["2020-04-17", "2020-04-20", "2020-04-21",
                                                                    "2020-04-22"]))
    df = fixing_frame(s, "eia:RWTC", "UTC", 23)
    assert list(df["close"]) == [18.0, 10.0]
    bad = df.copy()
    bad.loc[1, "close"] = -1.0
    with pytest.raises(ValueError, match="non-positive close"):
        validate_long(bad)


def test_ecb_end_period_derives_from_the_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    import goldbot.data.long_sources as ls
    assert pd.Timestamp(ls.ECB_END, tz="UTC") == CUTOFF - pd.Timedelta(days=1)
    seen: list[str] = []

    def fake(url: str, path: Path, refresh: bool = False, tries: int = 4, check: object = None) -> bytes:
        seen.append(url)
        return b"KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.USD,1999-01-04,1.1789\n"

    monkeypatch.setattr(ls, "fetch_cached", fake)
    ls.ecb("USD")
    assert f"endPeriod={(CUTOFF - pd.Timedelta(days=1)):%Y-%m-%d}" in seen[0]


# ------------------------------------------------------------------------- shipping to the brain: manifest, verify


def _ship_module() -> object:
    import importlib.util
    spec = importlib.util.spec_from_file_location("ship_long_history",
                                                  Path(__file__).resolve().parents[1] / "scripts" / "ship_long_history.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ship_manifest_has_sha_and_cutovers_and_verify_catches_drift(tmp_path: Path) -> None:
    ship: Any = _ship_module()
    (tmp_path / "long").mkdir()
    old = fixing_frame(pd.Series([300.0, 301.0], index=pd.to_datetime(["1990-01-02", "1990-01-03"])),
                       "bbk:gold", "Europe/Berlin", 17)
    df, _ = stitch([load_dukascopy_daily(_duka(tmp_path / "a.parquet")), old])
    df.to_parquet(tmp_path / "long" / "t_daily_long.parquet", index=False)
    df.to_parquet(tmp_path / "xauusd_daily_1978_present.parquet", index=False)
    files = ship.collect(tmp_path)
    assert [f.name for f in files] == ["t_daily_long.parquet", "xauusd_daily_1978_present.parquet"]
    m = ship.build_manifest(files)
    e = m["files"][0]
    assert e["sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest() and e["rows"] == len(df)
    assert [(s["source"], s["from"]) for s in e["segments"]] == [("bbk:gold", "1990-01-02"),
                                                                 ("dukascopy_1m:XAUUSD", "2003-05-05")]
    remote = ship.parse_sha256sum(ship.sha_lines(m).replace("  ", "  /opt/x/"))
    assert ship.verify(m, remote) == []
    remote["t_daily_long.parquet"] = "0" * 64
    del remote["xauusd_daily_1978_present.parquet"]
    probs = ship.verify(m, remote)
    assert any("mismatch t_daily_long" in p for p in probs) and any("missing xauusd" in p for p in probs)
    with pytest.raises(FileNotFoundError):
        ship.collect(tmp_path / "empty")


def test_no_parquet_or_csv_tracked_outside_the_fixture_dirs() -> None:
    """WS-2: the long history (and any data file) never enters git; only small synthetic fixtures are tracked, and
    the output paths are git-ignored so `git add .` cannot pick them up."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout.decode()
    data = [f for f in tracked.split("\0")
            if f.lower().endswith((".parquet", ".csv", ".csv.gz", ".feather", ".arrow", ".bi5", ".xls", ".xlsx"))]
    assert all(f.startswith(("tests/data/", "tests/fixtures/")) for f in data), data
    assert not [f for f in tracked.split("\0") if "daily_long" in f and not f.endswith(".py")]
    outs = ["raw/daily/long/xauusd_daily_long.parquet", "raw/daily/long/manifest.json",
            "raw/daily/xauusd_daily_1978_present.parquet", "raw/daily/_long_cache/yahoo_gcf.json",
            "state/daily_history/manifest.json"]
    ignored = subprocess.run(["git", "check-ignore", "--no-index", *outs], cwd=root, capture_output=True,
                             text=True).stdout.split()
    assert sorted(ignored) == sorted(outs)
