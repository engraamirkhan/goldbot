"""The holdout seal (EVAL-2 method v6 section 1b, prereg 2026-11-09): "any research loader that asks for bars in
2025-10-01..2026-09-30 outside `--score-holdout` must raise", and nothing reads TEST-OLD or a second confirm read
without its gate.

Two kinds of loader, two contracts
  named-window / span loaders   asking for the holdout (or a span reaching it) RAISES
  whole-file loaders            files that hold holdout rows (the 2025 file's Oct-Dec, the 2026 file) are read, and the
                                output never carries a holdout row (cut before it leaves the loader)
Persistence: a bar cache on disk is state too. A cached npz holding a holdout bar, or a bar past its window's end (a
2018 bar in the explore cache = an unsealed confirm read), must fail closed on load, never be served.
Static: every goldbot/research module that opens parquet bars names a holdout guard (new loaders cannot skip it).

All market data here is synthetic, written under tmp_path; nothing is read from raw/."""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import windows as W
from goldbot.research.catalogue import data as DA
from goldbot.research.catalogue import dispatcher as D
from goldbot.research.windows import HoldoutSealed, TestOldSealed

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = pd.Timestamp("2025-10-01", tz="UTC")
HOLDOUT_END = pd.Timestamp("2026-10-01", tz="UTC")
HOLDOUT_NS = int(HOLDOUT.value)


# ------------------------------------------------------------------ synthetic raw dir with holdout rows
def _minutes(start: str, end: str) -> pd.DataFrame:
    """1m bid/ask rows every 7 minutes in [start, end): the 2025 file runs into the holdout, the 2026 file is all
    holdout."""
    ts = pd.date_range(start, end, freq="7min", tz="UTC", inclusive="left")
    ts = ts[ts.weekday < 5]
    n = len(ts)
    mid = 1800 + np.cumsum(np.random.default_rng(len(ts)).normal(0, 0.2, n))
    f = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1)})
    for k, off in (("open", 0.0), ("high", 0.3), ("low", -0.3), ("close", 0.1)):
        f[f"bid_{k}"] = mid + off - 0.1
        f[f"ask_{k}"] = mid + off + 0.1
    f["tick_count"] = 10
    f["spread_mean"] = 0.2
    f["spread_max"] = 0.3
    return f


def _daily(tf: str) -> pd.DataFrame:
    step = pd.Timedelta(hours=4) if tf == "4h" else pd.Timedelta(days=1)
    ts = pd.date_range("2024-06-01 22:00", "2026-06-30", freq=step, tz="UTC")
    n = len(ts)
    c = 2000 + np.arange(n) * 0.1
    return pd.DataFrame({"date": ts.tz_localize(None).normalize(), "ts_utc": ts, "visible_at": ts + step,
                         "open": c, "high": c + 1, "low": c - 1, "close": c, "spread_mean": 0.2,
                         "volume": 1.0, "source": "dukascopy_1m:XAUUSD"})


@pytest.fixture()
def raw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    r = tmp_path / "raw"
    (r / "daily" / "bars").mkdir(parents=True)
    _minutes("2025-08-01", "2026-01-01").to_parquet(r / "xauusd_1m_dukascopy_2025.parquet")
    _minutes("2026-01-01", "2026-03-01").to_parquet(r / "xauusd_1m_dukascopy_2026.parquet")
    for tf in ("4h", "1d"):
        _daily(tf).to_parquet(r / "daily" / "bars" / f"xauusd_{tf}.parquet")
    monkeypatch.setenv("GOLDBOT_RAW", str(r))
    monkeypatch.setenv("CATALOGUE_CACHE", str(tmp_path / "cache"))
    DA._stored.cache_clear()
    monkeypatch.setattr(D, "_BAR_CACHE", {})
    monkeypatch.setattr(D, "_MINUTES", {})
    yield r
    DA._stored.cache_clear()


def _no_holdout(t_ns: np.ndarray, ta_ns: np.ndarray | None = None) -> None:
    assert len(t_ns), "vacuous: the loader returned no rows"
    assert int(np.max(t_ns)) < HOLDOUT_NS
    if ta_ns is not None:
        assert int(np.max(ta_ns)) <= HOLDOUT_NS


