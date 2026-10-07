"""Triple-barrier labels (barrier order, time exit, spread costs) and the point-in-time store (dedupe, as-of joins)."""
import numpy as np
import pandas as pd
import pytest

from goldbot.data.store import Store, asof_join
from goldbot.labels.triple_barrier import BarrierSpec, one_at_a_time, triple_barrier, uniqueness_weights

SPREAD = 0.2
SPEC = BarrierSpec(target_atr=2.0, stop_atr=1.0, max_bars=3)


def _bars(n: int = 8, mid: float = 100.0) -> pd.DataFrame:
    """Flat mid with a 0.2 spread and a 0.05 range: nothing touches a barrier unless a test edits a bar."""
    m = np.full(n, mid)
    b = pd.DataFrame({"ts_utc": pd.date_range("2025-03-05 10:00", periods=n, freq="15min", tz="UTC")})
    for side, off in (("bid", -SPREAD / 2), ("ask", SPREAD / 2)):
        b[f"{side}_close"] = m + off
        b[f"{side}_high"] = m + off + 0.05
        b[f"{side}_low"] = m + off - 0.05
    return b


def _set(b: pd.DataFrame, i: int, *, high: float | None = None, low: float | None = None) -> None:
    """Move bar i's mid high/low; bid and ask follow with the spread."""
    if high is not None:
        b.loc[i, "bid_high"], b.loc[i, "ask_high"] = high - SPREAD / 2, high + SPREAD / 2
    if low is not None:
        b.loc[i, "bid_low"], b.loc[i, "ask_low"] = low - SPREAD / 2, low + SPREAD / 2


def _label(b: pd.DataFrame, idx: int, side: int, spec: BarrierSpec = SPEC, atr: float = 1.0) -> pd.Series:
    out = triple_barrier(b, pd.DataFrame({"idx": [idx], "side": [side]}), spec, pd.Series(np.full(len(b), atr)))
    assert len(out) == 1
    return out.iloc[0]


# ---------------------------------------------------------------------------------------------- triple barrier
def test_long_enters_at_the_ask_and_only_bars_after_the_signal_count():
    b = _bars()
    _set(b, 0, high=150.0)                     # the signal bar's own spike must not count as the target
    _set(b, 2, high=102.3)                     # bid high 102.2 >= target 100.1 + 2
    r = _label(b, 0, 1)
    assert r["entry"] == pytest.approx(100.1) and r["t_entry"] == 1
    assert r["barrier_hit"] == "target" and r["label"] == 1 and r["t_exit"] == 2 and r["bars_held"] == 2
    assert r["exit"] == pytest.approx(102.1) and r["ret"] == pytest.approx(2.0 / 100.1) and r["target_hit"] == 1
    assert r["ts_utc"] == b["ts_utc"].iloc[0] and r["ts_exit"] == b["ts_utc"].iloc[2]


def test_long_target_needs_the_bid_to_reach_it_not_the_mid():
    b = _bars()
    _set(b, 2, high=102.15)                    # mid high above target, but bid high 102.05 < 102.1
    assert _label(b, 0, 1)["barrier_hit"] == "time"


def test_stop_is_assumed_first_when_both_barriers_are_inside_one_bar():
    b = _bars()
    _set(b, 2, high=105.0, low=95.0)
    r = _label(b, 0, 1)
    assert r["barrier_hit"] == "stop" and r["label"] == -1 and r["exit"] == pytest.approx(99.1)
    rs = _label(b, 0, -1)
    assert rs["barrier_hit"] == "stop" and rs["label"] == -1


def test_short_enters_at_the_bid_and_is_stopped_by_the_ask():
    b = _bars()
    _set(b, 3, high=100.85)                    # ask high 100.95 >= stop 99.9 + 1 although bid high is 100.75
    r = _label(b, 0, -1)
    assert r["entry"] == pytest.approx(99.9) and r["barrier_hit"] == "stop" and r["t_exit"] == 3
    assert r["ret"] == pytest.approx(-(100.9 - 99.9) / 99.9)
    b2 = _bars()
    _set(b2, 2, low=97.75)                     # ask low 97.85 <= target 97.9
    r2 = _label(b2, 0, -1)
    assert r2["barrier_hit"] == "target" and r2["label"] == 1 and r2["ret"] == pytest.approx(2.0 / 99.9)


@pytest.mark.parametrize("side", [1, -1])
def test_time_exit_on_a_flat_market_loses_the_round_trip_spread(side):
    r = _label(_bars(), 0, side)
    assert r["barrier_hit"] == "time" and r["t_exit"] == 0 + 1 + SPEC.max_bars and r["target_hit"] == 0
    assert r["ret"] == pytest.approx(-SPREAD / (100 + side * SPREAD / 2))
    assert r["label"] == -1                    # the sign of the realised return, after costs


