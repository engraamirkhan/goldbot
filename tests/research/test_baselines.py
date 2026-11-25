"""The obj-v1 baseline ladder (lifecycle plan N2 + A23): every baseline on the same rows; a model must beat the best."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from goldbot.research import baselines as B
from goldbot.research import objective as O


def _rows(n: int, year: int, seed: int, *, arm_means: dict[str, float] | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    arm_means = arm_means or {"A": 0.10, "Z": -0.20}
    arms = rng.choice(list(arm_means), size=n)
    t0 = pd.Timestamp(f"{year}-01-02", tz="UTC") + pd.to_timedelta(rng.integers(0, 360 * 24 * 60, n), unit="min")
    cost = rng.uniform(0.02, 0.12, n)
    gross = np.array([arm_means[a] for a in arms]) + rng.normal(0, 0.5, n)
    return pd.DataFrame({"arm": arms, "t0": t0, "t1": t0 + pd.Timedelta(minutes=30),
                         "day": t0.strftime("%Y-%m-%d"), "cost": cost, "gross": gross, "net": gross - cost,
                         "session": rng.integers(0, 3, n), "side": rng.choice([-1.0, 1.0], n)})


def test_b0_abstains_and_b1_takes_every_row():
    ev = _rows(3_000, 2014, 1)
    lad = B.ladder(ev, _rows(3_000, 2013, 2), model_keep=np.ones(len(ev), dtype=bool))
    assert lad["B0"].n_kept == 0 and lad["B0"].abstain
    assert lad["B1"].keep_share == 1.0
    assert lad["B1"].s == pytest.approx(ev["net"].mean() - max(0.0, ev["net"].mean()))


def test_b2_random_keep_matches_the_model_keep_share_in_every_cell():
    ev = _rows(4_000, 2014, 3)
    model_keep = (ev["arm"] == "A").to_numpy() | (np.arange(len(ev)) % 4 == 0)
    keep = B.keep_random_matched(ev, model_keep, cells=("arm",), seed=5)
    for a in ("A", "Z"):
        m = (ev["arm"] == a).to_numpy()
        assert keep[m].sum() == model_keep[m].sum()


def test_b3_per_arm_intercept_keeps_where_the_learn_arm_mean_gross_beats_the_known_cost():
    ev, lr = _rows(4_000, 2014, 4), _rows(4_000, 2013, 5)
    keep = B.keep_arm_intercept(ev, lr)
    mu = lr.groupby("arm")["gross"].mean()
    assert np.array_equal(keep, (ev["arm"].map(mu) - ev["cost"] > 0).to_numpy())


def test_b4_chooses_its_rule_on_learn_only():
    lr = _rows(6_000, 2013, 6)
    ev = _rows(6_000, 2014, 7)
    ev["net"] = -ev["net"]                               # a rule fitted on eval would choose differently
    rule = B.choose_rule(lr, rule_cols=("cost",))
    assert rule.col == "cost"
    keep = B.apply_rule(ev, rule)
    assert np.array_equal(keep, B.apply_rule(ev, B.choose_rule(lr, rule_cols=("cost",))))
    assert rule == B.choose_rule(lr, rule_cols=("cost",))


def test_b5_keeps_the_cells_whose_learn_mean_net_is_positive():
    lr = _rows(6_000, 2013, 8)
    ev = _rows(3_000, 2014, 9)
    keep = B.keep_cell_means(ev, lr, cells=("arm",))
    assert keep[(ev["arm"] == "A").to_numpy()].all() and not keep[(ev["arm"] == "Z").to_numpy()].any()


def test_b6_switches_an_arm_on_for_a_year_only_after_a_positive_trailing_year_whose_labels_had_closed():
    prev = _rows(3_000, 2013, 10, arm_means={"A": 0.30, "Z": -0.30})
    ev = _rows(3_000, 2014, 11)
    keep = B.keep_trailing_year(ev, pd.concat([prev, ev]))
    assert keep[(ev["arm"] == "A").to_numpy()].all() and not keep[(ev["arm"] == "Z").to_numpy()].any()
    late = prev.copy()
    late["t1"] = pd.Timestamp("2014-03-01", tz="UTC")    # labels still open on 1 Jan: not used
    assert not B.keep_trailing_year(ev, pd.concat([late, ev])).any()


def test_b7_applies_the_model_keep_to_the_matched_twin_and_is_descriptive_only():
    ev = _rows(3_000, 2014, 12)
    ev["twin_net"] = -0.05
    lad = B.ladder(ev, _rows(3_000, 2013, 13), model_keep=np.ones(len(ev), dtype=bool))
    assert lad["B7"].s == pytest.approx(-0.05)
    assert "B7" not in B.BAR
    assert B.ladder(ev.drop(columns="twin_net"), _rows(3_000, 2013, 13),
                    model_keep=np.ones(len(ev), dtype=bool))["B7"].available is False


def test_b8_arm_by_year_intercept_is_in_sample_and_never_negative():
    ev = _rows(4_000, 2014, 14)
    lad = B.ladder(ev, _rows(3_000, 2013, 15), model_keep=np.ones(len(ev), dtype=bool))
    assert lad["B8"].in_sample and (lad["B8"].s or 0.0) >= 0 and lad["B8"].s is not None


def test_a_model_must_beat_the_best_baseline_on_the_bar_to_be_served():
    ev, lr = _rows(6_000, 2014, 16), _rows(6_000, 2013, 17)
    keep_a = (ev["arm"] == "A").to_numpy()
    lad = B.ladder(ev, lr, model_keep=keep_a)
    name, best = B.best_bar(lad)
    assert name in B.BAR and best == max(float(lad[b].s or -1e9) for b in B.BAR)
    weak = O.read(ev["net"].to_numpy(), ev["gross"].to_numpy(), np.ones(len(ev), dtype=bool),
                  ev["day"].to_numpy(), boot=0)
    assert not B.beats_ladder(weak, lad)


def test_every_baseline_goes_through_the_same_abstain_rule():
    ev = _rows(300, 2014, 18)
    lad = B.ladder(ev, _rows(3_000, 2013, 19), model_keep=np.ones(len(ev), dtype=bool), cells=("arm", "session"))
    assert all(r.abstain == (r.n_kept < O.MIN_KEPT or r.n_kept_days < O.MIN_KEPT_DAYS) for r in lad.values())


def test_best_bar_reports_the_best_rung_with_and_without_the_in_sample_b8():
    ev, lr = _rows(6_000, 2014, 20), _rows(6_000, 2013, 21)
    lad = B.ladder(ev, lr, model_keep=np.ones(len(ev), dtype=bool))
    name_t, s_t = B.best_bar(lad, include_b8=False)
    assert name_t != "B8" and s_t == max(float(lad[b].s or -1e9) for b in B.BAR if b != "B8")
    assert B.best_bar(lad)[1] >= s_t


def test_the_ladder_refuses_learn_rows_whose_label_closes_after_the_first_judged_decision():
    ev, lr = _rows(2_000, 2014, 22), _rows(2_000, 2013, 23)
    late = lr.copy()
    late.loc[late.index[0], "t1"] = ev["t0"].min()
    with pytest.raises(ValueError, match="closes at or after"):
        B.ladder_keeps(ev, late, model_keep=np.ones(len(ev), dtype=bool))
    with pytest.raises(ValueError, match="closes at or after"):
        B.ladder_keeps(ev, pd.concat([lr, ev]), model_keep=np.ones(len(ev), dtype=bool))


def test_b2_keeps_rows_whose_cell_key_is_missing():
    ev = _rows(1_000, 2014, 24).astype({"session": float})
    ev.loc[ev.index[:200], "session"] = np.nan
    model_keep = np.zeros(len(ev), dtype=bool)
    model_keep[:50] = True                               # all inside the NaN cell
    keep = B.keep_random_matched(ev, model_keep, cells=("session",), seed=1)
    assert keep[:200].sum() == 50


def test_every_rung_reads_with_the_decision_time_cost_so_its_s_is_decomposed():
    ev, lr = _rows(3_000, 2014, 25), _rows(3_000, 2013, 26)
    keep = B.keep_arm_intercept(ev, lr)
    rd = O.read(ev["net"].to_numpy(), ev["gross"].to_numpy(), keep, ev["day"].to_numpy(),
                cost_x=ev["cost"].to_numpy(), boot=0)
    assert B.ladder(ev, lr, model_keep=keep)["B3"].s == pytest.approx(rd.s)
    assert rd.decomposition.cost_avoidance is not None
