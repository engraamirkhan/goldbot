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
    assert g == max(pd.Timedelta(days=5), D.input_lookback("4h", V.P30))
    start = pd.Timestamp("2014-01-01", tz="UTC")
    f = pd.DataFrame({"tf": "4h", "horizon_end_utc": [start - g - pd.Timedelta(seconds=1), start - g,
                                                      start - pd.Timedelta(days=1)]})
    assert T.train_mask(f, 2014).tolist() == [True, False, False]
    assert T.gap("1m") == max(pd.Timedelta(days=1), D.input_lookback("1m", V.P30))


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
    for name in ("G-cw", "AVG"):
        assert len(r["seed_stability"][name]["detail"]["seeds"]) == len(T.SEEDS) >= 5
    for name in ("L-cw", "HB"):        # quant M4 on #488: closed forms have no seed: not applicable, never "passed"
        assert r["seed_stability"][name]["passed"] is None
        assert "not applicable" in r["seed_stability"][name]["detail"]
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


# ---------------------------------------------------------------------------------------------- H5: one run record
class _FixtureStore(T.FrameData):
    """A store stand-in for the CLI phases: FrameData's rows, no Stage 0 events (Stage 0 is stubbed)."""
    used: dict[str, object] = {}

    def events(self, instrument: str, tf: str, years: object) -> pd.DataFrame:
        return pd.DataFrame()


def test_cli_phases_record_one_manifest_per_artifact_with_the_collected_train_end(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Quant H5 on #488: phase 1 persists train_end and label_end_max (phase 3 recorded the learn end instead), and
    each artifact's manifest is recorded before the unit's outputs are written (report.json names them)."""
    import argparse
    import json
    from types import SimpleNamespace

    from goldbot.research import run_manifest as RM
    cli = _cli()
    data, plan = _data(readable=False), _plan()
    store = _FixtureStore(data.frames)
    s0 = SimpleNamespace(status="fit", level="L3", body="pooled", model_dump=lambda mode="json": {"level": "L3"})
    monkeypatch.setattr(cli, "_store", lambda a: store)
    monkeypatch.setattr(T, "stage0", lambda *a, **k: s0)
    monkeypatch.setattr(T, "plan_from_stage0", lambda s: plan)
    tiers = tmp_path / "tiers.csv"
    tiers.write_text("key,month,tier\nXAUUSD,2013-01,A\n")
    a = argparse.Namespace(units="4h", members="XAUUSD,EURUSD", labels=str(tmp_path), features=str(tmp_path),
                           tiers=str(tiers), rho=str(RHO_FILE), ledger=str(tmp_path / "l.jsonl"), out=str(tmp_path / "o"),
                           stage0_only=False, read=False, cost_manifest=None)
    cli.phase1("4h", a)                                                                   # type: ignore[attr-defined]
    col = T.collect(data, plan, seed=T.SEEDS[0])
    saved = json.loads((tmp_path / "o" / "4h" / "collected.json").read_text())
    assert pd.Timestamp(saved["train_end"]) == col.train_end
    assert pd.Timestamp(saved["label_end_max"]) == col.label_end_max
    cli.phase2(["4h"], a)                                                                 # type: ignore[attr-defined]
    out = cli.phase3("4h", a)                                                             # type: ignore[attr-defined]
    root = tmp_path / "o" / "manifests"
    files = sorted(root.glob("*.json"))
    report = json.loads((tmp_path / "o" / "4h" / "report.json").read_text())
    assert len(files) == len(out["artifacts"]) == len(report["manifests"])
    assert {f.stem for f in files} == set(report["manifests"].values())
    for mid in report["manifests"].values():
        m = RM.load(mid, root)
        assert m.train_end == col.train_end and m.label_end_max == col.label_end_max


def test_store_version_covers_the_weights_and_rho_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Quant H5 on #488: the data version a manifest records names every input a rebuild reads: the uniqueness
    weights and the rho (D-12) file as well as the label keys and tiers."""
    w = tmp_path / "_weights" / "XAUUSD"
    w.mkdir(parents=True)
    (w / "4h.parquet").write_bytes(b"one")
    rho, tiers = tmp_path / "rho.json", tmp_path / "tiers.csv"
    rho.write_text("{}")
    tiers.write_text("key,month,tier\n")
    monkeypatch.setattr(T.StoreData, "keys", lambda self, *a: ("k" * 64, "f" * 64))
    store = T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tiers, rho=rho, ledger=tmp_path / "l.jsonl")
    v0 = store.version(["XAUUSD"], ["4h"], (0,))
    (w / "4h.parquet").write_bytes(b"two")
    v1 = store.version(["XAUUSD"], ["4h"], (0,))
    rho.write_text('{"x": 1}')
    v2 = store.version(["XAUUSD"], ["4h"], (0,))
    assert len({v0, v1, v2}) == 3, "a changed weights file or rho file changes the data version"