# ------------------------------------------------------------------ named windows and spans raise
@pytest.mark.parametrize("call", [
    lambda: DA.window("holdout"),
    lambda: DA.load("XAUUSD", "1h", "holdout"),
    lambda: DA.load_minutes("holdout"),
    lambda: DA.fred("DTB3", "holdout"),
    lambda: D.get_bars("XAUUSD", "1h", "holdout"),
    lambda: D.get_minutes("holdout"),
    lambda: W.bounds("holdout"),
    lambda: W.entry_mask([HOLDOUT], "holdout"),
], ids=["data.window", "data.load", "data.load_minutes", "data.fred", "dispatcher.get_bars",
        "dispatcher.get_minutes", "windows.bounds", "windows.entry_mask"])
def test_asking_a_research_loader_for_the_holdout_window_raises(call: object, raw: Path) -> None:
    with pytest.raises(HoldoutSealed):
        call()  # type: ignore[operator]


@pytest.mark.parametrize("end", [HOLDOUT + pd.Timedelta(minutes=1), pd.Timestamp("2026-03-01", tz="UTC"),
                                 HOLDOUT_END])
def test_span_loaders_refuse_any_end_past_the_holdout_start(end: pd.Timestamp, raw: Path) -> None:
    from goldbot.research.arms import assert_block_allowed
    from goldbot.research.sprint3000.market import load_minutes
    with pytest.raises(ValueError, match="holdout"):
        load_minutes(raw, pd.Timestamp("2025-09-01", tz="UTC"), end)
    with pytest.raises(ValueError, match="holdout"):
        assert_block_allowed(pd.Timestamp("2025-09-01", tz="UTC"), end)


def test_a_position_window_ending_in_the_holdout_cannot_be_built() -> None:
    from datetime import date

    from pydantic import ValidationError

    from goldbot.research.position.windows import Window
    # HoldoutViolation subclasses AssertionError, so pydantic re-raises it as a ValidationError: still refused
    with pytest.raises(ValidationError, match="reaches the holdout"):
        Window(name="x", start=date(2025, 1, 1), end=date(2025, 10, 1), use="test")
    Window(name="x", start=date(2025, 1, 1), end=date(2025, 9, 30), use="test")   # the last open day is fine


def test_test_old_needs_its_flag_on_every_catalogue_and_windows_path(raw: Path) -> None:
    for call in (lambda: DA.window("test_old"), lambda: DA.load("XAUUSD", "1d", "test_old"),
                 lambda: D.get_bars("XAUUSD", "1d", "test_old"), lambda: W.bounds("test_old")):
        with pytest.raises(TestOldSealed):
            call()


# ------------------------------------------------------------------ whole-file loaders cut before output
def test_catalogue_bars_from_files_holding_holdout_rows_stop_before_the_holdout(raw: Path) -> None:
    for tf in ("5m", "1h", "4h", "1d"):
        b = DA.load("XAUUSD", tf, "confirm")
        _no_holdout(b.t, b.ta)
    m = DA.load_minutes("confirm")
    _no_holdout(m.t, m.ta)


def test_the_last_confirm_bar_closes_at_the_holdout_start_and_none_opens_at_it(raw: Path) -> None:
    """Boundary: a 1h bar opening 2025-09-30 23:00 closes at 2025-10-01 00:00 (= HOLDOUT_FROM) and is kept (its
    close is known at the boundary, not after); the bar opening at 2025-10-01 00:00 is the holdout's first."""
    b = DA.load("XAUUSD", "1h", "confirm")
    assert int(b.ta.max()) == HOLDOUT_NS
    assert int(b.t.max()) == HOLDOUT_NS - 3_600_000_000_000


def test_shared_1m_readers_never_return_a_holdout_row(raw: Path) -> None:
    from goldbot.research.position.data import read_1m_files
    from goldbot.research.sprint3000.market import load_minutes
    p = read_1m_files(raw)
    assert len(p) and pd.Timestamp(p["ts_utc"].max()) < HOLDOUT
    s = load_minutes(raw, pd.Timestamp("2025-08-01", tz="UTC"), HOLDOUT)
    _no_holdout(s["t"].to_numpy(np.int64))
    w = W.read_window_bars(raw, "confirm")
    assert len(w) and w["ts_utc"].max() < HOLDOUT


