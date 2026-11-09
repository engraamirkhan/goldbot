"""EVAL-2 L2: the empirical-Bayes hierarchical model (prereg 2026-11-09 sections 1 and 4.5; method v6 section 2, L2).

"theta_c = mu_F + a_TF + b_inst + u_c on day-clustered cell means of excess; REML; Student-t; prior fitted
leave-family-out on every cell ever viewed (real + NULL-RAND), de-duplicated by config hash; empirical null from the
controls (Efron). Student-t (df 5, fixed) on the cell random effects only; Gaussian likelihood on day-clustered cell
means." "Carry: P(theta_c > 0) >= 0.80 and P(net_c > 0 at 1.5x deciding cost) >= 0.80; at most 3 cells per family by
posterior mean." "Cell kill: P(net > 0.05 R) < 0.05." "0.80 raised, never lowered ...; thresholds, model and code hash
frozen in the card before the L2 run on real cells.\""""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research.catalogue.data import DAY
from goldbot.research.eval2 import hier as H


def _population(seed: int = 0, n_fam: int = 30, tau_f: float = 0.05, tau_u: float = 0.03) -> pd.DataFrame:
    """Cells of n_fam families x 2 TF x 2 inst, true theta = mu_F + u_c, measured with se 0.05; 5 control families."""
    rng = np.random.default_rng(seed)
    rows = []
    for f in range(n_fam):
        fam = f"NULL-RAND-{f}" if f < 5 else f"F{f}"
        mu = 0.0 if f < 5 else rng.normal(0.0, tau_f)
        for tf in ("1h", "4h"):
            for inst in ("XAUUSD", "XAGUSD"):
                theta = mu + rng.normal(0.0, tau_u)
                se = 0.05
                rows.append({"cell_id": f"{fam}|{inst}|{tf}", "family": fam, "tf": tf, "inst": inst,
                             "mean": theta + rng.normal(0.0, se), "se": se, "n_days": 400, "twin_net": -0.05,
                             "config_hash": f"h-{fam}-{inst}-{tf}", "control": f < 5, "window": "explore"})
    return pd.DataFrame(rows)


SPEC = H.HierSpec(prior_population_hash="pop-1", code_hash=H.code_hash())


def test_the_spec_defaults_are_the_frozen_l2_thresholds():
    s = H.HierSpec()
    assert (s.terms, s.method, s.t_df, s.t_on) == (("family", "tf", "inst", "cell"), "REML", 5.0, "random_effects")
    assert (s.carry_p, s.carry_cost_mult, s.kill_net_r, s.kill_p, s.max_carry) == (0.80, 1.5, 0.05, 0.05, 3)


def test_freeze_spec_writes_the_spec_and_its_hash_into_the_card_once(tmp_path: Path):
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"family": "C03"}))
    h = H.freeze_spec(SPEC, card)
    doc = json.loads(card.read_text())
    assert doc["family"] == "C03" and doc["l2_spec"]["sha256"] == h and len(h) == 64
    assert doc["l2_spec"]["spec"]["carry_p"] == 0.80 and doc["l2_spec"]["spec"]["code_hash"] == H.code_hash()
    with pytest.raises(H.SpecFrozen):
        H.freeze_spec(SPEC, card)
    assert H.freeze_spec(SPEC, tmp_path / "b.json") == h                 # the hash is the spec's, not the card's


def test_freeze_spec_writes_yaml_cards_too(tmp_path: Path):
    import yaml
    card = tmp_path / "card.yaml"
    card.write_text("family: C02\n")
    h = H.freeze_spec(SPEC, card)
    assert yaml.safe_load(card.read_text())["l2_spec"]["sha256"] == h


def test_freeze_spec_refuses_a_lowered_threshold_or_a_spec_without_hashes(tmp_path: Path):
    """'The 0.80 is raised, never lowered'; the code hash and prior population are frozen with the thresholds."""
    c = H.code_hash()
    with pytest.raises(ValueError, match="carry_p"):
        H.freeze_spec(H.HierSpec(carry_p=0.75, prior_population_hash="p", code_hash=c), tmp_path / "a.json")
    assert H.freeze_spec(H.HierSpec(carry_p=0.85, prior_population_hash="p", code_hash=c), tmp_path / "r.json")
    with pytest.raises(ValueError, match="code_hash"):
        H.freeze_spec(H.HierSpec(prior_population_hash="p"), tmp_path / "c.json")
    with pytest.raises(ValueError, match="prior_population_hash"):
        H.freeze_spec(H.HierSpec(code_hash=c), tmp_path / "d.json")
    with pytest.raises(ValueError, match="t_df"):
        H.freeze_spec(H.HierSpec(t_df=4.0, prior_population_hash="p", code_hash=c), tmp_path / "e.json")
    with pytest.raises(ValueError, match="code_hash"):                    # a stale hash is not this code
        H.freeze_spec(H.HierSpec(prior_population_hash="p", code_hash="0" * 64), tmp_path / "f.json")


