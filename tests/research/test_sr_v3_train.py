"""The sr-v3 history trainer (goldbot/research/sr_v3_train.py): folds with a gap, past-only normalisation, decision-time
cost per fold, Stage 0 reads no label, the learner reports, deterministic v0 artifacts, rebuild from the run log, the
sealed-store refusal, and train/serve parity of the L-cw export."""
from __future__ import annotations

import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.base import load_record_json
from goldbot.research import decision_cost as DC
from goldbot.research import dense_labels as D
from goldbot.research import learners as L
from goldbot.research import sr_v3 as V
from goldbot.research import sr_v3_serve as S
from goldbot.research import sr_v3_train as T
from goldbot.research.self_retrain import FEATURES, INPUTS, LogitModel, design, design_row

ROOT = Path(__file__).resolve().parents[2]
RHO_FILE = ROOT / "docs" / "research" / "rho-measurement-2026-11.json"


def _frame(inst: str, year: int, *, tf: str = "4h", seed: int = 0, signal: float = 0.25) -> pd.DataFrame:
    rng = np.random.default_rng(zlib.crc32(f"{inst}|{year}|{seed}".encode()))     # stable across processes
    days = pd.bdate_range(f"{year}-01-01", f"{year}-12-31", tz="UTC")
    bars = pd.DatetimeIndex([d + pd.Timedelta(hours=h) for d in days for h in (2, 10, 18)])
    n = len(bars)
    dec = np.repeat(bars, 2)
    side = np.tile([1, -1], n)
    X = rng.normal(size=(2 * n, len(V.P30)))
    X[:, 0] = side
    gross = signal * X[:, 1] + rng.normal(0, 1.1, 2 * n)
    base = pd.DataFrame({"decision_utc": dec, "side": side, "max_hold_s": float(V.H_S[tf]), "entry": 1_500.0 + year,
                         "risk_usd": 12.0, "cost_rt_bp": 0.575})
    parts = T.cost_parts(base)
    full = parts["nights_full"].to_numpy()
    f = pd.DataFrame({"instrument": inst, "tf": tf, "phase": 0, "decision_utc": dec,
                      "horizon_end_utc": dec + pd.Timedelta(seconds=V.H_S[tf]), "side": side, "gross": gross,
                      "t0_gross": gross * 0.8 + rng.normal(0, 0.3, 2 * n), "stop": rng.random(2 * n) < 0.4,
                      "nights_held": np.floor(full * rng.random(2 * n)), "w": rng.uniform(0.1, 0.3, 2 * n),
                      "entry": 1_500.0 + year, "base_dist": 12.0, "cost_rt_bp": 0.575})
    f = pd.concat([f, parts[["cost_a", "cost_b", "cost_c", "nights_full"]]], axis=1)
    for j, c in enumerate(V.P30):
        if c != "side":
            f[c] = X[:, j]
    return f[list(T.ROW_COLS)]


ELIGIBLE = {"gate_eligible": True, "gate_block": []}


def _data(insts: tuple[str, ...] = ("XAUUSD", "EURUSD"), *, readable: bool = True, **kw: object) -> T.FrameData:
    """Fixture frames. `readable`: a gate-eligible read with a ub95 cost version (read-statistic mode allowed);
    otherwise FrameData's defaults (no gate status, W2 point costs): fit-only."""
    frames = {(i, "4h", y): _frame(i, y, **kw) for i in insts for y in V.LEARN_YEARS}  # type: ignore[arg-type]
    if not readable:
        return T.FrameData(frames)
    return T.FrameData(frames, gate=ELIGIBLE, cost_version="fixture-ub95")


def _plan(cols: tuple[str, ...] = V.CORE_6, members: tuple[str, ...] = ("XAUUSD", "EURUSD")) -> T.UnitPlan:
    return T.UnitPlan(unit="4h", tfs=("4h",), members=members, lcw_columns=cols, first_oof=2012, lam_a=820.0,
                      design_effect=1.2, stage0={"level": "L3", "judged": True})