# ---------------------------------------------------------------------------------------------- M4: seeds differ
def test_gcw_seeds_fit_different_resamples_even_below_the_tree_row_cap() -> None:
    """Quant M4 on #488: below MAX_TREE_ROWS (pi = 1) every seed fitted the same rows, so the 5 "seeds" were one fit
    and seed stability passed by construction. Each seed now fits its own 90 % seed-hash subsample."""
    data, plan = _data(), _plan()
    col = T.collect(data, plan, seed=T.SEEDS[0])
    assert col.pi == 1.0
    fm = T.fit_fold(2015, col, plan, hb=None, seeds=T.SEEDS[:3], dense_by_year={})
    te = data.frame("XAUUSD", "4h", 2015)
    X = T._with_dense(te, fm.dense, T.cost_of(te, fm.priors))[list(T.gcw_columns())].to_numpy(float)
    vals = [a.value(X) for a in fm.gcw.values()]
    assert not np.allclose(vals[0], vals[1]) and not np.allclose(vals[1], vals[2]), "each seed is its own resample"


def test_closed_form_learners_record_no_seed_stability_verdict(fitted: tuple, tmp_path: Path) -> None:
    data, plan, res = fitted
    frames = T.frames_file(data, tmp_path / "frames.parquet")
    mans = T.record_unit(res, plan, seeds=T.SEEDS, source={"kind": "frames"}, data_version="x", data_root=tmp_path,
                         data_files=[frames.name], hb_plans=[plan], root=tmp_path / "m")
    for name in ("L-cw", "HB", "B8"):
        assert "seed_stability" not in {q.check for q in mans[name].quality}
    assert "seed_stability" in {q.check for q in mans["G-cw"].quality}


# ---------------------------------------------------------------------------------------------- H1: cost of record
def _cost_files(tmp_path: Path, *, gold_asia_icm: float = 1.4) -> Path:
    """A cost manifest like the data team's (~/tmp/sr-v3-trainsets/manifest.json "costs"): per instrument x broker x
    session x tf rt_bp = max(W2, ecr ub95), and a monthly swap table per instrument."""
    import hashlib
    import json
    cells = [{"instrument": i, "broker": b, "session": s, "tf": "4h", "rt_bp": v}
             for i in ("XAUUSD", "EURUSD") for b in ("icm", "vantage") for s in ("asia", "london", "newyork")
             for v in [0.9 if b == "vantage" else (gold_asia_icm if (i, s) == ("XAUUSD", "asia") else 0.7)]]
    cells.append({"instrument": "XAUUSD", "broker": "icm", "session": "london", "tf": "1h", "rt_bp": 0.3})
    cells.append({"instrument": "XAUUSD", "broker": "vantage", "session": "london", "tf": "1h", "rt_bp": 0.2})
    months = [f"{y}-{m:02d}" for y in range(2009, 2019) for m in range(1, 13)]
    swap = [{"instrument": i, "month": mo, "long_per_night": lp, "short_per_night": sp}
            for i, lp, sp in (("XAUUSD", -0.000154, 0.0), ("EURUSD", -0.00004, 0.00001)) for mo in months]
    d = tmp_path / "costs"
    d.mkdir(exist_ok=True)
    pd.DataFrame(cells).to_parquet(d / "cost_table.parquet", index=False)
    pd.DataFrame(swap).to_parquet(d / "swap_table.parquet", index=False)

    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    man = {"costs": {"cost_version": "srv3-cost-fixture", "files": {
        "cost_table": str(d / "cost_table.parquet"), "cost_table_sha256": sha(d / "cost_table.parquet"),
        "swap_table": str(d / "swap_table.parquet"), "swap_table_sha256": sha(d / "swap_table.parquet")}}}
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    return tmp_path / "manifest.json"


