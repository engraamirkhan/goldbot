"""dl-v1 dense label factory (goldbot/research/dense_labels.py): no look-ahead, C5 entry, holdout, sealed store,
judged phase refusal, decision-time cost(x), uniqueness on an overlap fixture, C6 purge."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data import holdout_guard
from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F


def _minutes(start: str, n: int, seed: int = 7, spread: float = 0.2, source: str = "dukascopy") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    mid = 1500 + np.cumsum(rng.normal(0, 0.3, n))
    hi = mid + np.abs(rng.normal(0, 0.2, n))
    lo = mid - np.abs(rng.normal(0, 0.2, n))
    op = mid + rng.normal(0, 0.05, n)
    op = np.clip(op, lo, hi)
    half = spread / 2
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
                         "bid_open": op - half, "bid_high": hi - half, "bid_low": lo - half, "bid_close": mid - half,
                         "ask_open": op + half, "ask_high": hi + half, "ask_low": lo + half, "ask_close": mid + half,
                         "tick_count": 10.0, "spread_mean": spread, "spread_max": spread, "source": source,
                         "bid_only": False})


def _rows(m: pd.DataFrame, tf: str, phase: int = 0, source: str = "dukascopy") -> pd.DataFrame:
    """fs-v1-like rows (the factory reads only the row keys): one per closed decision bar."""
    mm = D.Minutes(m)
    bars = D.decision_bars(mm, tf, phase)
    dec = D._to_utc(bars["close_ns"].to_numpy())
    sec = F.tf_seconds(tf)
    return pd.DataFrame({"instrument": "XAUUSD", "tf": tf, "phase": phase,
                         "group_id": -(-bars["close_ns"].to_numpy() // (sec * 10**9)) * (sec * 10**9),
                         "decision_utc": dec, "available_utc": dec, "window_from_utc": dec - pd.Timedelta(hours=2),
                         "source": source, "bid_only": False})


@pytest.fixture(scope="module")
def mins() -> pd.DataFrame:
    return _minutes("2015-03-02", 6 * 1440)


def test_constants_pinned() -> None:
    assert [t.name for t in D.TEMPLATES] == [f"T{i}" for i in range(8)]
    assert D.SPEC.judged_head == "T2" and D.SPEC.clip_r == 10 and D.SPEC.sides == (1, -1)
    assert D.H_BY_TF["15m"] == 8 * 3600 and D.H_BY_TF["1d"] == 10 * 86400
    assert D.LABEL_COST_COLS == ("entry", "risk_usd", "barrier_hit")
    t2 = next(t for t in D.TEMPLATES if t.name == "T2")
    assert (t2.stop_k, t2.target_k) == (1.0, 2.0)


def test_entry_strictly_after_decision_and_16_heads(mins: pd.DataFrame) -> None:
    rows = _rows(mins, "15m")
    lab = D.label_rows(D.Minutes(mins), rows, "15m")
    assert len(lab) == 16 * len(rows)
    has = lab["entry_utc"].notna()
    assert (lab.loc[has, "entry_utc"] > lab.loc[has, "decision_utc"]).all()            # C5
    assert not lab.loc[~has, "label_ok"].any() and has.mean() > 0.99
    ok = lab[lab["label_ok"]]
    assert len(ok) > 0
    assert (ok["label_exit_utc"] <= ok["horizon_end_utc"]).all()
    assert (ok["label_exit_utc"] > ok["entry_utc"]).all()
    assert set(ok["barrier_hit"]) <= {"stop", "target", "time"}
    assert (ok.loc[ok["template"] == "T0", "barrier_hit"] == "time").all()
    assert ok["gross_r"].abs().max() <= 10


def test_no_look_ahead(mins: pd.DataFrame) -> None:
    """A label depends only on minutes up to its horizon end: perturbing every later minute changes nothing; and its
    base distance only on minutes up to the decision."""
    tf = "15m"
    rows = _rows(mins, tf)
    lab = D.label_rows(D.Minutes(mins), rows, tf)
    i = len(rows) // 2
    r = rows.iloc[[i]]
    end = lab.loc[lab["decision_utc"] == r["decision_utc"].iloc[0], "horizon_end_utc"].iloc[0]
    m2 = mins.copy()
    after = (m2["ts_utc"] + pd.Timedelta(minutes=1)) > end
    for c in ("bid_open", "bid_high", "bid_low", "bid_close", "ask_open", "ask_high", "ask_low", "ask_close"):
        m2.loc[after, c] = m2.loc[after, c] * 1.5
    a = lab[lab["decision_utc"] == r["decision_utc"].iloc[0]].reset_index(drop=True)
    b = D.label_rows(D.Minutes(m2), r, tf)
    pd.testing.assert_frame_equal(a[list(D.COLUMNS)], b[list(D.COLUMNS)])
    # base distance: perturbing minutes after the decision leaves d0 unchanged
    m3 = mins.copy()
    later = m3["ts_utc"] >= r["decision_utc"].iloc[0]
    m3.loc[later, ["bid_high", "ask_high"]] += 50
    lab3 = D.label_rows(D.Minutes(m3), r, tf)
    assert np.allclose(lab3["base_dist"], a["base_dist"])


def test_revision_moves_label_availability(mins: pd.DataFrame) -> None:
    """A seeded future release: a minute in the horizon revised (available) later makes the label known only then."""
    rows = _rows(mins, "15m").iloc[[100]]
    m = mins.copy()
    lab0 = D.label_rows(D.Minutes(m), rows, "15m")
    j = int(np.searchsorted(m["ts_utc"], lab0["entry_utc"].iloc[0])) + 5
    late = pd.Timestamp("2020-01-01", tz="UTC")
    m["available_utc"] = m["visible_at"]
    m.loc[j, "available_utc"] = late
    lab1 = D.label_rows(D.Minutes(m), rows, "15m")
    assert (lab0["label_available_utc"] < late).all()
    assert (lab1["label_available_utc"] == late).all()


def test_barrier_paths_on_a_fixture() -> None:
    """Hand-built long paths: gapped stop fills at the open; target at the level; trail; break-even."""
    e, z = np.array([0]), np.array([4])
    d0 = np.array([1.0])
    T2 = next(t for t in D.TEMPLATES if t.name == "T2")
    # entry 100; minute 2 opens at 98.5 below the stop 99 -> fill 98.5 = -1.5 R
    o = np.array([100, 100.2, 98.5, 98.0, 98.0])
    h = np.array([100.5, 100.6, 98.7, 98.2, 98.1])
    lo = np.array([99.6, 99.9, 98.2, 97.8, 97.9])
    c = np.array([100.2, 100.3, 98.4, 98.0, 98.0])
    fp = D.first_passage(o, h, lo, c, e, z, d0, T2)
    assert fp["hit"][0] == 1 and fp["gross"][0] == pytest.approx(-1.5) and fp["exit"][0] == 2
    # target 102 touched in minute 3
    o2 = np.array([100, 100.5, 101.0, 101.8, 101.0])
    h2 = np.array([100.6, 101.2, 101.9, 102.5, 101.5])
    lo2 = np.array([99.5, 100.2, 100.8, 101.5, 100.5])
    fp = D.first_passage(o2, h2, lo2, o2, e, z, d0, T2)
    assert fp["hit"][0] == 2 and fp["gross"][0] == pytest.approx(2.0) and fp["exit"][0] == 3
    # T7: +1 R reached in minute 1 (high 101.1), stop to 100 from minute 2; minute 2 low 99.9 -> 0 R, moved
    T7 = next(t for t in D.TEMPLATES if t.name == "T7")
    o3 = np.array([100, 100.5, 100.4, 100, 100])
    h3 = np.array([100.4, 101.1, 100.5, 100, 100])
    lo3 = np.array([99.5, 100.3, 99.9, 100, 100])
    fp = D.first_passage(o3, h3, lo3, o3, e, z, d0, T7)
    assert fp["hit"][0] == 1 and fp["gross"][0] == pytest.approx(0.0) and fp["moved"][0]
    # T5: trail 1 below the best high 101.1 -> stop 100.1 from minute 2; low 99.9 -> exit 100.1 = +0.1 R
    T5 = next(t for t in D.TEMPLATES if t.name == "T5")
    fp = D.first_passage(o3, h3, lo3, o3, e, z, d0, T5)
    assert fp["hit"][0] == 1 and fp["gross"][0] == pytest.approx(0.1) and fp["moved"][0]
    # T0: time exit at the close of z
    T0 = D.TEMPLATES[0]
    fp = D.first_passage(o3, h3, lo3, np.array([1, 1, 1, 1, 103.0]), e, z, d0, T0)
    assert fp["hit"][0] == 0 and fp["gross"][0] == pytest.approx(3.0)


def test_short_side_is_the_mirror_on_the_ask(mins: pd.DataFrame) -> None:
    rows = _rows(mins, "1h")
    lab = D.label_rows(D.Minutes(mins), rows, "1h")
    t0 = lab[(lab["template"] == "T0") & lab["label_ok"]]
    lo, sh = t0[t0["side"] == 1].reset_index(drop=True), t0[t0["side"] == -1].reset_index(drop=True)
    # constant spread: a short's T0 gross is minus the long's
    assert np.allclose(lo["gross_r_raw"], -sh["gross_r_raw"])


def test_holdout_refused() -> None:
    m = _minutes("2025-09-29", 3 * 1440)
    with pytest.raises(holdout_guard.HoldoutSealed):
        D.Minutes(m)
    m = _minutes("2025-09-27", 3 * 1440)
    m = m[m["ts_utc"] < holdout_guard.HOLDOUT_START].reset_index(drop=True)
    rows = _rows(m, "1h")
    lab = D.label_rows(D.Minutes(m), rows, "1h")
    # the last labels' horizons pass the end of the minutes (the holdout start): truncated, never shortened
    assert (~lab.loc[lab["decision_utc"] > pd.Timestamp("2025-09-29 01:00", tz="UTC"), "label_ok"]).all()
    bad = lab.iloc[:1].assign(decision_utc=pd.Timestamp("2025-09-30 23:30", tz="UTC"), max_hold_s=3600.0)
    with pytest.raises(holdout_guard.HoldoutSealed):
        holdout_guard.refuse_labels(bad, what="test")


CORE6 = D.COLUMN_SETS["core-6"]


def _tiers(tmp_path: Path, inst: str, months: list[str], tier: str = "A") -> F.Tiers:
    p = tmp_path / "tiers.csv"
    pd.DataFrame({"key": inst, "month": months, "tier": tier, "r11_id": "r11-test"}).to_csv(p, index=False)
    return F.Tiers.from_csv(p)


def _parts(m: pd.DataFrame, tag: str = "a") -> dict[str, str]:
    return {"fs_key": f"fs-{tag}", "minutes_sha256": f"sha-{tag}", "minutes_to_utc": str(m["ts_utc"].max())}


def _store(tmp_path: Path, m: pd.DataFrame, tf: str, phase: int, source: str, year: int = 2015,
           tag: str = "a") -> tuple[pd.DataFrame, str]:
    rows = _rows(m, tf, phase, source=source).assign(instrument="EURUSD" if source != "dukascopy" else "XAUUSD")
    lab = D.label_rows(D.Minutes(m), rows, tf)
    inst = str(rows["instrument"].iloc[0])
    key = D.write_partition(lab, cache=tmp_path, instrument=inst, tf=tf, year=year, phase=phase,
                            parts=_parts(m, tag))
    return lab, key


def test_judge_refuses_phase_and_sealed_store(tmp_path: Path, mins: pd.DataFrame,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    m = mins.assign(source="histdata:EURUSD")
    _, k0 = _store(tmp_path, m, "15m", 0, "histdata:EURUSD")
    _, k5 = _store(tmp_path, m, "15m", 5, "histdata:EURUSD")
    tiers = _tiers(tmp_path, "EURUSD", ["2015-02", "2015-03"])
    win = (pd.Timestamp("2015-01-01", tz="UTC"), pd.Timestamp("2016-01-01", tz="UTC"))
    keys = {(2015, 0): k0, (2015, 5): k5}

    def load(tiers_: F.Tiers | None, purpose: str, phases: tuple[int, ...] = (0,),
             columns: tuple[str, ...] | None = CORE6) -> tuple[pd.DataFrame, dict[str, Any]]:
        return D.load_labels(tmp_path, instrument="EURUSD", tf="15m", years=[2015], tiers=tiers_, columns=columns,
                             keys=keys, window=win, purpose=purpose, phases=phases)

    with pytest.raises(D.SealedStore):                   # card ruling 5: no recorded tiers sha, no label read
        load(tiers, "judge")
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset({tiers.sha256}))
    with pytest.raises(ValueError, match="phase 0"):
        load(tiers, "judge", phases=(0, 5))
    with pytest.raises(ValueError, match="tiers"):
        load(None, "judge")
    with pytest.raises(ValueError, match="columns"):     # quant C-3: the served column set is required
        load(tiers, "train", columns=None)
    lab, meta = load(tiers, "judge")
    assert len(lab) and (lab["phase"] == 0).all() and lab["usable"].all()
    assert set(D.LABEL_VALUES) <= set(lab.columns) and meta["columns"] == list(CORE6)
    tr, _ = load(tiers, "train", phases=(0, 5))
    assert set(tr["phase"]) == {0, 5}
    # gold rows under tiers that grade another source: ungraded, never usable (quant C3)
    _, kg = _store(tmp_path, mins, "15m", 0, "dukascopy")
    gold, _ = D.load_labels(tmp_path, instrument="XAUUSD", tf="15m", years=[2015], tiers=tiers, window=win,
                            columns=CORE6, keys={(2015, 0): kg})
    assert gold.empty


def test_label_values_live_apart_from_the_skeleton(tmp_path: Path, mins: pd.DataFrame) -> None:
    """Code review LOW-2: the skeleton file holds no label value; the values file is a separate file."""
    _, key = _store(tmp_path, mins, "15m", 0, "dukascopy")
    d = tmp_path / D.SPEC.spec_id / "XAUUSD" / "15m" / "p0" / "2015"
    sk = pd.read_parquet(d / f"{key[:24]}.skeleton.parquet")
    assert not set(D.LABEL_VALUES) & set(sk.columns)
    va = pd.read_parquet(d / f"{key[:24]}.values.parquet")
    assert set(va.columns) == {*D.VALUE_KEYS, *D.LABEL_VALUES}
    s2 = D.skeleton(tmp_path, instrument="XAUUSD", tf="15m", years=[2015])
    assert len(s2) == len(sk) and not set(D.LABEL_VALUES) & set(s2.columns)


def test_reads_only_the_partition_of_the_data_key(tmp_path: Path, mins: pd.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant C-2 / code review LOW-4: a revised-minutes rebuild (new key) is never shadowed by the stale file, and
    two candidates with no key to choose by raise."""
    m = mins.assign(source="histdata:EURUSD")
    _, stale = _store(tmp_path, m, "15m", 0, "histdata:EURUSD", tag="old")
    m2 = m.copy()
    m2.loc[100, ["bid_close", "ask_close"]] += 0.5           # a revised minute: a new minutes sha, a new key
    _, fresh = _store(tmp_path, m2, "15m", 0, "histdata:EURUSD", tag="new")
    assert stale != fresh
    with pytest.raises(ValueError, match="no data key"):
        D.skeleton(tmp_path, instrument="EURUSD", tf="15m", years=[2015])
    tiers = _tiers(tmp_path, "EURUSD", ["2015-02", "2015-03"])
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset({tiers.sha256}))
    win = (pd.Timestamp("2015-01-01", tz="UTC"), pd.Timestamp("2016-01-01", tz="UTC"))
    lab, meta = D.load_labels(tmp_path, instrument="EURUSD", tf="15m", years=[2015], tiers=tiers, window=win,
                              columns=CORE6, keys={(2015, 0): fresh})
    assert meta["keys"] == {"2015:0": fresh}
    with pytest.raises(FileNotFoundError):
        D.load_labels(tmp_path, instrument="EURUSD", tf="15m", years=[2015], tiers=tiers, window=win,
                      columns=CORE6, keys={(2015, 0): "0" * 64})


