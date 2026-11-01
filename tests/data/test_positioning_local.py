"""Local positioning build (goldbot/data/positioning_local.py): the full COT disaggregated/legacy splits, the SPDR GLD
archive (xlsx, read with the standard library), data-quality checks with quarantine, and the point-in-time contract
(a seeded future release is never visible before its available_utc)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import pytest

from goldbot.data import positioning_local as pl
from goldbot.data.positioning import (
    COLUMNS,
    COT_GOLD_CODE,
    GldSourceUnavailable,
    HistoryLoss,
    cot_frame,
    cot_legacy_frame,
    merge_releases,
    parse_cot,
    parse_cot_legacy,
)
from goldbot.data.store import Store, asof_join


def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


# ------------------------------------------------------------------------------------------------ COT splits
DISAGG_HEAD = ("Market_and_Exchange_Names,Report_Date_as_YYYY-MM-DD,CFTC_Contract_Market_Code,Open_Interest_All,"
               "Prod_Merc_Positions_Long_All,Prod_Merc_Positions_Short_All,Swap_Positions_Long_All,"
               "Swap__Positions_Short_All,Swap__Positions_Spread_All,M_Money_Positions_Long_All,"
               "M_Money_Positions_Short_All,M_Money_Positions_Spread_All,Other_Rept_Positions_Long_All,"
               "Other_Rept_Positions_Short_All,Other_Rept_Positions_Spread_All,NonRept_Positions_Long_All,"
               "NonRept_Positions_Short_All")


def _disagg_csv() -> str:
    rows = [f'"GOLD - COMMODITY EXCHANGE INC.",2026-10-20,{COT_GOLD_CODE},500000,10000,90000,20000,150000,30000,'
            "200000,40000,25000,15000,5000,8000,9000,6000",
            '"SILVER - COMMODITY EXCHANGE INC.",2026-10-20,084691,1,1,1,1,1,1,1,1,1,1,1,1,1,1']
    return "\n".join([DISAGG_HEAD, *rows]) + "\n"


def test_parse_cot_keeps_every_trader_category_for_gold():
    p = parse_cot(_disagg_csv())
    assert len(p) == 1
    r = p.iloc[0]
    assert (r["pm_long"], r["pm_short"], r["swap_long"], r["swap_short"], r["swap_spread"]) == (10000, 90000, 20000,
                                                                                               150000, 30000)
    assert (r["mm_long"], r["mm_short"], r["mm_spread"]) == (200000, 40000, 25000)
    assert (r["other_long"], r["other_short"], r["other_spread"], r["nonrept_long"], r["nonrept_short"]) == (
        15000, 5000, 8000, 9000, 6000)
    assert r["comm_net"] == (10000 - 90000) + (20000 - 150000)


def test_cot_frame_emits_the_split_series_only_on_request_and_accepts_core_only_frames():
    assert set(cot_frame(parse_cot(_disagg_csv()), pd.Timestamp("2026-10-24"))["series"]) == set(pl.COT_SERIES)
    out = cot_frame(parse_cot(_disagg_csv()), pd.Timestamp("2026-10-24"), split=True)
    got = dict(zip(out["series"], out["value"]))
    assert got["cot_pm_long"] == 10000 and got["cot_swap_short"] == 150000 and got["cot_mm_spread"] == 25000
    assert got["cot_nonrept_short"] == 6000 and got["cot_mm_net"] == 160000
    assert set(pl.COT_DISAGG_SERIES) == set(out["series"])
    core = pd.DataFrame({"report_date": [pd.Timestamp("2026-10-20")], "mm_long": [2.0], "mm_short": [1.0],
                         "open_interest": [9.0], "comm_net": [-1.0]})
    assert set(cot_frame(core, pd.Timestamp("2026-10-24"), split=True)["series"]) == {
        "cot_mm_long", "cot_mm_short", "cot_mm_net", "cot_open_interest", "cot_comm_net"}


LEGACY_HEAD = ('"Market and Exchange Names","As of Date in Form YYYY-MM-DD","CFTC Contract Market Code",'
               '"Open Interest (All)","Noncommercial Positions-Long (All)","Noncommercial Positions-Short (All)",'
               '"Noncommercial Positions-Spreading (All)","Commercial Positions-Long (All)",'
               '"Commercial Positions-Short (All)","Nonreportable Positions-Long (All)",'
               '"Nonreportable Positions-Short (All)"')


def test_parse_cot_legacy_keeps_commercial_legs_spreads_and_nonreportables():
    csv = LEGACY_HEAD + f'\n"GOLD - COMMODITY EXCHANGE INC.",2026-10-20,{COT_GOLD_CODE},500000,250000,50000,40000,' \
                        "100000,300000,20000,10000\n"
    p = parse_cot_legacy(csv)
    out = cot_legacy_frame(p, pd.Timestamp("2026-10-24"), split=True)
    got = dict(zip(out["series"], out["value"]))
    assert got["cot_legacy_comm_long"] == 100000 and got["cot_legacy_comm_short"] == 300000
    assert got["cot_legacy_nc_spread"] == 40000 and got["cot_legacy_nonrept_long"] == 20000
    assert got["cot_legacy_comm_net"] == -200000 and got["cot_legacy_nc_net"] == 200000
    assert set(pl.COT_LEGACY_ALL_SERIES) == set(out["series"])


# ------------------------------------------------------------------------------------------------ GLD archive (xlsx)
GLD_HEAD = ["Date", "Closing Price", "Ounces of Gold per Share", "NAV/Share at 10:30am NYT",
            "Indicative Price per Share at 4:15pm NYT", "Mid point of bid/ask spread at 4:15pm NYT",
            "Premium/Discount of GLD Mid Point vs Indicative Value of GLD at 4:15pm NYT", "Daily Share Volume",
            "Total Ounces of Gold in the Trust", "Tonnes of Gold", "Total Net Asset Value in the Trust"]


def _xlsx(rows: list[list[object]]) -> bytes:
    """A minimal two-sheet workbook shaped like the SPDR archive (disclaimer first, data on sheet 2)."""
    strings: list[str] = []

    def cell(ref: str, v: object) -> str:
        if isinstance(v, str):
            strings.append(v)
            return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
        return f'<c r="{ref}"><v>{v}</v></c>'

    def col(i: int) -> str:
        return chr(ord("A") + i)

    body = "".join(f'<row r="{n + 1}">' + "".join(cell(f"{col(i)}{n + 1}", v) for i, v in enumerate(r)) + "</row>"
                   for n, r in enumerate(rows))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet2 = f'<?xml version="1.0"?><worksheet {ns}><sheetData>{body}</sheetData></worksheet>'
    sst = f'<?xml version="1.0"?><sst {ns}>' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>"
    wb = (f'<?xml version="1.0"?><workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
          'relationships"><sheets><sheet name="Disclaimer" sheetId="1" r:id="rId4"/>'
          '<sheet name="US GLD Historical Archive" sheetId="2" r:id="rId5"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId4" Type="x" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId5" Type="x" Target="/xl/worksheets/sheet2.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0"?><worksheet {ns}><sheetData/></worksheet>')
        z.writestr("xl/worksheets/sheet2.xml", sheet2)
    return buf.getvalue()


def _gld_rows(n: int = 30, start: str = "2025-06-02") -> list[list[object]]:
    days = pd.bdate_range(start, periods=n)
    rows: list[list[object]] = [GLD_HEAD]
    for i, d in enumerate(days):
        oz = 30_000_000.0 + 10_000 * i
        rows.append([d.strftime("%d-%b-%Y"), 300.0, 0.0917, 299.0, 300.1, 300.0, 0.03, 5_000_000 + i,
                     oz, round(oz / pl.OZ_PER_TONNE, 2), oz * 3000])
    return rows


def test_parse_gld_xlsx_reads_the_archive_sheet_and_skips_holiday_rows():
    rows = _gld_rows(5)
    rows.insert(3, ["04-Jul-2025", "HOLIDAY", "", "", "", "", "", "", "", "", ""])
    p = pl.parse_gld_xlsx(_xlsx(rows))
    assert len(p) == 5
    assert list(p.columns) == ["value_date", "tonnes", "ounces", "oz_per_share", "volume"]
    assert p["value_date"].iloc[0] == pd.Timestamp("2025-06-02")
    assert p["ounces"].iloc[1] == 30_010_000.0 and p["volume"].iloc[0] == 5_000_000


@pytest.mark.parametrize("payload", [b"%PDF-1.5 not a workbook", b"<!DOCTYPE html><html></html>"])
def test_gld_archive_that_is_not_the_workbook_is_explicitly_unavailable(payload):
    with pytest.raises(GldSourceUnavailable):
        pl.parse_gld_xlsx(payload)


def test_gld_archive_frame_series_and_conservative_stamps():
    p = pl.parse_gld_xlsx(_xlsx(_gld_rows(3, start="2025-07-02")))      # Wed 2 Jul, Thu 3 Jul, Mon 7 Jul (bdate)
    out = pl.gld_archive_frame(p, pd.Timestamp("2026-11-01"))
    assert set(out["series"]) == set(pl.GLD_SERIES)
    assert list(out.columns) == list(COLUMNS)
    t = out[out["series"] == "gld_tonnes"].sort_values("value_date")
    # Thursday 3 July -> Monday 7 July 14:00 UTC (Friday 4 July is a federal holiday)
    assert list(t["available_utc"])[:2] == [_utc("2025-07-03 14:00"), _utc("2025-07-07 14:00")]
    sh = out[out["series"] == "gld_shares"]["value"].iloc[0]
    assert sh == pytest.approx(30_000_000.0 / 0.0917)
    assert (out["source"] == "spdr").all() and (out["series_id"] == "GLD").all()


# ------------------------------------------------------------------------------------------------ revisions
def test_a_new_series_backfilled_later_keeps_its_rule_stamps_but_a_missed_date_is_late():
    days = pd.date_range("2025-05-06", periods=4, freq="7D")
    core = pd.DataFrame({"report_date": days, "mm_long": 2.0, "mm_short": 1.0, "open_interest": 9.0, "comm_net": -1.0})
    t1 = _utc("2025-06-01 05:00")
    pub = merge_releases(None, cot_frame(core, t1), t1, {"cftc": t1})
    split = {c: 1.0 for c in ("pm_long", "pm_short", "swap_long", "swap_short", "swap_spread", "mm_spread",
                              "other_long", "other_short", "other_spread", "nonrept_long", "nonrept_short")}
    missed_day = pd.Timestamp("2025-04-29")
    full = pd.concat([core, core.iloc[:1].assign(report_date=missed_day)], ignore_index=True).assign(**split)
    t2 = _utc("2025-06-08 05:00")
    fresh = cot_frame(full, t2, split=True)
    out = merge_releases(pub, fresh, t2, {"cftc": t1})
    rule = fresh[fresh["series"] == "cot_pm_long"].sort_values("value_date")["available_utc"]
    got = out[out["series"] == "cot_pm_long"].sort_values("value_date")["available_utc"]
    assert list(got) == list(rule)                                               # a backfill, not a late release
    missed = out[(out["series"] == "cot_mm_long") & (out["value_date"] == missed_day)]
    assert list(missed["available_utc"]) == [t2]                                 # an existing series' missed date


# ------------------------------------------------------------------------------------------------ data quality
def _cot_ok(n: int = 6) -> pd.DataFrame:
    core = pd.DataFrame({"report_date": pd.date_range("2025-05-06", periods=n, freq="7D"), "mm_long": 200.0,
                         "mm_short": 100.0, "open_interest": 1000.0, "comm_net": -50.0})
    return cot_frame(core, pd.Timestamp("2025-07-01"))


def test_quality_errors_are_quarantined_and_warnings_are_kept():
    df = _cot_ok()
    bad_neg = (df["series"] == "cot_mm_short") & (df["value_date"] == df["value_date"].min())
    df.loc[bad_neg, "value"] = -5.0
    bad_avail = (df["series"] == "cot_mm_long") & (df["value_date"] == df["value_date"].max())
    df.loc[bad_avail, "available_utc"] = df.loc[bad_avail, "value_date"].dt.tz_localize("UTC")   # before the release
    gap = df["value_date"] == sorted(df["value_date"].unique())[2]
    df = df[~gap | (df["series"] != "cot_open_interest")]
    events = pl.check_positioning(df)
    kinds = {(e.check, e.severity) for e in events}
    assert ("negative_value", "error") in kinds and ("available_before_value", "error") in kinds
    assert ("cadence_gap", "warning") in kinds
    clean, quarantined = pl.quarantine_positioning(df, events)
    assert len(quarantined) == 2 and len(clean) == len(df) - 2
    assert not ((clean["series"] == "cot_mm_short") & (clean["value"] < 0)).any()


def test_quality_flags_positions_above_open_interest_and_gld_tonnes_mismatch():
    df = _cot_ok()
    m = (df["series"] == "cot_mm_long") & (df["value_date"] == df["value_date"].min())
    df.loc[m, "value"] = 5000.0
    p = pl.parse_gld_xlsx(_xlsx(_gld_rows(10)))
    p.loc[4, "tonnes"] = p.loc[4, "tonnes"] * 1.10
    g = pl.gld_archive_frame(p, pd.Timestamp("2026-11-01"))
    events = pl.check_positioning(pd.concat([df, g], ignore_index=True))
    kinds = {(e.check, e.severity) for e in events}
    assert ("above_open_interest", "error") in kinds
    assert ("gld_tonnes_ounces_mismatch", "warning") in kinds
    assert ("gld_jump", "warning") in kinds


def test_clean_data_raises_no_events():
    p = pl.parse_gld_xlsx(_xlsx(_gld_rows(10)))
    df = pd.concat([_cot_ok(), pl.gld_archive_frame(p, pd.Timestamp("2026-11-01"))], ignore_index=True)
    assert pl.check_positioning(df) == []


# ------------------------------------------------------------------------------------------------ build
class _Fake:
    """Stands in for the network: the build takes its fetchers as arguments."""

    def __init__(self, n_cot: int = 8, n_gld: int = 20, gld_ok: bool = True):
        self.n_cot, self.n_gld, self.gld_ok = n_cot, n_gld, gld_ok

    def cot(self, years: list[int]) -> tuple[pd.DataFrame, dict[str, bytes]]:
        days = pd.date_range("2025-05-06", periods=self.n_cot, freq="7D")
        i = np.arange(self.n_cot, dtype=float)
        return (pd.DataFrame({"report_date": days, "mm_long": 200.0 + i, "mm_short": 100.0, "open_interest": 1000.0,
                              "comm_net": -50.0}), {"disagg.zip": b"cot-bytes"})

    def legacy(self, years: list[int]) -> tuple[pd.DataFrame, dict[str, bytes]]:
        days = pd.date_range("2025-05-06", periods=self.n_cot, freq="7D")
        return (pd.DataFrame({"report_date": days, "nc_long": 300.0, "nc_short": 100.0, "open_interest": 1000.0,
                              "comm_net": -150.0}), {"legacy.zip": b"legacy-bytes"})

    def gld(self) -> tuple[pd.DataFrame, dict[str, bytes]]:
        if not self.gld_ok:
            raise GldSourceUnavailable("blocked")
        raw = _xlsx(_gld_rows(self.n_gld))
        return pl.parse_gld_xlsx(raw), {"gld.xlsx": raw}


def _build(tmp_path: Path, fake: _Fake, now: str) -> pl.BuildResult:
    return pl.build(tmp_path / "raw", tmp_path / "store", now=_utc(now), fetch_cot_fn=fake.cot,
                    fetch_legacy_fn=fake.legacy, fetch_gld_fn=fake.gld)


def test_build_writes_the_file_the_store_and_the_source_lineage(tmp_path):
    res = _build(tmp_path, _Fake(), "2025-07-01 12:00")
    f = tmp_path / "raw" / "positioning" / "positioning.parquet"
    df = pd.read_parquet(f)
    assert set(df["source"]) == {"cftc", "cftc_legacy", "spdr"}
    assert res.rows == len(df) and res.added == len(df)
    src = list((tmp_path / "raw" / "positioning" / "src" / "2025-07-01").iterdir())
    assert {p.name for p in src} == {"disagg.zip", "legacy.zip", "gld.xlsx", "manifest.json"}
    store_rows = Store(tmp_path / "store").read("positioning")
    assert len(store_rows) == len(df)


def test_build_is_idempotent_and_keeps_history_when_gld_goes_dark(tmp_path):
    _build(tmp_path, _Fake(), "2025-07-01 12:00")
    again = _build(tmp_path, _Fake(), "2025-07-02 12:00")
    assert again.added == 0
    dark = _build(tmp_path, _Fake(gld_ok=False), "2025-07-03 12:00")
    assert dark.added == 0 and "spdr" in dark.unavailable
    df = pd.read_parquet(tmp_path / "raw" / "positioning" / "positioning.parquet")
    assert (df["source"] == "spdr").sum() == 20 * len(pl.GLD_SERIES)


def test_build_refuses_to_write_a_frame_that_loses_history(tmp_path, monkeypatch):
    _build(tmp_path, _Fake(), "2025-07-01 12:00")
    f = tmp_path / "raw" / "positioning" / "positioning.parquet"
    before = f.read_bytes()
    monkeypatch.setattr(pl, "merge_releases", lambda prev, fresh, now, last_ok: prev.iloc[:-3])
    with pytest.raises(HistoryLoss):
        _build(tmp_path, _Fake(), "2025-07-02 12:00")
    assert f.read_bytes() == before


def test_build_records_quality_events_and_quarantines_errors(tmp_path):
    class Bad(_Fake):
        def cot(self, years):
            df, raw = super().cot(years)
            df.loc[0, "mm_short"] = -1.0
            return df, raw

    res = _build(tmp_path, Bad(), "2025-07-01 12:00")
    assert res.quarantined >= 1
    df = pd.read_parquet(tmp_path / "raw" / "positioning" / "positioning.parquet")
    assert not ((df["series"] == "cot_mm_short") & (df["value"] < 0)).any()
    q = pd.read_parquet(tmp_path / "raw" / "positioning" / "quarantine.parquet")
    assert (q["series"] == "cot_mm_short").any() and "dq_check" in q.columns
    ev = Store(tmp_path / "store").read("dq_events")
    assert {"negative_value"} <= set(ev["check"])


def test_a_seeded_future_release_is_never_joined_before_its_available_utc(tmp_path):
    """DoD: as-of on availability only. A report positions-dated Tuesday is invisible to bars before Friday 15:30 ET;
    a later revision is invisible before the download that saw it."""
    _build(tmp_path, _Fake(n_cot=3), "2025-06-01 12:00")
    df = pd.read_parquet(tmp_path / "raw" / "positioning" / "positioning.parquet")
    s = df[df["series"] == "cot_mm_long"].sort_values("available_utc")
    last = s.iloc[-1]                                     # value_date Tue 2025-05-20 -> Fri 2025-05-23 19:30 UTC
    assert last["available_utc"] == _utc("2025-05-23 19:30")
    bars = pd.DataFrame({"ts_utc": pd.DatetimeIndex([_utc("2025-05-21 12:00"), _utc("2025-05-23 19:29"),
                                                     _utc("2025-05-23 19:30")])})
    j = asof_join(bars, s[["available_utc", "value"]])
    assert list(j["value"].iloc[:2]) == [s.iloc[-2]["value"]] * 2 and j["value"].iloc[2] == last["value"]

    class Revised(_Fake):
        def cot(self, years):
            d, raw = super().cot(years)
            d.loc[0, "mm_long"] = 999.0
            return d, raw

    _build(tmp_path, Revised(n_cot=3), "2025-06-10 12:00")
    df2 = pd.read_parquet(tmp_path / "raw" / "positioning" / "positioning.parquet")
    rev = df2[(df2["series"] == "cot_mm_long") & (df2["value"] == 999.0)]
    assert list(rev["available_utc"]) == [_utc("2025-06-10 12:00")]
    first = df2[(df2["series"] == "cot_mm_long") & (df2["value_date"] == pd.Timestamp("2025-05-06"))]
    assert len(first) == 2 and 200.0 in set(first["value"])                       # first release kept