def _cost_rows(inst: str = "XAUUSD", tf: str = "4h", hour: int = 2, n: int = 4) -> pd.DataFrame:
    dec = pd.DatetimeIndex([pd.Timestamp(f"2013-03-0{4 + i} {hour:02d}:00", tz="UTC") for i in range(n)])
    return pd.DataFrame({"instrument": inst, "tf": tf, "decision_utc": dec, "side": [1, -1] * (n // 2),
                         "max_hold_s": float(V.H_S[tf]), "entry": 1_500.0, "risk_usd": 12.0, "cost_rt_bp": DC.RT_BP})


def test_a_ub95_cell_above_w2_is_charged_at_ub95_and_the_worst_broker(tmp_path: Path) -> None:
    """Quant H1 on #488: rt_bp per row from the per-cell table, max(W2, ecr ub95) by broker x session x tf; the run
    charges the worse broker of the cell (conservative: the account is not known at history time)."""
    table = T.CostTable.from_manifest(_cost_files(tmp_path))
    rows = _cost_rows(hour=2)                                    # 02:00 UTC: asia
    k = DC.row_k(rows)
    parts = T.cost_parts(rows, table)
    assert np.allclose(parts["cost_rt_bp"], 1.4) and np.allclose(parts["cost_a"], k * 1.4)
    assert (parts["cost_a"].to_numpy() > k * DC.RT_BP).all()
    assert np.allclose(T.cost_parts(_cost_rows(hour=10), table)["cost_rt_bp"], 0.9), "london: vantage 0.9 > icm 0.7"
    assert np.allclose(T.cost_parts(_cost_rows(tf="1h", hour=10), table)["cost_rt_bp"], DC.RT_BP), "W2 floor"
    assert table.version == "srv3-cost-fixture"


def test_rows_with_no_cost_cell_are_refused(tmp_path: Path) -> None:
    table = T.CostTable.from_manifest(_cost_files(tmp_path))
    with pytest.raises(T.CostCellMissing, match="XAUUSD:asia:30m"):
        T.cost_parts(_cost_rows(tf="30m"), table)
    with pytest.raises(T.CostCellMissing, match="USDJPY"):
        T.cost_parts(_cost_rows(inst="USDJPY"), table)
    late = _cost_rows().assign(decision_utc=lambda f: f["decision_utc"] + pd.Timedelta(days=3_000))
    with pytest.raises(T.CostCellMissing, match="swap"):
        T.cost_parts(late, table)


def test_swap_is_charged_per_instrument_from_the_table(tmp_path: Path) -> None:
    table = T.CostTable.from_manifest(_cost_files(tmp_path))
    rows = _cost_rows(inst="EURUSD", hour=10)
    parts = T.cost_parts(rows, table)
    full = DC.nights_full_hold(rows)
    assert (full > 0).any()
    want = full * DC.row_k(rows) * 1e4 * np.where(rows["side"] > 0, 0.00004, 0.00001)
    assert np.allclose(parts["cost_c"], want), "EURUSD's own swap, never gold's"


def test_a_tampered_cost_file_is_refused(tmp_path: Path) -> None:
    man = _cost_files(tmp_path)
    pd.DataFrame([{"instrument": "XAUUSD", "broker": "icm", "session": "asia", "tf": "4h", "rt_bp": 0.0}]).to_parquet(
        tmp_path / "costs" / "cost_table.parquet", index=False)
    with pytest.raises(ValueError, match="sha256"):
        T.CostTable.from_manifest(man)


def test_read_mode_refuses_w2_point_costs_even_on_a_gate_eligible_read() -> None:
    data, plan = _data(readable=False), _plan()
    w2 = T.FrameData(data.frames, gate=ELIGIBLE)
    with pytest.raises(T.ReadRefused, match="w2-point"):
        T.fit_unit(w2, plan, hb_by_fold=None, seeds=T.SEEDS[:1], with_harness=False, read=True)


def test_the_store_reports_its_cost_table_version(tmp_path: Path) -> None:
    table = T.CostTable.from_manifest(_cost_files(tmp_path))
    store = T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tmp_path, ledger=tmp_path / "l", costs=table)
    assert store.cost_version() == "srv3-cost-fixture"
    assert T.StoreData(labels=tmp_path, features=tmp_path, tiers_path=tmp_path).cost_version() == T.W2_COST_VERSION


def test_the_data_teams_cost_manifest_loads_when_present() -> None:
    man = Path.home() / "tmp" / "sr-v3-trainsets" / "manifest.json"
    if not man.exists():
        pytest.skip("no local cost manifest (market-derived, never in the repo)")
    t = T.CostTable.from_manifest(man)
    assert t.version.startswith("srv3-cost-") and t.rt_bp(_cost_rows()).min() >= DC.RT_BP


# ---------------------------------------------------------------------------------------------- M1: embargo
def test_the_gap_covers_the_longest_input_lookback_of_the_unit() -> None:
    """Quant M1 on #488 (card R-B): gap(unit) = max(1 d, max H over the unit's tfs, max input_lookback over P30 and
    the served columns), so no training row's inputs reach into the judged year."""
    assert T.gap("1h") >= D.input_lookback("1h", ["rv_ratio"])
    for unit, tfs in V.UNITS.items():
        want = max([pd.Timedelta(days=1)] + [pd.Timedelta(seconds=V.H_S[t]) for t in tfs]
                   + [D.input_lookback(t, V.P30) for t in tfs])
        assert all(T.gap(t) == want for t in tfs), unit
    assert T.gap("1d") == T.gap("1w"), "1d+1w is one unit with one gap"
    extra = pd.Timedelta(days=400)
    assert T.gap("1h", extra_s=extra.total_seconds()) == extra, "a served column's longer lookback widens the gap"