def test_confirm_and_test_old_need_a_booked_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quant C-1: an unsealed store refuses a 2018 read without a booked id and reads 2015; a booked id under the
    frozen spec hash opens it and goes into the manifest; learn reads end at the learn end. Quant C-4: the first
    consumption under an id fixes its columns, spec hash and keys."""
    m15 = _minutes("2015-03-02", 6 * 1440).assign(source="histdata:EURUSD")
    m18 = _minutes("2018-03-05", 6 * 1440).assign(source="histdata:EURUSD")
    _, k15 = _store(tmp_path, m15, "15m", 0, "histdata:EURUSD", year=2015)
    _, k18 = _store(tmp_path, m18, "15m", 0, "histdata:EURUSD", year=2018)
    months = [str(p) for p in pd.period_range("2014-01", "2015-03", freq="M")] + \
        [str(p) for p in pd.period_range("2017-01", "2018-03", freq="M")]
    tiers = _tiers(tmp_path, "EURUSD", months)
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset({tiers.sha256}))
    learn = (pd.Timestamp("2010-01-01", tz="UTC"), pd.Timestamp("2018-01-01", tz="UTC"))
    confirm = (pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2025-10-01", tz="UTC"))
    ledger = tmp_path / "window_reads.jsonl"

    def load(year: int, key: str, window: tuple[pd.Timestamp, pd.Timestamp], *, read: str | None = None,
             frozen: str | None = None, columns: tuple[str, ...] = CORE6) -> tuple[pd.DataFrame, dict[str, Any]]:
        return D.load_labels(tmp_path, instrument="EURUSD", tf="15m", years=[year], tiers=tiers, columns=columns,
                             keys={(year, 0): key}, window=window, booked_read=read,
                             ledger=ledger if read else None, frozen_spec_hash=frozen)

    lab, meta = load(2015, k15, learn)
    assert len(lab) and meta["booked_read"] is None
    with pytest.raises(PermissionError, match="booked"):
        load(2018, k18, confirm)
    ledger.write_text('{"family": "sr_v3", "window": "confirm", "status": "started", "key": "read-1", '
                      '"ref": {"spec_hash": "frozen-1"}}\n')
    with pytest.raises(PermissionError, match="spec hash"):
        load(2018, k18, confirm, read="read-1", frozen="other")
    got, meta = load(2018, k18, confirm, read="read-1", frozen="frozen-1")
    assert len(got) and meta["booked_read"] == "read-1"
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    cons = [r for r in rows if r.get("type") == "consumption"]
    assert len(cons) == 1 and cons[0]["columns"] == sorted(CORE6) and cons[0]["keys"] == {"2018:0": k18}
    load(2018, k18, confirm, read="read-1", frozen="frozen-1")          # the same consumption again: allowed
    load(2018, k18, confirm, read="read-1", frozen="frozen-1",
         columns=tuple(reversed(CORE6)))                                 # column order does not matter (a set)
    with pytest.raises(PermissionError, match="first consumed"):        # C-4: P30 after core-6 under one id
        load(2018, k18, confirm, read="read-1", frozen="frozen-1", columns=D.COLUMN_SETS["P30"])
    # a learn read whose window ends past the learn end still drops every row reaching confirm
    lab2, _ = load(2015, k15, (learn[0], pd.Timestamp("2017-12-31", tz="UTC")))
    assert (lab2["horizon_end_utc"] <= D.learn_end("EURUSD")).all()
    assert D.judged_spans("US500")[0][0] == pd.Timestamp("2022-01-01", tz="UTC")


def test_recorded_tiers_are_in_adr_0085_a4() -> None:
    """Code review LOW-3: every recorded sha is a full 64-hex string on an ADR 0085 line that records a tiers file."""
    import re
    text = (D.ROOT / "docs" / "decisions" / "0085-power-first-gates.md").read_text()
    recorded = {h for line in text.splitlines() if re.search(r"\brecord", line, re.I) and "tiers" in line
                for h in re.findall(r"\b[0-9a-f]{64}\b", line)}
    for sha in D.RECORDED_TIERS_SHA256:
        assert re.fullmatch(r"[0-9a-f]{64}", sha), sha
        assert sha in recorded, f"{sha} is not on a recording line of ADR 0085 A4"


def test_tier_rules(tmp_path: Path, mins: pd.DataFrame) -> None:
    m = mins.assign(source="histdata:EURUSD")
    rows = _rows(m, "15m", source="histdata:EURUSD").assign(instrument="EURUSD")
    lab = D.label_rows(D.Minutes(m), rows, "15m")
    b = D.attach_tiers(lab, _tiers(tmp_path, "EURUSD", ["2015-02", "2015-03"], "B"), columns=CORE6)
    assert not b["usable"].any()                         # intraday: B months label nothing
    other = D.attach_tiers(lab.assign(source="dukascopy"), _tiers(tmp_path, "EURUSD", ["2015-02", "2015-03"]),
                           columns=CORE6)
    assert (other["tier"] == "ungraded").all()           # tiers of another source never grade the rows (C3)
    a = D.attach_tiers(lab, _tiers(tmp_path, "EURUSD", ["2015-02", "2015-03"], "A-consistent"), columns=CORE6)
    assert (a["usable"] == a["label_ok"]).all()
    # P30 reads d1 context (d1_slope_ema20): its lookback reaches months the A-only file leaves ungraded
    p30 = D.attach_tiers(lab, _tiers(tmp_path, "EURUSD", ["2015-02", "2015-03"], "A"), columns=D.COLUMN_SETS["P30"])
    assert not p30["usable"].any()
    lab_d = lab.assign(tf="1d")
    months = [str(p) for p in pd.period_range("2012-01", "2015-03", freq="M")]
    bd = D.attach_tiers(lab_d, _tiers(tmp_path, "EURUSD", months, "B"), columns=CORE6)
    assert (bd.loc[bd["usable"], "template"] == "T0").all() and bd["usable"].any()   # B: close-to-close only


def test_column_sets_and_input_lookback() -> None:
    """ADR 0104 4 (quant ruling 2026-11-26): the lookback is the max of each column's declared lookback, x 7/5 below
    1w; a d1_ column reads its base column's 1d lookback in daily bars x 7/5 on a tf with the d1 context."""
    from goldbot.features.registry import column_lookback
    assert len(D.COLUMN_SETS["core-6"]) == 6 and len(D.COLUMN_SETS["core-12"]) == 12
    assert len(D.COLUMN_SETS["P30"]) == 30 and "side" in D.COLUMN_SETS["P30"]
    assert "d1_slope_ema20" in D.COLUMN_SETS["P30"]                       # never dropped by hand
    prod = D.column_producers()
    assert all(c in prod for cs in D.COLUMN_SETS.values() for c in cs if c != "side")
    bars = max(column_lookback(c, "15m") for c in CORE6 if c != "side")
    assert D.input_lookback("15m", CORE6) == pd.Timedelta(seconds=round(bars * 900 * 7 / 5))
    assert D.input_lookback("1w", CORE6) == pd.Timedelta(weeks=max(column_lookback(c, "1w") for c in CORE6[1:]))
    assert D.input_lookback("15m", ["d1_slope_ema20"]) == pd.Timedelta(
        seconds=round(column_lookback("slope_ema20", "1d") * 86400 * 7 / 5))
    assert D.input_lookback("1d", ["d1_slope_ema20"]) == pd.Timedelta(0)    # 1d frames carry no d1 context
    assert D.input_lookback("15m", ["min_since_london_open"]) == pd.Timedelta(seconds=round(900 * 7 / 5))
    with pytest.raises(ValueError):
        D.input_lookback("15m", ["not_a_column"])


