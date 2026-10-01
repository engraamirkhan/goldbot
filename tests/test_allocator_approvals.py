import time

import pytest

from goldbot.allocator import Regime, RuleAllocator
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal


def test_rule_allocator_weights_and_blackout():
    a = RuleAllocator()
    w = a.weights(Regime(adx_1h=30, atr_1h_quartile=2, vol_tercile=1, minutes_to_tier1=120, minutes_since_tier1=400))
    assert w == {"trend": 1.0, "mean_reversion": 0.0, "breakout": 0.0, "session_open": 0.75}
    w2 = a.weights(Regime(adx_1h=12, atr_1h_quartile=0, vol_tercile=0, minutes_to_tier1=None, minutes_since_tier1=None))
    assert w2["mean_reversion"] == 0.5 and w2["breakout"] == 0.5  # summed cap at 1.0
    w3 = a.weights(Regime(adx_1h=30, atr_1h_quartile=0, vol_tercile=1, minutes_to_tier1=10, minutes_since_tier1=None))
    assert all(v == 0.0 for v in w3.values())
    aw = RuleAllocator.agent_weights({"session_open": 0.75}, {"a1": ("session_open", 2.0), "a2": ("session_open", 1.0), "a3": ("session_open", -1.0)})
    assert aw["a1"] == pytest.approx(0.5) and aw["a2"] == pytest.approx(0.25) and aw["a3"] == 0.0


def _prop(pid="p1", window=90):
    return Proposal(pid, "icm-demo", "session_open-g0-x", 1, 0.12, 2400.0, 2396.0, 2406.0, 0.61, 0.35, 22.0,
                    [("dist_res_atr", 0.4), ("mfi12", -0.2), ("adx14", 0.1)], window_s=window)


def test_approval_flow_permissions_reasons_and_expiry():
    decided = []
    c = ApprovalCenter({111}, totp_verify=lambda code: code == "123456", on_decision=decided.append)
    c.propose(_prop())
    with pytest.raises(PermissionError):
        c.decide("p1", 999, True)
    with pytest.raises(ValueError):
        c.decide("p1", 111, False, "because")
    p = c.decide("p1", 111, False, "news")
    assert p.outcome == Outcome.REJECTED and p.reason_code == "news" and decided == [p]
    c.propose(_prop("p2", window=0))
    time.sleep(0.01)
    assert c.decide("p2", 111, True).outcome == Outcome.EXPIRED
    c.propose(_prop("p3"))
    assert c.decide("p3", 111, True).outcome == Outcome.APPROVED
    assert c.veto_value()["approved"] == 1 and c.veto_value()["rejected"] == 1


def test_commands_totp_rules():
    c = ApprovalCenter({111}, totp_verify=lambda code: code == "123456")
    assert c.command(111, "/halt").startswith("HALTED") and c.halted
    assert "TOTP required" in c.command(111, "/rearm")
    assert c.command(111, "/rearm", totp="123456").startswith("re-armed") and not c.halted
    assert "TOTP required" in c.command(111, "/mode", "auto")
    assert c.command(111, "/mode", "auto", "123456") == "mode set to auto"
    with pytest.raises(PermissionError):
        c.command(222, "/status")
