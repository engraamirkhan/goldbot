"""Data-sufficiency contract (goldbot/research/data_sufficiency.py): requirements from the sr-v3 card, certificates."""
from __future__ import annotations

from typing import Any

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
    assert g.projected == "SHORT" and "D-1" in [x.item for x in g.gaps]
    assert g.n_eff_judge is None or g.method == "dense-skeleton"         # judge: measured skeleton count only
    assert cells["gold/1m"].verdict == "SHORT"


# ------------------------------------------------------------------ consumers and routes (owner directive 2026-11-26)
def _cfg() -> dict:
    import yaml

    from goldbot.config import ROOT
    return yaml.safe_load((ROOT / "config" / "data" / "sufficiency.yaml").read_text())


def _route(**kw) -> D.Route:
    base = dict(id="R", kind="organic", title="t", gain={"gold/1h": 100}, effort_days=1.0, owner="data-engineer",
                due="2026-12-01", status="s")
    return D.Route(**{**base, **kw})


def test_synthetic_rows_count_zero_and_need_the_quants_signature():
    with pytest.raises(ValueError, match="count 0"):
        D.check_route(_route(kind="synthetic", gain={"gold/1h": 1}, signoff="quant"))
    with pytest.raises(ValueError, match="signed by the quant"):
        D.check_route(_route(kind="synthetic", gain={}, signoff=""))
    assert D.check_route(_route(kind="synthetic", gain={}, signoff="quant")).counted() == 0
    for r in D.routes(_cfg()):
        if r.kind == "synthetic":
            assert r.counted() == 0 and "quant" in r.signoff


def test_routes_rank_by_expected_counted_n_eff_per_day():
    a = _route(id="a", gain={"gold/1h": 1000}, effort_days=4, p_deliver=1.0)          # 250 / day
    b = _route(id="b", gain={"gold/1h": 1000}, effort_days=1, p_deliver=0.5)          # 500 / day
    c = _route(id="c", gain={"gold/1h": 600}, effort_days=1, p_deliver=0.5, due="2026-11-30")   # 300 / day
    assert [r.id for r in D.rank_routes([a, b, c])] == ["b", "c", "a"]
    with pytest.raises(ValueError, match="p_deliver"):
        D.check_route(_route(p_deliver=0.0))


def test_pooled_supply_is_card_2_2():
    assert D.pooled_supply([[100, 0, None], [300, 50, None]], 0.5) == [266, 50, None]
    assert D.pooled_supply([[100], [300]], 0.0) == [400]


def test_a_short_consumer_cell_without_a_route_is_refused():
    con = D.Consumer(id="m", label="m", inputs=10, tfs=("1h",), source="s")
    sup = {"gold": {"1h": (1_000, 100)}}
    with pytest.raises(ValueError, match="no route"):
        D.consumer_cells([con], sup, [])
    cells = D.consumer_cells([con], sup, [_route()])
    assert cells[0].status == "SHORT" and cells[0].short_learn == 3_300 and cells[0].routes == ("R",)
    ok = D.consumer_cells([con], {"gold": {"1h": (5_000, 2_000)}}, [])
    assert ok[0].status == "MEETS" and ok[0].short_learn == ok[0].short_judge == 0


def test_the_contract_gives_every_short_consumer_cell_a_ranked_route():
    cfg = _cfg()
    cells = D.consumer_cells(D.consumers(cfg), D.supply(cfg), D.routes(cfg))
    assert {c.consumer for c in cells} >= {"zoo-v2", "sr-v3", "seq-1", "rl-x1", "agg-v1", "x6"}
    assert all(c.routes for c in cells if c.status == "SHORT")
    assert all(c.status == "FORWARD" for c in cells if c.consumer == "agg-v1")