def test_efron_null_estimates_the_controls_centre_and_spread_robustly():
    z = np.random.default_rng(1).normal(0.1, 1.12, 4000)
    z[:20] = 40.0                                                       # a few wild controls do not move it
    m, s = H.efron_null(z)
    assert m == pytest.approx(0.1, abs=0.06) and s == pytest.approx(1.12, abs=0.06)
    assert H.efron_null(np.array([0.3, np.nan])) == (0.0, 1.0)          # too few controls: the theoretical null


def test_fit_prior_leaves_the_family_out_and_de_duplicates_by_config_hash():
    pop = _population()
    dup = pd.concat([pop, pop.iloc[:8].assign(mean=9.0)], ignore_index=True)    # re-views of 8 cells, same hashes
    a = H.fit_prior(pop, leave_out_family="F7", spec=SPEC)
    b = H.fit_prior(dup, leave_out_family="F7", spec=SPEC)
    assert a == b
    assert a["n_cells"] == len(pop) - 4
    c = H.fit_prior(pop.assign(mean=np.where(pop["family"] == "F7", 50.0, pop["mean"])), leave_out_family="F7",
                    spec=SPEC)
    assert c == a                                                        # the held-out family never shapes its prior


def test_fit_prior_recovers_the_variance_components_by_reml():
    pop = _population(seed=3, n_fam=200, tau_f=0.10, tau_u=0.0)
    p = H.fit_prior(pop, leave_out_family="none", spec=SPEC)
    assert p["tau2_family"] == pytest.approx(0.10 ** 2, rel=0.35)
    assert p["s2_cell"] < 0.003
    assert abs(p["mu"]) < 0.03


def _family(means: list[float], se: float = 0.04, twin_net: float = 0.0, fam: str = "C03") -> pd.DataFrame:
    return pd.DataFrame([{"cell_id": f"{fam}|c{k}", "family": fam, "tf": "1h", "inst": "XAUUSD", "mean": m, "se": se,
                          "n_days": 300 + k, "twin_net": twin_net} for k, m in enumerate(means)])


PRIOR = {"mu": 0.0, "mu_var": 0.0, "tau2_family": 0.01, "tau2_tf": 0.0, "tau2_inst": 0.0, "s2_cell": 0.0025,
         "t_df": 5.0, "null_mean": 0.0, "null_sd": 1.0, "n_cells": 100.0}


def test_posteriors_shrink_cell_means_towards_the_family_and_report_probabilities():
    cells = _family([0.30, 0.10, -0.05])
    posts = H.posteriors(cells, PRIOR, spec=SPEC, deciding_cost_r={c: 0.02 for c in cells["cell_id"]})
    assert [p.cell_id for p in posts] == list(cells["cell_id"])
    top = posts[0]
    assert 0.0 < top.mean < 0.30                                          # shrunk
    assert posts[0].mean > posts[1].mean > posts[2].mean
    assert top.p_pos > 0.9 and top.sd > 0 and top.n_days == 300
    assert all(0 <= p.p_net_gt_kill <= 1 and 0 <= p.p_net_pos_gate <= 1 for p in posts)


def test_student_t_cell_effects_shrink_an_outlier_cell_less_than_a_gaussian_would():
    """A heavy-tailed u_c (df 5) lets a cell far from its family keep more of its own mean."""
    cells = _family([0.0, 0.0, 0.0, 0.0, 0.60], se=0.03)
    t = H.posteriors(cells, PRIOR, spec=SPEC, deciding_cost_r={c: 0.0 for c in cells["cell_id"]})
    gauss = H.posteriors(cells, PRIOR, spec=H.HierSpec(t_df=1e9, prior_population_hash="p", code_hash=H.code_hash()),
                         deciding_cost_r={c: 0.0 for c in cells["cell_id"]})
    assert t[-1].mean > gauss[-1].mean


