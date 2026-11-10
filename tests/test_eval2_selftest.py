"""scripts/eval2_selftest.py (EVAL-2 Lane 4, prereg 2026-11-09 sections 4.10 and 9): the frozen API, the fail-closed
exit code and `assert_harness_sane`, the harness hash, the statistics helpers, the A1 cell floor and its tested
sign-flipped skeleton route, the per-bar NULL table the S1 null population is built on (checked against the real
`simulate` + `simulate_twins` path), the three S1 arms, the S3 plant and its BLOCKED reading, the S4 OCO
de-duplication, and the S1/S2/S3 plumbing against Lane 3's API (fake Lane 3 modules; the real ones replace them).

The real-bar runs (S0 on explore 2010-17, S6 on real bars) and the S3 fast run carry @pytest.mark.selftest; the real-bar
ones skip where raw/ market data is absent (CI: market data stays local)."""
from __future__ import annotations

import importlib.util
import inspect
import itertools
import json
import math
import sys
import types
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import sim as SIM
from goldbot.research.catalogue.core import EB
from goldbot.research.catalogue.costs import default_model

ROOT = Path(__file__).resolve().parents[1]


def _st() -> Any:
    if "eval2_selftest" in sys.modules:
        return sys.modules["eval2_selftest"]
    spec = importlib.util.spec_from_file_location("eval2_selftest", ROOT / "scripts" / "eval2_selftest.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval2_selftest"] = mod
    spec.loader.exec_module(mod)
    return mod


ST = _st()


# ------------------------------------------------------------------ frozen API (prereg 4.10)
FROZEN = {
    "s0_harness": ["harness_hash", "seed_key", "fast"],
    "s1_null_population": ["reps", "workers", "seed_key"],
    "s2_label_shift": ["plants_r", "reps", "seed_key", "fast"],
    "s3_full_path": ["reps", "seed_key", "fast"],
    "s4_leakage": ["seed_key"],
    "s5_execution": ["broker", "tf_band", "replay_version"],
    "s6_truncation": ["samples", "seed_key", "fast"],
}


@pytest.mark.parametrize("name", sorted(FROZEN))
def test_selftest_functions_keep_the_frozen_keyword_only_signature(name: str) -> None:
    sig = inspect.signature(getattr(ST, name))
    params = list(sig.parameters.values())
    assert [p.name for p in params][: len(FROZEN[name])] == FROZEN[name]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params)
    extra = params[len(FROZEN[name]):]
    assert all(p.default is not inspect.Parameter.empty for p in extra), "added parameters must be optional"


def test_selftest_result_is_frozen_with_the_registered_fields_first() -> None:
    names = [f.name for f in fields(ST.SelfTestResult)]
    assert names[:5] == ["test", "passed", "metrics", "threshold", "harness_hash"]
    r = ST.SelfTestResult("S0", True, {}, "x", "h")
    with pytest.raises(AttributeError):
        setattr(r, "passed", False)


def test_main_fails_closed_when_a_test_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "goldbot.research.eval2.forward", None)    # Lane 5A absent: import fails
    assert ST.main(["--only", "S5"]) == 1
    assert list(tmp_path.glob("report_*.json"))


def test_main_passes_only_when_every_selected_test_passed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    ok = ST.SelfTestResult("S4", True, {}, "t", "h")
    bad = ST.SelfTestResult("S6", False, {}, "t", "h")
    monkeypatch.setattr(ST, "run", lambda only, **k: [ok])
    assert ST.main(["--only", "S4"]) == 0
    monkeypatch.setattr(ST, "run", lambda only, **k: [ok, bad])
    assert ST.main(["--only", "S4", "S6"]) == 1
    monkeypatch.setattr(ST, "run", lambda only, **k: [])
    assert ST.main(["--only", "S4"]) == 1                         # nothing observed is not a pass


def test_a_harness_edited_during_the_run_fails_and_is_marked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    hashes = iter(["aaaa", "bbbb"])
    monkeypatch.setattr(ST, "harness_hash", lambda: next(hashes))
    monkeypatch.setattr(ST, "run", lambda only, **k: [ST.SelfTestResult("S4", True, {}, "t", "aaaa")])
    assert ST.main(["--only", "S4"]) == 1
    doc = json.loads(next(tmp_path.glob("report_*.json")).read_text())
    assert doc["harness_changed"] is True


@pytest.mark.parametrize("w", [0, 9])
def test_workers_are_capped_at_eight(w: int) -> None:
    with pytest.raises(SystemExit):
        ST.main(["--workers", str(w), "--only", "S5"])


# ------------------------------------------------------------------ harness hash (code review HIGH-1)
REQUIRED_HARNESS = {"goldbot/research/catalogue/stats.py", "goldbot/research/catalogue/lint.py",
                    "goldbot/research/catalogue/run.py", "goldbot/research/catalogue/specs.yaml",
                    "goldbot/research/catalogue/detectors_catalogue.py",
                    "goldbot/research/catalogue/detectors_patterns.py", "goldbot/research/catalogue/sim.py",
                    "goldbot/research/catalogue/costs.py", "config/catalogue_costs.yaml", "scripts/eval2_selftest.py"}


def test_the_harness_hash_covers_every_file_that_can_change_a_result() -> None:
    assert REQUIRED_HARNESS <= set(ST.HARNESS_FILES)
    assert "goldbot/research/eval2/*.py" in ST.HARNESS_GLOBS
    for f in ST.HARNESS_FILES:
        assert (ROOT / f).exists(), f


