"""The key-level primitives in the sprint-3000 vocabulary (goldbot/research/sprint3000/vocab_levels.py; brief ADDENDUM A,
ADR 0068) and the separate level batch (prereg amendment C1 ruling e: "The 379 specs that use the ADDENDUM A level types
form a separate later batch ... That batch has its own ensembles and its own BH family").

Design rule under test: "a zone exists only from the close of the bar that confirms it". Entries compare the bar with
the zones known at the PREVIOUS decision close, filters read the book at the decision close; nothing before a cut moves
when the minutes after it are removed or rewritten, and nothing acts on an immature book."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.features import key_levels as kl
from goldbot.research import level_vocab as lv
from goldbot.research.registry import TrialRegistry
from goldbot.research.sprint3000 import engine as E
from goldbot.research.sprint3000 import trials as T
from goldbot.research.sprint3000 import vocab as V
from goldbot.research.sprint3000 import vocab_levels as VL
from goldbot.research.sprint3000.market import WINDOWS, Market
from tests.research.sprint3000_bars import synthetic_minutes, window

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "tests" / "fixtures" / "sprint3000" / "sample_catalogue.yaml"
REAL = ROOT / "config" / "research" / "sprint3000_catalogue.yaml"

ENTRIES: dict[str, dict[str, Any]] = {
    "level_break": {"min_strength_pct": 50, "min_tf": "1h"},
    "level_reject": {"min_strength_pct": 50, "min_tf": "1h"},
}
FILTERS: dict[str, dict[str, Any]] = {
    "near_level": {"max_dist_atr": 0.5, "min_strength_pct": 75},
    "room_to_level": {"min_r": 2.0},
}


@pytest.fixture()
def young_book(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 10-day maturity, so a 75-day synthetic tape carries level signals (the frozen 540 days needs ~2 years)."""
    monkeypatch.setattr(kl, "MATURE_DAYS", 10)


def _market(m: pd.DataFrame, end: str = "2012-03-20") -> Market:
    return Market.from_minutes(m.reset_index(drop=True), window("2012-01-02", end))


def _later_rewritten(m: pd.DataFrame, cut: pd.Timestamp) -> pd.DataFrame:
    """The same tape with every minute at or after the cut moved: highs up, lows down, closes up (a different future)."""
    out = m.copy()
    fut = out.t >= cut.value
    out.loc[fut, "h"] += 40.0
    out.loc[fut, "l"] -= 40.0
    out.loc[fut, ["o", "c"]] += 7.0
    return out


def _signals(rule: str, kind: str, mk: Market, tf: str) -> tuple[np.ndarray, np.ndarray]:
    b = mk.bars[tf]
    if kind == "entry":
        return V.entry_signals(V.Rule(type=rule, params=ENTRIES[rule]), b, mk)
    return V.filter_ok(V.Rule(type=rule, params=FILTERS[rule]), b, mk, 2.0 * V.atr(b, 14, mk))


CASES = [(r, "entry") for r in ENTRIES] + [(r, "filter") for r in FILTERS]


# ---------------------------------------------------------------- registration and schema
def test_the_four_level_types_are_registered_so_no_real_catalogue_spec_is_pending() -> None:
    assert set(ENTRIES) <= set(V.ENTRY) and set(FILTERS) <= set(V.FILTER)
    cat = V.load_catalogue(REAL)
    assert not [s.id for s in cat.specs if s.pending]
    level = [s for s in cat.specs if s.batch == "levels"]
    assert len(level) == 379                                  # prereg section 5: 379 level-based specs
    assert all(s.level_types for s in level)
    assert not [s for s in cat.specs if s.batch == "main" and s.level_types]


@pytest.mark.parametrize(("rule", "params"), [
    ("level_break", {"min_strength_pct": 50, "min_tf": "5m"}),          # not a key-level timeframe
    ("level_break", {"min_strength_pct": 120, "min_tf": "1h"}),         # a percentile is 0..100
    ("level_reject", {"min_strength_pct": 50}),                         # missing parameter
    ("near_level", {"max_dist_atr": 0.5, "min_strength_pct": 75, "tf": "1h"}),   # extra parameter
    ("room_to_level", {"min_r": 0}),
])
def test_level_params_outside_the_vocabulary_are_refused(rule: str, params: dict[str, Any]) -> None:
    kind = "entry" if rule in ENTRIES else "filter"
    base: dict[str, Any] = {"id": "S3K-9001", "rank": 1, "prior_score": 0.0, "family": "key_level", "tf": "1h",
                            "entry": {"type": "donchian_break", "params": {"n": 10}}, "filters": [], "side": "signal",
                            "stop": {"atr_n": 14, "atr_mult": 2.0}, "exit": {"type": "time", "params": {"bars": 8}},
                            "max_hold_bars": 8}
    if kind == "entry":
        base["entry"] = {"type": rule, "params": params}
    else:
        base["filters"] = [{"type": rule, "params": params}]
    with pytest.raises(ValueError):
        V.Spec.model_validate(base)