def test_gate_net_uses_deciding_cost_once_in_the_l2_carry():
    """P(net_c > 0 at 1.5x): net at 1.0x is theta + twin net (both already pay the deciding cost once), so 1.5x
    subtracts only 0.5 x the deciding cost. Charging 1.5x on top of a net figure would double-count."""
    cells = _family([0.20], se=0.0001, twin_net=0.0)
    big_prior = {**PRIOR, "tau2_family": 100.0, "s2_cell": 100.0}       # flat prior: posterior ~ the data
    dc = 0.30
    post = H.posteriors(cells, big_prior, spec=SPEC, deciding_cost_r={"C03|c0": dc})[0]
    assert post.mean == pytest.approx(0.20, abs=1e-3)
    assert post.p_net_pos_gate > 0.99                                   # 0.20 - 0.5 x 0.30 = +0.05 > 0
    dc2 = 0.50
    post2 = H.posteriors(cells, big_prior, spec=SPEC, deciding_cost_r={"C03|c0": dc2})[0]
    assert post2.p_net_pos_gate < 0.01                                  # 0.20 - 0.25 < 0


def test_posteriors_need_a_deciding_cost_for_every_cell():
    cells = _family([0.1, 0.2])
    with pytest.raises(KeyError):
        H.posteriors(cells, PRIOR, spec=SPEC, deciding_cost_r={"C03|c0": 0.02})


def test_the_empirical_null_widens_but_never_narrows_the_cell_likelihood():
    cells = _family([0.15], se=0.05)
    dc = {"C03|c0": 0.0}
    base = H.posteriors(cells, PRIOR, spec=SPEC, deciding_cost_r=dc)[0]
    wide = H.posteriors(cells, {**PRIOR, "null_sd": 1.3}, spec=SPEC, deciding_cost_r=dc)[0]
    narrow = H.posteriors(cells, {**PRIOR, "null_sd": 0.7}, spec=SPEC, deciding_cost_r=dc)[0]
    assert wide.p_pos < base.p_pos
    assert narrow.p_pos == pytest.approx(base.p_pos)


def _post(cell: str, mean: float, p_pos: float, p_gate: float, p_kill: float) -> H.CellPosterior:
    return H.CellPosterior(cell, mean, 0.05, p_pos, p_gate, p_kill, 300)


def test_l2_carries_at_most_three_cells_by_posterior_mean_and_kills_hopeless_cells():
    posts = [_post("a", 0.10, 0.95, 0.90, 0.60), _post("b", 0.20, 0.97, 0.95, 0.80), _post("c", 0.05, 0.85, 0.81, 0.3),
             _post("d", 0.15, 0.99, 0.99, 0.9), _post("e", 0.08, 0.79, 0.95, 0.4),       # p_pos below 0.80: held
             _post("f", -0.2, 0.01, 0.0, 0.01)]                                           # killed
    d = H.l2_decide(posts, family="C03", spec=SPEC)
    assert d.family == "C03" and d.carried == ("b", "d", "a")
    assert d.killed == ("f",) and set(d.held) == {"c", "e"}


def test_l2_needs_both_carry_probabilities_at_the_threshold():
    posts = [_post("a", 0.1, 0.80, 0.79, 0.5), _post("b", 0.1, 0.80, 0.80, 0.5)]
    assert H.l2_decide(posts, family="F", spec=SPEC).carried == ("b",)


def test_cell_table_builds_day_clustered_cell_means_from_units():
    rng = np.random.default_rng(2)
    n = 120
    u = pd.DataFrame({"cell_id": "F|XAUUSD|1h", "family": "F", "inst": "XAUUSD", "tf": "1h", "side": 1,
                      "day": np.repeat(np.arange(60), 2), "decision_ns": np.arange(n) * DAY,
                      "net_r": rng.normal(0.1, 1, n), "twin_mean_net_r": rng.normal(-0.02, 0.2, n),
                      "cost_r": 0.03, "year": 2012})
    u["excess_r"] = u["net_r"] - u["twin_mean_net_r"]
    t = H.cell_table(u, window="explore")
    row = t.iloc[0]
    assert row["window"] == "explore"
    from goldbot.research.catalogue.stats import cluster_se
    assert row["mean"] == pytest.approx(u["excess_r"].mean())
    assert row["se"] == pytest.approx(cluster_se(u["excess_r"].to_numpy(), u["day"].to_numpy()))
    assert row["n_days"] == 60 and row["twin_net"] == pytest.approx(u["twin_mean_net_r"].mean())
    assert (row["family"], row["tf"], row["inst"]) == ("F", "1h", "XAUUSD")