# ------------------------------------------------------------------ bar caches fail closed
HOUR_NS = 3_600_000_000_000


def _npz(path: Path, t: np.ndarray, step: int, *, win: str | None = None, meta: bool = True) -> None:
    """A cache file as dispatcher.save_cache writes it (with the window's span), or a legacy file (meta=False)."""
    n = len(t)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = np.full(n, 2000.0)
    extra = {}
    if meta:
        w = DA.window(win or path.parent.name, allow_test_old=True)
        extra = D.cache_meta(w, D.cache_lo(w, "1m" if path.stem.endswith("_1m") else path.stem.split("_")[-1]))
    np.savez(path, t=t, ta=t + step, o=c, h=c + 1, l=c - 1, c=c, spr=np.full(n, 0.2), co=np.zeros(n, bool),
             seg=np.zeros(n, np.int32), vol=np.ones(n), **extra)  # type: ignore[arg-type]


@pytest.mark.parametrize("win, first_bad", [("confirm", "2025-10-01"), ("confirm", "2026-02-02"),
                                             ("explore", "2018-01-02"), ("explore", "2017-12-31 23:30")])
def test_a_cached_bar_past_the_window_end_fails_closed_instead_of_being_served(
        raw: Path, tmp_path: Path, win: str, first_bad: str) -> None:
    """A stale or corrupt $CATALOGUE_CACHE/<win>/<inst>_<tf>.npz (another window's build, an older loader, a
    hand-copied file) was served as is: a holdout bar, or a 2018 bar in the explore cache (an unsealed confirm read).
    The explore 23:30 bar closes 2018-01-01 00:30, after the window end."""
    good = np.arange(int(pd.Timestamp("2017-06-01", tz="UTC").value), int(pd.Timestamp(first_bad, tz="UTC").value),
                     HOUR_NS, dtype=np.int64)[-200:]
    t = np.append(good, int(pd.Timestamp(first_bad, tz="UTC").value))
    _npz(tmp_path / "cache" / win / "XAUUSD_1h.npz", t, HOUR_NS)
    with pytest.raises(HoldoutSealed):
        D.get_bars("XAUUSD", "1h", win)
    _npz(tmp_path / "cache" / win / "XAUUSD_1m.npz", t, HOUR_NS)     # a corrupt 1m cache: closes stamped an hour on
    with pytest.raises(HoldoutSealed):
        D.get_minutes(win)


@pytest.mark.parametrize("tf, first", [("1d", "2006-03-01"), ("4h", "2008-08-29"), ("1h", "2009-06-01")])
def test_a_cached_bar_before_the_windows_warm_up_start_fails_closed(raw: Path, tmp_path: Path, tf: str,
                                                                    first: str) -> None:
    """Explore reads warm-up from 2008-09-01 (daily/4h) and 120 days before 2010 (gold intraday): a 2006 bar (TEST-OLD
    proper), or one a day before the warm-up start, is not explore data and is never served from the explore cache."""
    step = {"1d": 24 * HOUR_NS, "4h": 4 * HOUR_NS, "1h": HOUR_NS}[tf]
    t = np.arange(int(pd.Timestamp(first, tz="UTC").value), int(pd.Timestamp("2010-03-01", tz="UTC").value), step,
                  dtype=np.int64)
    _npz(tmp_path / "cache" / "explore" / f"XAUUSD_{tf}.npz", t, step)
    with pytest.raises(HoldoutSealed, match="before"):
        D.get_bars("XAUUSD", tf, "explore")


def test_an_explore_cache_file_served_as_confirm_fails_closed(raw: Path, tmp_path: Path) -> None:
    """Code review LOW-1: a file built for explore and copied (or mis-pathed) into the confirm cache carries explore's
    span; the window it is loaded as must match it."""
    t = np.arange(int(pd.Timestamp("2017-01-02", tz="UTC").value), int(pd.Timestamp("2017-12-01", tz="UTC").value),
                  HOUR_NS, dtype=np.int64)
    _npz(tmp_path / "cache" / "confirm" / "XAUUSD_1h.npz", t, HOUR_NS, win="explore")
    with pytest.raises(HoldoutSealed, match="explore"):
        D.get_bars("XAUUSD", "1h", "confirm")