# ---------------------------------------------------------------- point in time
@pytest.mark.usefixtures("young_book")
def test_level_entries_read_the_zones_of_the_previous_decision_close_and_filters_the_book_at_the_close() -> None:
    mk = _market(synthetic_minutes(days=75, seed=11))
    b = mk.bars["1h"]
    tape = pd.DataFrame({"ts_utc": pd.to_datetime(mk.tape.t, utc=True), "open": mk.tape.o, "high": mk.tape.h,
                         "low": mk.tape.l, "close": mk.tape.c})
    f = kl.LevelFilter(50.0, "1h")
    ctx = lv.LevelContext.build(tape, pd.DataFrame({"visible_at": pd.to_datetime(b.dec, utc=True)}), [f])
    bars = pd.DataFrame({"close": b.c, "high": b.h, "low": b.l})
    L, S = _signals("level_break", "entry", mk, "1h")
    want = lv.level_break(bars, ctx.prev(f))
    assert np.array_equal(L, want > 0) and np.array_equal(S, want < 0) and L.any() and S.any()
    at_close = lv.level_break(bars, ctx.at(f))                # the bar's own close's zones: a different (wrong) rule
    assert not np.array_equal(want, at_close)


@pytest.mark.usefixtures("young_book")
@pytest.mark.parametrize("tf", ["15m", "1h", "4h"])
@pytest.mark.parametrize(("rule", "kind"), CASES)
@pytest.mark.parametrize("cut", ["2012-03-01 13:37", "2012-03-01 16:00"])      # mid-bar, and exactly at a bar close
def test_no_level_signal_before_a_cut_changes_when_later_minutes_are_removed_or_rewritten(
        rule: str, kind: str, tf: str, cut: str) -> None:
    m = synthetic_minutes(days=75, seed=11)
    t_cut = pd.Timestamp(cut, tz="UTC")
    full = _signals(rule, kind, _market(m), tf)
    other = _signals(rule, kind, _market(_later_rewritten(m, t_cut)), tf)
    part_mk = _market(m[m.t < t_cut.value], "2012-03-02")
    part = _signals(rule, kind, part_mk, tf)
    k = int(np.searchsorted(part_mk.bars[tf].dec, t_cut.value, side="right"))  # bars closed by the cut
    assert k > 50
    for side in (0, 1):
        assert np.array_equal(full[side][:k], part[side][:k]), (rule, tf, "removed")
        assert np.array_equal(full[side][:k], other[side][:k]), (rule, tf, "rewritten")
    assert full[0][:k].any() or full[1][:k].any()             # the check is not vacuous


@pytest.mark.parametrize(("rule", "kind"), CASES)
def test_nothing_fires_on_a_book_younger_than_the_frozen_540_days(rule: str, kind: str) -> None:
    mk = _market(synthetic_minutes(days=75, seed=11))
    L, S = _signals(rule, kind, mk, "1h")
    assert not L.any() and not S.any()


def test_the_level_window_warms_up_860_days_but_never_below_its_windows_data_floor() -> None:
    for name, w in WINDOWS.items():
        lw = VL.level_window(w)
        assert (lw.name, lw.dec_from, lw.end) == (w.name, w.dec_from, w.end), name   # trades: the same window
        assert lw.data_from <= w.data_from and lw.data_from >= VL.LEVEL_DATA_FLOOR[name], name
        want = max(VL.LEVEL_DATA_FLOOR[name], w.dec_from - pd.Timedelta(days=VL.LEVEL_WARMUP_DAYS))
        assert lw.data_from == min(w.data_from, want), name
    # quant review F1: explore never reads a test_old decision year (2006-02..2008); B3 lets it warm up on 2009 only
    assert VL.level_window(WINDOWS["explore"]).data_from >= pd.Timestamp("2009-01-01", tz="UTC")
    assert VL.level_window(WINDOWS["explore"]).data_from == WINDOWS["explore"].data_from
    assert VL.level_window(WINDOWS["confirm"]).data_from == pd.Timestamp("2015-08-25", tz="UTC")  # 2018-01-01 - 860 d
    assert VL.LEVEL_WARMUP_DAYS == kl.MATURE_DAYS + kl.PCT_WINDOW_DAYS + 2 * kl.SWING_LAG * 7


