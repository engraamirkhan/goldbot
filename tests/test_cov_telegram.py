"""Approval centre and cross-process bus: allow-list, 90 s window, reason codes, first decision wins, fail-closed halt."""
import time
from typing import Any

import pytest

from goldbot.telegram.approvals import REASON_CODES, ApprovalCenter, Outcome, Proposal, constant_time_equal
from goldbot.telegram.bus import ApprovalBus, BusDecision

OWNER, BACKUP, STRANGER = 111, 222, 999


def _prop(pid: str = "icm-1-a", age_s: float = 0.0, **kw: Any) -> Proposal:
    base: dict[str, Any] = dict(proposal_id=pid, account_id="icm-demo", agent_id="trend-g0-x", side=1, lots=0.12,
                                entry=2400.2, stop=2396.2, target=2406.2, p=0.62, ev_r=0.31, spread_points=22,
                                top_features=[("adx14", 31.2), ("rv_20", -0.4), ("ema_slope", 0.7), ("extra", 1.0)],
                                created=time.time() - age_s)
    base.update(kw)
    return Proposal(**base)


def _center(**kw: Any) -> tuple[ApprovalCenter, list[Proposal]]:
    seen: list[Proposal] = []
    c = ApprovalCenter({OWNER, BACKUP}, on_decision=seen.append, **kw)
    return c, seen


# ---------------------------------------------------------------------------------------------- proposals
def test_proposal_expires_after_its_window_but_never_once_decided():
    assert not _prop(age_s=89).expired
    assert _prop(age_s=91).expired
    assert not _prop(age_s=91, outcome=Outcome.APPROVED).expired
    assert _prop(age_s=31, window_s=30).expired


def test_proposal_text_carries_what_the_owner_needs_to_decide():
    txt = _prop(side=-1, risk_usd=48.0).text()
    assert txt.splitlines()[0] == "🔵 SHORT 0.12 lots XAUUSD  ·  risk $48"        # direction, size and $ risk first
    assert "entry 2400.20" in txt and "stop 2396.20" in txt and "target 2406.20" in txt
    assert "p=0.62" in txt and "EV=+0.31R" in txt and "spread 22pt" in txt and "90s" in txt
    assert "adx14 +31.20" in txt and "extra" not in txt              # top three features only
    assert "icm-demo · trend-g0-x" in txt
    assert "risk $" not in _prop().text()                            # proposals without the field still render
    assert "no feature importances" in _prop(top_features=[]).text()


def test_bus_recent_lists_finished_and_submitted_newest_first(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop("open"))
    bus.publish(_prop("sub"))
    bus.archive(_prop("old", outcome=Outcome.EXPIRED))
    bus.submit("sub", False, "cost", by="dashboard:o@x.io")
    rows = bus.recent()
    assert [(p.proposal_id, d is not None) for p, d in rows] == [("sub", True), ("old", False)]   # "open" is not decided
    assert rows[0][1] is not None and rows[0][1].reason_code == "cost"
    assert bus.recent(max_age_s=-1) == []


def test_only_allow_listed_users_decide_and_only_once():
    c, seen = _center()
    c.propose(_prop())
    with pytest.raises(PermissionError):
        c.decide("icm-1-a", STRANGER, True)
    assert "icm-1-a" in c.pending and not seen
    p = c.decide("icm-1-a", BACKUP, True, reason_code="news")
    assert p.outcome == Outcome.APPROVED and p.decided_by == BACKUP and p.reason_code is None
    assert seen == [p] and not c.pending
    with pytest.raises(KeyError):
        c.decide("icm-1-a", OWNER, False, "other")                  # already decided
    with pytest.raises(KeyError):
        c.decide("nope", OWNER, True)


