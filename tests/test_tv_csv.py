"""The owner's TradingView "Export chart data" CSV becomes `tv_indicators` rows that never reach a bar early, never
displace a live alert, and are labelled unvalidated until the repaint check passes (docs/plans/tradingview-integration.md).
Fixtures are synthetic: no real indicator output."""
from typing import Any

import pandas as pd
import pytest

from goldbot.data import tv_csv
from goldbot.data.store import Store, asof_join
from goldbot.features import tradingview as tvf
from goldbot.webhook.app import Alert, to_row
from goldbot.webhook.inbox import TABLE, TvInbox, sync_to_store

T0 = pd.Timestamp("2026-10-12T08:00:00Z")
LATER = pd.Timestamp("2026-10-18T00:00:00Z")


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def csv_text(n=4, step_s=900, *, header="time,open,high,low,close,Fast,Slow,Slow", unix=False, bom=False, cells=None):
    rows = []
    for i in range(n):
        t = T0 + pd.Timedelta(seconds=step_s * i)
        rows.append(f"{int(t.timestamp()) if unix else stamp(t)},1,2,0.5,1.5,{i + 0.5},{i + 10},{i + 20}")
    if cells:
        rows[cells[0]] = cells[1]
    return ("﻿" if bom else "") + header + "\n" + "\n".join(rows) + "\n"


def imp(tmp_path, text, *, tf="15", now=LATER, name="x.csv", version="1.0"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    store = Store(tmp_path / "data")
    return store, tv_csv.import_tv_csv(p, store=store, indicator="smc", version=version, tf=tf, now=now)


def alert(tmp_path, bar, value, store, plot="plot_0"):
    box = TvInbox(tmp_path / "tv.db")
    a = Alert.model_validate({"indicator": "smc", "version": "1.0", "symbol": "XAUUSD", "tf": "15",
                              "bar_time": bar.isoformat(), "fired_at": (bar + pd.Timedelta(minutes=15)).isoformat(),
                              "secret": "x", "plots": {plot: str(value)}})
    box.put(to_row(a, (bar + pd.Timedelta(minutes=16)).to_pydatetime()))
    sync_to_store(box, store)


def test_header_labels_map_to_positional_plots_even_when_names_repeat(tmp_path):
    store, rep = imp(tmp_path, csv_text())
    df = store.read(TABLE, source="smc@1.0")
    assert sorted(df["plot"].unique()) == ["plot_0", "plot_1", "plot_2"]
    labels = df.drop_duplicates("plot").set_index("plot")["plot_label"].to_dict()
    assert labels == {"plot_0": "Fast", "plot_1": "Slow", "plot_2": "Slow"}
    assert rep.rows == 12 and rep.bars == 4


def test_unix_seconds_bom_empty_cells_and_nan_are_tolerated(tmp_path):
    t1 = T0 + pd.Timedelta(minutes=15)
    store, _ = imp(tmp_path, csv_text(unix=True, bom=True, cells=(1, f"{int(t1.timestamp())},1,2,0.5,1.5,,NaN,7")))
    df = store.read(TABLE, source="smc@1.0")
    bar1 = df[df["bar_time"] == t1].set_index("plot")["value"]
    assert pd.isna(bar1["plot_0"]) and pd.isna(bar1["plot_1"]) and bar1["plot_2"] == 7.0


def test_rows_are_csv_sourced_and_unvalidated(tmp_path):
    store, _ = imp(tmp_path, csv_text())
    df = store.read(TABLE, source="smc@1.0")
    assert set(df["origin"]) == {"csv"} and not df["repaint_validated"].any() and not df["intrabar"].any()
    assert df["file_sha256"].nunique() == 1 and len(df["file_sha256"].iloc[0]) == 64


def test_asof_join_sees_a_plot_at_the_bar_close_not_one_ns_before(tmp_path):
    store, _ = imp(tmp_path, csv_text())
    frame = tvf.tv_features(store, "smc", "15")
    close = T0 + pd.Timedelta(minutes=15)
    assert frame["available_utc"].iloc[0] == close
    bars = pd.DataFrame({"ts_utc": [close - pd.Timedelta(1, "ns"), close]})
    j = asof_join(bars, frame, prefix="tv_")
    assert pd.isna(j["tv_plot_0"].iloc[0]) and j["tv_plot_0"].iloc[1] == 0.5


def test_reimport_is_idempotent(tmp_path):
    store, _ = imp(tmp_path, csv_text())
    _, rep = imp(tmp_path, csv_text())
    assert len(store.read(TABLE, source="smc@1.0")) == 12 and rep.rows == 12


def test_a_live_alert_is_never_overwritten_and_wins_on_the_same_bar(tmp_path):
    store, _ = imp(tmp_path, csv_text())
    alert(tmp_path, T0, 99.0, store)
    imp(tmp_path, csv_text(), name="again.csv")           # the CSV is imported again after the alert
    df = store.read(TABLE, source="smc@1.0")
    live = df[df["origin"].isna() & (df["bar_time"] == T0)]
    assert live["value"].tolist() == [99.0]
    wide = tvf.tv_features(store, "smc", "15")
    assert len(wide) == 4
    assert wide[wide["available_utc"] == T0 + pd.Timedelta(minutes=16)]["plot_0"].tolist() == [99.0]


def test_missing_time_column_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="time"):
        imp(tmp_path, csv_text(header="date,open,high,low,close,Fast,Slow,Slow"))
    assert not list((tmp_path / "data").rglob("*.parquet"))