def test_a_level_window_with_no_floor_is_refused() -> None:
    with pytest.raises(KeyError):
        VL.level_window(window("2012-01-02", "2012-03-20"))


def test_after_the_warm_up_a_book_built_from_a_later_start_equals_the_whole_history_book() -> None:
    """Why the level batch loads LEVEL_WARMUP_DAYS before a window's first decision: from then on the zones and their
    strength percentiles no longer depend on where the tape starts (the frozen 540-day maturity covers the zones; the
    percentile pool samples 250 more days of books; 1w swings confirm SWING_LAG weeks late at each end)."""
    m = synthetic_minutes(days=VL.LEVEL_WARMUP_DAYS + 120, seed=3)
    t0 = int(m.t.iloc[0]) + 60 * 86_400 * 10**9
    end = str((pd.Timestamp(int(m.t.iloc[-1]), tz="UTC") + pd.Timedelta(days=1)).date())
    whole = Market.from_minutes(m, window("2012-01-02", end))
    late = Market.from_minutes(m[m.t >= t0].reset_index(drop=True),
                               window(str(pd.Timestamp(t0, tz="UTC").date()), end))
    f = kl.LevelFilter(75.0, None)
    a, b = VL.context(whole, "4h", [f]).at(f), VL.context(late, "4h", [f]).at(f)
    from_t = t0 + VL.LEVEL_WARMUP_DAYS * 86_400 * 10**9
    ka = int(np.searchsorted(whole.bars["4h"].dec, from_t)), int(np.searchsorted(late.bars["4h"].dec, from_t))
    ta, tb = a.iloc[ka[0]:].reset_index(drop=True), b.iloc[ka[1]:].reset_index(drop=True)
    assert len(ta) > 100 and ta["book_mature"].all() and ta["pct_ready"].all()
    pd.testing.assert_frame_equal(ta, tb, check_exact=True)


# ---------------------------------------------------------------- the separate level batch (amendment C1 ruling e)
def _level_catalogue(tmp_path: Path) -> V.Catalogue:
    specs = list(yaml.safe_load(SAMPLE.read_text())["specs"])
    specs[0]["entry"] = {"type": "level_break", "params": {"min_strength_pct": 50, "min_tf": "1h"}}
    specs[0]["tf"], specs[0]["filters"] = "1h", []
    specs[2]["filters"] = [{"type": "room_to_level", "params": {"min_r": 2.0}}]
    specs[4]["entry"] = {"type": "level_reject", "params": {"min_strength_pct": 50, "min_tf": "1h"}}
    specs[5]["filters"] = [{"type": "near_level", "params": {"max_dist_atr": 0.5, "min_strength_pct": 75}}]
    p = tmp_path / "cat.yaml"
    p.write_text(yaml.safe_dump({"version": "t", "specs": specs}))
    return V.load_catalogue(p)


LEVEL_IDS = ["S3K-0001", "S3K-0003", "S3K-0005", "S3K-0006"]


def test_the_main_batch_still_reports_level_specs_as_not_evaluable_and_never_runs_them(tmp_path: Path) -> None:
    cat = _level_catalogue(tmp_path)
    rep = E.run_screen(cat, _market(synthetic_minutes(days=75, seed=11)), workers=1, cache_dir=tmp_path)
    assert rep.batch == "main" and rep.n_specs == 20
    out = {r.id: r for r in rep.results}
    assert [i for i, r in out.items() if r.status == "not_evaluable"] == LEVEL_IDS
    assert out["S3K-0003"].pending_types == ("room_to_level",) and out["S3K-0001"].verdict == "NOT-EVALUABLE"
    assert rep.n_evaluated == 16


@pytest.mark.usefixtures("young_book")
def test_the_levels_batch_runs_only_level_specs_identically_on_one_or_two_workers(tmp_path: Path) -> None:
    cat = _level_catalogue(tmp_path)
    mk = _market(synthetic_minutes(days=75, seed=11))
    one = E.run_screen(cat, mk, workers=1, cache_dir=tmp_path, batch="levels")
    two = E.run_screen(cat, mk.model_copy(update={"memo": {}, "prepared": {}}), workers=2, cache_dir=tmp_path,
                       batch="levels")
    assert [r.id for r in one.results] == LEVEL_IDS and one.batch == "levels"
    assert all(r.status == "evaluated" for r in one.results)
    assert sum(r.n_signals for r in one.results) > 0
    assert one.model_dump(exclude={"elapsed_s", "workers"}) == two.model_dump(exclude={"elapsed_s", "workers"})