# ---------------------------------------------------------------------------------------------- folds
def test_a_training_row_ends_before_the_test_year_minus_the_gap() -> None:
    g = T.gap("4h")
    assert g == pd.Timedelta(days=5)
    start = pd.Timestamp("2014-01-01", tz="UTC")
    f = pd.DataFrame({"tf": "4h", "horizon_end_utc": [start - g - pd.Timedelta(seconds=1), start - g,
                                                      start - pd.Timedelta(days=1)]})
    assert T.train_mask(f, 2014).tolist() == [True, False, False]
    assert T.gap("1m") == pd.Timedelta(days=1)


def test_fold_moments_hold_only_rows_labelled_before_the_gap() -> None:
    data, plan = _data(), _plan()
    col = T.collect(data, plan, seed=T.SEEDS[0])
    for Y in plan.oof_years:
        want = sum(int(T.train_mask(data.frame(i, "4h", y), Y).sum()) for i in plan.members for y in V.LEARN_YEARS)
        assert sum(m.n for m in col.lcw[Y].values()) == want
    end = T.WN.SPANS[T.WN.Window.LEARN][1]
    v0 = sum(int((data.frame(i, "4h", y)["horizon_end_utc"] <= end).sum()) for i in plan.members for y in V.LEARN_YEARS)
    assert sum(m.n for m in col.lcw[2018].values()) == v0


def test_normalisation_uses_past_rows_only() -> None:
    plan = _plan()
    a = T.collect(_data(), plan, seed=T.SEEDS[0])
    late = dict(_data().frames)
    for i in plan.members:
        f = late[(i, "4h", 2017)].copy()
        f[list(V.P30[1:])] = f[list(V.P30[1:])] * 3 + 5
        late[(i, "4h", 2017)] = f
    b = T.collect(T.FrameData(late), plan, seed=T.SEEDS[0])
    for Y in (2012, 2015, 2017):
        fa = L.lcw_from_moments(a.lcw[Y], a.impute[:6], plan.lcw_columns, lam=540, lam_a=820, design_effect=1.2)
        fb = L.lcw_from_moments(b.lcw[Y], b.impute[:6], plan.lcw_columns, lam=540, lam_a=820, design_effect=1.2)
        assert L.artifact_sha(fa) == L.artifact_sha(fb)
    assert np.array_equal(a.impute, b.impute)


def test_cost_per_fold_is_decision_cost_cost_x_with_that_folds_priors() -> None:
    f = _frame("XAUUSD", 2013)
    labels = f.assign(arm="XAUUSD:4h:T2", max_hold_s=float(V.H_S["4h"]), risk_usd=12.0, entry=1_500.0 + 2013,
                      label_exit_utc=f["decision_utc"] + pd.Timedelta(hours=30),
                      barrier_hit=np.where(f["stop"], "stop", "time"), template="T2")
    acc = T.PriorAcc()
    acc.add(f.assign(nights_held=DC.nights_held(labels)))
    pri = acc.priors(pd.Timestamp("2015-01-01", tz="UTC"))
    ref = DC.priors(labels, before=pd.Timestamp("2015-01-01", tz="UTC"))
    assert pri.p_stop == pytest.approx(ref.p_stop) and pri.night_share == pytest.approx(ref.night_share)
    want = DC.cost_x(labels, ref)
    assert np.allclose(T.cost_of(f, pri), want)
    rows = labels.assign(gross_r_raw=f["gross"], instrument="XAUUSD", tf="4h")
    assert np.allclose(D.net_r(rows, ref), O_clip(f["gross"].to_numpy() - T.cost_of(f, pri)))


def O_clip(x: np.ndarray) -> np.ndarray:
    return np.clip(x, -10, 10)