def test_the_pinned_p30_lookbacks_equal_dense_labels_input_lookback() -> None:
    """sr_v3.P30_LOOKBACK_S is a constant (the fit path may not import dense_labels); pinned here."""
    for tf in V.H_S:
        assert V.P30_LOOKBACK_S[tf] == D.input_lookback(tf, V.P30).total_seconds(), tf


def test_a_fold_with_no_training_row_under_the_gap_is_skipped_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(V.P30_LOOKBACK_S, "4h", (4 * 365 + 30) * 86_400)     # 2014's cut falls before 2010
    data, plan = _data(readable=False), _plan()
    res = T.fit_unit(data, plan, hb_by_fold=None, seeds=T.SEEDS[:1])
    assert res.report["folds_without_training_rows"] == [2012, 2013, 2014]
    assert set(res.oof["year"]) == {2015, 2016, 2017}


# ---------------------------------------------------------------------------------------------- M2: net_read
def test_net_read_charges_the_realised_overshoot_and_the_nights_held() -> None:
    """Quant M2 on #488: net_read = gross − k·(RT_ub95 + OV·1[stop]) − nights held × swap; cost(x) keeps."""
    f = _frame("XAUUSD", 2013).head(200)
    full = f["nights_full"].to_numpy(float)
    per_night = np.divide(f["cost_c"].to_numpy(float), full, out=np.zeros(len(f)), where=full > 0)
    want = np.clip(f["gross"] - f["cost_a"] - f["cost_b"] * f["stop"] - per_night * f["nights_held"], -10, 10)
    assert np.allclose(T.net_read(f), want)
    stop, held = f["stop"].to_numpy(bool), f["nights_held"].to_numpy(float)
    calm = ~stop & (held == 0)
    assert np.allclose(T.net_read(f)[calm], np.clip(f["gross"] - f["cost_a"], -10, 10)[calm]), "no stop, no night"
    assert (T.net_read(f)[stop] < np.clip(f["gross"] - f["cost_a"], -10, 10)[stop]).all(), "OV only on stops"


def test_every_read_statistic_uses_net_read_and_keep_uses_cost_x(fitted: tuple) -> None:
    _, _, res = fitted
    o, r = res.oof, res.report
    assert not np.allclose(o["net_read"], o["net"]), "the fixture's realised leg differs from cost(x)"
    mean = float(o["net_read"].mean())
    assert r["baselines"]["B1_take_all"] == pytest.approx(mean - max(0.0, mean))
    lr = T.learner_read(o, "L-cw")
    assert lr["mean_net"] == pytest.approx(mean)
    k = o["k_L-cw"].to_numpy(float)
    assert lr["s_2x_cost"] == pytest.approx(float((o["net2"] * k).mean() - max(0.0, float(o["net2"].mean()))))
    assert np.array_equal(o["k_L-cw"], o["v_L-cw"] - o["cost"] > 0), "keep is ĝ − cost(x) > 0, never realised"


# ---------------------------------------------------------------------------------------------- M3: N6 by block
def test_the_shuffle_permutes_gross_only_within_instrument_session_day_blocks() -> None:
    """Quant M3 on #488 (card 5.1 N6): "gross labels permuted in (instrument, day) blocks"."""
    f = pd.concat([_frame(i, 2013).head(300) for i in ("XAUUSD", "EURUSD")], ignore_index=True)
    perm = T.block_permutation(f, seed=V.SEED_B2)
    key = f["instrument"].astype(str).to_numpy() + "|" + np.asarray(T.session_date(f["decision_utc"]).astype(str))
    assert (key[perm] == key).all(), "every row draws its gross from its own instrument x session day"
    assert sorted(perm.tolist()) == list(range(len(f))) and (perm != np.arange(len(f))).any()
    assert np.array_equal(perm, T.block_permutation(f, seed=V.SEED_B2)), "seeded"


def test_the_shuffle_check_passes_per_instrument_tf_session_cell(fitted: tuple) -> None:
    """Quant M3 on #488: the N6 rule of diagnostics.shuffled_keep_check (false keep <= 5 % or fewer than MIN_KEPT
    kept) per (instrument x tf, session) cell, not pooled."""
    from goldbot.research import diagnostics as G
    sh = fitted[2].report["harness"]["shuffle"]
    for name, v in sh.items():
        cells = v["cells"]
        assert cells and all(c.count(":") == 1 and "|" in c for c in cells), name
        assert {c.split("|")[0] for c in cells} == {"XAUUSD:4h", "EURUSD:4h"}
        assert {c.split("|")[1] for c in cells} <= {"asia", "london", "newyork"}
        want = all(c["false_keep_share"] <= G.MAX_FALSE_KEEP or c["kept"] < V.MIN_KEPT for c in cells.values())
        assert v["passed"] is want, name