def test_editing_any_harness_file_or_adding_an_eval2_module_changes_the_hash(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ST, "ROOT", tmp_path)
    for f in ST.HARNESS_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("base\n")
    (tmp_path / "goldbot/research/eval2").mkdir(parents=True, exist_ok=True)
    (tmp_path / "goldbot/research/eval2/hier.py").write_text("base\n")
    seen = {ST.harness_hash()}
    for f in [*ST.HARNESS_FILES, "goldbot/research/eval2/hier.py"]:
        (tmp_path / f).write_text("edited\n")
        h = ST.harness_hash()
        assert h not in seen, f"editing {f} did not change the harness hash"
        seen.add(h)
    (tmp_path / "goldbot/research/eval2/confirm.py").write_text("new\n")
    assert ST.harness_hash() not in seen
    (tmp_path / ST.HARNESS_FILES[0]).unlink()
    assert ST.harness_hash() not in seen


# ------------------------------------------------------------------ assert_harness_sane (code review HIGH-2)
def _report(d: Path, name: str, hh: str, results: dict[str, tuple[bool, bool]], *, fast: bool = False,
            changed: bool = False) -> None:
    doc = {"harness_hash": hh, "harness_changed": changed, "fast": fast,
           "results": [{"test": t, "passed": p, "metrics": {"blocked": 1.0} if b else {}} for t, (p, b) in
                       results.items()]}
    (d / f"report_{name}.json").write_text(json.dumps(doc))


ALL_PASS = {t: (True, False) for t in ("S0", "S1", "S2", "S3", "S4", "S6")}


def test_assert_harness_sane_accepts_only_a_full_passing_set_for_the_hash(tmp_path: Path) -> None:
    _report(tmp_path, "20261110T000000_1", "h1", ALL_PASS)
    assert set(ST.assert_harness_sane("h1", report_dir=tmp_path)) == set(ALL_PASS)
    with pytest.raises(ST.HarnessNotSane, match="S5"):
        ST.assert_harness_sane("h1", forward=True, report_dir=tmp_path)       # forward reads need S5 too


@pytest.mark.parametrize("case", ["no report", "other hash", "fast report", "blocked S1", "failed S6",
                                  "later fail overrides", "harness changed", "corrupt report", "missing S2"])
def test_assert_harness_sane_refuses(tmp_path: Path, case: str) -> None:
    if case == "other hash":
        _report(tmp_path, "20261110T000000_1", "h2", ALL_PASS)
    elif case == "fast report":
        _report(tmp_path, "20261110T000000_1", "h1", ALL_PASS, fast=True)
    elif case == "blocked S1":
        _report(tmp_path, "20261110T000000_1", "h1", {**ALL_PASS, "S1": (False, True)})
    elif case == "failed S6":
        _report(tmp_path, "20261110T000000_1", "h1", {**ALL_PASS, "S6": (False, False)})
    elif case == "later fail overrides":
        _report(tmp_path, "20261110T000000_1", "h1", ALL_PASS)
        _report(tmp_path, "20261110T010000_1", "h1", {"S0": (False, False)})
    elif case == "harness changed":
        _report(tmp_path, "20261110T000000_1", "h1", ALL_PASS, changed=True)
    elif case == "corrupt report":
        (tmp_path / "report_20261110T000000_1.json").write_text("{not json")
    elif case == "missing S2":
        _report(tmp_path, "20261110T000000_1", "h1", {k: v for k, v in ALL_PASS.items() if k != "S2"})
    with pytest.raises(ST.HarnessNotSane):
        ST.assert_harness_sane("h1", report_dir=tmp_path)