def test_a_cell_that_is_not_a_number_fails_closed(tmp_path):
    bad = f"{stamp(T0 + pd.Timedelta(minutes=30))},1,2,0.5,1.5,abc,1,1"
    with pytest.raises(ValueError, match="not a number"):
        imp(tmp_path, csv_text(cells=(2, bad)))


def test_time_must_increase(tmp_path):
    lines = csv_text().splitlines()
    lines[2], lines[3] = lines[3], lines[2]
    with pytest.raises(ValueError, match="increas"):
        imp(tmp_path, "\n".join(lines) + "\n")


def test_spacing_off_the_timeframe_grid_fails_but_gaps_are_allowed(tmp_path):
    gap = csv_text().splitlines()
    gap[4] = f"{stamp(T0 + pd.Timedelta(hours=3))},1,2,0.5,1.5,1,1,1"
    _, rep = imp(tmp_path, "\n".join(gap) + "\n")
    assert rep.bars == 4
    off = csv_text().splitlines()
    off[4] = f"{stamp(T0 + pd.Timedelta(minutes=55))},1,2,0.5,1.5,1,1,1"
    with pytest.raises(ValueError, match="spacing"):
        imp(tmp_path, "\n".join(off) + "\n", name="off.csv", version="9")


def test_a_bar_that_opens_in_the_future_fails_and_the_forming_bar_is_dropped(tmp_path):
    _, rep = imp(tmp_path, csv_text(), now=T0 + pd.Timedelta(minutes=60))          # last bar closes 09:00
    assert rep.bars == 4 and rep.dropped_forming == 0
    _, rep2 = imp(tmp_path, csv_text(), now=T0 + pd.Timedelta(minutes=50), name="b.csv", version="7")
    assert rep2.bars == 3 and rep2.dropped_forming == 1                             # bar 3 (08:45) still forming
    with pytest.raises(ValueError, match="future"):
        imp(tmp_path, csv_text(), now=T0 + pd.Timedelta(minutes=20), name="c.csv", version="8")


def test_unknown_timeframe_is_refused(tmp_path):
    with pytest.raises(ValueError, match="timeframe"):
        imp(tmp_path, csv_text(), tf="banana")


def test_unvalidated_csv_is_labelled_by_the_reader_and_kept_out_of_the_feature(tmp_path):
    store, _ = imp(tmp_path, csv_text())
    frame = tvf.tv_features(store, "smc", "15")
    assert frame.attrs["tv_label"] == tvf.UNVALIDATED == "unvalidated (repaint risk)"
    assert tvf.tv_features(store, "smc", "15", include_unvalidated=False).empty
    long = store.read(TABLE, source="smc@1.0")
    out = tvf.f_tradingview(pd.DataFrame({"ts_utc": [T0 + pd.Timedelta(hours=5)]}), {"tv_indicators": long})
    assert out.shape[1] == 0