def test_a_legacy_cache_file_without_its_span_is_rebuilt_never_served(raw: Path, tmp_path: Path) -> None:
    """A cache file written before the span was recorded is not trusted: it is rebuilt from raw and rewritten."""
    f = tmp_path / "cache" / "confirm" / "XAUUSD_1h.npz"
    junk = np.arange(int(pd.Timestamp("2025-09-01", tz="UTC").value), HOLDOUT_NS, HOUR_NS, dtype=np.int64)
    _npz(f, junk, HOUR_NS, meta=False)
    b = D.get_bars("XAUUSD", "1h", "confirm")
    want = DA.load("XAUUSD", "1h", "confirm")
    assert np.array_equal(b.t, want.t) and not np.array_equal(b.c[: len(junk)], np.full(len(junk), 2000.0))
    z = np.load(f, allow_pickle=False)
    assert str(z["win"]) == "confirm" and int(z["end"]) == HOLDOUT_NS


def test_a_clean_cache_file_is_still_served(raw: Path, tmp_path: Path) -> None:
    t = np.arange(int(pd.Timestamp("2025-09-01", tz="UTC").value), HOLDOUT_NS, HOUR_NS, dtype=np.int64)
    _npz(tmp_path / "cache" / "confirm" / "XAUUSD_1h.npz", t, HOUR_NS)
    b = D.get_bars("XAUUSD", "1h", "confirm")
    assert len(b) == len(t) and int(b.ta.max()) == HOLDOUT_NS


def test_every_catalogue_cache_write_goes_through_save_cache() -> None:
    """run.prebuild and the dispatcher write caches only via dispatcher.save_cache (span recorded)."""
    for rel in ("goldbot/research/catalogue/run.py", "goldbot/research/catalogue/dispatcher.py"):
        src = (ROOT / rel).read_text()
        assert src.count("np.savez(") == (1 if rel.endswith("dispatcher.py") else 0), rel


# ------------------------------------------------------------------ EVAL-2 self-test bars: explore 2010-17 only
def _selftest() -> object:
    spec = importlib.util.spec_from_file_location("eval2_selftest", ROOT / "scripts" / "eval2_selftest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval2_selftest"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("bad", ["2009-12-31 22:00", "2018-01-01 00:00", "2025-10-01 00:00"])
def test_selftest_bar_cache_outside_2010_2017_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            bad: str) -> None:
    st = _selftest()
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path / "st"))
    hour = 3_600_000_000_000
    t = np.append(np.arange(int(pd.Timestamp("2012-01-02", tz="UTC").value),
                            int(pd.Timestamp("2012-01-10", tz="UTC").value), hour, dtype=np.int64),
                  int(pd.Timestamp(bad, tz="UTC").value))
    _npz(tmp_path / "st" / "bars" / "XAUUSD_1h_2010_2017.npz", np.sort(t), hour, meta=False)
    with pytest.raises(HoldoutSealed):
        st.explore_bars("XAUUSD", "1h")   # type: ignore[attr-defined]


def test_selftest_refuses_years_outside_explore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    st = _selftest()
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path / "st"))
    for yrs in ((2009,), (2018,), (2025,), (2026,)):
        with pytest.raises(HoldoutSealed):
            st.explore_bars("XAUUSD", "1h", yrs)   # type: ignore[attr-defined]


# ------------------------------------------------------------------ confirm is read once
def test_a_second_catalogue_confirm_read_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.research.catalogue import run as R
    reg = tmp_path / "state" / "trial_registry.jsonl"
    reg.parent.mkdir(parents=True)
    monkeypatch.setattr(R, "default_registry", lambda: reg)
    monkeypatch.setattr(R, "head", lambda: "abc")
    R._claim("confirm")
    rows = [json.loads(x) for x in (reg.parent / R.READS_LEDGER).read_text().splitlines()]
    assert rows[-1]["window"] == "confirm"
    with pytest.raises(SystemExit, match="second read"):
        R._claim("confirm")


# ------------------------------------------------------------------ static: loaders call a guard
READER_NAMES = {"read_parquet", "read_csv", "read_table", "read_feather", "read_bar_files", "read_ipc", "scan_parquet",
                "scan_csv", "scan_ipc", "read_database"}