def test_revised_minute_floor_and_late_bar(mins: pd.DataFrame) -> None:
    """Code review LOW-1: a minute revised after a decision never enters that decision's floor, and the bar holding
    it is no decision bar (drop_late, as fs-v1)."""
    m = mins.copy()
    m["available_utc"] = m["visible_at"]
    j = 2000
    m.loc[j, ["bid_high", "ask_high"]] += 40.0                      # a huge range, revised a day later
    m.loc[j, "available_utc"] = pd.DatetimeIndex(m["visible_at"])[j] + pd.Timedelta(days=1)
    mm = D.Minutes(m)
    vis = pd.DatetimeIndex(m["visible_at"])
    dec = np.array([vis[j + 30].value])
    # expected: the median 1m mid TR over the 1,440 minutes up to the decision, minute j left out (not yet visible)
    mi = j + 30
    win = [i for i in range(mi - 1439, mi + 1) if i != j]
    mid = {k: ((m[f"bid_{k}"] + m[f"ask_{k}"]) / 2).to_numpy()[win] for k in ("high", "low", "close")}
    prev = np.concatenate(([np.nan], mid["close"][:-1]))
    tr = np.fmax(np.fmax(mid["high"] - mid["low"], np.abs(mid["high"] - prev)), np.abs(mid["low"] - prev))
    assert np.allclose(mm.floor_at(dec), 1.5 * np.nanmedian(tr))
    assert not np.allclose(mm.floor_at(dec), mm.tr_floor[mi])      # the rolling value would have read minute j
    bars = D.decision_bars(mm, "15m")
    late_close = pd.DatetimeIndex(m["ts_utc"])[j].floor("15min") + pd.Timedelta(minutes=15)
    assert late_close.value not in set(bars["close_ns"])
    assert (np.diff(bars["close_ns"].to_numpy()) > 0).all()