def test_assert_harness_sane_refuses_on_the_current_tree_while_lanes_are_blocked(tmp_path: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: the report main writes today (S5 blocked) can never satisfy the gate."""
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "goldbot.research.eval2.forward", None)
    ST.main(["--only", "S5"])
    with pytest.raises(ST.HarnessNotSane):
        ST.assert_harness_sane(ST.harness_hash())


# ------------------------------------------------------------------ statistics helpers
def test_clopper_pearson_bounds_at_the_edges() -> None:
    assert ST.cp_upper(0, 50) == pytest.approx(1 - 0.1 ** (1 / 50), rel=1e-9)
    assert ST.cp_upper(50, 50) == 1.0 and ST.cp_lower(0, 50) == 0.0
    assert ST.cp_lower(50, 50) == pytest.approx(0.1 ** (1 / 50), rel=1e-9)
    assert ST.cp_upper(5, 100) > 0.05 > ST.cp_lower(5, 100)


def test_cluster_t_counts_one_cluster_per_day_not_per_instance() -> None:
    rng = np.random.default_rng(1)
    day = np.repeat(np.arange(200), 5)
    x = np.repeat(rng.normal(0, 1, 200), 5)          # five identical instances per day: n_eff = 200, not 1,000
    m, t, se, n = ST.cluster_t(x, day)
    assert n == 1000
    assert se == pytest.approx(x.std(ddof=0) / math.sqrt(200), rel=0.02)
    _, _, se_iid, _ = ST.cluster_t(x, np.arange(1000))
    assert se > 2 * se_iid


def test_poisson_binomial_matches_brute_force() -> None:
    p = [0.1, 0.25, 0.02, 0.4, 0.15]
    brute = np.zeros(len(p) + 1)
    for bits in itertools.product((0, 1), repeat=len(p)):
        brute[sum(bits)] += np.prod([q if b else 1 - q for q, b in zip(p, bits)])
    np.testing.assert_allclose(ST.poisson_binomial_pmf(p), brute, atol=1e-15)


# ------------------------------------------------------------------ A1 (prereg 9.3)
def _cell(name: str, m: float, se: float, n: int = 600, ex: float = 0.0, se_ex: float = 0.06) -> dict[str, Any]:
    return {"cell": name, "n": n, "gross": m, "t_gross": m / se, "se_gross": se, "n_excess": n, "excess": ex,
            "t_excess": ex / se_ex, "se_excess": se_ex}


def test_a1_reproduces_the_signed_reading_of_the_2026_11_09_run() -> None:
    """Prereg 9.4 from the run's own cell SEs (report S0_*.json): gross K 19, F 2, F_crit 5, P(F >= 2) 0.61; we rebuild
    it from SEs with the same sum of p (E 1.98) to check the counting, the percentile and the Bonferroni line."""
    se = [0.0589, 0.0497] + [0.03] * 9 + [0.06] * 6 + [0.07, 0.05]          # 19 cells
    m = [-0.0708, -0.0658] + [0.0] * 17
    cells = [_cell(f"c{k}", mu, s) for k, (mu, s) in enumerate(zip(m, se))]
    a = ST.a1_floor(cells, "gross")
    p = [sps.norm.cdf(-0.05 / s) for s in se]
    pmf = ST.poisson_binomial_pmf(p)
    assert a["K"] == 19 and a["F"] == 2
    assert a["F_crit"] == int(np.argmax(np.cumsum(pmf) >= 0.975))
    assert a["p_ge_obs"] == pytest.approx(1 - pmf[:2].sum())
    assert a["t_crit"] == pytest.approx(sps.norm.ppf(0.025 / 19)) and a["t_crit"] == pytest.approx(-3.01, abs=0.01)
    assert a["pass"] and not a["severe"]


@pytest.mark.parametrize("case, passes", [("one severe cell", False), ("too many below the floor", False),
                                          ("at the count limit", True), ("below floor at t just above t_crit", True)])
def test_a1_count_and_severity_boundaries(case: str, passes: bool) -> None:
    se = 0.05
    k = 19
    t_crit = sps.norm.ppf(0.025 / k)
    f_crit = int(np.argmax(np.cumsum(ST.poisson_binomial_pmf([sps.norm.cdf(-0.05 / se)] * k)) >= 0.975))
    m = [0.0] * k
    if case == "one severe cell":
        m[0] = (t_crit - 0.01) * se
    elif case == "too many below the floor":
        m[: f_crit + 1] = [-0.06] * (f_crit + 1)
    elif case == "at the count limit":
        m[:f_crit] = [-0.06] * f_crit
    else:
        m[0] = (t_crit + 0.01) * se
    a = ST.a1_floor([_cell(f"c{j}", mu, se) for j, mu in enumerate(m)], "gross")
    assert a["pass"] is passes, a


def test_a1_reads_excess_on_the_cells_with_a_twin_count_of_300() -> None:
    cells = [_cell("a", 0.0, 0.05, ex=-0.30, se_ex=0.05), {**_cell("b", 0.0, 0.05, ex=-0.5), "n_excess": 299}]
    a = ST.a1_floor(cells, "excess")
    assert a["K"] == 1 and a["severe"] == ["a"] and not a["pass"]


def _m(gross_pass: bool = True, excess_pass: bool = True) -> dict[str, Any]:
    return {"gross": 0.001, "t_gross": 0.3, "excess": 0.001, "t_excess": 0.2, "chi2_p": 0.5,
            "a1_gross": {"pass": gross_pass}, "a1_excess": {"pass": excess_pass}}


@pytest.mark.parametrize("kw, status", [
    ({}, "BLOCKED"),                                                          # core pass, no L2 sentinel half
    ({"sentinel_l2": {"buy_every_close": False, "drift_walk": False}}, "PASS"),
    ({"sentinel_l2": {"buy_every_close": True, "drift_walk": False}}, "FAIL"),
    ({"m": _m(excess_pass=False)}, "FAIL"),                                  # excess has no exception
    ({"m": _m(gross_pass=False)}, "FAIL"),                                   # gross-only, route not run
    ({"m": _m(gross_pass=False), "route": {"tested": False, "pass": True}}, "FAIL"),   # untested route reads FAIL
    ({"m": _m(gross_pass=False), "route": {"tested": True, "pass": False}}, "FAIL"),
    ({"m": _m(gross_pass=False), "route": {"tested": True, "pass": True}}, "BLOCKED"),
    ({"e9": [True, False]}, "FAIL"),
])
def test_s0_reading_under_a1_and_the_research_director_conditions(kw: dict[str, Any], status: str) -> None:
    got, why = ST.s0_decide(kw.get("m", _m()), sentinels_e9=kw.get("e9", [False, False]), route=kw.get("route"),
                            sentinel_l2=kw.get("sentinel_l2"))
    assert got == status, why


def test_s0_metrics_report_literal_floor_a1_fields_and_ambiguous_share() -> None:
    rng = np.random.default_rng(3)
    frames = []
    for k, (mu, n) in enumerate(((0.0, 400), (-0.30, 2000), (0.0, 20))):
        g = rng.normal(mu, 1.3, n)
        frames.append(pd.DataFrame({"cell_id": f"c{k}", "tf": "4h", "day": np.arange(n), "gross_r": g, "net_r": g,
                                    "twin_mean_net_r": 0.0, "excess_r": g, "n_twins": 20,
                                    "ambiguous": np.arange(n) % 10 == 0}))
    m = ST.s0_metrics(frames)
    assert "c1" in m["floor_fail"] and "c2" not in m["floor_fail"]
    assert m["a1_gross"]["K"] == 2 and "c1" in m["a1_gross"]["severe"] and not m["a1_gross"]["pass"]
    assert m["chi2_df"] == 2
    assert all(c["ambiguous_share"] == pytest.approx(0.1, abs=0.01) for c in m["cells"])


# ------------------------------------------------------------------ A1 (iv): the sign-flipped skeleton route
def test_the_skeleton_keeps_times_ranges_bodies_and_gaps_and_flips_signs() -> None:
    b = ST.ou_bars(2000, 3)
    s = ST.sign_flip_skeleton(b, seed_key="k")
    assert np.array_equal(s.t, b.t) and np.array_equal(s.ta, b.ta) and np.array_equal(s.co, b.co)
    np.testing.assert_allclose(s.h - s.l, b.h - b.l, rtol=1e-9)
    np.testing.assert_allclose(np.abs(s.c - s.o), np.abs(b.c - b.o), rtol=1e-9)
    np.testing.assert_allclose(np.abs(s.o[1:] - s.c[:-1]), np.abs(b.o[1:] - b.c[:-1]), rtol=1e-6, atol=1e-9)
    flips = np.sign(s.c - s.o) != np.sign(b.c - b.o)
    assert 0.4 < flips.mean() < 0.6
    assert np.array_equal(ST.sign_flip_skeleton(b, seed_key="k").c, s.c)          # keyed
    assert not np.array_equal(ST.sign_flip_skeleton(b, seed_key="j").c, s.c)


def test_the_skeleton_route_is_tested_market_structure_shows_and_the_skeleton_removes_it() -> None:
    """Research-director condition: the route counts only once tested. Mean reversion makes the NULL gross
    significantly negative; the skeleton of the same path brings it back to |t| < 2."""
    r = ST.skeleton_route_selfcheck()
    assert r["ou_t"] <= -2.0, r
    assert abs(r["skeleton_t"]) < 2.0, r
    assert r["tested"] == 1.0


# ------------------------------------------------------------------ per-bar NULL table == the harness path
def test_null_table_reproduces_simulate_and_simulate_twins_for_null_geometry() -> None:
    b = ST.rw_bars("1h", 1500, seed=5)
    cm = default_model()
    ev = ST.random_entries(b, rate=0.05, seed_key="eq")
    tr = SIM.simulate(b, ev, control="random_side", seed_key="eq", costs=cm)
    tab = ST.null_table(b, cm)
    net = tab["net"]
    col = ST.COL
    sides = np.where(tr[:, col["side"]] > 0, 0, 1)
    want = tr[:, col["gross"]] - tr[:, col["cost"]]
    got = net[tr[:, col["i"]].astype(int), sides]
    assert len(want) > 20
    np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(tab["dc"][tr[:, col["i"]].astype(int), sides], tr[:, col["cost"]], rtol=1e-9)
    row_of = {(int(i), int(s)): k for k, (i, s) in enumerate(zip(ev.i, ev.side))}
    ks = [row_of[(int(r[col["i"]]), int(r[col["side"]]))] for r in tr]
    exits = np.full(len(ev), -1, np.int64)
    exits[ks] = tr[:, col["x"]].astype(np.int64)
    fire = np.zeros(len(b), bool)
    fire[ev.i] = True
    draws = SIM.draw_twins(b, ev, exits=exits, family_fire=fire, seed_key="eq")
    tw = SIM.simulate_twins(b, ev, [draws[k] for k in ks], costs=cm)
    for r, k in enumerate(ks):
        d = draws[k].decision_bars
        ok = d >= 0
        np.testing.assert_allclose(tw[r, ok], net[d[ok], sides[r]], rtol=1e-9, atol=1e-12, equal_nan=True)


def test_the_null_table_charges_lane_2s_deciding_cost_on_gold_and_never_zero() -> None:
    b = ST.rw_bars("1h", 400, seed=6)
    tab = ST.null_table(b, default_model(), deciding=True)
    ok = np.isfinite(tab["net"])
    assert ok.sum() > 100
    assert np.isfinite(tab["dc"][ok]).all() and (tab["dc"][ok] > 0).all()
    plain = ST.null_table(b, default_model())
    assert not np.allclose(tab["dc"][ok], plain["dc"][ok])                  # Lane 2's cost, not the sim's


def test_the_null_table_cache_key_names_the_bars() -> None:
    b = ST.rw_bars("1h", 300, seed=1)
    c = ST.rw_bars("1h", 300, seed=2)
    assert ST.bars_digest(b) == ST.bars_digest(ST.rw_bars("1h", 300, seed=1))
    assert ST.bars_digest(b) != ST.bars_digest(c)


def _tables(seeds: tuple[int, ...] = (9,), n: int = 1200) -> dict[tuple[str, str], tuple[Any, Any]]:
    out = {}
    for s, tf in zip(seeds, ("1h", "4h")):
        b = ST.rw_bars(tf, n, seed=s)
        b.meta["dec_from"] = int(b.ta[30])
        out[("XAUUSD", tf)] = (b, ST.null_table(b, default_model(), deciding=True))
    return out


def test_null_family_units_never_overlaps_and_draws_twins_off_its_fire_bars() -> None:
    tabs = _tables()
    b = tabs[("XAUUSD", "1h")][0]
    u = ST.null_family_units(tabs, family="F", seed_key="k", rate={"1h": 0.2})
    dec = np.searchsorted(b.ta, u["decision_ns"].to_numpy())
    assert len(u) > 20 and (np.diff(dec) > 10).all()
    assert set(u.columns) >= {"cell_id", "family", "inst", "tf", "side", "day", "decision_ns", "net_r",
                              "twin_mean_net_r", "excess_r", "cost_r", "deciding_cost_r", "hold_days", "year"}
    assert (u["deciding_cost_r"] > 0).all() and u["hold_days"].notna().all()
    again = ST.null_family_units(tabs, family="F", seed_key="k", rate={"1h": 0.2})
    pd.testing.assert_frame_equal(u, again)                                  # keyed, reproducible
    other = ST.null_family_units(tabs, family="G", seed_key="k", rate={"1h": 0.2})
    assert not np.array_equal(other["decision_ns"].to_numpy()[:20], u["decision_ns"].to_numpy()[:20])


def test_sign_flip_units_keep_magnitude_twins_and_identity() -> None:
    u = ST.null_family_units(_tables(), family="F", seed_key="k", rate={"1h": 0.2})
    f = ST.sign_flip_units(u, seed_key="s", family="C1")
    np.testing.assert_allclose(np.abs(f["excess_r"]), np.abs(u["excess_r"]))
    np.testing.assert_allclose(f["net_r"], f["twin_mean_net_r"] + f["excess_r"])
    flipped = np.sign(f["excess_r"].to_numpy()) != np.sign(u["excess_r"].to_numpy())
    assert 0.2 < flipped.mean() < 0.8
    same_day = f.groupby("day").apply(lambda g: len(set(np.sign(g["excess_r"] / u.loc[g.index, "excess_r"]))))
    assert (same_day == 1).all()                                             # one coin per (cell, session day)
    assert set(f["family"]) == {"C1"} and f["cell_id"].str.startswith("C1|").all()


def test_deciding_cost_map_refuses_a_missing_cost() -> None:
    u = pd.DataFrame({"cell_id": ["a", "a", "b"], "deciding_cost_r": [0.1, 0.2, np.nan]})
    with pytest.raises(ValueError, match="cost of 0"):
        ST.deciding_cost_map(u)
    assert ST.deciding_cost_map(u.iloc[:2]) == {"a": pytest.approx(0.15)}


# ------------------------------------------------------------------ S1 arm B: shifted real signals
def _oco_events(b: Any, every: int = 37) -> Any:
    a = K.atr(b, 14)
    eb = EB()
    for j in range(40, len(b) - 30, every):
        if np.isfinite(a[j]):
            eb.oco_pair(j, long={"kind": 1, "px": b.c[j] + 0.3 * a[j], "stop_d": a[j], "tgt_d": 2 * a[j], "hold": 8},
                        short={"kind": 1, "px": b.c[j] - 0.3 * a[j], "stop_d": a[j], "tgt_d": 2 * a[j], "hold": 8})
    return eb.build()


def test_shifted_events_move_whole_weeks_of_at_least_three_and_keep_own_geometry() -> None:
    b = ST.rw_bars("1h", 6000, seed=4)
    b.meta.update(dec_from=int(b.ta[30]))
    ev = _oco_events(b)
    monkey = {"EXPLORE_FROM": int(b.ta[0]), "EXPLORE_END": int(b.ta[-1]) + 1}
    old = {k: getattr(ST, k) for k in monkey}
    try:
        for k, v in monkey.items():
            setattr(ST, k, v)
        sev, weeks = ST.shift_events(b, ev, seed_key="w")
    finally:
        for k, v in old.items():
            setattr(ST, k, v)
    assert weeks >= 3 and weeks * 7 >= 20
    assert len(sev) > 0 and len(sev) % 2 == 0                               # OCO pairs move as pairs
    assert int(sev.oco.sum()) == len(sev) // 2
    assert set(sev.kind.tolist()) == set(ev.kind.tolist()) and set(sev.hold.tolist()) == {8}
    a14 = K.atr(b, 14)
    lead = sev.i[::2]
    np.testing.assert_allclose(sev.stop_d[::2], a14[lead], rtol=1e-9)        # own geometry at the new bar's ATR


# ------------------------------------------------------------------ S3 plant
def test_the_s3_plant_jumps_right_after_the_fill_bar_opens_and_stays() -> None:
    b0 = ST.rw_bars("1h", 300, seed=2)
    a = K.atr(b0, 14)
    eb = EB()
    eb.add(100, -1, stop_d=a[100], tgt_d=2 * a[100], hold=10)
    jumps = ST.plant_jumps(eb.build(), a, 0.2)
    assert jumps == {101: pytest.approx(-0.2 * a[100])}
    p = ST.rw_bars("1h", 300, seed=2, jumps=jumps)
    assert np.array_equal(p.o[:102], b0.o[:102]) and np.array_equal(p.c[:101], b0.c[:101])
    np.testing.assert_allclose(p.c[101:] - b0.c[101:], -0.2 * a[100], rtol=1e-9)
    np.testing.assert_allclose(p.o[102:] - b0.o[102:], -0.2 * a[100], rtol=1e-9)


def test_a_planted_signal_gains_g_r_on_average_on_its_own_path() -> None:
    """Optional stopping: with the jump right after the fill, the mean gross gain over the unplanted path is +g R."""
    gains = []
    for seed in range(6):
        b0 = ST.rw_bars("1h", 4000, seed=100 + seed)
        a = K.atr(b0, 14)
        eb = EB()
        for j in range(60, 3960, 25):
            eb.add(j, 1 if (j // 25) % 2 else -1, stop_d=a[j], tgt_d=2 * a[j], hold=10)
        ev = eb.build()
        b = ST.rw_bars("1h", 4000, seed=100 + seed, jumps=ST.plant_jumps(ev, a, 0.2))
        g0 = SIM.simulate(b0, ev, control="random_side", seed_key="p", costs=default_model())[:, ST.COL["gross"]]
        g1 = SIM.simulate(b, ev, control="random_side", seed_key="p", costs=default_model())[:, ST.COL["gross"]]
        gains.append(g1 - g0 if len(g1) == len(g0) else np.full(1, np.nan))
    d = np.concatenate(gains)
    assert np.isfinite(d).all() and len(d) > 500
    assert abs(d.mean() - 0.2) < 3 * d.std(ddof=1) / np.sqrt(len(d)), d.mean()


def _fake_s3_rep(args: tuple[float, int, str]) -> dict[str, Any]:
    g, rep, _ = args
    rng = np.random.default_rng(rep)
    n = 1000
    ex = rng.normal(0.0, 1.3, n)
    ex = ex - ex.mean() + g                                     # exact: the planted rep sits at +g, the null at 0
    fam = f"S3P{rep:05d}" if g else f"NULL-S3{rep:05d}"
    df = pd.DataFrame({"cell_id": f"{fam}|XAUUSD|1h", "family": fam, "inst": "XAUUSD", "tf": "1h",
                       "day": np.arange(n), "excess_r": ex, "net_r": ex, "twin_mean_net_r": 0.0,
                       "cost_r": 0.1, "deciding_cost_r": 0.1})
    m, t, se, k = ST.cluster_t(ex, np.arange(n))
    return {"g": g, "rep": rep, "n": k, "excess": m, "t": t, "se": se, "detected": float(t >= ST.Z_DETECT),
            "units": df}


def test_s3_reads_blocked_not_pass_while_hier_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    monkeypatch.setattr(ST, "_s3_rep", _fake_s3_rep)
    monkeypatch.setitem(sys.modules, "goldbot.research.eval2.hier", None)
    r = ST.s3_full_path(reps=40, seed_key="x", fast=False, workers=1)
    assert not r.passed and r.metrics["blocked"] == 1.0 and r.metrics["harness_half_pass"] == 1.0, r


def test_s3_never_passes_while_the_cost_separation_plants_are_unbuilt(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    monkeypatch.setattr(ST, "_s3_rep", _fake_s3_rep)
    hier, _, _ = _fake_lane3()
    monkeypatch.setitem(sys.modules, "goldbot.research.eval2.hier", hier)
    r = ST.s3_full_path(reps=40, seed_key="x", fast=False, workers=1)
    assert r.metrics["l2_pass"] == 1.0 and r.metrics["blocked"] == 1.0 and not r.passed, r


def test_s3_harness_half_failure_reads_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    monkeypatch.setattr(ST, "_s3_rep", lambda a: _fake_s3_rep((0.0, a[1], a[2])) | {"g": a[0]})
    r = ST.s3_full_path(reps=10, seed_key="x", fast=False, workers=1)
    assert not r.passed and not r.metrics.get("blocked"), r


@pytest.mark.selftest
def test_s3_fast_harness_half_detects_planted_plus_0_20_r_and_not_planted_0() -> None:
    r = ST.s3_full_path(reps=4, seed_key="ci-s3", fast=True, workers=1)
    print(r)
    assert r.metrics["harness_half_pass"] == 1.0, r


# ------------------------------------------------------------------ S4: OCO de-duplication, Lane 3 probe
def test_an_oco_pair_is_one_instance_with_the_side_that_fills_first_or_side_0() -> None:
    b = ST.rw_bars("1h", 400, seed=8)
    a = K.atr(b, 14)
    eb = EB()
    j = 100
    eb.oco_pair(j, long={"kind": 1, "px": b.c[j] + 0.01, "stop_d": a[j], "hold": 5},          # long fills at once
                short={"kind": 1, "px": b.c[j] - 50 * a[j], "stop_d": a[j], "hold": 5})
    eb.oco_pair(200, long={"kind": 1, "px": b.c[200] + 0.001, "stop_d": a[200], "hold": 5},   # both legs touched on one bar
                short={"kind": 1, "px": b.c[200] - 0.001, "stop_d": a[200], "hold": 5})
    eb.add(300, -1, stop_d=a[300], hold=5)                                         # a plain market order
    ev = eb.build()
    inst = ST.oco_instances(b, ev)
    assert len(inst) == 3
    by_bar = {int(ev.i[k]): (f, s) for k, f, s in inst}
    assert by_bar[100][1] == 1 and by_bar[300] == (301, -1)
    hi, lo = b.h[201], b.l[201]
    both = hi >= b.c[200] + 0.001 and lo <= b.c[200] - 0.001
    assert by_bar[200][1] == (0 if both else by_bar[200][1])


def test_probe_units_hands_one_instance_per_pair_to_lane_3s_probe() -> None:
    calls: list[int] = []

    def first_passage(b: Any, *, fill_bars: np.ndarray, sides: np.ndarray, horizon_bars: int) -> np.ndarray:
        calls.append(len(fill_bars))
        return np.where(np.asarray(sides) == 0, -1.0, 0.5)

    probe = types.SimpleNamespace(first_passage=first_passage)
    b = ST.rw_bars("1h", 3000, seed=12)
    b.meta.update(dec_from=int(b.ta[30]))
    ev = _oco_events(b)
    sig, tw, day = ST.probe_units(b, ev, probe, seed_key="p")
    pairs = len(ev) // 2                                       # EB.oco_pair flags both legs
    assert len(sig) <= pairs and len(sig) > 0.5 * pairs
    assert tw.shape == (len(sig), SIM.TWIN_M) and np.isfinite(tw).any()
    assert len(day) == len(sig)


# ------------------------------------------------------------------ S1 / S2 / S3 plumbing on fake Lane 3 modules
@dataclass(frozen=True)
class _Post:
    cell_id: str
    mean: float
    sd: float
    p_pos: float
    p_net_pos_gate: float
    p_net_gt_kill: float
    n_days: int


def _fake_lane3(carry_z: float = 1.2816) -> tuple[Any, Any, Any]:
    calls: dict[str, int] = {}

    def cell_table(units: pd.DataFrame, *, window: str) -> pd.DataFrame:
        rows = []
        for cid, g in units.groupby("cell_id", sort=True):
            m, _, se, _ = ST.cluster_t(g["excess_r"].to_numpy(float), g["day"].to_numpy())
            rows.append({"cell_id": str(cid), "family": str(g["family"].iloc[0]), "tf": str(g["tf"].iloc[0]),
                         "inst": str(g["inst"].iloc[0]), "mean": m, "se": se, "n_days": g["day"].nunique(),
                         "twin_net": float(g["twin_mean_net_r"].mean()), "twin_se": 0.0, "window": window})
        return pd.DataFrame(rows)

    def fit_prior(cells: pd.DataFrame, *, leave_out_family: str, spec: Any) -> dict[str, float]:
        # the population passed includes the family; leaving it out is fit_prior's job (frozen signature)
        assert leave_out_family in set(cells["family"]) and "control" in cells.columns
        assert (cells["window"] == "explore").all() and cells["config_hash"].notna().all()
        assert not cells["config_hash"].duplicated().any()    # one hash per (family, cell): de-dup collapses nothing
        calls["fit_prior"] = calls.get("fit_prior", 0) + 1
        return {"mu": 0.0, "tau": 0.05}

    def posteriors(cells: pd.DataFrame, prior: Any, *, spec: Any, deciding_cost_r: Any) -> list[_Post]:
        assert all(np.isfinite(float(deciding_cost_r[c])) and float(deciding_cost_r[c]) > 0 for c in cells["cell_id"])
        rows: list[Any] = cells.to_dict("records")
        return [_Post(str(r["cell_id"]), float(r["mean"]), float(r["se"]), float(sps.norm.cdf(r["mean"] / r["se"])),
                      float(sps.norm.cdf(r["mean"] / r["se"])), 1.0, int(r["n_days"])) for r in rows]

    def l2_decide(posts: Any, *, family: str, spec: Any) -> Any:
        car = tuple(p.cell_id for p in sorted(posts, key=lambda p: -p.mean) if p.mean / p.sd >= carry_z)[:3]
        return types.SimpleNamespace(family=family, carried=car, killed=(), held=())

    def power_at(*, n: int, sd: float, delta: float, alpha: float) -> float:
        return float(sps.norm.sf(sps.norm.isf(alpha) - delta / (sd / math.sqrt(n))))

    def hier_spec(*, prior_population_hash: str, code_hash: str) -> Any:
        assert prior_population_hash and code_hash == "fake-code"   # the self-test freezes every spec it uses
        return types.SimpleNamespace(carry_p=0.8, prior_population_hash=prior_population_hash, code_hash=code_hash)

    hier = types.SimpleNamespace(HierSpec=hier_spec, code_hash=lambda: "fake-code", cell_table=cell_table,
                                 fit_prior=fit_prior, posteriors=posteriors, l2_decide=l2_decide, calls=calls)

    @dataclass(frozen=True)
    class BatchPRDS:
        window: str
        q: float = 0.10

        def level(self, j: int, n_j: int) -> float:
            return self.q * 0.5

        def test(self, p: Any, *, j: int) -> np.ndarray:
            p = np.asarray(p)
            k = np.sort(p) <= self.level(j, len(p)) * np.arange(1, len(p) + 1) / len(p)
            r = int(np.max(np.flatnonzero(k)) + 1) if k.any() else 0
            return p <= (np.sort(p)[r - 1] if r else -1)

    def boot_p_one_sided(units: pd.DataFrame, stat: Any, *, mean_block: float, resamples: int, seed_key: str) -> float:
        assert resamples >= 10_000 and mean_block >= 5
        calls["boot"] = calls.get("boot", 0) + 1
        x = units["excess_r"].to_numpy(float)
        stat(units)
        return float(sps.norm.sf(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))))

    def control_variate_net(units: pd.DataFrame, weights: Any) -> float:
        assert weights and all(w > 0 for w in weights.values())
        return float(units["excess_r"].mean() + units["twin_mean_net_r"].mean())

    confirm = types.SimpleNamespace(BatchPRDS=BatchPRDS, boot_p_one_sided=boot_p_one_sided,
                                    confirm_resamples=lambda m, a: max(10_000, math.ceil(20 * m / a)))
    stats = types.SimpleNamespace(power_at=power_at, control_variate_net=control_variate_net)
    return hier, confirm, stats


def _source_family() -> list[tuple[str, Any, Any]]:
    """A synthetic batch-1 family: two registered units, one on two gold cells (1h, 4h), one on the 1h cell too."""
    out = []
    for unit, tf, seed, every in (("FAM-V0", "1h", 21, 29), ("FAM-V0", "4h", 22, 7), ("FAM-V1", "1h", 21, 41)):
        b = ST.rw_bars(tf, 1500, seed=seed)
        b.meta.update(dec_from=int(b.ta[30]))
        out.append((unit, b, _oco_events(b, every=every)))
    return out


def _shifted_fams(reps: int) -> list[pd.DataFrame]:
    src = _source_family()
    return [ST.shifted_family_units(r, "s", "FAM", src, None, costs=default_model()) for r in range(reps)]


def test_each_arm_b_family_shifts_every_cell_of_one_batch1_family_under_one_id() -> None:
    """Quant round 2 (C1): a replicate shifts every registered unit and gold cell of ONE batch-1 family with that
    replicate's keyed shift, all under one family id: its cells are the source family's cells, one for one."""
    src = _source_family()
    want = {f"{unit}|{b.inst}|{b.tf}" for unit, b, _ in src}
    for r in range(3):
        u = ST.shifted_family_units(r, "s", "FAM", src, None, costs=default_model())
        assert set(u["family"]) == {f"S1B{r:04d}"} and set(u["source_family"]) == {"FAM"}
        got = {c.split("|", 1)[1] for c in u["cell_id"]}
        assert got == want, (got, want)
        assert u["shift_weeks"].nunique() == 1 and int(u["shift_weeks"].iloc[0]) >= 3   # one shift per replicate
    w = {int(ST.shifted_family_units(r, "s", "FAM", src, None, costs=default_model())["shift_weeks"].iloc[0])
         for r in range(6)}
    assert len(w) > 1                                                       # keyed per replicate


def test_arm_b_replicates_cycle_through_the_batch1_families() -> None:
    fams: dict[str, list[Any]] = {"C02": [], "C03": [], "S21": []}
    picked = [ST.arm_b_family(r, fams) for r in range(6)]
    assert picked == ["C02", "C03", "S21", "C02", "C03", "S21"]


def test_s1_runs_three_arms_through_l2_and_the_control_variate_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    hier, confirm, stats = _fake_lane3()
    monkeypatch.setattr(ST, "EXPLORE_FROM", 0)
    r = ST.s1_from_arms(_tables((9, 10), 900), _shifted_fams(10), reps=10, seed_key="s1-ci", hier=hier,
                        confirm=confirm, stats=stats, hh="h")
    for arm in ("null_geometry", "shifted", "sign_flip"):
        assert r.metrics[f"{arm}_families"] == 10, arm
        assert 0 <= r.metrics[f"{arm}_carry_rate"] <= r.metrics[f"{arm}_carry_ub90"] <= 1
        assert r.metrics[f"{arm}_runs"] == 1
    assert hier.calls["fit_prior"] == 30                     # one leave-family-out prior per null family per arm
    assert not r.passed                                      # 10 families per arm are below the 500 floor
    assert any("shared history" in n for n in r.notes)


def test_s1_carry_bound_fails_when_every_null_family_carries(monkeypatch: pytest.MonkeyPatch) -> None:
    hier, confirm, stats = _fake_lane3(carry_z=-99.0)         # a broken L2 that carries everything
    monkeypatch.setattr(ST, "EXPLORE_FROM", 0)
    r = ST.s1_from_arms(_tables((9,), 900), [], reps=10, seed_key="s1-bad", hier=hier, confirm=confirm, stats=stats,
                        hh="h")
    assert not r.passed and r.metrics["null_geometry_carry_rate"] == 1.0
    assert hier.calls.get("boot", 0) > 0                     # carried families go to the confirm leg
    assert any("arm B" in n for n in r.notes)


def test_s2_plumbing_detects_large_plants_and_leaves_the_15m_neighbour() -> None:
    hier, _, stats = _fake_lane3(carry_z=2.33)
    r = ST.s2_run(plants_r=(0.20,), reps=2, seed_key="s2-ci", hier=hier, stats=stats, hh="h", n_fam=6)
    assert r.metrics["detect_0.2"] > 0.5 and r.metrics["not15_0.2"] >= 0.9, r.metrics


def test_lane3_dependent_tests_read_blocked_not_pass_while_lane3_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    for mod in ("goldbot.research.eval2.hier", "goldbot.research.eval2.confirm", "goldbot.research.eval2.probe"):
        monkeypatch.setitem(sys.modules, mod, None)
    for r in (ST.s1_null_population(reps=1, workers=1, seed_key="x"),
              ST.s2_label_shift(reps=1, seed_key="x"), ST.s4_leakage(seed_key="x")):
        assert not r.passed and r.metrics["blocked"] == 1.0 and r.threshold.startswith("BLOCKED")


def test_s4_lint_refuses_every_realised_cost_filter() -> None:
    r = ST.s4_leakage(seed_key="x")
    assert r.metrics["lint_refused"] == r.metrics["lint_cards"] and r.metrics["lint_clean_ok"] == 1.0


# ------------------------------------------------------------------ real bars (local market data only)
def _have_raw() -> bool:
    from goldbot.research.catalogue.data import raw_dir
    return (raw_dir() / "xauusd_1m_dukascopy_2016.parquet").exists()


@pytest.mark.selftest
@pytest.mark.skipif(not _have_raw(), reason="real-bar S0 needs local raw/ market data (never in CI or git)")
def test_s0_fast_on_real_gold_1h_2016_17(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL2_SELFTEST_DIR", str(tmp_path))
    r = ST.s0_harness(harness_hash="h", seed_key="ci-s0", fast=True, workers=1)
    print(r)
    assert r.passed and r.metrics["n"] > 300, r


def test_cell_frame_refuses_cells_that_share_a_config_hash(monkeypatch):
    """Quant condition (b) on aa48a84e: two cells with one hash would be collapsed by L2 dedup and shrink the null."""
    st = ST

    class _H:
        @staticmethod
        def cell_table(units, *, window):
            return pd.DataFrame({"cell_id": ["F|a", "F|b"], "window": [window] * 2})

    monkeypatch.setattr(st, "cell_config_hash", lambda cell_id: "same")
    units = pd.DataFrame({"excess_r": [0.1, 0.2]})
    with pytest.raises(ValueError, match="share a config_hash"):
        st.cell_frame(units, _H, control=False)
