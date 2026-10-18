"""D-2 (batch 2026-10-18 readout): the reading rule's FAIL clause ("net <= 0") has no event floor."""
from __future__ import annotations

import pytest

from goldbot.research.registry import TrialRegistry
from tests.test_trial_slots import H01, _scr, _screened


@pytest.mark.parametrize("rule_id,config,family", [("H-04", {"stop_atr": 1.5}, "asia_drift"), ("H-01", H01, "tsmom"),
                                                    (None, {"x": 1}, "breakout")])
def test_below_the_event_floor_a_non_positive_net_mean_r_may_be_read_stop_but_never_continue(
        tmp_path, rule_id, config, family):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    row = _screened(reg, family, config, _scr("inconclusive (event floor)", 40, 0.5, -0.02, -0.3), rule_id=rule_id)
    with pytest.raises(ValueError, match="pre-registered decision"):
        reg.record_verdict(row, "continue", "x")
    v = reg.record_verdict(row, "stop", "net mean R <= 0 (rule_ref)")
    assert v["decision"] == "stop" and v["rule_checked"] is True and "family_evidence" not in v


def test_below_the_event_floor_a_positive_net_mean_r_is_still_only_inconclusive(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    row = _screened(reg, "asia_drift", {"stop_atr": 1.5}, _scr("inconclusive (event floor)", 40, 0.5, 0.02, 0.3),
                    rule_id="H-04")
    with pytest.raises(ValueError, match="pre-registered decision"):
        reg.record_verdict(row, "stop", "x")
    assert reg.record_verdict(row, "inconclusive", "x")["decision"] == "inconclusive"


def test_a_zero_event_degenerate_row_may_be_stopped_and_is_flagged_not_family_evidence(tmp_path):
    reg = TrialRegistry(tmp_path / "r.jsonl")
    scr = {"verdict": "inconclusive (event floor)", "passed": False, "n": 0, "net_mean_r": None, "net_t": None}
    row = _screened(reg, "asia_drift", {"stop_atr": 1.5}, scr, rule_id="H-04")
    with pytest.raises(ValueError, match="pre-registered decision"):
        reg.record_verdict(row, "continue", "x")
    v = reg.record_verdict(row, "stop", "no trades generated")
    assert v["decision"] == "stop" and v["family_evidence"] is False
    assert v["reason"].startswith("degenerate config (0 events)")