# ---------------------------------------------------------------------------------------------- Stage 0
def test_stage0_reads_no_label_column() -> None:
    rng = np.random.default_rng(3)
    dec = pd.date_range("2010-01-04", "2011-12-30", freq="4h", tz="UTC")
    ev = pd.DataFrame({"instrument": "XAUUSD", "tf": "4h", "phase": 0, "decision_utc": dec, "usable": True,
                       "u": rng.uniform(0.1, 0.3, len(dec))})
    with_labels = ev.assign(gross_r_raw=rng.normal(size=len(dec)), barrier_hit="stop")
    a = T.stage0_counts(ev, years=(2010, 2011))
    b = T.stage0_counts(with_labels.drop(columns=["gross_r_raw"]), years=(2010, 2011))
    c = T.stage0_counts(with_labels, years=(2010, 2011))
    assert a == b == c
    s0 = T.stage0("4h", ("4h",), {"XAUUSD": ev, "EURUSD": ev.assign(instrument="EURUSD")}, rho_file=RHO_FILE)
    s1 = T.stage0("4h", ("4h",), {"XAUUSD": with_labels, "EURUSD": with_labels.assign(instrument="EURUSD")},
                  rho_file=RHO_FILE)
    assert s0.model_dump() == s1.model_dump()
    assert s0.rho["gold"].source_sha256 == V.file_sha256(RHO_FILE)


REV8_CORE12 = ("side", "tsmom_score", "dist_vwap48_atr", "rv_ratio", "er_30", "bb_z_20", "ret_16", "ret_96",
               "donchian_pos_20", "adx14", "compression_8_96", "min_since_london_open")


def _sequenced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every precondition of the quant sequencing as it will stand on main: A4 sha, X6 merged instant, rev 8 order."""
    from goldbot.features import price_action_x6 as X6
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset({"a" * 64}))
    monkeypatch.setattr(X6, "X6_MERGED_UTC", "2026-11-26T11:30:53Z")
    monkeypatch.setattr(D, "CORE12", REV8_CORE12)


def test_the_trainer_core_order_is_card_rev_8_with_rv_ratio_at_position_4() -> None:
    assert V.CORE_12 == REV8_CORE12 and V.CORE_6 == REV8_CORE12[:6]
    assert "atr_ratio_14_100" in V.P30_REST and "rv_ratio" not in V.P30_REST


def test_no_label_or_stage0_read_while_x6_merged_utc_is_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Quant sequencing on #482 (binding): no sr-v3 label value or Stage 0 statistic until #482, #484, rev 8 and
    X6_MERGED_UTC are all on main. The store itself refuses, so no caller can read around the CLI."""
    from goldbot.features import price_action_x6 as X6

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("a label was read before the sequencing held")
    _sequenced(monkeypatch)
    monkeypatch.setattr(X6, "X6_MERGED_UTC", None)
    monkeypatch.setattr(D, "load_labels", boom)
    monkeypatch.setattr(D, "skeleton", boom)
    store = T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=RHO_FILE)
    monkeypatch.setattr(T.StoreData, "keys", lambda self, *a: ("k" * 64, "f" * 64))
    with pytest.raises(T.SequencingRefused, match="X6_MERGED_UTC"):
        store.frame("XAUUSD", "4h", 2013)
    with pytest.raises(T.SequencingRefused, match="X6_MERGED_UTC"):
        store.events("XAUUSD", "4h", (2010, 2011))


def test_each_missing_sequencing_step_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.features import price_action_x6 as X6
    _sequenced(monkeypatch)
    assert T.sequencing_gaps() == []
    monkeypatch.setattr(D, "CORE12", (*REV8_CORE12[:3], "atr_ratio_14_100", *REV8_CORE12[4:]))
    assert any("rev 8" in g for g in T.sequencing_gaps())
    _sequenced(monkeypatch)
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset())
    assert T.sequencing_gaps() == ["ADR 0085 A4 tiers sha (PR #482)"]
    _sequenced(monkeypatch)
    monkeypatch.setattr(X6, "X6_MERGED_UTC", None)
    assert len(T.sequencing_gaps()) == 1 and "X6_MERGED_UTC" in T.sequencing_gaps()[0]


def test_store_stage0_never_calls_load_labels(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sequenced(monkeypatch)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("Stage 0 read a label")
    monkeypatch.setattr(D, "load_labels", boom)
    store = T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=RHO_FILE)
    assert store.events("XAUUSD", "4h", (2010, 2011)).empty