# ------------------------------------------------------------------ review round 1 (code review HIGH, quant B1/B2/M1/M6)
def _reml_case(seed: int, k: int, n_fam: int, fam_sd: float) -> pd.DataFrame:
    """The code reviewer's repro (hier_em4.py / hier_em2.py): k cells over n_fam families, 6 TF and 25 instruments,
    se ~ U(0.02, 0.3), true u_c sd 0.05 (s2_cell 0.0025), family sd `fam_sd`."""
    rng = np.random.default_rng(seed)
    fam = rng.integers(0, n_fam, k)
    se = rng.uniform(0.02, 0.3, k)
    y = rng.normal(0, fam_sd, n_fam)[fam] + rng.normal(0, 0.05, k) + se * rng.standard_normal(k)
    return pd.DataFrame({"cell_id": [f"c{i}" for i in range(k)], "family": [f"F{i}" for i in fam],
                         "tf": rng.choice(["5m", "15m", "1h", "4h", "1d", "1w"], k),
                         "inst": rng.choice([f"I{i}" for i in range(25)], k), "mean": y, "se": se, "n_days": 300,
                         "twin_net": 0.0, "config_hash": [f"h{i}" for i in range(k)], "control": False,
                         "window": "explore"})


GAUSS = H.HierSpec(t_df=1e9, prior_population_hash="p", code_hash=H.code_hash())


@pytest.mark.parametrize("seed", [0, 2])
def test_reml_recovers_a_known_cell_variance_at_300_cells(seed: int):
    """Code review HIGH: from log(var(y)/4) L-BFGS-B left s2_cell at the e^-30 bound (9e-14 against a true 2.5e-3)."""
    p = H.fit_prior(_reml_case(seed, 300, 40, 0.03), leave_out_family="none", spec=GAUSS)
    assert 0.0012 < p["s2_cell"] < 0.0045


def test_reml_recovers_a_known_cell_variance_at_6000_cells():
    p = H.fit_prior(_reml_case(2, 6000, 1500, 0.0), leave_out_family="none", spec=GAUSS)
    assert p["s2_cell"] == pytest.approx(0.0025, rel=0.15)
    assert p["tau2_family"] < 0.0005 and p["tau2_tf"] < 0.0005 and p["tau2_inst"] < 0.0005


def test_reml_reaches_a_likelihood_at_least_as_good_as_the_truth():
    cells = _reml_case(0, 300, 40, 0.03)
    y, se = cells["mean"].to_numpy(), cells["se"].to_numpy()
    des = H._design(cells, GAUSS.terms)
    comps, _, _ = H._fit(y, se, des, GAUSS.t_df, True)
    vy = float(np.var(y))

    def m2ll(tf: float, s2: float) -> float:
        g2 = np.full(des.z2.shape[1], 1e-6 * vy)
        return H._reml(y, H._VInv(se ** 2 + s2, des, tf, g2))[0]
    assert m2ll(comps["tau2_family"], comps["s2_cell"]) <= m2ll(0.03 ** 2, 0.0025) + 1e-6