@pytest.mark.parametrize("code", [None, "", "NEWS", "because"])
def test_rejection_needs_a_listed_reason_code_and_a_bad_one_leaves_it_pending(code):
    c, seen = _center()
    c.propose(_prop())
    with pytest.raises(ValueError):
        c.decide("icm-1-a", OWNER, False, code)
    assert "icm-1-a" in c.pending and not seen


@pytest.mark.parametrize("code", REASON_CODES)
def test_every_reason_code_is_accepted_and_recorded(code):
    c, _ = _center()
    c.propose(_prop())
    p = c.decide("icm-1-a", OWNER, False, code)
    assert p.outcome == Outcome.REJECTED and p.reason_code == code


def test_a_late_approval_is_logged_as_expired_unapproved():
    c, seen = _center()
    c.propose(_prop(age_s=120))
    p = c.decide("icm-1-a", OWNER, True)
    assert p.outcome == Outcome.EXPIRED and p.outcome.value == "EXPIRED_UNAPPROVED"
    assert seen == [p] and c.veto_value()["expired"] == 1


def test_sweep_expires_only_proposals_past_their_window():
    c, seen = _center()
    c.propose(_prop("old", age_s=95))
    c.propose(_prop("new", age_s=5))
    done = c.sweep_expired()
    assert [p.proposal_id for p in done] == ["old"] and list(c.pending) == ["new"]
    assert c.sweep_expired() == []


def test_veto_value_asks_for_review_when_other_dominates_rejections():
    c, _ = _center()
    for i, (approve, code) in enumerate([(True, None), (False, "other"), (False, "other"), (False, "cost")]):
        c.propose(_prop(f"p{i}"))
        c.decide(f"p{i}", OWNER, approve, code)
    v = c.veto_value()
    assert v["approved"] == 1 and v["rejected"] == 3 and v["other_share"] == pytest.approx(2 / 3) and v["review_prompt"]
    empty = ApprovalCenter({OWNER}).veto_value()
    assert empty["other_share"] == 0.0 and not empty["review_prompt"]


# ---------------------------------------------------------------------------------------------- commands
def test_halt_needs_no_second_factor_but_authority_raising_commands_do():
    c, _ = _center(totp_verify=lambda code: code == "123456")
    assert c.command(OWNER, "/halt").startswith("HALTED") and c.halted
    assert c.command(OWNER, "/rearm").startswith("TOTP required") and c.halted
    assert c.command(OWNER, "/rearm", totp="000000").startswith("TOTP required") and c.halted
    assert c.command(OWNER, "/mode", "auto").startswith("TOTP required") and c.mode == "paper"
    assert c.command(OWNER, "/set", "risk 0.02").startswith("TOTP required")
    assert c.command(OWNER, "/mode", "yolo", totp="123456") == "mode must be paper | propose | auto" and c.mode == "paper"
    assert c.command(OWNER, "/mode", "propose", totp="123456") == "mode set to propose"
    assert "re-armed" in c.command(OWNER, "/rearm", totp="123456") and not c.halted
    assert c.command(OWNER, "/status").startswith("mode=propose halted=False")
    assert c.command(OWNER, "/bogus") == "unknown command"


def test_default_totp_verifier_refuses_everything_and_strangers_cannot_command():
    c = ApprovalCenter({OWNER})
    assert c.command(OWNER, "/mode", "auto", totp="123456").startswith("TOTP required")
    for cmd in ("/halt", "/status"):
        with pytest.raises(PermissionError):
            c.command(STRANGER, cmd)
    assert not c.halted


def test_constant_time_equal():
    assert constant_time_equal("abc", "abc")
    assert not constant_time_equal("abc", "abd") and not constant_time_equal("abc", "ab")