def test_store_frame_goes_through_load_labels_on_the_learn_window(monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    tiers = tmp_path / "tiers.csv"
    tiers.write_text("key,month,tier\nXAUUSD,2013-01,A\n")

    def sealed(*a: object, **k: object) -> None:
        seen.update(k)
        raise D.SealedStore("sealed")
    _sequenced(monkeypatch)
    monkeypatch.setattr(D, "load_labels", sealed)
    store = T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tiers)
    monkeypatch.setattr(T.StoreData, "keys", lambda self, *a: ("k" * 64, "f" * 64))
    with pytest.raises(D.SealedStore):
        store.frame("XAUUSD", "4h", 2013)
    assert seen["window"] == T.WN.SPANS[T.WN.Window.LEARN] and seen["purpose"] == "train"
    assert seen["columns"] == V.P30 and seen.get("booked_read") is None
    assert seen["years"] == list(V.LEARN_YEARS), "one ledgered load per instrument x tf for every learn year (#482)"
    assert seen["procedure"] == "sr-v3", "the consumption row names sr-v3, so the X6 legitimacy guard sees it"


# ---------------------------------------------------------------------------------------------- the unit fit
@pytest.fixture(scope="module")
def fitted() -> tuple[T.FrameData, T.UnitPlan, T.UnitResult]:
    data, plan = _data(), _plan()
    col = T.collect(data, plan, seed=T.SEEDS[0])
    hb = T.hb_from_plans(data, [plan], seed=T.SEEDS[0])
    return data, plan, T.fit_unit(data, plan, hb_by_fold=hb, col=col, harness_rows=20_000, read=True)


def test_every_built_learner_is_reported_against_the_gbt_baseline(fitted: tuple) -> None:
    _, _, res = fitted
    r = res.report
    assert set(r["learners"]) == {"L-cw", "G-cw", "HB", "AVG"}
    for name in ("L-cw", "HB", "AVG"):
        d = r["learners"][name]["d_vs_gcw"]
        assert d["n"] > 0 and d["lo90"] <= d["d_per_day"] <= d["hi90"]
    assert {"L-cw vs G-cw", "L-cw vs HB", "G-cw vs AVG"} <= set(r["paired_d"])
    assert r["learners"]["L-cw"]["read"]["s"] > 0, "the planted tsmom signal is found out of fold"
    assert set(r["baselines"]["buy_and_hold"]) == {"XAUUSD", "EURUSD"}
    assert r["baselines"]["buy_and_hold"]["XAUUSD"]["pct"] < 0.01
    assert r["learners"]["L-cw"]["twin_s_expectation"] is not None


def test_report_carries_seed_stability_shift_canaries_and_the_shuffle_check(fitted: tuple) -> None:
    r = fitted[2].report
    for name in ("L-cw", "G-cw", "HB", "AVG"):
        assert len(r["seed_stability"][name]["detail"]["seeds"]) == len(T.SEEDS) >= 5
    assert r["seed_stability"]["L-cw"]["detail"]["seed_invariant"]
    h = r["harness"]
    assert set(h["canaries"]) == {"L-cw", "HB", "G-cw"} and len(h["canaries"]["L-cw"]) == 3
    assert all(set(c) == {"delta", "s_model", "s_oracle", "s_null", "recovered"} for c in h["canaries"]["L-cw"])
    ps = h["parameter_shift"]
    assert ps["L-cw"]["check"] == "param_shift" and len(ps["L-cw"]["detail"]["grid"]) == 8
    assert {g["param"] for g in ps["G-cw"]["detail"]["grid"]} == {"learning_rate", "num_iterations", "min_child_share"}
    assert {g["shift"] for g in ps["HB"]["detail"]["grid"]} == {-0.2, -0.1, 0.1, 0.2}
    assert all("auc_in_band" in v for v in h["shuffle"].values())
    assert len(h["seed_s"]["G-cw"]) == 5 and h["learning_curve_lcw"][-1]["n"] == h["train_rows"]
    assert "XAUUSD" in r["levels"] and set(r["levels"]["XAUUSD"]) == {"shrunk", "unshrunk"}
    assert 0 <= r["v0_keep_share_2015_17"] <= 1


