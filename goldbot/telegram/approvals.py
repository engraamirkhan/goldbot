"""Approval flow: confirm-entry-then-automate.

Transport-agnostic core (tested without Telegram), plus a thin python-telegram-bot adapter.
* Only allow-listed owner user ids may act. `/halt` and `/approve` need no second factor; `/rearm`,
  `/mode` and parameter changes require a TOTP within 60 s, except `/mode propose`, which lowers authority as /halt
  does. `/mode auto` also needs the auto-mode evidence (goldbot/telegram/automode.py, row A10).
* Each proposal has a 90 s window; timeouts are logged EXPIRED_UNAPPROVED. Rejections carry a reason code.
* Exits are never gated here.
* The bot also acts as the credential prompter for goldbot.ops.accounts (headless secret entry).
"""
from __future__ import annotations

import hmac
import time
from enum import Enum
from typing import TYPE_CHECKING, Callable

from pydantic import Field

from goldbot.base import Record

if TYPE_CHECKING:
    from goldbot.telegram.bus import ApprovalBus

REASON_CODES = ("news", "cost", "discretion", "duplicate", "other")
TOTP_COMMANDS = {"/rearm", "/mode", "/set"}


class Outcome(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED_UNAPPROVED"


class Proposal(Record):
    proposal_id: str
    account_id: str
    agent_id: str
    side: int
    lots: float
    entry: float
    stop: float
    target: float
    p: float
    ev_r: float
    spread_points: float
    top_features: list[tuple[str, float]]
    risk_usd: float | None = None        # account currency lost if the stop is hit: lots x stop distance x 100 oz
    created: float = Field(default_factory=time.time)
    window_s: int = 90
    outcome: Outcome | None = None
    reason_code: str | None = None
    decided_by: int | None = None
    decided_via: str | None = None       # "telegram:<id>" | "dashboard:<email>" for decisions from the bus
    gate_refusal: list[str] | None = None    # approved, but the RiskGate re-check at approval time refused the order

    @property
    def expired(self) -> bool:
        return self.outcome is None and time.time() > self.created + self.window_s

    def text(self) -> str:
        """The Telegram message: the same essentials, in the same order, as the dashboard approval card."""
        side = "🟢 LONG" if self.side > 0 else "🔵 SHORT"
        risk = f"  ·  risk ${self.risk_usd:,.0f}" if self.risk_usd is not None else ""
        feats = ", ".join(f"{n} {v:+.2f}" for n, v in self.top_features[:3])
        return (f"{side} {self.lots:.2f} lots XAUUSD{risk}\n"
                f"entry {self.entry:.2f}  stop {self.stop:.2f}  target {self.target:.2f}\n"
                f"p={self.p:.2f}  EV={self.ev_r:+.2f}R  spread {self.spread_points:.0f}pt\n"
                f"why: {feats or 'no feature importances'}\n"
                f"{self.account_id} · {self.agent_id}\n"
                f"⏱ approve within {self.window_s}s or it expires · exits are automatic")


class ApprovalCenter:
    def __init__(self, allowed_user_ids: set[int], totp_verify: Callable[[str], bool] | None = None,
                 on_decision: Callable[[Proposal], None] | None = None, bus: ApprovalBus | None = None,
                 auto_eligible: Callable[[], bool] | None = None):
        self.allowed = set(allowed_user_ids)
        self.totp_verify = totp_verify or (lambda code: False)
        self.on_decision = on_decision or (lambda p: None)
        self.pending: dict[str, Proposal] = {}
        self.log: list[Proposal] = []
        self.mode = "paper"
        self.halted = False
        self.auto_eligible = auto_eligible or (lambda: False)    # A10 evidence; none given: auto is refused
        self.bus = bus                      # set in production: proposals and decisions cross process boundaries

    # ------------------------------------------------------------- proposals
    def propose(self, p: Proposal) -> Proposal:
        self.pending[p.proposal_id] = p
        if self.bus is not None:
            self.bus.publish(p)
        return p

    def poll_bus(self) -> list[Proposal]:
        """Apply decisions written to the bus by the dashboard or the Telegram service (already authenticated
        there). A decision arriving after the window counts as expired, as it would in-process."""
        if self.bus is None or not self.pending:
            return []
        done = []
        for d in self.bus.decisions_for(set(self.pending)):
            p = self.pending[d.proposal_id]
            if p.expired:
                done.append(self._finish(p, Outcome.EXPIRED))
                continue
            p.decided_via = d.by
            p.reason_code = None if d.approve else d.reason_code
            done.append(self._finish(p, Outcome.APPROVED if d.approve else Outcome.REJECTED))
        return done

    def decide(self, proposal_id: str, user_id: int, approve: bool, reason_code: str | None = None) -> Proposal:
        if user_id not in self.allowed:
            raise PermissionError("user not allowed")
        p = self.pending.get(proposal_id)
        if p is None:
            raise KeyError("unknown or already decided proposal")
        if p.expired:
            return self._finish(p, Outcome.EXPIRED)
        if not approve and reason_code not in REASON_CODES:
            raise ValueError(f"rejection needs a reason code from {REASON_CODES}")
        p.decided_by = user_id
        p.reason_code = None if approve else reason_code
        return self._finish(p, Outcome.APPROVED if approve else Outcome.REJECTED)

    def sweep_expired(self) -> list[Proposal]:
        done = [self._finish(p, Outcome.EXPIRED) for p in list(self.pending.values()) if p.expired]
        return done

    def _finish(self, p: Proposal, outcome: Outcome) -> Proposal:
        p.outcome = outcome
        self.pending.pop(p.proposal_id, None)
        self.log.append(p)
        if self.bus is not None:
            self.bus.archive(p)
        self.on_decision(p)
        return p

    # ------------------------------------------------------------- commands
    def command(self, user_id: int, cmd: str, arg: str = "", totp: str | None = None) -> str:
        if user_id not in self.allowed:
            raise PermissionError("user not allowed")
        lowers = cmd == "/mode" and arg == "propose"          # less authority: no second factor, like /halt
        if cmd in TOTP_COMMANDS and not lowers and not (totp and self.totp_verify(totp)):
            return "TOTP required: resend as `<command> <arg> <6-digit code>`"
        if cmd == "/halt":
            self.halted = True
            return "HALTED: no new entries. Open positions keep their stops. /rearm <code> to resume."
        if cmd == "/rearm":
            self.halted = False
            return "re-armed; 30 days propose-and-approve probation begins"
        if cmd == "/mode":
            if arg not in ("paper", "propose", "auto"):
                return "mode must be paper | propose | auto"
            if arg == "auto" and not self.auto_eligible():
                return "auto mode not available: the evidence check (100 proposals, no breach, no veto difference) fails"
            self.mode = arg
            return f"mode set to {arg}"
        if cmd == "/status":
            return f"mode={self.mode} halted={self.halted} pending={len(self.pending)} decided={len(self.log)}"
        return "unknown command"

    # ------------------------------------------------------------- analytics
    def veto_value(self) -> dict:
        """Approved vs rejected proposals; outcomes are attached later by the journal (needs realised PnL)."""
        a = [p for p in self.log if p.outcome == Outcome.APPROVED]
        r = [p for p in self.log if p.outcome == Outcome.REJECTED]
        e = [p for p in self.log if p.outcome == Outcome.EXPIRED]
        other = sum(1 for p in r if p.reason_code == "other")
        return {"approved": len(a), "rejected": len(r), "expired": len(e),
                "other_share": other / len(r) if r else 0.0, "review_prompt": bool(r) and other / len(r) > 0.3}


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