def test_cost_x_identical_across_exits() -> None:
    """Same decision inputs, gapped stop / overnight time exit / target exit: one cost(x)."""
    dec = pd.Timestamp("2015-03-04 20:00", tz="UTC")
    base = {"instrument": "XAUUSD", "tf": "1h", "template": "T2", "side": 1, "decision_utc": dec,
            "max_hold_s": 86400.0, "entry": 1200.0, "risk_usd": 3.0, "cost_rt_bp": DC.RT_BP}
    rows = pd.DataFrame([
        {**base, "barrier_hit": "stop", "gross_r_raw": -1.8, "label_exit_utc": dec + pd.Timedelta(minutes=3)},
        {**base, "barrier_hit": "time", "gross_r_raw": 0.4, "label_exit_utc": dec + pd.Timedelta(hours=23)},
        {**base, "barrier_hit": "target", "gross_r_raw": 2.0, "label_exit_utc": dec + pd.Timedelta(hours=2)},
    ])
    pri = DC.CostPriors(p_stop={"XAUUSD:1h:T2": 0.4}, night_share={"XAUUSD:1h:T2": 0.5}, p_stop_pooled=0.4,
                        night_share_pooled=0.5, n_rows=10, labelled_before="2015-01-01")
    net = D.net_r(rows, pri)
    cost = rows["gross_r_raw"].to_numpy() - net
    assert np.allclose(cost, cost[0]) and cost[0] > 0
    ref = DC.cost_x(rows.assign(arm="XAUUSD:1h:T2"), pri)
    assert np.allclose(cost, ref)                            # W2: exactly decision_cost.cost_x
    hi = D.net_r(rows, pri, rt_bp=2 * DC.RT_BP)              # ub95 above W2 charges more, still exit-blind
    assert np.allclose(rows["gross_r_raw"].to_numpy() - hi, (rows["gross_r_raw"].to_numpy() - hi)[0])
    assert (hi < net).all()