def test_level_batch_ensembles_have_their_own_ids_and_are_preregistered_in_k(tmp_path: Path) -> None:
    rows = [E.SpecResult(id=f"S3K-{i:04d}", rank=i, family="key_level", tf="1h", window="explore",
                         status="evaluated", gross_t=3.0) for i in range(1, 4)]
    ens = E.build_ensembles(rows, E.load_rules(), batch="levels")
    assert [e.id for e in ens] == ["ENS-LV-key_level"]
    assert [e.id for e in E.build_ensembles(rows, E.load_rules())] == ["ENS-key_level"]
    cat = _level_catalogue(tmp_path)
    planned = {cfg["sprint3000"] for _, cfg in T.planned_rows(cat)}
    level_fams = {s.family for s in cat.specs if s.batch == "levels"}
    assert {f"ENS-LV-{f}" for f in level_fams} | {f"ENS-LV-NULL-{f}" for f in level_fams} <= planned
    sample = V.load_catalogue(SAMPLE)                         # no level spec: the main rows are unchanged
    assert not any(cfg["sprint3000"].startswith("ENS-LV-") for _, cfg in T.planned_rows(sample))
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    assert T.preregister_catalogue(reg, cat) == len(T.planned_rows(cat))


def test_a_report_of_one_batch_is_refused_as_the_passers_of_the_other(tmp_path: Path) -> None:
    cat = _level_catalogue(tmp_path)
    rep = {"window": "explore", "catalogue_sha256": cat.sha256, "status": "OK", "audit_required": False,
           "rules_sha256": E.rules_sha256(), "engine_sha256": E.engine_provenance()[1], "batch": "levels",
           "results": [{"id": i, "passed": True} for i in LEVEL_IDS], "ensembles": []}
    assert E.passer_ids(cat, rep, "confirm", batch="levels") == LEVEL_IDS
    with pytest.raises(ValueError, match="batch"):
        E.passer_ids(cat, rep, "confirm")
    old = {k: v for k, v in rep.items() if k != "batch"}       # a report written before batches: the main batch
    with pytest.raises(ValueError, match="batch"):
        E.passer_ids(cat, old, "confirm", batch="levels")
    assert E.passer_ids(cat, None, "explore", batch="levels") == LEVEL_IDS
    with pytest.raises(ValueError, match="levels batch"):
        E.passer_ids(cat, None, "explore", ids=["S3K-0002"], batch="levels")


def test_the_level_batch_confirm_is_its_own_one_time_read(tmp_path: Path) -> None:
    cat = _level_catalogue(tmp_path)
    ledger = tmp_path / "reads.jsonl"
    ledger.write_text(json.dumps({"window": "confirm", "catalogue_sha256": cat.sha256, "plan_hash": "a" * 64}) + "\n")
    p = E.plan(cat, None, "explore", batch="levels")
    h = E.plan_hash(p, "confirm", cat, batch="levels")
    assert h != E.plan_hash(p, "confirm", cat)
    E.claim_read(ledger, "confirm", cat, h, batch="levels")   # the main batch's earlier read does not block it
    with pytest.raises(ValueError, match="already read"):
        E.claim_read(ledger, "confirm", cat, "b" * 64, batch="levels")
    with pytest.raises(ValueError, match="already read"):
        E.claim_read(ledger, "confirm", cat, "c" * 64)        # the main batch keeps its own marker


def test_validate_on_the_levels_batch_lists_379_specs_and_their_zone_views_without_reading_bars(
        tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("s3k_cli", ROOT / "scripts" / "sprint3000_screen.py")
    assert spec_ and spec_.loader
    cli = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(cli)

    def no_bars(*a: Any, **k: Any) -> None:
        raise AssertionError("validate read bars")
    monkeypatch.setattr(cli.Market, "load", no_bars)
    assert cli.main(["--validate", "--batch", "levels", "--catalogue", str(REAL)]) == 0
    out = capsys.readouterr().out
    assert "batch levels: 379 specs" in out and "pending 0" in out
    assert "zone views" in out and "warm-up 860 days" in out
