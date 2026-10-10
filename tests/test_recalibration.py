"""Weekly bounded recalibration (proposal P9): only the probability map of a champion or challenger is refitted, on
its recent counterfactual shadow outcomes (every candidate, taken or not), shrunk toward the current calibration by a
prior worth `recal_prior_trades` trades, and p moves at most `recal_max_shift` per run. Nothing is promoted."""
import pickle

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowBook
from goldbot.ops.jobs import JobContext, recalibrate
from goldbot.research.metrics import calibration_ece
from goldbot.research.model import MetaLabelModel, PlattCalibrator, RecalibratedCalibrator, fit_recalibration
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

SETTINGS = load_settings()
NOW = pd.Timestamp("2027-01-09 11:30", tz="UTC")       # a Saturday


def _overconfident(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Predictions 0.10 too high on average: the true rate is p - 0.10."""
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.35, 0.75, n)
    y = (rng.uniform(size=n) < p - 0.10).astype(int)
    return p, y


# ------------------------------------------------------------------------------------------------ the fit
def test_no_update_below_the_minimum_sample():
    p, y = _overconfident(49)
    assert fit_recalibration(p, y, prior_weight=200, max_shift=0.05, min_samples=50) is None


def test_the_prior_keeps_a_small_sample_close_and_a_large_one_moves_further_but_never_past_the_cap():
    small = fit_recalibration(*_overconfident(60), prior_weight=200, max_shift=0.05, min_samples=50)
    large = fit_recalibration(*_overconfident(4000), prior_weight=200, max_shift=0.05, min_samples=50)
    free = fit_recalibration(*_overconfident(4000), prior_weight=200, max_shift=1.0, min_samples=50)
    assert small is not None and large is not None and free is not None
    grid = np.linspace(0.05, 0.95, 91)
    shift = {k: f.layer().apply(grid) - grid for k, f in (("small", small), ("large", large), ("free", free))}
    # the prior worth 200 trades: 60 outcomes move p a little, 4,000 move it to the data (about -0.10 in the middle)
    assert np.abs(shift["small"]).max() < np.abs(shift["free"]).max()
    assert shift["free"][45] == pytest.approx(-0.10, abs=0.03)
    # bounded: whatever the data say, p moves at most max_shift
    assert np.abs(shift["large"]).max() <= 0.05 + 1e-12 and shift["large"][45] == pytest.approx(-0.05)


def test_ece_before_and_after_are_recorded():
    p, y = _overconfident(3000, seed=3)
    fit = fit_recalibration(p, y, prior_weight=200, max_shift=0.05, min_samples=50)
    assert fit is not None and fit.n == 3000
    assert fit.ece_before == pytest.approx(calibration_ece(p, y.astype(float)))
    assert fit.ece_after == pytest.approx(calibration_ece(fit.layer().apply(p), y.astype(float)))
    assert fit.ece_after < fit.ece_before
    assert fit.max_abs_shift <= 0.05 + 1e-12


def test_a_well_calibrated_model_stays_put():
    rng = np.random.default_rng(5)
    p = rng.uniform(0.3, 0.7, 2000)
    y = (rng.uniform(size=2000) < p).astype(int)
    fit = fit_recalibration(p, y, prior_weight=200, max_shift=0.05, min_samples=50)
    assert fit is not None and abs(fit.coef - 1) < 0.15 and abs(fit.intercept) < 0.1 and fit.max_abs_shift < 0.03


def test_layers_stack_on_the_validated_calibrator_and_pickle():
    base = PlattCalibrator()
    base.coef, base.intercept = 0.8, -0.1
    fit = fit_recalibration(*_overconfident(500), prior_weight=200, max_shift=0.05, min_samples=50)
    assert fit is not None
    once = RecalibratedCalibrator.on_top(base, fit.layer())
    twice = RecalibratedCalibrator.on_top(once, fit.layer())
    assert twice.base is base and len(twice.layers) == 2           # flat: the validated calibrator plus the layers
    raw = np.linspace(0.1, 0.9, 9)
    assert np.all(np.abs(once.predict(raw) - base.predict(raw)) <= 0.05 + 1e-12)
    assert np.all(np.abs(twice.predict(raw) - once.predict(raw)) <= 0.05 + 1e-12)
    assert np.allclose(pickle.loads(pickle.dumps(twice)).predict(raw), twice.predict(raw))
    assert np.allclose(RecalibratedCalibrator.on_top(None, fit.layer()).predict(raw), fit.layer().apply(raw))


# ------------------------------------------------------------------------------------------------ the weekly job
def _ctx(tmp_path, **research) -> JobContext:
    s = SETTINGS.model_copy(update={"research": SETTINGS.research.model_copy(update=research)}) if research else SETTINGS
    return JobContext(settings=s, store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "t.jsonl"),
                      accounts=[], population=Population(tmp_path / "population.json"))


def _record_outcomes(tmp_path, version: str, n: int, seed: int = 1, legacy: int = 0) -> tuple[int, int]:
    """n counterfactual outcomes (overconfident model; about half below the threshold) plus `legacy` older
    selected-only trades, every one exited in the last 90 days. Returns (taken, not taken)."""
    rng = np.random.default_rng(seed)
    book = ShadowBook(tmp_path)
    book.track(version, NOW - pd.Timedelta(days=120))
    for i in range(n + legacy):
        ts = NOW - pd.Timedelta(days=90) + pd.Timedelta(hours=2 * i)
        p = float(rng.uniform(0.35, 0.75))
        is_legacy = i >= n
        t = book.open_trade(version=version, agent_id="agent-x", side=1, bar_ts=ts, entry=2400.0, atr_usd=2.0,
                            target_atr=1.5, stop_atr=1.0, max_bars=4, p=p, p_raw=None if is_legacy else p,
                            threshold=None if is_legacy else 0.55, taken=True if is_legacy else p > 0.55)
        assert t is not None
        hit = rng.uniform() < p - 0.10
        ShadowBook._close(t, ts + pd.Timedelta(hours=1), t.target if hit else t.stop, "target" if hit else "stop")
        book.books[version].open.remove(t)
        book.books[version].closed.append(t)
    book.save(NOW)
    rec = book.outcomes(version)
    return sum(t.taken for t in rec), sum(not t.taken for t in rec)


def test_weekly_job_is_a_no_op_below_the_minimum_sample(tmp_path):
    ctx = _ctx(tmp_path)
    e = ctx.models.add_challenger(MetaLabelModel(feature_names=["x"]), family="tsmom", agent_id="agent-x", backtest={},
                                  now=NOW - pd.Timedelta(days=120))
    sha0 = e.sha256
    _record_outcomes(tmp_path, e.version, SETTINGS.research.recal_min_samples - 1, legacy=30)   # legacy never counts
    out = recalibrate(ctx, NOW)
    assert out[e.version]["action"] == "skipped" and out[e.version]["n"] == SETTINGS.research.recal_min_samples - 1
    entry = ModelRegistry(tmp_path / "models").get(e.version)
    assert entry.sha256 == sha0 and entry.recalibrations == []
    assert ctx.models.load(entry).calibrator is None


def test_weekly_job_refits_only_the_calibrator_on_every_candidate_and_promotes_nothing(tmp_path):
    ctx = _ctx(tmp_path)
    champ = ctx.models.add_challenger(MetaLabelModel(feature_names=["x"]), family="tsmom", agent_id="agent-x",
                                      backtest={"n_trades": 300}, now=NOW - pd.Timedelta(days=200))
    ctx.models.promote(champ.version, now=NOW - pd.Timedelta(days=150))
    sha0, artefact0 = champ.sha256, champ.artefact
    ch = ctx.models.add_challenger(MetaLabelModel(feature_names=["x"]), family="tsmom", agent_id="agent-x",
                                   backtest={"n_trades": 300}, now=NOW - pd.Timedelta(days=120))
    taken, not_taken = _record_outcomes(tmp_path, champ.version, 400, legacy=25)
    assert taken and not_taken
    _record_outcomes(tmp_path, ch.version, 10, seed=2)                     # too few: left alone
    before = ctx.models.load(ctx.models.get(champ.version))
    out = recalibrate(ctx, NOW)
    r = out[champ.version]
    assert r["action"] == "recalibrated" and out[ch.version]["action"] == "skipped"
    # unbiased: the sample is every candidate with a recorded decision, the 25 selected-only legacy trades excluded
    assert (r["n"], r["n_taken"], r["n_not_taken"]) == (taken + not_taken, taken, not_taken)
    assert r["ece_after"] < r["ece_before"]
    reg = ModelRegistry(tmp_path / "models")
    entry = reg.get(champ.version)
    assert entry.status == "champion" and reg.get(ch.version).status == "challenger"     # nothing promoted or retired
    assert entry.version == champ.version and entry.sha256 != sha0 and entry.artefact != artefact0
    rec = entry.recalibrations[-1]
    assert rec["ece_before"] == r["ece_before"] and rec["ece_after"] == r["ece_after"] and rec["n"] == r["n"]
    after = reg.load(entry)                                                # checksummed artefact, same trees
    assert after.feature_names == before.feature_names and after.model is before.model is None
    raw = np.linspace(0.3, 0.8, 11)
    assert np.all(np.abs(after.calibrated(raw) - before.calibrated(raw)) <= SETTINGS.research.recal_max_shift + 1e-12)
    assert np.all(after.calibrated(raw) <= raw)                            # overconfident: p comes down
    log = (tmp_path / "recalibration.jsonl").read_text().splitlines()
    assert len(log) == 1 and champ.version in log[0]