def test_time_exit_label_is_the_sign_of_the_realised_return():
    b = _bars()
    for i in range(1, 8):
        b.loc[i, ["bid_close", "ask_close"]] = [100.8, 101.0]       # drifted up less than the target
    r = _label(b, 0, 1)
    assert r["barrier_hit"] == "time" and r["label"] == 1 and r["exit"] == pytest.approx(100.8)


def test_the_time_barrier_is_cut_at_the_end_of_the_data():
    b = _bars(4)
    r = _label(b, 1, 1, BarrierSpec(target_atr=2, stop_atr=1, max_bars=50))
    assert r["t_exit"] == 3 and r["barrier_hit"] == "time"


def test_signals_without_a_next_bar_or_a_valid_atr_are_dropped():
    b = _bars(6)
    atr = pd.Series([1.0, np.nan, 0.0, -1.0, 1.0, 1.0])
    out = triple_barrier(b, pd.DataFrame({"idx": [0, 1, 2, 3, 5], "side": [1, 1, -1, 1, 1]}), SPEC, atr)
    assert list(out["idx"]) == [0]
    empty = triple_barrier(b, pd.DataFrame({"idx": [5], "side": [1]}), SPEC, atr)
    assert empty.empty and uniqueness_weights(empty, len(b)).empty and one_at_a_time(empty).empty


def test_uniqueness_weights_discount_overlapping_labels():
    labels = pd.DataFrame({"t_entry": [0, 2, 20], "t_exit": [9, 11, 29], "ret": [0.01, 0.01, 0.01]})
    w = uniqueness_weights(labels, 30)
    assert w.mean() == pytest.approx(1.0)
    assert w.iloc[2] > w.iloc[0] and w.iloc[0] == pytest.approx(w.iloc[1])
    bigger = uniqueness_weights(labels.assign(ret=[0.01, 0.01, 0.03]), 30)
    assert bigger.iloc[2] / bigger.iloc[0] > w.iloc[2] / w.iloc[0]          # return attribution


def test_one_at_a_time_sorts_by_entry_and_skips_overlaps():
    labels = pd.DataFrame({"t_entry": [10, 1, 5, 12], "t_exit": [14, 6, 8, 13]})
    kept = one_at_a_time(labels)
    assert list(kept["t_entry"]) == [1, 10]                                  # 5 starts before 6 exits; 12 before 14


# ---------------------------------------------------------------------------------------------- store
def _ticks(times: list[str], bid: float = 2400.0) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": pd.to_datetime(times, utc=True, format="ISO8601"), "bid": bid, "ask": bid + 0.2})


def test_store_rejects_unknown_tables_and_rows_without_time(tmp_path):
    s = Store(tmp_path)
    with pytest.raises(ValueError, match="unknown table"):
        s.append("bars_2m", _ticks(["2025-03-05 10:00"]), source="x")
    with pytest.raises(ValueError, match="ts_utc"):
        s.append("ticks", pd.DataFrame({"bid": [1.0]}), source="x")
    assert s.append("ticks", _ticks([]), source="x") == 0
    assert s.read("ticks").empty and s.read("fills").empty


def test_dedupe_keeps_the_last_copy_of_a_key_and_append_only_logs_keep_everything(tmp_path):
    s = Store(tmp_path)
    fill = {"ts_utc": pd.Timestamp("2025-03-05 10:00", tz="UTC"), "client_order_id": "icm-1", "filled": 2400.1}
    s.append("fills", pd.DataFrame([fill]), source="icm")
    s.append("fills", pd.DataFrame([{**fill, "filled": 2400.3}]), source="icm")
    got = s.read("fills", source="icm")
    assert len(got) == 1 and got["filled"].iloc[0] == 2400.3
    s.append("ticks", _ticks(["2025-03-05 10:00"]), source="icm", dedupe=False)
    s.append("ticks", _ticks(["2025-03-05 10:00"]), source="icm", dedupe=False)
    assert len(s.read("ticks", source="icm")) == 2
    s.append("calendar_events", pd.DataFrame({"ts_utc": [fill["ts_utc"]], "event_id": ["cpi"], "forecast": [0.2]}), source="ff")
    s.append("calendar_events", pd.DataFrame({"ts_utc": [fill["ts_utc"]], "event_id": ["cpi"], "forecast": [0.3]}), source="ff")
    ev = s.read("calendar_events")
    assert len(ev) == 1 and ev["forecast"].iloc[0] == 0.3          # a re-fetched event replaces the earlier copy