def test_live_alert_only_data_has_no_label(tmp_path):
    store = Store(tmp_path / "data")
    alert(tmp_path, T0, 1.0, store)
    assert "tv_label" not in tvf.tv_features(store, "smc", "15").attrs


def test_cli_imports_and_prints_the_counts(tmp_path, capsys):
    p = tmp_path / "e.csv"
    p.write_text(csv_text(), encoding="utf-8")
    args = ["--indicator", "smc", "--version", "1.0", "--tf", "15", "--data-root", str(tmp_path / "data")]
    assert tv_csv.main([str(p), *args, "--now", "2026-10-18T00:00:00Z"]) == 0
    assert "4 bars" in capsys.readouterr().out
    assert tv_csv.main([str(tmp_path / "nope.csv"), *args]) == 2


def ny_csv():
    """Chart set to New York (EDT, UTC-4 in October): 04:00 local is the 08:00 UTC bar open."""
    rows = ["time,open,high,low,close,Fast"]
    for i in range(4):
        t = (T0 - pd.Timedelta(hours=4) + pd.Timedelta(minutes=15 * i)).tz_localize(None)
        rows.append(f"{t.strftime('%Y-%m-%d %H:%M:%S')},1,2,0.5,1.5,{i}")
    return "\n".join(rows) + "\n"


def test_a_time_without_a_zone_is_refused_unless_the_zone_is_stated(tmp_path):
    with pytest.raises(ValueError, match="no timezone.*--tz.*--assume-utc"):
        imp(tmp_path, ny_csv())


def test_new_york_chart_time_is_converted_with_the_given_zone(tmp_path):
    p = tmp_path / "ny.csv"
    p.write_text(ny_csv(), encoding="utf-8")
    store = Store(tmp_path / "data")
    tv_csv.import_tv_csv(p, store=store, indicator="smc", version="1.0", tf="15", now=LATER, tz="America/New_York")
    df = store.read(TABLE, source="smc@1.0")
    assert df["bar_time"].min() == T0
    assert df["available_utc"].min() == T0 + pd.Timedelta(minutes=15)


def test_assume_utc_reads_a_zoneless_time_as_utc(tmp_path):
    p = tmp_path / "u.csv"
    p.write_text(ny_csv(), encoding="utf-8")
    store = Store(tmp_path / "data")
    tv_csv.import_tv_csv(p, store=store, indicator="smc", version="1.0", tf="15", now=LATER, assume_utc=True)
    assert store.read(TABLE, source="smc@1.0")["bar_time"].min() == T0 - pd.Timedelta(hours=4)


def test_tz_and_assume_utc_together_or_an_unknown_zone_are_refused(tmp_path):
    p = tmp_path / "ny.csv"
    p.write_text(ny_csv(), encoding="utf-8")
    store = Store(tmp_path / "data")
    kw: dict[str, Any] = dict(store=store, indicator="smc", version="1.0", tf="15", now=LATER)
    with pytest.raises(ValueError, match="not both"):
        tv_csv.import_tv_csv(p, tz="America/New_York", assume_utc=True, **kw)
    with pytest.raises(ValueError, match="unknown timezone"):
        tv_csv.import_tv_csv(p, tz="Mars/Base", **kw)


def test_a_time_in_the_repeated_dst_hour_is_refused(tmp_path):
    text = "time,open,high,low,close,A\n2026-11-01 01:30:00,1,2,1,1,3\n"
    p = tmp_path / "d.csv"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous"):
        tv_csv.import_tv_csv(p, store=Store(tmp_path / "data"), indicator="smc", version="1.0", tf="15", now=LATER,
                             tz="America/New_York")


def test_a_file_over_the_size_cap_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(tv_csv, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="larger than"):
        imp(tmp_path, csv_text(50))


def test_cli_takes_tz_and_refuses_a_zoneless_file_without_it(tmp_path, capsys):
    p = tmp_path / "ny.csv"
    p.write_text(ny_csv(), encoding="utf-8")
    args = ["--indicator", "smc", "--version", "1.0", "--tf", "15", "--data-root", str(tmp_path / "data"),
            "--now", "2026-10-18T00:00:00Z"]
    assert tv_csv.main([str(p), *args]) == 2
    assert "--tz" in capsys.readouterr().err
    assert tv_csv.main([str(p), *args, "--tz", "America/New_York"]) == 0