def test_uniqueness_on_an_overlap_fixture() -> None:
    """Events [0,3], [2,5], [4,5] on a 6-minute grid: c = 1,1,2,2,2,2; u = (1+1+.5+.5)/4, (.5*4)/4, (.5+.5)/2."""
    u, c = D.uniqueness(np.array([0, 2, 4]), np.array([3, 5, 5]), 6)
    assert c.tolist() == [1, 1, 2, 2, 2, 2]
    assert np.allclose(u, [0.75, 0.5, 0.5])
    # a phase event weighs against the phase-0 concurrency without adding to it
    up, _ = D.uniqueness(np.array([1]), np.array([2]), 6, c)
    assert np.allclose(up, [(1 + 0.5) / 2])


def test_weights_one_event_per_bar_and_phase_rows(mins: pd.DataFrame) -> None:
    mm = D.Minutes(mins)
    ev = {}
    for ph in (0, 5):
        rows = _rows(mins, "15m", ph)
        lab = D.label_rows(mm, rows, "15m")
        one = lab[lab["template"] == "T2"].drop_duplicates(["phase", "decision_utc"])
        ev[ph] = one
    events = pd.concat(ev.values(), ignore_index=True)
    w = D.weights({"15m": events}, mm.ts, {"15m": 2})["15m"]
    assert len(w) == len(events)                                    # one weight per bar event, not per head
    p0 = w[w["phase"] == 0]
    assert np.allclose(p0["w"], p0["u_tf"] / 2)
    ne = D.n_eff(w, "15m")
    ne0 = D.n_eff(w[w["phase"] == 0], "15m")
    assert ne == ne0                                                # phases add no n_eff
    # H = 8 h on 15 min bars: about 32 concurrent, u about 1/32
    assert 1 / 40 < p0.loc[p0["u_tf"] > 0, "u_tf"].median() < 1 / 25


