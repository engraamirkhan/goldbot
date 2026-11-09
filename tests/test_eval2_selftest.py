"""scripts/eval2_selftest.py (EVAL-2 Lane 4, prereg 2026-11-09 section 4.10): the frozen API, the fail-closed exit
code, the statistics helpers, the per-bar NULL table the S1 null population is built on (checked against the real
`simulate` + `simulate_twins` path), the S3 plant, and the S1/S2 plumbing against the frozen Lane 3 signatures (fake
Lane 3 modules; the real ones replace them when Lane 3 merges).

The real-bar runs (S0 on explore 2010-17, S6 on real bars) and the S3 fast run carry @pytest.mark.selftest; the real-bar
ones skip where raw/ market data is absent (CI: market data stays local)."""
from __future__ import annotations

import importlib.util
import inspect
import math
import sys
import types
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue import core as K
from goldbot.research.catalogue import sim as SIM
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


@pytest.mark.parametrize("w", [0, 9])
def test_workers_are_capped_at_eight(w: int) -> None:
    with pytest.raises(SystemExit):
        ST.main(["--workers", str(w), "--only", "S5"])


def test_harness_hash_changes_with_any_harness_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h0 = ST.harness_hash()
    src = tmp_path / "sim.py"
    src.write_text("x = 1\n")
    monkeypatch.setattr(ST, "ROOT", tmp_path)
    monkeypatch.setattr(ST, "HARNESS_FILES", ("sim.py",))
    h1 = ST.harness_hash()
    src.write_text("x = 2\n")
    assert len({h0, h1, ST.harness_hash()}) == 3


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


def test_s0_metrics_cell_floor_and_its_expected_false_failures() -> None:
    """At n 300 and sd 1.3 R a sound cell is below -0.05 R a quarter of the time: the metric says so."""
    rng = np.random.default_rng(3)
    frames = []
    for k, (mu, n) in enumerate(((0.0, 300), (-0.30, 2000), (0.0, 20))):
        g = rng.normal(mu, 1.3, n)
        frames.append(pd.DataFrame({"cell_id": f"c{k}", "tf": "4h", "day": np.arange(n), "gross_r": g, "net_r": g,
                                    "twin_mean_net_r": 0.0, "excess_r": g, "n_twins": 20}))
    m = ST.s0_metrics(frames)
    assert "c1" in m["floor_fail"] and "c1" in m["floor_fail_sig"]
    assert "c2" not in m["floor_fail"]                                       # n < 300: not judged
    assert m["floor_cells"] == 2
    assert 0.2 < m["floor_expected_false"] < 0.3 + 0.01                      # c0 ~ Phi(-0.05 / 0.075) + c1 ~ 0
    assert m["chi2_df"] == 2                                                 # the n 20 cell is not in the chi2


# ------------------------------------------------------------------ per-bar NULL table == the harness path
def test_null_table_reproduces_simulate_and_simulate_twins_for_null_geometry() -> None:
    b = ST.rw_bars("1h", 1500, seed=5)
    cm = default_model()
    ev = ST.random_entries(b, rate=0.05, seed_key="eq")
    tr = SIM.simulate(b, ev, control="random_side", seed_key="eq", costs=cm)
    net = ST.null_table(b, cm)
    col = ST.COL
    sides = np.where(tr[:, col["side"]] > 0, 0, 1)
    want = tr[:, col["gross"]] - tr[:, col["cost"]]
    got = net[tr[:, col["i"]].astype(int), sides]
    assert len(want) > 20
    np.testing.assert_allclose(got, want, rtol=1e-9, atol=1e-12)
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


def test_null_family_units_never_overlaps_and_draws_twins_off_its_fire_bars() -> None:
    b = ST.rw_bars("1h", 1200, seed=9)
    net = ST.null_table(b, default_model())
    b.meta["dec_from"] = int(b.ta[30])
    u = ST.null_family_units({("XAUUSD", "1h"): (b, net)}, family="F", seed_key="k", rate={"1h": 0.2})
    dec = np.searchsorted(b.ta, u["decision_ns"].to_numpy())
    assert len(u) > 20 and (np.diff(dec) > 10).all()
    assert set(u.columns) >= {"cell_id", "family", "inst", "tf", "side", "day", "decision_ns", "net_r",
                              "twin_mean_net_r", "excess_r", "cost_r", "year"}
    again = ST.null_family_units({("XAUUSD", "1h"): (b, net)}, family="F", seed_key="k", rate={"1h": 0.2})
    pd.testing.assert_frame_equal(u, again)                                  # keyed, reproducible
    other = ST.null_family_units({("XAUUSD", "1h"): (b, net)}, family="G", seed_key="k", rate={"1h": 0.2})
    assert not np.array_equal(other["decision_ns"].to_numpy()[:20], u["decision_ns"].to_numpy()[:20])


