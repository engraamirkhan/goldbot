"""Data-sufficiency contract (goldbot/research/data_sufficiency.py): requirements from the sr-v3 card, certificates."""
from __future__ import annotations

import pytest

from goldbot.research import data_sufficiency as D

GAP = D.Gap(gap="gold untiered", plan="D-1 grade Dukascopy gold", owner="data-engineer", due="2026-12-02")


def test_the_requirements_are_the_cards_riley_430_per_input_and_the_a22_power_row():
    req = D.Requirement(cell="gold-15m", unit="XAUUSD", tf="15m", inputs=40)
    assert req.n_train() == 17_200 and req.n_judge() == 1_109
    assert D.power_n(0.05) == 2474                       # sd 1 R cross-check of the k = 1 row
    assert 0 < D.r2_from_auc(0.53) < D.r2_from_auc(0.60) < 0.1


def test_a_short_cell_names_its_gap_plan_owner_and_date_never_just_too_little_data():
    req = D.Requirement(cell="gold-4h", unit="XAUUSD", tf="4h")
    c = D.certify(req, 414, 400, [GAP])
    assert c.verdict == "SHORT" and c.max_inputs == 0
    line = c.health_line()
    assert line.startswith("SHORT gold-4h") and "D-1 grade Dukascopy gold" in line and "2026-12-02" in line
    with pytest.raises(ValueError, match="gap"):
        D.certify(req, 414, 400, [])


def test_an_unmeasured_window_is_short_and_a_covered_cell_is_sufficient():
    req = D.Requirement(cell="fx-1m", unit="FX10", tf="1m")
    assert D.certify(req, None, 10_948, [GAP]).verdict == "SHORT"
    held = D.certify(req, 11_399, 10_948, [GAP])
    assert held.verdict == "SHORT" and held.projected == "SUFFICIENT"           # numbers fine, item open
    ok = D.certify(req, 11_399, 10_948, [GAP.model_copy(update={"blocks": False})])
    assert ok.verdict == "SUFFICIENT" and ok.max_inputs == 26 and ok.health_line().startswith("SUFFICIENT fx-1m")


def _cells() -> dict[str, D.Certificate]:
    import yaml

    from goldbot.config import ROOT
    cfg = yaml.safe_load((ROOT / "config" / "data" / "sufficiency.yaml").read_text())
    return {c.cell: c for c in D.certificates(cfg)}


def test_a_cell_no_pooling_can_rescue_is_infeasible_with_its_data_remedies_and_no_pool_item():
    """Quant C1, owner rule: the plan names the data remedies, not only the input cap or rule-only fallback."""
    c = _cells()["gold/4h"]
    items = [g.item for g in c.gaps]
    assert c.projected == "INFEASIBLE" and "INFEASIBLE" in items and "POOL" not in items
    plan = next(g.plan for g in c.gaps if g.item == "INFEASIBLE")
    assert "D-13" in plan and "2003-09" in plan and "D-12" in plan and "rule-only" in plan


def test_supply_is_what_the_loader_keeps_b_units_are_daily_only_and_gold_is_graded():
    """Quant C2."""
    cells = _cells()
    b = cells["index_energy_b/15m"]
    assert b.n_eff_judge == 0 and b.projected == "n/a: B is daily-only" and b.verdict == "SHORT"
    g = cells["gold/15m"]                                  # graded by D-1 (r11-v2.1): measured, no projection label
    assert g.projected == "SHORT" and "D-1" in [x.item for x in g.gaps] and g.n_eff_judge == 292
    assert cells["gold/1m"].verdict == "SHORT"


def test_a_zero_row_cell_is_not_started_data_with_no_pool_item() -> None:
    """Quant A1 on PR #482: learn == 0 (measured) is NOT STARTED (data), gap NO-ROWS, no POOL or INFEASIBLE."""
    c = _cells()["gold/1d"]
    items = [g.item for g in c.gaps]
    assert c.n_eff_learn == 0 and c.projected == "NOT STARTED (data)"
    assert "NO-ROWS" in items and "POOL" not in items and "INFEASIBLE" not in items
    g = next(x for x in c.gaps if x.item == "NO-ROWS")
    assert g.gap == "no usable graded rows under ADR 0104 §4 for the column set"
    assert g.plan == "D-1 (ii), D-13, D-14; re-assessed at each monthly regrade (card 2.3)"


def test_2h_is_not_measurable_and_tf_2h_stays_first() -> None:
    """Quant A2 on PR #482: gold 2h learn is null (no bar rule), never 0; the TF-2h gap leads."""
    c = _cells()["gold/2h"]
    assert c.n_eff_learn is None and c.gaps[0].item == "TF-2h" and c.projected == "SHORT"
    assert "NO-ROWS" not in [g.item for g in c.gaps]


def test_one_method_per_cell_and_gold_is_measured_on_both_sides() -> None:
    """Quant A3 on PR #482: every unit states its method; a unit whose learn and judge methods differ is refused."""
    cells = _cells()
    assert {c.method for c in cells.values()} <= set(D.METHODS)
    assert cells["gold/15m"].method == "dense-skeleton" and cells["fx10/15m"].method == "card-2.2"
    base = {"requirement": {"inputs": 10, "keep_share": 0.33, "objective": "obj-v1", "label_templates": "T"},
            "tfs": ["15m"], "items": {}, "units": {"u": {"learn": [5000], "judge": [2000], "items": []}}}
    with pytest.raises(ValueError, match="method"):
        D.certificates(base)                                                     # no method stated
    base["units"]["u"]["method"] = {"learn": "dense-skeleton", "judge": "card-2.2"}
    with pytest.raises(ValueError, match="one method per cell"):
        D.certificates(base)
    base["units"]["u"]["method"] = {"learn": "dense-skeleton", "judge": "dense-skeleton"}
    assert D.certificates(base)[0].method == "dense-skeleton"