def test_purge_drops_overlap_and_phase_copies(mins: pd.DataFrame) -> None:
    rows = _rows(mins, "15m")
    lab = D.label_rows(D.Minutes(mins), rows, "15m")
    one = lab[(lab["template"] == "T2") & (lab["side"] == 1)].reset_index(drop=True)
    day = pd.Timestamp("2015-03-05", tz="UTC")
    test = one[(one["decision_utc"] >= day) & (one["decision_utc"] < day + pd.Timedelta(days=1))]
    train = one[~one.index.isin(test.index)]
    copy5 = test.assign(phase=5, decision_utc=test["decision_utc"] + pd.Timedelta(minutes=5),
                        window_from_utc=test["window_from_utc"] + pd.Timedelta(minutes=5))
    kept = D.purge(pd.concat([train, copy5], ignore_index=True), test, "15m")
    assert not (kept["phase"] == 5).any()                           # C6: every phase copy purged
    # nothing kept overlaps a test interval, nothing within the embargo after the block
    for _, t in test.iterrows():
        assert not ((kept["window_from_utc"] <= t["horizon_end_utc"])
                    & (kept["horizon_end_utc"] >= t["decision_utc"])).any()
    end = test["horizon_end_utc"].max()
    assert not ((kept["decision_utc"] > test["decision_utc"].min())
                & (kept["decision_utc"] <= end + D.embargo("15m"))).any()
    assert len(kept) > 0                                            # rows before the block survive