def test_v0_artifacts_are_deterministic_and_load(fitted: tuple) -> None:
    data, plan, res = fitted
    again = T.fit_unit(data, plan, hb_by_fold=None, seeds=T.SEEDS[:1], with_harness=False)
    for k in ("L-cw", "B8", "G-cw", "D-40"):
        assert res.report["artifacts"][k] == again.report["artifacts"][k]
        assert isinstance(L.load_artifact(res.artifacts[k]), (L.LcwArtifact, L.GcwArtifact, L.DenseArtifact))
    assert "AVG" in res.artifacts and "HB" in res.artifacts


def test_every_v0_artifact_is_recorded_with_its_quality_and_rebuilds_byte_exactly(fitted: tuple,
                                                                                   tmp_path: Path) -> None:
    from goldbot.research import run_manifest as RM
    data, plan, res = fitted
    frames = T.frames_file(data, tmp_path / "frames.parquet")
    mans = T.record_unit(res, plan, seeds=T.SEEDS, source={"kind": "frames"}, data_version=data.manifest()[
        "frames_sha256"], data_root=tmp_path, data_files=[frames.name], hb_plans=[plan], root=tmp_path / "manifests")
    assert set(mans) == set(res.artifacts)
    g = mans["G-cw"]
    assert g.family == "C" and g.builder == T.BUILDER and g.data_files[frames.name]
    assert g.manifest_key == T.MANIFEST_KEY and g.seeds == list(T.SEEDS) and len(g.seeds) == 5 and g.served == "seed0"
    assert g.unit == plan.unit and g.fold_keys[-1] == "v0:2010-2017"
    assert {q.check for q in g.quality} == {"seed_stability", "param_shift"}
    # beats_baseline books a registry trial (K +1): it is read at promotion, never by the trainer
    assert not [q for m in mans.values() for q in m.quality if q.check == "beats_baseline"]
    lcw = mans["L-cw"]
    assert lcw.train_end <= lcw.label_end_max <= pd.Timestamp("2018-01-01", tz="UTC")
    assert RM.load(g.manifest_id, tmp_path / "manifests") == g
    spec = RM.BuildSpec(data_files={frames.name: str(frames)}, settings=g.settings, seeds=g.seeds,
                        train_end=g.train_end, scaler_fit_end=g.scaler_fit_end)
    for name in ("G-cw", "HB"):
        m = mans[name]
        got = RM.build(T.BUILDER, spec.model_copy(update={"settings": m.settings}))
        assert RM.sha256_bytes(got) == m.artifact_sha256, name


def test_rebuild_from_changed_data_gives_other_bytes(fitted: tuple, tmp_path: Path) -> None:
    data, plan, res = fitted
    bumped = dict(data.frames)
    bumped[("XAUUSD", "4h", 2016)] = bumped[("XAUUSD", "4h", 2016)].assign(gross=lambda f: f["gross"] + 0.5)
    other = T.fit_unit(T.FrameData(bumped), plan, hb_by_fold=None, seeds=T.SEEDS[:1], with_harness=False)
    assert other.report["artifacts"]["L-cw"] != res.report["artifacts"]["L-cw"]


def test_intercept_only_unit_serves_level_minus_cost() -> None:
    data, plan = _data(), _plan(cols=())
    res = T.fit_unit(data, plan, hb_by_fold=None, seeds=T.SEEDS[:1], with_harness=False)
    a = L.load_artifact(res.artifacts["L-cw"])
    assert isinstance(a, L.LcwArtifact) and a.columns == () and set(a.levels) == {"XAUUSD", "EURUSD"}
    assert "intercept-only" in T.report_md(res.report)


