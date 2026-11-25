"""The demo learning cohort (ADR 0095) on a demo account without limits (owner decision 2026-11-25, "allow absolutely
anything"; ADR 0097): the cohort's per-arm, weekly, daily and hourly caps and its one-open-per-arm rule no longer
refuse and its loss cap retires no member, while every fill keeps its `learning_cohort` evidence class."""
from __future__ import annotations

import pandas as pd

from goldbot.engine import exploration, exploration_cohort
from tests import test_demo_learning_cohort as tc
from tests.test_demo_learning_cohort import (
    _rules,  # noqa: F401 - autouse: the fixed rule, stop loss and start day
)
from tests.test_exploration_engine import CLOSE, _actions, _at


def test_with_the_flag_the_cohort_caps_no_longer_refuse_and_its_fills_stay_learning_cohort(tmp_path) -> None:
    _fill = tc._fill
    _fill(tmp_path, "a", "Z1P", -14.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))     # 14 + 1 + 1 > 15: capped
    co = tc._cohort_cfg(("Z1P",))
    eng, pb = tc._engine(tmp_path, [tc._arm("Z1P", max_open=5)], co)
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": True})
    for k in range(3):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert tc._arms_sent(eng) == ["Z1P"] * 3, tc._refusals(eng, "Z1P")
    recs = exploration.trades(str(tmp_path), "icm-probe")
    assert {r.evidence_class for r in recs if r.fill_price is not None and r.pnl is None} == \
        {exploration_cohort.EVIDENCE_CLASS}


def test_with_the_flag_the_cohort_loss_cap_retires_no_member(tmp_path) -> None:
    tc._fill(tmp_path, "a", "Z1P", -20.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))
    eng, pb = tc._engine(tmp_path, [tc._arm("Z1P")], tc._cohort_cfg(("Z1P",)))
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": True})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert tc._arms_sent(eng) == ["Z1P"]
    assert _actions(eng, exploration_cohort.LOSS_CAP) == []


def test_with_the_flag_the_cohort_week_cap_is_journalled_in_shadow_when_it_would_bind(tmp_path) -> None:
    """prereg-amendment-2026-11-26-cohort-unlimited.md (quant F4): the weekly cap is lifted but judged in shadow once a
    week (`exploration_cohort_week_cap_shadow`); the real hold is never journalled and members keep trading."""
    tc._fill(tmp_path, "a", "Z1P", -60.0, pd.Timestamp("2025-03-03 10:00", tz="UTC"))     # GBP 60 > the GBP 50 cap
    eng, pb = tc._engine(tmp_path, [tc._arm("Z1P")], tc._cohort_cfg(("Z1P",)))
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": True})
    for k in range(2):
        exploration.on_pass(eng, _at(eng, pb, CLOSE + pd.Timedelta(minutes=15 * k)))
    assert len(_actions(eng, exploration_cohort.WEEK_CAP_SHADOW)) == 1
    assert not [d for d in eng.decisions if d.get("action") == exploration_cohort.WEEK_CAP]     # no real hold
    assert tc._arms_sent(eng)