# ------------------------------------------------------------------ S3 plant
def test_the_s3_plant_jumps_right_after_the_fill_bar_opens_and_stays() -> None:
    """The jump follows the fill (bar i + 1's open is untouched) inside the fill bar, then the whole suffix carries it;
    the unplanted path is the same seed's path."""
    b0 = ST.rw_bars("1h", 300, seed=2)
    a = K.atr(b0, 14)
    from goldbot.research.catalogue.core import EB
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
        from goldbot.research.catalogue.core import EB
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


@pytest.mark.selftest
def test_s3_fast_planted_plus_0_20_r_is_detected_and_planted_0_is_not() -> None:
    r = ST.s3_full_path(reps=4, seed_key="ci-s3", fast=True, workers=1)
    print(r)
    assert r.passed, r


# ------------------------------------------------------------------ S1 / S2 plumbing on fake Lane 3 modules
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
    from scipy import stats as sps
    calls: dict[str, int] = {}

    def cell_day_means(units: pd.DataFrame, *, col: str = "excess") -> pd.DataFrame:
        g = units.groupby(["cell_id", "day"])[col]
        return pd.DataFrame({"mean": g.mean(), "n": g.size()}).reset_index()

    def fit_prior(cells: pd.DataFrame, *, leave_out_family: str, spec: Any) -> dict[str, float]:
        # the population passed includes the family; leaving it out is fit_prior's job (frozen signature)
        assert leave_out_family in set(cells["family"])
        calls["fit_prior"] = calls.get("fit_prior", 0) + 1
        return {"mu": 0.0, "tau": 0.05}

    def posteriors(cells: pd.DataFrame, prior: Any, *, spec: Any, deciding_cost_r: Any) -> list[_Post]:
        out = []
        for cid, c in cells.groupby("cell_id"):
            x = c["mean"].to_numpy(float)
            m, se = float(x.mean()), float(x.std(ddof=1) / math.sqrt(len(x)))
            p = float(sps.norm.cdf(m / se))
            out.append(_Post(str(cid), m, se, p, p, p, len(x)))
        return out

    def l2_decide(posts: Any, *, family: str, spec: Any) -> Any:
        car = tuple(p.cell_id for p in sorted(posts, key=lambda p: -p.mean) if p.mean / p.sd >= carry_z)[:3]
        return types.SimpleNamespace(family=family, carried=car, killed=(), held=())

    def power_at(*, n: int, sd: float, delta: float, alpha: float) -> float:
        return float(sps.norm.sf(sps.norm.isf(alpha) - delta / (sd / math.sqrt(n))))

    hier = types.SimpleNamespace(HierSpec=lambda: types.SimpleNamespace(carry_p=0.8), fit_prior=fit_prior,
                                 posteriors=posteriors, l2_decide=l2_decide, calls=calls)

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
        assert resamples >= 10_000
        x = units["excess_r"].to_numpy(float)
        x = x[np.isfinite(x)]
        return float(sps.norm.sf(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))))

    confirm = types.SimpleNamespace(BatchPRDS=BatchPRDS, boot_p_one_sided=boot_p_one_sided,
                                    confirm_resamples=lambda m, a: max(10_000, math.ceil(20 * m / a)))
    stats = types.SimpleNamespace(cell_day_means=cell_day_means, power_at=power_at)
    return hier, confirm, stats


def test_s1_plumbing_runs_the_null_population_through_l2_and_confirm_on_the_frozen_api() -> None:
    hier, confirm, stats = _fake_lane3()
    tabs = {}
    for k, tf in enumerate(("1h", "4h")):
        b = ST.rw_bars(tf, 900, seed=40 + k)
        tabs[("XAUUSD", tf)] = (b, ST.null_table(b, default_model()))
    r = ST.s1_from_tables(tabs, reps=12, seed_key="s1-ci", hier=hier, confirm=confirm, stats=stats, hh="h")
    assert r.test == "S1" and r.metrics["families"] == 12
    assert hier.calls["fit_prior"] == 12                      # one leave-family-out prior per null family
    assert 0 <= r.metrics["carry_rate"] <= r.metrics["carry_ub90"] <= 1


def test_s1_carry_bound_fails_when_every_null_family_carries() -> None:
    hier, confirm, stats = _fake_lane3(carry_z=-99.0)         # a broken L2 that carries everything
    b = ST.rw_bars("1h", 900, seed=41)
    r = ST.s1_from_tables({("XAUUSD", "1h"): (b, ST.null_table(b, default_model()))}, reps=6, seed_key="s1-bad",
                          hier=hier, confirm=confirm, stats=stats, hh="h")
    assert not r.passed and r.metrics["carry_rate"] == 1.0


def test_s2_plumbing_detects_large_plants_and_leaves_the_15m_neighbour() -> None:
    hier, _, stats = _fake_lane3()
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