# ---------------------------------------------------------------------------------------------- serving parity
def test_lcw_export_loads_in_shadow_scoring_and_scores_the_engine_vector_identically() -> None:
    data, plan = _data(), _plan()
    f = pd.concat([data.frame(i, "4h", y) for i in plan.members for y in (2012, 2013)], ignore_index=True)
    td = L.TrainData(X=f[list(plan.lcw_columns)].to_numpy(float), columns=plan.lcw_columns,
                     gross=f["gross"].to_numpy(float), net=f["gross"].to_numpy(float), cost=np.zeros(len(f)),
                     w=f["w"].to_numpy(float), instrument=f["instrument"].to_numpy(), day=np.zeros(len(f)),
                     tf=f["tf"].to_numpy(), design_effect=1.0)
    a = L.fit_lcw(td, lam=540, lam_a=820)
    lm = load_record_json(LogitModel, S.to_logit_model(a, "XAUUSD").model_dump_json(), source="test")
    rng = np.random.default_rng(9)
    for _ in range(25):
        feats: dict[str, float | None] = {c: float(rng.normal()) for c in FEATURES}
        feats["atr_ratio_14_100"] = None
        side = int(rng.choice([1, -1]))
        x = design_row(feats, side)
        g = S.gross_row(lm, x)
        assert lm.score_row(x) == 1.0 / (1.0 + np.exp(-g))
        p30 = S.p30_row(x)
        lc = [V.P30.index(c) for c in plan.lcw_columns]
        assert g == pytest.approx(a.value_row(p30[lc], "XAUUSD"), abs=1e-12)
        row = pd.DataFrame([{**feats, "side": side}])
        assert np.allclose(design(row)[list(INPUTS)].to_numpy(float)[0], x, equal_nan=True)
    assert lm.keep(pd.DataFrame([{c: 0.0 for c in INPUTS}]))[0], "tau 0: the sr-v2 rule keeps (never refuses)"


def test_hook_needs_are_listed_for_the_trading_safety_review() -> None:
    assert any("GATE_SPECS" in n for n in S.ENGINE_HOOK_NEEDS)
    assert any("fails open" in n for n in S.ENGINE_HOOK_NEEDS)


def test_the_cli_refuses_to_run_until_the_sequencing_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    from goldbot.features import price_action_x6 as X6
    spec = importlib.util.spec_from_file_location("train_sr_v3", ROOT / "scripts" / "research" / "train_sr_v3.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _sequenced(monkeypatch)
    monkeypatch.setattr(X6, "X6_MERGED_UTC", None)
    with pytest.raises(SystemExit, match="X6_MERGED_UTC"):
        mod.refuse_before_x6()
    _sequenced(monkeypatch)
    mod.refuse_before_x6()                              # every precondition on main: no refusal


# ---------------------------------------------------------------------------------------------- H2: fit-only
READ_KEYS = ("learners", "paired_d", "baselines", "seed_stability", "harness", "levels", "v0_keep_share_2015_17")


def test_fit_only_is_the_default_and_writes_artifacts_and_sealed_scores_but_no_read_statistic(tmp_path: Path) -> None:
    data, plan = _data(readable=False), _plan()
    res = T.fit_unit(data, plan, hb_by_fold=None, seeds=T.SEEDS[:2])
    r = res.report
    assert r["mode"] == "fit-only"
    assert not [k for k in READ_KEYS if k in r], "fit-only computes no S, kept mean, S@2x, D or other read statistic"
    assert {"L-cw", "B8", "G-cw", "D-40"} <= set(res.artifacts)
    assert not {"gross", "net", "net2", "t0_gross", "entry", "base_dist"} & set(res.oof.columns), "scores only"
    assert {"v_L-cw", "k_L-cw", "v_G-cw", "cost", "decision_utc", "instrument"} <= set(res.oof.columns)
    assert r["oof_scores_sha256"] == T.scores_sha(res.oof)
    assert r["gate_eligible"] is False and r["gate_block"]
    d = T.write_unit(res, tmp_path)
    assert not (d / "oof.parquet").exists() and (d / "oof_scores.parquet").exists()
    assert T.scores_sha(pd.read_parquet(d / "oof_scores.parquet")) == r["oof_scores_sha256"]
    for text in ((d / "report.json").read_text(), (d / "report.md").read_text()):
        assert '"s"' not in text and "kept mean" not in text and "D/day" not in text


def test_read_statistic_mode_refuses_unless_the_label_read_is_gate_eligible() -> None:
    data, plan = _data(readable=False), _plan()
    blocked = T.FrameData(data.frames, gate={"gate_eligible": False, "gate_block": ["cost_version w2-point"]},
                          cost_version="fixture-ub95")
    with pytest.raises(T.ReadRefused, match="w2-point"):
        T.fit_unit(blocked, plan, hb_by_fold=None, seeds=T.SEEDS[:1], with_harness=False, read=True)
    unknown = T.FrameData(data.frames, cost_version="fixture-ub95")
    with pytest.raises(T.ReadRefused, match="gate"):
        T.fit_unit(unknown, plan, hb_by_fold=None, seeds=T.SEEDS[:1], with_harness=False, read=True)


def test_gate_status_and_mode_are_written_into_every_manifest(tmp_path: Path) -> None:
    data, plan = _data(readable=False), _plan()
    res = T.fit_unit(data, plan, hb_by_fold=None, seeds=T.SEEDS)
    frames = T.frames_file(data, tmp_path / "frames.parquet")
    mans = T.record_unit(res, plan, seeds=T.SEEDS, source={"kind": "frames"}, data_version="x", data_root=tmp_path,
                         data_files=[frames.name], hb_plans=[], root=tmp_path / "m")
    assert set(mans) == set(res.artifacts)
    for m in mans.values():
        assert m.results["mode"] == "fit-only" and m.results["gate_eligible"] is False and m.results["gate_block"]
        assert m.results["oof_scores_sha256"] == res.report["oof_scores_sha256"]
        assert "s" not in m.results and not m.quality, "no read statistic and no net-based verdict in fit-only"


def test_store_keeps_load_labels_gate_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from goldbot.research import feature_store as F
    tiers = tmp_path / "tiers.csv"
    tiers.write_text("key,month,tier\nXAUUSD,2013-01,A\n")
    meta = {"keys": {}, "tiers_sha256": "t", "rows": 0, "gate_eligible": False,
            "gate_block": ["h_ref = H: the exit templates' h_ref is not frozen (D-4)"]}
    empty = pd.DataFrame(columns=["template", "phase", "decision_utc", "side", "gross_r_raw"])
    monkeypatch.setattr(D, "load_labels", lambda *a, **k: (empty, meta))
    monkeypatch.setattr(F, "load", lambda *a, **k: (pd.DataFrame(columns=["phase", "decision_utc"]), {"keys": {}}))
    store = T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tiers, ledger=tmp_path / "l.jsonl")
    monkeypatch.setattr(T.StoreData, "keys", lambda self, *a: ("k" * 64, "f" * 64))
    assert store.gate_status()["gate_eligible"] is False, "no label read yet: not eligible"
    store.frame("XAUUSD", "4h", 2013)
    g = store.gate_status()
    assert g["gate_eligible"] is False and "h_ref" in g["gate_block"][0]
    monkeypatch.setattr(D, "load_labels", lambda *a, **k: (empty, {**meta, "gate_eligible": True, "gate_block": []}))
    store.frame("EURUSD", "4h", 2013)
    assert store.gate_status()["gate_eligible"] is False, "one blocked load blocks the unit"