def _controls_and_real(n_ctrl: int = 200, centre: float = 0.1, hashless: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    z = rng.normal(centre, 1.1, n_ctrl)
    ctrl = pd.DataFrame({"cell_id": [f"N{i}" for i in range(n_ctrl)], "family": [f"NULL-RAND-{i}" for i in range(n_ctrl)],
                         "tf": "1h", "inst": "XAUUSD", "mean": z * 0.05, "se": 0.05, "n_days": 300, "twin_net": 0.0,
                         "config_hash": None if hashless else [f"n{i}" for i in range(n_ctrl)], "control": True,
                         "window": "explore"})
    real = pd.DataFrame({"cell_id": ["A|1", "B|1"], "family": ["A", "B"], "tf": "1h", "inst": "XAUUSD",
                         "mean": [0.1, 0.02], "se": 0.05, "n_days": 300, "twin_net": 0.0, "config_hash": ["ha", "hb"],
                         "control": False, "window": "explore"})
    return pd.concat([ctrl, real], ignore_index=True)


def test_population_never_collapses_rows_without_a_config_hash():
    """Quant B1: drop_duplicates on a NaN hash kept one control of 200, and the Efron null fell back to N(0, 1)."""
    cells = _controls_and_real()
    p = H.fit_prior(cells, leave_out_family="A", spec=SPEC)
    assert p["n_cells"] == 201
    z = (cells["mean"] / cells["se"]).to_numpy()[:200]
    assert (p["null_mean"], p["null_sd"]) == pytest.approx(H.efron_null(z))


def test_a_real_family_row_without_a_config_hash_is_refused():
    cells = _controls_and_real()
    cells.loc[cells["family"] == "B", "config_hash"] = None
    with pytest.raises(ValueError, match="config_hash"):
        H.fit_prior(cells, leave_out_family="A", spec=SPEC)


def test_prior_drops_other_families_rows_that_share_a_hash_with_the_held_out_family():
    """Quant B2: a copy of a held-out cell filed under another family (same config hash) is the cell's own data."""
    pop = _population()
    base = H.fit_prior(pop, leave_out_family="F7", spec=SPEC)
    copy = pop[pop["family"] == "F7"].iloc[:1].assign(family="G", cell_id="G|copy", mean=50.0)
    leaked = H.fit_prior(pd.concat([pop, copy], ignore_index=True), leave_out_family="F7", spec=SPEC)
    assert leaked == base


def test_prior_drops_cells_that_overlap_the_held_out_family_by_15_pp_or_more():
    """Quant B2: cells with excess overlap e >= 15 pp against the held-out family (the card's overlap list,
    `overlaps`) leave its prior too."""
    pop = _population().assign(overlaps="")
    base = H.fit_prior(pop, leave_out_family="F7", spec=SPEC)
    twin = pd.DataFrame([{**pop.iloc[40].to_dict(), "family": "S20", "cell_id": "S20|x", "config_hash": "hs20",
                          "mean": 50.0, "overlaps": "F7|C02"}])
    assert H.fit_prior(pd.concat([pop, twin], ignore_index=True), leave_out_family="F7", spec=SPEC) == base


def test_rows_with_a_bad_se_are_dropped_before_de_duplication():
    """Code review LOW: a NaN-se re-view of a hash must not displace the good row of the same hash."""
    pop = _population()
    bad = pop.iloc[:1].assign(se=np.nan)
    a = H.fit_prior(pd.concat([bad, pop], ignore_index=True), leave_out_family="F7", spec=SPEC)
    assert a == H.fit_prior(pop, leave_out_family="F7", spec=SPEC)


def test_fit_prior_on_an_empty_population_raises():
    pop = _population()
    with pytest.raises(ValueError, match="empty"):
        H.fit_prior(pop[pop["family"] == "F7"], leave_out_family="F7", spec=SPEC)


def test_fit_prior_refuses_cells_outside_the_explore_window():
    """Quant leakage note: the prior is explore-window cell statistics only."""
    pop = _population()
    with pytest.raises(ValueError, match="explore"):
        H.fit_prior(pop.assign(window=np.where(pop.index == 3, "confirm", "explore")), leave_out_family="F7",
                    spec=SPEC)
    with pytest.raises(ValueError, match="window"):
        H.fit_prior(pop.drop(columns="window"), leave_out_family="F7", spec=SPEC)


def test_a_negative_control_centre_never_raises_real_cells():
    """Quant M1: the Efron location is applied only when it lowers the cells."""
    cells = _family([0.05], se=0.05)
    dc = {"C03|c0": 0.0}
    base = H.posteriors(cells, PRIOR, spec=SPEC, deciding_cost_r=dc)[0]
    neg = H.posteriors(cells, {**PRIOR, "null_mean": -0.25}, spec=SPEC, deciding_cost_r=dc)[0]
    pos = H.posteriors(cells, {**PRIOR, "null_mean": 0.25}, spec=SPEC, deciding_cost_r=dc)[0]
    assert neg.p_pos == pytest.approx(base.p_pos) and pos.p_pos < base.p_pos


def test_a_significantly_negative_control_centre_stops_the_fit():
    """Quant M1: a control centre below -2 x 1.2533 / sqrt(n_controls) is a biased harness (S0 re-run), not a shift."""
    with pytest.raises(ValueError, match="S0"):
        H.fit_prior(_controls_and_real(centre=-0.5, hashless=False), leave_out_family="A", spec=SPEC)


def test_posteriors_refuse_an_unfrozen_or_stale_spec():
    """Quant M6: the freeze is enforced in code: the spec must carry this code's hash and the prior population hash."""
    cells = _family([0.1])
    dc = {"C03|c0": 0.0}
    for bad in (H.HierSpec(), H.HierSpec(prior_population_hash="p", code_hash="0" * 64),
                H.HierSpec(code_hash=H.code_hash())):
        with pytest.raises(ValueError):
            H.posteriors(cells, PRIOR, spec=bad, deciding_cost_r=dc)
        with pytest.raises(ValueError):
            H.l2_decide([_post("a", 0.1, 0.9, 0.9, 0.5)], family="F", spec=bad)


def test_the_code_hash_covers_hier_stats_and_probe():
    from pathlib import Path as P
    root = P(H.__file__).resolve().parents[1]
    import hashlib
    h = hashlib.sha256()
    for f in (root / "eval2" / "hier.py", root / "catalogue" / "stats.py", root / "eval2" / "probe.py"):
        h.update(f.read_bytes())
    assert H.code_hash() == h.hexdigest()
