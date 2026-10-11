"""The engine's card money (review of b97f9f9): the normal risk shown is the gate's effective target (halved and
capped in the size-down stage, so the note never contradicts itself) and the risk is rounded up to the cent."""
from types import SimpleNamespace

import pytest

from goldbot.risk import sizing
from goldbot.risk.gate import RiskLimits, Stage
from tests.test_engine_clock import _engine, _intent


@pytest.mark.parametrize("stage", [Stage.NORMAL, Stage.SIZE_DOWN])
def test_card_money_states_the_gates_own_normal_risk_and_cap(tmp_path, stage):
    eng, _, _ = _engine(tmp_path)
    eng.gate.limits = RiskLimits(min_lot_risk_cap=0.01)
    eng.state.stage = stage
    size_down = stage == Stage.SIZE_DOWN
    intent = _intent().model_copy(update={"multiplier": 1.4})
    gd = SimpleNamespace(reasons=["min_lot_exception"], risk_amount=8.2012, risk_fraction=0.0082012)
    m = eng._card_money(gd, intent)
    assert m["risk_normal"] == pytest.approx(sizing.effective_risk(eng.gate.limits, size_down, 1.4).target)
    assert m["risk_cap"] == eng.gate.min_lot_cap(size_down)
    assert m["risk_amount"] == 8.21                                   # up to the cent: never below the gate's
    if size_down:
        assert m["risk_normal"] < m["risk_cap"]                       # "above the normal x%, within your y% cap"
