"""D-1 grading helpers: the GLD-implied fix parse (London closures, stale NAV, holdout) and the A16 run encoding."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load("dukascopy_gold_grade")
F = _load("gold_fix_gld")

HEAD = ["Date", "Closing Price", "Ounces of Gold per Share", "NAV/Share at 10:30am NYT"]


def test_rule_blob_is_the_imported_module() -> None:
    from goldbot.data import era_quality
    blob = subprocess.run(["git", "-C", str(ROOT), "hash-object", era_quality.__file__],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert blob == G.RULE_BLOB == G.git_state()[2]


def test_implied_fix_drops_london_closures_stale_days_and_the_holdout() -> None:
    rows: list[list[str | None]] = [list(HEAD)]
    rows += [["03-Jan-2017", "110", "0.1", "115.0"],         # 1150.0
             ["04-Jan-2017", "110", "0.1", "116.0"],         # 1160.0
             ["05-Jan-2017", "110", "0.1", "116.000001"],    # stale: < 0.2 bp move (NAV carried the price)
             ["17-Apr-2017", "110", "0.1", "117.0"],         # Easter Monday: London shut, no PM price
             ["18-Apr-2017", "110", "0.1", "US Holiday"],    # unparsed
             ["30-Sep-2025", "110", "0.1", "300.0"],
             ["01-Oct-2025", "110", "0.1", "301.0"]]         # sealed holdout
    f, counts = F.implied_fix(rows)
    assert list(f["observation_date"].dt.strftime("%Y-%m-%d")) == ["2017-01-03", "2017-01-04", "2025-09-30"]
    assert f["value"].iloc[0] == 1150.0
    assert counts == {"rows_read": 7, "rows_unparsed": 1, "dropped_london_closed_or_early_close": 1,
                      "dropped_stale": 1, "dropped_holdout": 1, "rows_kept": 3}
    assert str(f["fix_utc"].iloc[0]) == "2017-01-03 15:00:00+00:00"          # 15:00 London in winter = UTC
    assert (f["available_utc"] > f["fix_utc"]).all()                         # known after the NY NAV strike


def test_fix_bp_uses_the_last_bar_known_at_the_fix_and_at_most_five_minutes_old() -> None:
    ts = pd.Series(pd.to_datetime(["2017-01-03 14:58", "2017-01-03 14:59", "2017-01-03 16:00"], utc=True))
    bars = pd.DataFrame({"ts_utc": ts, "bid_close": [1000.0, 1001.0, 1002.0]})
    at = pd.Series(pd.to_datetime(["2017-01-03 15:00", "2017-01-03 15:30"], utc=True))
    bp = G.fix_bp(bars, at, pd.Series([1001.0, 1000.0]).to_numpy())
    assert len(bp) == 1 and abs(bp[0]) < 1e-9          # 14:59 bar closes 15:00; the 15:30 fix has no fresh bar


def test_runs_merge_consecutive_months_of_one_label() -> None:
    m = ["2009-11", "2009-12", "2010-01", "2010-02"]
    r = G._runs(m, ["a", "a", "b", "a"])
    assert [(x["from"], x["to"], x["serves"], x["months"]) for x in r] == [
        ("2009-11", "2009-12", "a", 2), ("2010-01", "2010-01", "b", 1), ("2010-02", "2010-02", "a", 1)]


def test_sufficiency_counts_reproduce_the_card_fx10_learn_row() -> None:
    C = _load_research("sufficiency_counts")
    assert [C.n_eff(17718, 10, tf) for tf in C.TFS] == [11399, 8468, 6306, 4174, 3221, 1611, 644, 322, 161]
    t = pd.DataFrame({"key": ["EURGBP", "EURGBP", "XAGUSD", "EURGBP"], "month": ["2012-01", "2012-02", "2012-01", "2019-01"],
                      "tier": ["B", "C", "A", "B"], "days": [20, 21, 22, 23]})
    assert C.unit_days(t, ("EURGBP", "XAGUSD"), C.AB, "2010-01", "2017-12") == 42


def _load_research(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "research" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------- r11-v2.2 gold fix window
def _bars(minutes: list[int], close: list[float], day: str = "2017-01-03") -> pd.DataFrame:
    """1m bars opening at 15:00 London + m minutes (winter: London = UTC)."""
    ts = pd.Series(pd.Timestamp(f"{day} 15:00", tz="UTC") + pd.to_timedelta(minutes, unit="min"))
    return pd.DataFrame({"ts_utc": ts, "bid_close": close})


def _fix(value: float, day: str = "2017-01-03") -> pd.DataFrame:
    return pd.DataFrame({"fix_utc": [pd.Timestamp(f"{day} 15:00", tz="UTC")], "value": [value]})


def test_window_takes_bars_closing_in_1500_1510_only() -> None:
    from goldbot.data.era_quality import gold_fix_window_d
    # bar opening 14:59 closes 15:00 (excluded); bars opening 15:00..15:09 close 15:01..15:10 (included); 15:10 out
    m = [-1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    c = [1000.0] + [1010.0] * 10 + [1000.0]
    d, dropped = gold_fix_window_d(_bars(m, c), _fix(1000.0))
    assert dropped == 0 and abs(d[0] - np.log(1.01) * 1e4) < 1e-9          # the exact 1000 bars sit outside


def test_d_is_the_minimum_over_the_window_and_short_days_are_dropped() -> None:
    from goldbot.data.era_quality import gold_fix_window_d
    d, dropped = gold_fix_window_d(_bars(list(range(10)), [1010.0, 1005.0, 1001.0] + [1020.0] * 7), _fix(1000.0))
    assert dropped == 0 and abs(d[0] - np.log(1.001) * 1e4) < 1e-9
    d, dropped = gold_fix_window_d(_bars([0, 1, 2, 3], [1000.0] * 4), _fix(1000.0))      # 4 < 5 bars
    assert len(d) == 0 and dropped == 1


def test_calibration_set_is_v21_a_independent_months_2015_04_to_2018_12_only() -> None:
    t = pd.DataFrame({
        "month": ["2015-03", "2015-04", "2016-06", "2017-01", "2018-12", "2019-01", "2012-05"],
        "tier": ["A", "A", "A", "B", "A", "A-consistent", "A"],
        "same_source": [False, False, False, False, False, True, False],
        "reference": ["native"] * 7})
    assert G.select_calibration(t) == ["2015-04", "2016-06", "2018-12"]


def _good(n: int = 20, bp: float = 1.0) -> np.ndarray:
    return np.full(n, bp)


def test_tol_is_the_smallest_grid_value_letting_all_but_one_month_pass_and_power_can_fail() -> None:
    from goldbot.data.era_quality import calibrate_gold_tol, power_check
    month_d = {f"2016-{i:02d}": _good(bp=2.2) for i in range(1, 11)}
    month_d["2017-01"] = _good(bp=9.0)                                    # one month may fail
    cal = calibrate_gold_tol(month_d)
    assert cal.tol_bp == 2.5 and cal.passing == 10 and not cal.void
    # shifted fixes that still pass every month: the check has no power -> void
    weak = {m: (_good(bp=2.0), _good(bp=2.0)) for m in month_d}
    bad = power_check(cal, weak)
    assert bad.void and bad.power_fail_share == 0.0 and "power" in bad.reason
    strong = {m: (_good(bp=7.5), _good(bp=7.5)) for m in month_d}
    good = power_check(cal, strong)
    assert not good.void and good.power_fail_share == 1.0


def test_tol_above_15_bp_voids_the_rule() -> None:
    from goldbot.data.era_quality import calibrate_gold_tol
    cal = calibrate_gold_tol({f"2016-{i:02d}": _good(bp=16.2) for i in range(1, 13)})
    assert cal.void and cal.tol_bp == 16.5 and "15.0" in cal.reason


def test_confirm_disclosure_lists_c_months_and_the_vol_mix() -> None:
    t = pd.DataFrame({"month": ["2017-12", "2018-01", "2018-02", "2018-03", "2019-01", "2019-02"],
                      "tier": ["A", "A", "C", "A", "C", "A-consistent"], "rv": [1, 2, 3, 4, 5, 6]})
    out = G.confirm_disclosure(t)
    assert out["c_months"] == ["2018-02", "2019-01"]
    mix = out["rv_tercile_mix"]
    assert sum(mix["kept"].values()) == 3 and sum(mix["excluded"].values()) == 2


def test_b_is_daily_only_in_the_counts_index_energy_15m_learn_is_zero() -> None:
    C = _load_research("sufficiency_counts")
    t = pd.DataFrame({"key": ["US500", "US500"], "month": ["2018-01", "2019-01"], "tier": ["B", "B"], "days": [20, 21]})
    out = C.counts(t)["index_energy_b"]
    learn = dict(zip(C.TFS, out["learn"], strict=True))
    assert learn["15m"] == 0 and learn["1d"] == round(41 * 0.1 / (1 + 5 * 0.5))


def test_rendered_b_unit_is_daily_only() -> None:
    import yaml

    from goldbot.research import data_sufficiency as D
    cfg = yaml.safe_load((ROOT / "config" / "data" / "sufficiency.yaml").read_text())
    c = {x.cell: x for x in D.certificates(cfg)}["index_energy_b/15m"]
    assert c.n_eff_learn == 0 and c.projected == "n/a: B is daily-only"


def test_d13_cache_never_holds_a_sealed_day() -> None:
    import lzma
    R = _load_research("d13_independent_rank")
    rec = np.zeros(3, dtype=R.REC)
    rec["sec"] = [int((pd.Timestamp(d) - pd.Timestamp("2025-01-01")).total_seconds())
                  for d in ("2025-09-29", "2025-09-30", "2025-10-01")]
    rec["close"], rec["volume"] = 100, 1.0
    out = np.frombuffer(lzma.decompress(R.cap(lzma.compress(rec.tobytes(), format=lzma.FORMAT_ALONE), 2025)),
                        dtype=R.REC)
    assert len(out) == 2
