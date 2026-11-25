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
