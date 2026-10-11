"""ADR 0009: an approved order never exceeds the card the owner approved. The RiskGate re-check at approval can only
reduce the size (equity rose: the card's lots are sent), and a re-check whose risk at the sent lots is more than 2%
above the card's (the broker's conversion moved) is refused with `risk_above_approved`."""
from typing import Any

import pytest

from goldbot.risk.gate import RiskLimits
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import Outcome, Proposal, ceil_cent
from tests.test_engine_clock import WED, _engine, _intent, _tick


def _card(eng: Any, intent: Any) -> Proposal:
    """The proposal exactly as the engine would build it from the gate's decision now."""
    gd = eng.gate.check(intent, eng.state)
    assert gd.allowed
    agent = SPECIALISTS["session_open"]()
    prop = Proposal(proposal_id="icm-demo-card", account_id="icm-demo", agent_id=agent.agent_id, side=1, lots=gd.lots,
                    entry=2400.2, stop=2400.2 - gd.stop_distance, target=2406.2, p=0.62, ev_r=0.2, spread_points=20,
                    top_features=[], risk_amount=ceil_cent(gd.risk_amount))
    eng.pending[prop.proposal_id] = (intent, prop, agent)
    return prop


def _approve(eng: Any, prop: Proposal) -> None:
    prop.outcome = Outcome.APPROVED
    eng._on_decision(prop)


def test_equity_rising_before_the_click_never_sends_more_than_the_card(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    intent = _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0))
    prop = _card(eng, intent)
    pb._balance = 20_000.0                                     # equity doubles while the card waits
    eng._refresh_account(_tick(WED))
    assert eng.gate.check(intent, eng.state).lots > prop.lots  # the re-check alone would size up
    _approve(eng, prop)
    [pos] = pb.positions()
    assert pos.lots == prop.lots
    assert any(d.get("action") == "approval_size_capped" for d in eng.decisions)


def _gbp_terms(fx: list[float]) -> Any:
    def terms(side: int, price: float, stop_distance: float, cycle: Any = None) -> dict[str, Any]:
        return {"contract_oz": 100.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 2.0,
                "stops_level_points": 0.0, "account_currency": "GBP", "acct_per_usd": fx[0]}
    return terms


@pytest.mark.parametrize(("fx_at_click", "placed"), [(0.76, True), (0.80, False)])
def test_a_conversion_rise_past_two_percent_refuses_the_approval(tmp_path, fx_at_click, placed):
    eng, pb, _ = _engine(tmp_path)
    eng.gate.limits = RiskLimits(risk_per_trade=0.0001, min_lot_risk_cap=0.01)     # the minimum lot: size is fixed
    fx = [0.75]
    eng._sizing_terms = _gbp_terms(fx)                          # type: ignore[method-assign]
    eng._refresh_account(_tick(WED))
    intent = _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0))
    prop = _card(eng, intent)
    assert prop.lots == 0.01 and prop.risk_amount == pytest.approx(3.0)
    fx[0] = fx_at_click                                         # +1.3% is within the 2% tolerance, +6.7% is not
    _approve(eng, prop)
    assert bool(pb.positions()) is placed
    if not placed:
        assert prop.gate_refusal == ["risk_above_approved"]
        assert any(d.get("action") == "gate_at_approval:risk_above_approved" for d in eng.decisions)


def test_a_card_from_an_older_engine_without_risk_is_still_capped_at_its_lots(tmp_path):
    eng, pb, _ = _engine(tmp_path)
    eng._refresh_account(_tick(WED))
    intent = _intent().model_copy(update=eng._sizing_terms(1, 2400.2, 4.0))
    prop = _card(eng, intent)
    prop.risk_amount = None
    pb._balance = 20_000.0
    eng._refresh_account(_tick(WED))
    _approve(eng, prop)
    [pos] = pb.positions()
    assert pos.lots == prop.lots