def test_supply_uses_the_measured_judged_head_and_derived_gains_are_computed_not_typed():
    cfg = _cfg()
    sup = D.supply(cfg)
    m = cfg["units"]["gold"]["measured"]
    assert sup["gold"]["4h"][0] == m["learn_T2_core6"][6]          # measured T2 core-6, not the card count
    assert sup["index_energy_b"]["15m"] == (0, 0)                  # B loads no intraday row
    tier = next(r for r in D.routes(cfg) if r.id == "GOLD-TIER")      # R-a re-counted, never projected (S-3)
    assert tier.gain.get("gold/4h", 0) == m["learn_T2_core6_Ra"][6] - m["learn_T2_core6"][6]
    assert "untiered" not in str(cfg["routes"])
    pool = next(r for r in D.routes(cfg) if r.id == "A2-pool")
    pooled_1h, gold_1h = sup["pooled_gold_fx"]["1h"][0], sup["gold"]["1h"][0]
    assert pooled_1h is not None and gold_1h is not None
    assert pool.gain["gold/1h"] == pooled_1h - gold_1h


def test_a_cell_is_closed_by_the_first_ranked_route_whose_cumulative_gain_covers_it_else_residual_and_cap():
    con = D.Consumer(id="m", label="m", inputs=10, tfs=("1h",), source="s")
    sup = {"gold": {"1h": (1_000, 2_000)}, "pool": {"1h": (2_000, 2_000)}}
    small = _route(id="small", gain={"gold/1h": 1_000}, effort_days=1)
    big = _route(id="big", gain={"pool/1h": 2_500}, effort_days=10, due="2026-12-09")
    cells = {c.cell: c for c in D.consumer_cells([con], sup, [small, big], {"gold": ["pool"]})}
    g = cells["gold/1h"]
    assert g.routes == ("small", "big") and g.closed_by == "big" and g.closed_due == "2026-12-09"
    assert g.residual_learn == 0 and g.max_inputs_after == 10
    p = D.consumer_cells([con], {"pool": {"1h": (1_000, 2_000)}}, [big])[0]
    assert p.closed_by is None and p.residual_learn == 800 and p.max_inputs_after == 8


def test_pooled_divisors_are_the_d12_binding_rho_by_bucket_never_the_admission_bound():
    """Quant S-1 on #486: pooled projections use the D-12 binding day-mean rho_u,inst for the body's membership and
    hold bucket; 0.2 (the D-13 admission bound) is never a pooling divisor."""
    cfg = _cfg()
    src = cfg["pooled"]["gold_fx"]["rho_inst_from"]
    rho = D.measured_rho(cfg, src)
    assert D.hold_bucket("1m") == "le60m" and D.hold_bucket("5m") == "1h-4h" and D.hold_bucket("1h") == "gt4h"
    assert rho["1m"] == pytest.approx(0.39895) and rho["5m"] == pytest.approx(0.26154) and rho["4h"] == pytest.approx(0.51038)   # D-12-xt: max(D-12 0.31666, xt-v1 re-run 0.51038)
    assert 0.2 not in set(rho.values())
    assert "RHO-measure" not in {r.id for r in D.routes(cfg)}
    learn, _ = D.unit_rows(cfg, "fx10")
    card = cfg["units"]["fx10"]["learn"]
    fx = D.measured_rho(cfg, cfg["units"]["fx10"]["card_pooled"]["rho_inst_from"])
    assert learn is not None and learn[0] == int(card[0] * 5.5 / (1 + 9 * fx["1m"]))


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
    unit: dict[str, Any] = {"learn": [5000], "judge": [2000], "items": []}
    base: dict[str, Any] = {"requirement": {"inputs": 10, "keep_share": 0.33, "objective": "obj-v1",
                                            "label_templates": "T"}, "tfs": ["15m"], "items": {}, "units": {"u": unit}}
    with pytest.raises(ValueError, match="method"):
        D.certificates(base)                                                     # no method stated
    unit["method"] = {"learn": "dense-skeleton", "judge": "card-2.2"}
    with pytest.raises(ValueError, match="one method per cell"):
        D.certificates(base)
    unit["method"] = {"learn": "dense-skeleton", "judge": "dense-skeleton"}
    assert D.certificates(base)[0].method == "dense-skeleton"