def test_embargo() -> None:
    assert D.embargo("1m") == pd.Timedelta(days=140)                # d1 context lookback
    assert D.embargo("1d") >= pd.Timedelta(days=140)
    assert D.embargo("1w") >= pd.Timedelta(weeks=100)


def test_daily_time_exit_on_the_feature_day_grid() -> None:
    m = _minutes("2015-03-02", 30 * 1440)
    rows = _rows(m, "1d")
    lab = D.label_rows(D.Minutes(m), rows, "1d")
    t0 = lab[(lab["template"] == "T0") & lab["label_ok"]]
    grid = set(rows["decision_utc"])
    assert len(t0) and t0["horizon_end_utc"].isin(grid).all()        # C5: close to close on the fs-v1 grid
    assert (t0["label_exit_utc"] == t0["horizon_end_utc"]).all()


def test_reserve_weeks() -> None:
    a = pd.Series(pd.to_datetime(["2015-01-05", "2026-10-05", "2026-10-12"], utc=True))
    b = a + pd.Timedelta(days=1)
    r = D.iso_reserve(a, b)
    # ISO week of 2026-10-05 is 41 (odd: reserve), 2026-10-12 is 42
    assert r.tolist() == [False, True, False]


def _race_consume(ledger: str, columns: list[str], start: Any, out: Any) -> None:
    start.wait()
    try:
        D.consume(Path(ledger), "race-1", columns=columns, label_spec_hash="h", keys={"2018:0": "k"})
        out.put("ok")
    except PermissionError:
        out.put("refused")


@pytest.mark.parametrize("locked", [True, False], ids=["with-lock", "lock-removed"])
def test_consume_is_atomic_across_processes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool) -> None:
    """Code review LOW-A / LOW-2: two processes consume one booked id with different column sets, and a barrier right
    after each one's read of the ledger forces the interleaving (both read before either appends, unless the lock
    keeps the second out). With the lock exactly one is accepted and one consumption row is written; with the lock
    removed both are accepted, which is the race the lock prevents."""
    import contextlib
    import multiprocessing as mp
    import os
    import threading
    from collections.abc import Iterator

    from goldbot.research import windows as WN
    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(2)
    orig = D._consumptions

    def read_then_wait(ledger: Path, read_id: str) -> list[dict[str, Any]]:
        rows = orig(ledger, read_id)
        with contextlib.suppress(threading.BrokenBarrierError):
            barrier.wait(timeout=2.0)          # with the lock the other process never gets here: timeout, go on
        return rows

    @contextlib.contextmanager
    def no_lock(path: Path | str) -> Iterator[int]:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            yield fd
        finally:
            os.close(fd)

    monkeypatch.setattr(D, "_consumptions", read_then_wait)
    if not locked:
        monkeypatch.setattr(WN, "locked_ledger", no_lock)
    ledger = tmp_path / "window_reads.jsonl"
    start, out = ctx.Event(), ctx.Queue()
    ps = [ctx.Process(target=_race_consume, args=(str(ledger), cols, start, out))
          for cols in (list(CORE6), list(D.COLUMN_SETS["P30"]))]
    for p in ps:
        p.start()
    start.set()
    for p in ps:
        p.join(30)
    got = sorted(out.get(timeout=5) for _ in ps)
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    n_cons = sum(r.get("type") == "consumption" for r in rows)
    if locked:
        assert got == ["ok", "refused"] and n_cons == 1
    else:
        assert got == ["ok", "ok"] and n_cons == 2