# ---------------------------------------------------------------------------------------------- H4: one ledger
def _cli() -> object:
    import importlib.util
    spec = importlib.util.spec_from_file_location("train_sr_v3", ROOT / "scripts" / "research" / "train_sr_v3.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_store_and_cli_default_ledger_is_the_one_the_x6_guard_reads(monkeypatch: pytest.MonkeyPatch,
                                                                         tmp_path: Path) -> None:
    """Quant H4 on #488: a worktree has no raw/ or state/ of its own; the read-once / consumption ledger must be the
    one beside the research registry (`default_registry().with_name(READS_LEDGER)`), which the X6 legitimacy guard
    reads (tests/test_price_action_x6.py `_ledgers()[0]`), never <worktree>/state/."""
    from goldbot.research.catalogue.run import default_registry
    from tests.test_price_action_x6 import _ledgers
    monkeypatch.delenv("GOLDBOT_REGISTRY", raising=False)
    monkeypatch.delenv("GOLDBOT_READ_LEDGERS", raising=False)
    want = default_registry().with_name(T.WN.READS_LEDGER)
    assert want == _ledgers()[0]
    assert T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tmp_path).ledger == want
    assert Path(_cli().parser().parse_args([]).ledger) == want   # type: ignore[attr-defined]
    monkeypatch.setenv("GOLDBOT_REGISTRY", str(tmp_path / "state" / "research_registry.jsonl"))
    assert T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tmp_path).ledger == _ledgers()[0]
