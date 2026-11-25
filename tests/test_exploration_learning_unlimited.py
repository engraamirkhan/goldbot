"""Learning arms (demo-limits ADR 2026-11-02) on a demo account without limits (owner decision 2026-11-25, "allow
absolutely anything"; ADR 0097): what the arm's registered spec says stays, its loss cap (kill rule) and its `until`
(a pre-registered end): neither is an account limit."""
from __future__ import annotations

from goldbot.engine import exploration
from tests import test_exploration_demo_limits as tl
from tests.test_exploration_demo_limits import (  # noqa: F401 - autouse: K rows, rules, stop loss
    _registry,
    _rules,
)
from tests.test_exploration_engine import CLOSE, _actions, _at


def test_with_the_flag_a_learning_arms_registered_loss_cap_still_stops_it(tmp_path) -> None:
    """The loss cap is in the arm's frozen, registered spec (its kill rule, a condition of the learning class): the
    protocol its evidence is judged on, not an account limit (ADR 0097 inventory: statistical)."""
    tl._fill(tmp_path, "a", pnl=-12.0)
    eng, pb = tl._engine(tmp_path, [tl._learning(loss_cap=10.0), tl._control()])
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": True})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert "L1" not in tl._arms_sent(eng)
    assert _actions(eng, "exploration_arm_learning_loss_cap")


def test_with_the_flag_a_learning_arm_still_retires_past_its_until(tmp_path) -> None:
    eng, pb = tl._engine(tmp_path, [tl._learning(until="2025-03-03"), tl._control()])
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": True})
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert tl._arms_sent(eng) == [] and _actions(eng, "exploration_arm_learning_expired")
