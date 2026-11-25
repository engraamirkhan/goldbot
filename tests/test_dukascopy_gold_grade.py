"""D-1 grading helpers: the GLD-implied fix parse (London closures, stale NAV, holdout) and the A16 run encoding."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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


def test_frozen_rule_blob_is_the_module_on_disk() -> None:
    blob = subprocess.run(["git", "-C", str(ROOT), "hash-object", "goldbot/data/era_quality.py"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert blob == G.FROZEN_BLOB


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