GUARD_CALLS = {"assert_sealed", "assert_before_holdout", "assert_no_holdout_rows", "cut_at_holdout",
               "truncate_to_explore", "window", "bounds", "entry_mask", "assert_block_allowed", "checked_cache",
               "read_window_bars", "get_window", "get_windows", "_check_span", "stage_bars"}
NO_BARS = {"goldbot/research/ruin.py": "reads a trades file (column r), not bars"}


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return ""


def _is_reader(call: ast.Call) -> bool:
    name = _dotted(call.func)
    last = name.rsplit(".", 1)[-1]
    base = name.rsplit(".", 1)[0] if "." in name else ""
    return (last in READER_NAMES or (last == "load" and base in ("np", "numpy"))
            or (last == "dataset" and ("ds" == base or "pyarrow" in base))
            or (base.split(".")[0] == "duckdb") or (last in ("sql", "query", "execute") and "duckdb" in base)
            or (last.startswith(("read_", "scan_")) and base in ("pl", "polars")))


def _mentions_holdout(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and "HOLDOUT" in n.id:
            return True
        if isinstance(n, ast.Attribute) and "HOLDOUT" in n.attr:
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("2025-10-01"):
            return True
    return False


def guard_problem(src: str) -> str | None:
    """None when the module reads nothing or calls a holdout guard (or compares / filters against the holdout start);
    else what it reads. A docstring or comment naming the holdout is not a guard."""
    tree = ast.parse(src)
    readers = [_dotted(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call) and _is_reader(c)]
    if not readers:
        return None
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _dotted(n.func).rsplit(".", 1)[-1] in GUARD_CALLS:
            return None
        if isinstance(n, ast.Compare) and _mentions_holdout(n):
            return None
        if isinstance(n, ast.Call) and any(k.arg == "filters" and _mentions_holdout(k.value) for k in n.keywords):
            return None
    return f"reads via {sorted(set(readers))} with no holdout guard call"


def test_every_research_module_that_opens_bar_files_calls_a_holdout_guard() -> None:
    bad = {}
    for p in sorted((ROOT / "goldbot" / "research").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        why = guard_problem(p.read_text())
        if why and rel not in NO_BARS:
            bad[rel] = why
    assert not bad, f"research loaders with no holdout guard: {bad}"


@pytest.mark.parametrize("src", [
    "import pandas as pd\nNOTE = 'HOLDOUT_FROM is sealed'\ndef f(p):\n    return pd.read_parquet(p)\n",
    "import pandas as pd\ndef f(p):\n    return pd.read_csv(p)  # holdout cut later\n",
    "import pyarrow.dataset as ds\ndef f(p):\n    return ds.dataset(p).to_table()\n",
    "import duckdb\ndef f(p):\n    return duckdb.sql(f'select * from {p}')\n",
    "import polars as pl\ndef f(p):\n    return pl.scan_parquet(p).collect()\n",
    "import numpy as np\ndef f(p):\n    return np.load(p)\n",
], ids=["docstring-only", "comment-only", "pyarrow.dataset", "duckdb", "polars", "np.load"])
def test_the_static_guard_flags_readers_whose_holdout_mention_is_not_a_guard(src: str) -> None:
    assert guard_problem(src) is not None


@pytest.mark.parametrize("src", [
    "import pandas as pd\nfrom x import HOLDOUT_FROM\ndef f(p):\n    d = pd.read_parquet(p)\n"
    "    return d[d.ts < HOLDOUT_FROM]\n",
    "import polars as pl\nfrom x import assert_sealed\ndef f(p):\n    d = pl.read_parquet(p)\n"
    "    assert_sealed(d['ta'])\n    return d\n",
    "import pandas as pd\nfrom x import HOLDOUT_FROM\ndef f(p):\n"
    "    return pd.read_parquet(p, filters=[('ts', '<', HOLDOUT_FROM)])\n",
    "def f(x):\n    return x + 1\n",
], ids=["compare", "guard-call", "pushed-down-filter", "no-reader"])
def test_the_static_guard_accepts_a_real_guard(src: str) -> None:
    assert guard_problem(src) is None
