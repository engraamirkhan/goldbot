"""SEQ-1's primary path under quant ruling R-c (goldbot/research/seq1_embed.py): an outcome-free pretext read from the
window only, an 8-dim embedding refit per outer fold on purged training rows (embargo >= TCN window + H), learn only,
5-seed stability, A8 parity on the 8 dims (the run record and rebuild: test_history_runs)."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import seq1 as S
from goldbot.research import seq1_embed as E
from goldbot.research.dense_labels import H_BY_TF
from goldbot.research.windows import HoldoutSealed
from tests.research.test_seq1 import _bars

ROOT = Path(__file__).resolve().parents[2]


def _rows(n: int = 700, start: str = "2012-01-02") -> tuple[S.Seq1Data, pd.DataFrame]:
    bars = _bars(start, n)
    dec = bars["visible_at"].iloc[20:n - 30].reset_index(drop=True)
    return E.rows({("XAUUSD", "1h"): dec}, {("XAUUSD", "1h"): bars}), bars


def test_the_pretext_reads_only_the_window_and_never_a_label_or_outcome():
    src = (ROOT / "goldbot/research/seq1_embed.py").read_text()
    names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
    assert not ({"gross_r", "gross_r_raw", "net_r", "load_labels", "load_learn"} & (names | attrs))
    data, _ = _rows()
    t, m = E.pretext_targets(data.X)
    assert t.shape == (len(data), S.SPEC.window - 1, 2) and m.shape == t.shape[:2]
    # the target at position t is window bar t + 1: the last target is the decision bar itself, never later
    np.testing.assert_array_equal(t[:, -1, 0], data.X[:, E.R_CLOSE, -1])


def test_rows_carry_the_window_start_and_a_horizon_end_for_the_purge_and_stay_in_learn():
    data, bars = _rows()
    k = data.keys
    assert (pd.DatetimeIndex(k["window_from_utc"]) < pd.DatetimeIndex(k["decision_utc"])).all()
    assert ((pd.DatetimeIndex(k["horizon_end_utc"]) - pd.DatetimeIndex(k["decision_utc"]))
            == pd.Timedelta(seconds=H_BY_TF["1h"])).all()
    late = _bars("2017-12-20", 400)
    with pytest.raises(ValueError, match="learn"):
        E.rows({("XAUUSD", "1h"): late["visible_at"].iloc[100:]}, {("XAUUSD", "1h"): late})
    hold = _bars("2025-10-02", 200)
    with pytest.raises(HoldoutSealed):
        E.rows({("XAUUSD", "1h"): hold["visible_at"].iloc[100:]}, {("XAUUSD", "1h"): hold})


def test_the_fold_embargo_is_the_tcn_window_plus_the_horizon():
    data, _ = _rows()
    assert E.fold_embargo(data) == pd.Timedelta(hours=64) + pd.Timedelta(seconds=H_BY_TF["1h"])


def test_the_linear_cka_is_one_for_a_rotation_and_low_for_noise():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, (500, 8))
    q, _ = np.linalg.qr(rng.normal(0, 1, (8, 8)))
    assert E.linear_cka(a, 3 * a @ q) == pytest.approx(1.0)
    assert E.linear_cka(a, rng.normal(0, 1, (500, 8))) < 0.1


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


needs_torch = pytest.mark.skipif(not _has_torch(),
                                 reason="the optional dl group (torch) is not installed")
FAST = E.SPEC.model_copy(update={"base": S.SPEC.model_copy(update={"max_epochs": 2, "batch": 128, "threads": 1})})


@needs_torch
def test_the_fold_purges_training_rows_within_window_plus_horizon_of_the_test_rows_and_refits():
    data, _ = _rows()
    d = pd.DatetimeIndex(data.keys["decision_utc"])
    test = (d >= d[300]) & (d < d[400])
    train = ~test
    art, kept, emb = E.fold_embed(data, np.asarray(train), np.asarray(test), spec=FAST)
    assert emb.shape == (int(test.sum()), E.DIM) and np.isfinite(emb).all()
    gone = np.asarray(train) & ~kept
    assert gone.any() and not (kept & np.asarray(test)).any()
    t0, t1 = d[test].min(), pd.DatetimeIndex(data.keys["horizon_end_utc"])[test].max() + E.fold_embargo(data)
    k_from = pd.DatetimeIndex(data.keys["window_from_utc"])[kept]
    k_end = pd.DatetimeIndex(data.keys["horizon_end_utc"])[kept]
    assert not ((k_end >= t0) & (k_from <= t1)).any()           # no kept row overlaps the embargoed test span
    with pytest.raises(ValueError, match="both"):
        E.fold_embed(data, np.ones(len(data), bool), np.asarray(test), spec=FAST)


@needs_torch
def test_the_embedding_is_deterministic_and_round_trips_with_its_sha():
    data, _ = _rows()
    a, b = E.fit(data, seed=7, spec=FAST), E.fit(data, seed=7, spec=FAST)
    assert a.sha256 == b.sha256 == a.compute_sha256() and a.outcome_free
    np.testing.assert_array_equal(E.embed(E.load(a.model_dump_json()), data.X[:30]), E.embed(a, data.X[:30]))
    with pytest.raises(ValueError, match="sha"):
        E.load(a.model_dump_json().replace(a.sha256, "1" * 64))


@needs_torch
def test_the_pretext_loss_of_the_validation_rows_is_the_fits_recorded_validation_loss():
    data, _ = _rows()
    art = E.fit(data, seed=7, spec=FAST)
    _, val = S.inner_split(data, FAST.base)
    assert E.pretext_loss(art, val.X) == pytest.approx(art.val_loss, rel=1e-5)
    assert E.pretext_loss(art, val.X[:0]) is None


@needs_torch
def test_embed_row_matches_the_batch_and_parity_is_exact_on_identical_inputs():
    data, _ = _rows()
    art = E.fit(data, seed=3, spec=FAST)
    batch = E.embed(art, data.X[:15])
    for i in range(15):
        np.testing.assert_allclose(E.embed_row(art, data.X[i]), batch[i], rtol=1e-5, atol=1e-6)
    p = E.parity(art, data.X[:200], data.X[:200].copy())
    assert len(p.max_abs_diff) == E.DIM and max(p.max_abs_diff) == 0.0 and sum(p.nan_mask_diff) == 0
    noisy = data.X[:200].copy()
    noisy[:, :4] += np.random.default_rng(0).normal(0, 0.5, noisy[:, :4].shape).astype(np.float32)
    assert max(E.parity(art, data.X[:200], noisy).max_abs_diff) > 0


@needs_torch
def test_the_embedding_ignores_bars_after_the_decision():
    data, bars = _rows()
    art = E.fit(data, seed=3, spec=FAST)
    d = pd.Series([bars["visible_at"].iloc[500]])
    tf = S.tf_channels(["1h"])
    full = np.concatenate([S.bar_windows(bars, d)[0], tf], axis=1)
    pref = np.concatenate([S.bar_windows(bars[bars["visible_at"] <= d.iloc[0]], d)[0], tf], axis=1)
    np.testing.assert_array_equal(E.embed(art, full), E.embed(art, pref))


@needs_torch
def test_seed_stability_fits_every_seed_and_reports_pairwise_cka():
    data, _ = _rows()
    st = E.seed_stability(data, data.X[:200], FAST)
    assert len(st.seeds) == 5 and len(set(st.shas)) == 5 and len(st.pairwise_cka) == 10
    assert all(0 <= c <= 1 + 1e-9 for c in st.pairwise_cka)
