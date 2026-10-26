"""CFTC legacy Commitments of Traders for COMEX gold (futures only, 1986 on): parsing the CFTC files, the conservative
publication stamps of the semi-monthly (to Sept 1992) and fortnightly-published (to 2000) eras, the release rows, and
the as-of contract (a seeded future report is never seen before its available_utc)."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from goldbot.data.positioning import (
    COT_GOLD_CODE,
    COT_LEGACY_SERIES,
    check_keeps_history,
    cot_available_utc,
    cot_legacy_available_utc,
    cot_legacy_frame,
    merge_releases,
    parse_cot_legacy,
    read_release,
    write_release,
)
from goldbot.data.store import asof_join

# the real header of FUT86_16.txt / annual.txt (first 17 columns; note the leading space CFTC ships in one name)
HEADER = ('"Market and Exchange Names","As of Date in Form YYMMDD","As of Date in Form YYYY-MM-DD",'
          '"CFTC Contract Market Code","CFTC Market Code in Initials","CFTC Region Code","CFTC Commodity Code",'
          '"Open Interest (All)","Noncommercial Positions-Long (All)","Noncommercial Positions-Short (All)",'
          '"Noncommercial Positions-Spreading (All)","Commercial Positions-Long (All)","Commercial Positions-Short (All)",'
          '" Total Reportable Positions-Long (All)","Total Reportable Positions-Short (All)",'
          '"Nonreportable Positions-Long (All)","Nonreportable Positions-Short (All)"')
LEGACY_CSV = "\n".join([
    HEADER,
    # two real gold rows (1986, semi-monthly) and one in the weekly era, plus silver (084691) that must be ignored
    '"GOLD - COMMODITY EXCHANGE INC.",860115,1986-01-15,088691,CMX,01,088,  148186,   18589,    7706,   12038,   80962,'
    '   89990,  111589,  109734,   36597,   38452',
    '"GOLD - COMMODITY EXCHANGE INC.",860131,1986-01-31,088691,CMX,01,088,  144149,   15364,    9303,   10088,   82262,'
    '   85250,  107714,  104641,   36435,   39508',
    '"SILVER - COMMODITY EXCHANGE INC.",860131,1986-01-31,084691,CMX,01,084,  90000,   1,    2,   3,   4,'
    '   5,  6,  7,   8,   9',
    '"GOLD - COMMODITY EXCHANGE INC.",161220,2016-12-20,088691,CMX ,01,088 ,  398661,  219067,  104233,   44342,'
    '   87960,  221982,  351369,  370557,   47292,   28104',
]) + "\n"


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def test_parse_cot_legacy_keeps_gold_only():
    df = parse_cot_legacy(LEGACY_CSV.encode())
    assert list(df["report_date"]) == [pd.Timestamp("1986-01-15"), pd.Timestamp("1986-01-31"), pd.Timestamp("2016-12-20")]
    first = df.iloc[0]
    assert (first["nc_long"], first["nc_short"], first["open_interest"]) == (18589, 7706, 148186)
    assert first["comm_net"] == 80962 - 89990


def test_parse_cot_legacy_refuses_a_changed_format():
    with pytest.raises(ValueError, match="lacks"):
        parse_cot_legacy(LEGACY_CSV.replace("Noncommercial Positions-Long (All)", "NC Long"))


def test_legacy_stamps_are_conservative_in_the_early_eras_and_the_weekly_rule_after():
    got = cot_legacy_available_utc(pd.DatetimeIndex(["1986-01-15", "1995-03-07", "2016-12-20"]))
    # semi-monthly era (to Sept 1992): not before the Friday on or after report date + 28 days
    assert got[0] == _utc("1986-02-14 20:30")
    # fortnightly publication (Oct 1992 - 2000): not before the Friday on or after report date + 14 days
    assert got[1] == _utc("1995-03-24 20:30")
    # weekly publication: exactly the disaggregated report's rule (Friday 15:30 New York time)
    assert got[2] == cot_available_utc(pd.DatetimeIndex(["2016-12-20"]))[0] == _utc("2016-12-23 20:30")
    assert (got >= pd.DatetimeIndex(["1986-01-15", "1995-03-07", "2016-12-20"]).tz_localize("UTC")).all()


def test_cot_legacy_frame_rows_carry_source_and_availability():
    rows = cot_legacy_frame(parse_cot_legacy(LEGACY_CSV), pd.Timestamp("2026-10-26"))
    assert set(rows["series"]) == set(COT_LEGACY_SERIES)
    assert (rows["source"] == "cftc_legacy").all() and (rows["series_id"] == COT_GOLD_CODE).all()
    assert (rows["available_utc"] == rows["ts_utc"]).all()
    net = rows[(rows["series"] == "cot_legacy_nc_net") & (rows["value_date"] == pd.Timestamp("1986-01-15"))]
    assert net["value"].item() == 18589 - 7706


def test_a_seeded_future_legacy_report_is_never_seen_before_its_available_utc():
    rel = cot_legacy_frame(parse_cot_legacy(LEGACY_CSV), pd.Timestamp("2026-10-26"))
    net = rel[rel["series"] == "cot_legacy_nc_net"][["value_date", "value", "available_utc"]]
    bars = pd.DataFrame({"ts_utc": pd.date_range("1986-01-31", "1986-03-20", freq="1D", tz="UTC")})
    j = asof_join(bars, net, prefix="nc_")
    stamp_jan15 = _utc("1986-02-14 20:30")
    seen = j.set_index("ts_utc")["nc_value"]
    assert seen[seen.index < stamp_jan15].isna().all()          # the 15 Jan report (and 31 Jan) are not yet public
    assert seen[_utc("1986-02-15")] == 18589 - 7706
    assert seen[_utc("1986-03-15")] == 15364 - 9303             # 31 Jan report public from Fri 28 Feb 1986


def _script():
    spec = importlib.util.spec_from_file_location(
        "positioning_data", Path(__file__).resolve().parents[1] / "scripts" / "positioning_data.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_workflow_script_adds_legacy_from_1986_once_then_only_recent_years(tmp_path, monkeypatch):
    mod = _script()
    cot = pd.DataFrame({"report_date": pd.date_range("2024-01-02", periods=4, freq="7D"), "mm_long": 1.0,
                        "mm_short": 0.0, "open_interest": 10.0, "comm_net": -1.0})
    monkeypatch.setattr(mod, "fetch_cot", lambda years: cot)
    asked: list[list[int]] = []

    def legacy(years):
        asked.append(years)
        return parse_cot_legacy(LEGACY_CSV)

    monkeypatch.setattr(mod, "fetch_cot_legacy", legacy)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    prev = tmp_path / "prev.parquet"
    write_release(pd.DataFrame(columns=["series", "series_id", "value_date", "value", "vintage", "available_utc",
                                        "ts_utc", "source"]).astype({"value": float}), str(prev), {})
    out = tmp_path / "out.parquet"
    # a published file without legacy rows (the state of positioning-v1 today): legacy is backfilled from 1986
    assert mod.main(["--out", str(out), "--existing", str(prev), "--skip-gld"]) == 0
    assert asked[-1][0] == 1986
    df, ok = read_release(str(out))
    assert (df["source"] == "cftc_legacy").sum() == 3 * len(COT_LEGACY_SERIES) and "cftc_legacy" in ok
    # the next run only asks for last year and this year, and keeps the published legacy rows
    out2 = tmp_path / "out2.parquet"
    assert mod.main(["--out", str(out2), "--existing", str(out), "--skip-gld"]) == 0
    assert asked[-1][0] == pd.Timestamp.now(tz="UTC").year - 1
    check_keeps_history(df, read_release(str(out2))[0])


def test_a_legacy_failure_keeps_the_published_rows_and_exits_1(tmp_path, monkeypatch):
    mod = _script()
    monkeypatch.setattr(mod, "fetch_cot", lambda years: pd.DataFrame(
        {"report_date": pd.date_range("2024-01-02", periods=2, freq="7D"), "mm_long": 1.0, "mm_short": 0.0,
         "open_interest": 10.0, "comm_net": -1.0}))
    rel = cot_legacy_frame(parse_cot_legacy(LEGACY_CSV), pd.Timestamp("2026-10-01"))
    prev = tmp_path / "prev.parquet"
    write_release(merge_releases(None, rel, _utc("2026-10-01")), str(prev), {"cftc_legacy": _utc("2026-10-01")})

    def down(years):
        raise RuntimeError("cftc.gov: 503")

    monkeypatch.setattr(mod, "fetch_cot_legacy", down)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "out.parquet"
    assert mod.main(["--out", str(out), "--existing", str(prev), "--skip-gld"]) == 1
    assert (read_release(str(out))[0]["source"] == "cftc_legacy").sum() == len(rel)