# ---------------------------------------------------------------------------------------------- bus
def test_bus_submit_validates_and_the_first_decision_wins(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop())
    with pytest.raises(ValueError):
        bus.submit("icm-1-a", False, None, "dashboard:a@x")
    with pytest.raises(ValueError):
        bus.submit("icm-1-a", False, "whatever", "dashboard:a@x")
    with pytest.raises(KeyError):
        bus.submit("unknown", True, None, "dashboard:a@x")
    d = bus.submit("icm-1-a", True, "news", "telegram:111")
    assert d.approve and d.reason_code is None and d.by == "telegram:111"
    with pytest.raises(KeyError, match="already decided"):
        bus.submit("icm-1-a", False, "cost", "dashboard:b@x")
    assert bus.pending() == []                                       # decided proposals leave the queue


def test_bus_refuses_decisions_on_expired_proposals(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(age_s=100))
    with pytest.raises(KeyError, match="expired"):
        bus.submit("icm-1-a", True, None, "dashboard:a@x")
    assert bus.pending() == []


def test_bus_pending_skips_unreadable_files_and_get_finds_done_ones(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop("good"))
    (bus.pending_dir / "torn.json").write_text('{"proposal_id": "torn", ')
    assert [p.proposal_id for p in bus.pending()] == ["good"]
    assert bus.get("good") is not None and bus.get("missing") is None
    assert bus.outcome("good") is None
    p = _prop("good", outcome=Outcome.REJECTED, reason_code="cost")
    bus.archive(p)
    assert not (bus.pending_dir / "good.json").exists()
    got = bus.get("good")
    assert got is not None and got.outcome == Outcome.REJECTED
    assert bus.outcome("good") == "REJECTED"


def test_engine_side_poll_applies_bus_decisions_and_archives_them(tmp_path):
    bus = ApprovalBus(tmp_path)
    c, seen = _center(bus=bus)
    c.propose(_prop("a"))
    c.propose(_prop("b"))
    c.propose(_prop("c"))
    assert {p.proposal_id for p in bus.pending()} == {"a", "b", "c"}
    bus.submit("a", True, None, "dashboard:owner@x")
    bus.submit("b", False, "duplicate", "telegram:222")
    (bus.decisions_dir / "c.json").write_text('{"proposal_id": "c", "appr')       # torn: ignored, not crashed
    done = {p.proposal_id: p for p in c.poll_bus()}
    assert done["a"].outcome == Outcome.APPROVED and done["a"].decided_via == "dashboard:owner@x"
    assert done["b"].outcome == Outcome.REJECTED and done["b"].reason_code == "duplicate"
    assert list(c.pending) == ["c"] and len(seen) == 2
    assert bus.outcome("a") == "APPROVED" and bus.outcome("b") == "REJECTED"
    assert not (bus.decisions_dir / "a.json").exists()
    assert c.poll_bus() == []


def test_poll_without_a_bus_or_pending_is_a_no_op(tmp_path):
    assert ApprovalCenter({OWNER}).poll_bus() == []
    assert ApprovalCenter({OWNER}, bus=ApprovalBus(tmp_path)).poll_bus() == []


def test_a_decision_file_written_after_the_window_counts_as_expired(tmp_path):
    bus = ApprovalBus(tmp_path)
    c, _ = _center(bus=bus)
    p = c.propose(_prop("late", age_s=100))
    (bus.decisions_dir / "late.json").write_text(
        BusDecision(proposal_id="late", approve=True, by="dashboard:x", ts=time.time()).model_dump_json())
    [done] = c.poll_bus()
    assert done is p and done.outcome == Outcome.EXPIRED and done.decided_via is None


def test_owner_halt_round_trips_and_fails_closed_on_a_corrupt_file(tmp_path):
    bus = ApprovalBus(tmp_path)
    assert not bus.control().halted
    c = bus.set_halt(True, "telegram:111", "news spike")
    assert c.halted and bus.control().halted and bus.control().reason == "news spike"
    assert not bus.set_halt(False, "dashboard:owner@x").halted and not bus.control().halted
    bus.control_path.write_text("{garbage")
    assert bus.control().halted and bus.control().reason == "unreadable control.json"
    assert not list(tmp_path.glob("*.tmp"))