def test_duplicates_inside_the_first_batch_are_deduplicated(tmp_path):
    s = Store(tmp_path)
    ts = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    s.append("calendar_events", pd.DataFrame({"ts_utc": [ts, ts], "event_id": ["cpi", "cpi"], "forecast": [0.2, 0.3]}),
             source="ff")
    ev = s.read("calendar_events")
    assert len(ev) == 1 and ev["forecast"].iloc[0] == 0.3


def test_reads_are_start_inclusive_end_exclusive_utc_and_filtered_by_source_and_symbol(tmp_path):
    s = Store(tmp_path)
    s.append("ticks", _ticks(["2025-01-31 23:59:59", "2025-02-01 00:00", "2025-02-01 00:01"]), source="icm")
    s.append("ticks", _ticks(["2025-02-01 00:00"], bid=10.0), source="vantage")
    s.append("ticks", _ticks(["2025-02-01 00:00"], bid=20.0), source="icm", symbol="XAGUSD")
    assert len(list((tmp_path / "ticks" / "source=icm" / "symbol=XAUUSD").glob("year=2025/month=*"))) == 2
    got = s.read("ticks", source="icm", start="2025-02-01 00:00", end="2025-02-01 00:01")
    assert list(got["ts_utc"]) == [pd.Timestamp("2025-02-01 00:00", tz="UTC")]
    assert str(got["ts_utc"].dt.tz) == "UTC"
    # an aware non-UTC bound is converted: 19:00 New York on Jan 31 is 00:00 UTC on Feb 1
    ny = pd.Timestamp("2025-01-31 19:00", tz="America/New_York")
    assert len(s.read("ticks", source="icm", start=ny)) == 2
    assert set(s.read("ticks", start="2025-02-01")["bid"]) == {2400.0, 10.0}
    assert list(s.read("ticks", symbol="XAGUSD")["bid"]) == [20.0]
    assert s.sql("SELECT count(*) AS n FROM ticks")["n"].iloc[0] == 5


def test_asof_join_never_uses_a_row_before_it_was_available():
    bars = pd.DataFrame({"ts_utc": pd.date_range("2025-03-05 12:00", periods=6, freq="15min", tz="UTC"),
                         "close": range(6)}).sample(frac=1.0, random_state=1)          # unsorted on purpose
    macro = pd.DataFrame({"value_date": pd.to_datetime(["2025-03-04", "2025-03-05", "2025-03-05"], utc=True),
                          "available_utc": pd.to_datetime(["2025-03-05 11:00", "2025-03-05 12:30", "2025-03-05 13:01"],
                                                          utc=True).as_unit("s"),
                          "cpi": [1.0, 2.0, 3.0]})
    out = asof_join(bars, macro).set_index("ts_utc")
    assert "available_utc" not in out.columns
    assert list(out["cpi"]) == [1.0, 1.0, 2.0, 2.0, 2.0, 3.0]          # 12:30 is usable at 12:30, 13:01 only at 13:15
    # nothing is known before the first release
    early = asof_join(pd.DataFrame({"ts_utc": [pd.Timestamp("2025-03-05 10:00", tz="UTC")]}), macro)
    assert early["cpi"].isna().all()
    # a tolerance stops a stale value from being carried forward forever
    stale = asof_join(bars, macro, tolerance=pd.Timedelta(minutes=20)).set_index("ts_utc")
    assert stale.loc[pd.Timestamp("2025-03-05 12:15", tz="UTC"), "cpi"] != stale.loc[pd.Timestamp("2025-03-05 12:15", tz="UTC"), "cpi"]
    with pytest.raises(ValueError, match="never join on nominal dates"):
        asof_join(bars, macro.drop(columns=["available_utc"]))


def test_asof_join_prefix_namespaces_the_joined_columns():
    bars = pd.DataFrame({"ts_utc": pd.date_range("2025-03-05 12:00", periods=2, freq="h", tz="UTC")})
    other = pd.DataFrame({"available_utc": pd.to_datetime(["2025-03-05 11:00"], utc=True), "level": [5.0]})
    out = asof_join(bars, other, prefix="m_")
    assert list(out.columns) == ["ts_utc", "m_level"] and list(out["m_level"]) == [5.0, 5.0]


def test_asof_join_keeps_the_bar_time_when_joining_a_store_table():
    bars = pd.DataFrame({"ts_utc": pd.date_range("2025-03-05 12:00", periods=2, freq="h", tz="UTC"), "close": [1.0, 2.0]})
    macro = pd.DataFrame({"ts_utc": pd.to_datetime(["2025-03-04"], utc=True),
                          "available_utc": pd.to_datetime(["2025-03-05 11:00"], utc=True), "level": [5.0]})
    out = asof_join(bars, macro)
    assert list(out["ts_utc"]) == list(bars["ts_utc"])
