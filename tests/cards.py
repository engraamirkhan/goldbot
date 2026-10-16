"""Test helpers for card-bound decisions (BACKLOG 63): a decision carries the `created` of the card the owner saw.
These decide on the card currently pending on the bus, as the owner's tap on the card shown would."""
from __future__ import annotations

from goldbot.telegram.approvals import Proposal, decision_callbacks
from goldbot.telegram.bus import ApprovalBus, BusDecision


def shown(bus: ApprovalBus, proposal_id: str) -> float:
    """The `created` of the card shown for `proposal_id`; 0.0 when there is none (the bus then refuses as unknown)."""
    p = bus.get(proposal_id)
    return p.created if p is not None else 0.0


def submit_shown(bus: ApprovalBus, proposal_id: str, approve: bool, reason_code: str | None,
                 by: str) -> BusDecision:
    """`bus.submit` for a decision made on the card currently pending."""
    return bus.submit(proposal_id, approve, reason_code, by=by, card_created=shown(bus, proposal_id))


def tap_data(bus: ApprovalBus, data: str) -> str:
    """"ok:<id>" / "no:<id>:<reason>" as the button data of the card currently pending (decision_callbacks); any
    other button's data (deploy, skip) as it is."""
    action, pid, *reason = data.split(":")
    if action not in ("ok", "no"):
        return data
    p = bus.get(pid)
    card = decision_callbacks(p if p is not None else Proposal(
        proposal_id=pid, account_id="-", agent_id="-", side=1, lots=0.0, entry=0.0, stop=0.0, target=0.0, p=0.0,
        ev_r=0.0, spread_points=0.0, top_features=[], created=0.0))
    return card[None] if action == "ok" else card[reason[0]]
