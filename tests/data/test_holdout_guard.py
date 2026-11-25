"""The shared holdout guard (goldbot/data/holdout_guard.py; quant R10, plan 2026-11-25 S1): "EVERY new loader refuses
the holdout explicitly (forward_replay, closed-bar seed, dense/augment generators, free 1m pulls, broker backfills) -
live paths bypass windows.py". A frame touching 2025-10-01..2026-09-30 raises in a research or judging path; a pull
never asks for it; live paths are exempt only when listed. All data here is synthetic."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.data import holdout_guard as G
from goldbot.research import windows as W

ROOT = Path(__file__).resolve().parents[2]
HS = pd.Timestamp("2025-10-01", tz="UTC")
HE = pd.Timestamp("2026-10-01", tz="UTC")
MIN = pd.Timedelta(minutes=1)


def _frame(*ts: pd.Timestamp) -> pd.DataFrame:
    return pd.DataFrame({"ts_utc": pd.DatetimeIndex(ts), "x": np.arange(len(ts), dtype=float)})


def test_the_sealed_span_is_2025_10_01_to_2026_09_30_and_windows_shares_its_error_and_start():
    assert (G.HOLDOUT_START, G.HOLDOUT_END) == (HS, HE)
    assert W.HoldoutSealed is G.HoldoutSealed and W.HOLDOUT_FROM == G.HOLDOUT_START


@pytest.mark.parametrize("ts", [HS, HS + MIN, pd.Timestamp("2026-03-15 12:00", tz="UTC"), HE - MIN])
def test_a_frame_touching_any_minute_of_the_holdout_raises(ts):
    with pytest.raises(G.HoldoutSealed, match="unit test"):
        G.refuse(_frame(HS - pd.Timedelta(days=3), ts), what="unit test")


@pytest.mark.parametrize("ts", [HS - MIN, HE, HE + pd.Timedelta(days=50)])
def test_minutes_just_outside_the_holdout_pass(ts):
    G.refuse(_frame(ts), what="unit test")
    assert G.cut(_frame(ts), what="unit test").shape[0] == 1


def test_naive_timestamps_are_read_as_utc_and_series_or_index_are_accepted():
    with pytest.raises(G.HoldoutSealed):
        G.refuse(pd.Series([pd.Timestamp("2026-01-02 03:04")]), what="naive")
    with pytest.raises(G.HoldoutSealed):
        G.refuse(pd.DatetimeIndex([HS]), what="index")
    G.refuse(pd.Series([], dtype="datetime64[ns, UTC]"), what="empty")


def test_cut_and_split_keep_the_sealed_rows_out_of_the_research_part():
    f = _frame(HS - MIN, HS, HE - MIN, HE)
    assert list(G.cut(f, what="t")["ts_utc"]) == [HS - MIN, HE]
    research, sealed = G.split(f)
    assert list(research["ts_utc"]) == [HS - MIN, HE] and list(sealed["ts_utc"]) == [HS, HE - MIN]


@pytest.mark.parametrize(("start", "end", "want"), [
    (HE + pd.Timedelta(days=5), HE + pd.Timedelta(days=9), [(HE + pd.Timedelta(days=5), HE + pd.Timedelta(days=9))]),
    (pd.Timestamp("2026-08-17", tz="UTC"), pd.Timestamp("2026-11-25", tz="UTC"),
     [(HE, pd.Timestamp("2026-11-25", tz="UTC"))]),
    (pd.Timestamp("2025-09-01", tz="UTC"), pd.Timestamp("2025-12-01", tz="UTC"),
     [(pd.Timestamp("2025-09-01", tz="UTC"), HS)]),
    (pd.Timestamp("2025-09-01", tz="UTC"), pd.Timestamp("2026-11-01", tz="UTC"),
     [(pd.Timestamp("2025-09-01", tz="UTC"), HS), (HE, pd.Timestamp("2026-11-01", tz="UTC"))]),
    (HS, HE, []),
], ids=["after", "to-present", "into", "across", "inside"])
def test_a_pull_span_is_split_around_the_holdout_so_no_sealed_minute_is_asked_for(start, end, want):
    assert G.pull_spans(start, end) == want


def test_a_label_decided_before_the_holdout_but_still_open_in_it_is_refused_or_cut():
    """Quant N1: 2025-09-30 23:30 held 1 h reads holdout bars; held 20 min it does not."""
    d = pd.Timestamp("2025-09-30 23:30", tz="UTC")
    labels = pd.DataFrame({"decision_utc": [d, d, HE - MIN, HE], "max_hold_s": [3600, 1200, 60, 3600]})
    assert G.label_in_holdout(labels).tolist() == [True, False, True, False]
    with pytest.raises(G.HoldoutSealed, match="still open"):
        G.refuse_labels(labels.iloc[:1], what="t")
    G.refuse_labels(labels.iloc[[1, 3]], what="t")
    assert G.cut_labels(labels, what="t")["max_hold_s"].tolist() == [1200, 3600]


def _straddling_replay():
    from goldbot.research import exploration_data as ex
    rows = pd.DataFrame({"candidate_id": ["c"], "arm_id": ["a"], "tf": ["15m"], "side": [1],
                         "decision_utc": [pd.Timestamp("2025-09-30 23:30", tz="UTC")], "session_day": ["2025-09-30"],
                         "stop_dist": [5.0], "target_dist": [5.0], "max_hold_s": [3600]})
    bars = pd.DataFrame({"ts_utc": pd.date_range(HS - pd.Timedelta(hours=1), periods=10, freq="1min")})
    ex.replay_frame_v2(bars, rows, lambda _s: None, cutoff=HE)   # type: ignore[arg-type,return-value]


# ------------------------------------------------------------------ every loader outside windows.py refuses it
def _exploration_prior():
    from goldbot.research import exploration_data as ex
    ex.prior_from_history("a", pd.DataFrame({"decision_utc": [pd.Timestamp("2012-01-03", tz="UTC"), HS],
                                             "r_net": [0.1, 0.2]}), prior_version="v")


def _forward_replay():
    from goldbot.research import exploration_data as ex
    rows = pd.DataFrame({"candidate_id": ["c"], "arm_id": ["a"], "tf": ["15m"], "side": [1],
                         "decision_utc": [HE - pd.Timedelta(hours=2)], "session_day": ["2026-09-30"],
                         "stop_dist": [5.0], "target_dist": [5.0], "max_hold_s": [3600]})
    bars = pd.DataFrame({"ts_utc": pd.date_range(HE - pd.Timedelta(hours=3), periods=10, freq="1min")})
    ex.replay_frame_v2(bars, rows, lambda _s: None, cutoff=HE + pd.Timedelta(days=1))   # type: ignore[arg-type,return-value]


def _arms_assert():
    from goldbot.research import arms as A
    A.assert_no_holdout_rows(_frame(HS), "ts_utc")


def _model_zoo():
    from goldbot.research import model_zoo as Z
    Z._refuse_holdout(pd.DataFrame({"decision_utc": [HS]}))


LOADERS = {"exploration_data.prior_from_history (H-60 prior seed)": _exploration_prior,
           "exploration_data.replay_frame_v2 (forward replay)": _forward_replay,
           "exploration_data.replay_frame_v2 (label straddling the holdout start)": _straddling_replay,
           "arms.assert_no_holdout_rows (arm history replay)": _arms_assert,
           "model_zoo._refuse_holdout (H-101 candidates)": _model_zoo}


@pytest.mark.parametrize("load", list(LOADERS.values()), ids=list(LOADERS))
def test_every_guarded_loader_refuses_a_frame_touching_the_holdout(load):
    with pytest.raises((G.HoldoutSealed, ValueError, AssertionError), match=r"(?i)holdout"):
        load()


def test_the_m1_backfill_never_asks_the_broker_for_a_holdout_minute(monkeypatch, capsys):
    from goldbot.ops import m1_backfill as MB
    asked: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    class Acc:
        account_id, symbol = "acc", "XAUUSD"

    class R:
        first_stored, minutes_added, chunks_pulled, chunks_needed, chunks = None, 0, 0, 0, 0
        open_missing, max_open_hole, stopped, errors = 0, 0, None, []

    budgets: list[float | None] = []

    def backfill(store, broker, acc_id, symbol, *, start, end, budget_s, pause_s):
        asked.append((start, end))
        budgets.append(budget_s)
        return R()
    monkeypatch.setattr(MB, "_accounts", lambda ids: [Acc()])
    monkeypatch.setattr(MB, "_store", lambda root: object())
    monkeypatch.setattr(MB, "_broker", lambda acc: object())
    monkeypatch.setattr(MB, "_now", lambda: pd.Timestamp("2026-11-25 10:00", tz="UTC"))
    monkeypatch.setattr("goldbot.data.crossfeed.backfill_broker_m1", backfill)
    assert MB.main(["--all", "--days", "500", "--pause-s", "0"]) == 0
    assert asked and all(not (s < HE and e > HS) for s, e in asked)
    assert asked[-1] == (HE, pd.Timestamp("2026-11-25 10:00", tz="UTC"))
    out = capsys.readouterr().out
    assert "holdout" in out and out.count("minutes added") == 2                 # one line per span
    budgets.clear()
    assert MB.main(["--all", "--days", "500", "--pause-s", "0", "--budget-s", "100"]) == 0
    b0, b1 = budgets
    assert b0 is not None and b1 is not None and b1 <= b0 <= 100                # one budget shared by both spans
    asked.clear()
    monkeypatch.setattr(MB, "_now", lambda: pd.Timestamp("2026-09-20 10:00", tz="UTC"))
    assert MB.main(["--all", "--days", "30"]) == 2 and not asked          # all inside the holdout: refused


def _year(folder: Path, year: int) -> None:
    from goldbot.data.resample import BAR_COLUMNS
    ts = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="6h", tz="UTC")
    df = pd.DataFrame({"ts_utc": ts, "visible_at": ts + MIN})
    for c in BAR_COLUMNS[2:]:
        df[c] = 1000.0
    df["dq_flag"] = ""
    df.to_parquet(folder / f"xauusd_1m_dukascopy_{year}.parquet", index=False)


def test_the_default_raw_loader_cuts_the_holdout_and_only_a_gated_read_asks_for_it(tmp_path):
    """W1-E (PR #453): raw/ holds the 2025 and 2026 files, holdout and forward minutes included, in the directory
    research reads by default. `read_bar_files` drops the holdout unless the caller is a gated holdout read
    (`include_holdout=True`: autodisc's holdout stage, research_pass --score-holdout); forward minutes are kept."""
    from goldbot.data.release import read_bar_files
    for y in (2025, 2026):
        _year(tmp_path, y)
    b = read_bar_files(tmp_path, 2025, 2026)
    assert not G.in_holdout(b).any() and b["ts_utc"].min() < HS and b["ts_utc"].max() >= HE
    assert G.in_holdout(read_bar_files(tmp_path, 2025, 2026, include_holdout=True)).any()


# ------------------------------------------------------------------ live paths are exempt only when listed
def test_every_live_exemption_names_a_real_module_and_says_why():
    assert G.LIVE_EXEMPT
    for mod, why in G.LIVE_EXEMPT.items():
        assert (ROOT / (mod.replace(".", "/") + ".py")).is_file(), mod
        assert len(why) > 20, mod


def test_no_new_script_reads_bars_without_a_holdout_guard_or_a_listed_reason():
    """tests/test_holdout_seal.py holds goldbot/research to a guard call; outside it, a reader of bar files is either
    guarded, live-exempt (G.LIVE_EXEMPT) or a pre-guard script frozen in G.UNGUARDED (none may join; one leaves by
    calling the guard)."""
    from tests.test_holdout_seal import guard_problem
    found = set()
    for d in ("goldbot/data", "goldbot/ops", "goldbot/engine", "scripts"):
        for p in sorted((ROOT / d).rglob("*.py")):
            if guard_problem(p.read_text()):
                found.add(p.relative_to(ROOT).as_posix())
    listed = {m.replace(".", "/") + ".py" for m in G.LIVE_EXEMPT} | set(G.UNGUARDED)
    assert found - listed == set(), "new bar readers with no holdout guard"
    assert set(G.UNGUARDED) - found == set(), "guarded now: drop them from holdout_guard.UNGUARDED"


def test_the_guard_module_imports_nothing_from_research_so_ingest_can_use_it():
    tree = ast.parse((ROOT / "goldbot/data/holdout_guard.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(str(m).startswith("goldbot.research") for m in mods)