def test_ledger_tolerates_one_torn_final_line_only(tmp_path: Path) -> None:
    """Code review LOW-B: one unparsable unterminated final line is left out and reported (and cut before the next
    append); a malformed line in the middle refuses the ledger. The windows read-once ledger shares the reader."""
    from goldbot.research import windows as WN
    ledger = tmp_path / "window_reads.jsonl"
    good = '{"family": "sr_v3", "window": "confirm", "status": "started", "key": "r", "ref": {"spec_hash": "s"}}'
    ledger.write_text(good + "\n" + '{"type": "consumption", "id": "r", "colu')
    assert len(WN.read_ledger(ledger)) == 1
    assert D.booked(ledger, "r", "s")["key"] == "r"
    D.consume(ledger, "r", columns=list(CORE6), label_spec_hash="h", keys={"2018:0": "k"})
    rows = WN.read_ledger(ledger)
    assert len(rows) == 2 and rows[-1]["type"] == "consumption"
    assert (tmp_path / "window_reads.jsonl.torn").exists()
    ledger.write_text(good + "\n" + "not json\n" + good + "\n")
    with pytest.raises(WN.LedgerCorrupt):
        WN.read_ledger(ledger)
    with pytest.raises(WN.LedgerCorrupt):
        D.booked(ledger, "r", "s")


def _mixed_tiers(tmp_path: Path, inst: str, by_month: dict[str, str]) -> F.Tiers:
    p = tmp_path / "tiers_mixed.csv"
    pd.DataFrame({"key": inst, "month": list(by_month), "tier": list(by_month.values()),
                  "r11_id": "r11-test"}).to_csv(p, index=False)
    return F.Tiers.from_csv(p)


def test_rule_r_a_close_only_lookbacks_accept_b_months_nothing_else_does(tmp_path: Path) -> None:
    """R-a (quant ruling on PR #486, ADR 0104 4): B lookback months only for close-only columns; the label window
    stays A / A-consistent; C never; usability is the conjunction over the requested columns."""
    m = _minutes("2015-03-26", 8 * 1440).assign(source="histdata:EURUSD")
    rows = _rows(m, "15m", source="histdata:EURUSD").assign(instrument="EURUSD")
    lab = D.label_rows(D.Minutes(m), rows, "15m")
    assert D.close_only("ret_96") and not D.close_only("atr_ratio_14_100") and D.close_only("d1_rv_ratio")

    def month(ts: pd.Series) -> np.ndarray:
        return D.months_between(pd.Series(ts), pd.Series(ts))[0]

    def starts_in_march(col: str) -> np.ndarray:
        return month(pd.Series(pd.DatetimeIndex(lab["decision_utc"]) - D.input_lookback("15m", [col]))) == "2015-03"
    april = (month(lab["decision_utc"]) == "2015-04") & (month(lab["horizon_end_utc"]) == "2015-04")
    ok = lab["label_ok"].to_numpy(bool) & april
    reach = starts_in_march("ret_96") & april
    strict_reach = starts_in_march("spread_rel_median_48")
    assert (reach & ~strict_reach).any() and (april & ~reach).any() and ok.any()
    mar_b = _mixed_tiers(tmp_path, "EURUSD", {"2015-03": "B", "2015-04": "A"})
    close = D.attach_tiers(lab, mar_b, columns=("side", "ret_96", "spread_rel_median_48"))
    # B only in a close-only lookback: usable; B in the strict column's lookback or the label window: not
    assert (close["usable"].to_numpy() == (ok & ~strict_reach)).all() and close["usable"].to_numpy()[reach].any()
    assert set(close.loc[reach, "tier"]) == {"B"}            # reported tier = the worst month read
    strict = D.attach_tiers(lab, mar_b, columns=("side", "atr_ratio_14_100"))
    assert starts_in_march("atr_ratio_14_100")[april].all() and not strict["usable"].any()   # strict lookback: B refused
    both = D.attach_tiers(lab, mar_b, columns=("side", "ret_96", "atr_ratio_14_100"))
    assert not both["usable"].any()                          # conjunction over the columns
    mar_c = _mixed_tiers(tmp_path, "EURUSD", {"2015-03": "C", "2015-04": "A"})
    c = D.attach_tiers(lab, mar_c, columns=("side", "ret_96"))
    assert not c.loc[reach, "usable"].any() and (c["usable"].to_numpy()[april & ~reach] == ok[april & ~reach]).all()
