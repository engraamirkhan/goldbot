"""Approval flow: confirm-entry-then-automate.

Transport-agnostic core (tested without Telegram), plus a thin python-telegram-bot adapter.
* Only allow-listed owner user ids may act. `/halt` and `/approve` need no second factor; `/rearm`,
  `/mode` and parameter changes require a TOTP within 60 s, except `/mode propose`, which lowers authority as /halt
  does. `/mode auto` also needs the auto-mode evidence (goldbot/telegram/automode.py, row A10).
* Each proposal has a 90 s window; timeouts are logged EXPIRED_UNAPPROVED. Rejections carry a reason code. The window
  never trusts the wall clock alone (BACKLOG 60): a `created` stamped ahead of now or not a finite number counts as
  expired, and the engine's center also holds a monotonic deadline per proposal it raised.
* Exits are never gated here.
* The bot also acts as the credential prompter for goldbot.ops.accounts (headless secret entry).
"""
from __future__ import annotations

import base64
import hmac
import logging
import math
import re
import struct
import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from pydantic import Field, model_validator

from goldbot.base import FrozenRecord, Record
from goldbot.data.timeutil import heartbeat_problem
from goldbot.execution.not_confirmed import Confirmation

if TYPE_CHECKING:
    from goldbot.ops.account_view import LossBudget
    from goldbot.telegram.bus import ApprovalBus

log = logging.getLogger("goldbot.approvals")

REASON_CODES = ("news", "cost", "discretion", "duplicate", "other")
TOTP_COMMANDS = {"/rearm", "/mode", "/set"}


OPEN_ASIDE = "(open trades aside)"   # the budget does not reserve open positions' stop risk
CURRENCY_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def cash(x: float, currency: str | None) -> str:
    """An amount in the account currency as the owner writes it: "£8.20", "$1,234.50"; "8.20 CHF" for a currency
    without a symbol here; a bare number when the currency is unknown (never a guessed sign)."""
    sym = CURRENCY_SYMBOL.get((currency or "").upper())
    return f"{sym}{x:,.2f}" if sym else f"{x:,.2f} {currency}" if currency else f"{x:,.2f}"


def ceil_cent(x: float) -> float:
    """Up to the cent, so a risk is never shown below the RiskGate's figure (8.2012 -> 8.21; 8.21 stays 8.21: the
    x 100 product is rounded to 6 places first so float noise never adds a cent)."""
    return math.ceil(round(x * 100, 6)) / 100


def floor_cent(x: float) -> float:
    """Down to the cent: a budget left is never shown larger than it is."""
    return math.floor(round(x * 100, 6)) / 100


def pct(fraction: float) -> str:
    """0.0082 -> "0.82%", 0.005 -> "0.5%", 0.01 -> "1%" (the dashboard's `pct` in web/src/lib/approval.ts)."""
    return f"{100 * fraction:.2f}".rstrip("0").rstrip(".") + "%"


def window_over(created: Any, window_s: Any, now: float) -> bool:
    """True when an approval window opened at `created` (epoch s) is over at `now`, by the wall clock. Fail closed
    (BACKLOG 60, the heartbeat rule of BACKLOG 58): also when `created` is more than CLOCK_AHEAD_TOLERANCE_S ahead of
    `now` (a restored backup, a bad write, a clock stepped back: `now - created` is negative and the window would last
    longer than `window_s`), and when `created` or `window_s` is not a finite number (NaN compared False with every
    limit, so read as open forever)."""
    if isinstance(window_s, bool) or not isinstance(window_s, (int, float)) or not math.isfinite(window_s):
        return True
    return heartbeat_problem(created, now, float(window_s)) is not None


def decision_channel(by: str) -> str:
    """"telegram:<id>" -> "telegram", "dashboard:<email>" -> "dashboard": the channel without the identity."""
    return by.split(":", 1)[0]


# BACKLOG 63: a tap is bound to the exact card the owner saw. Telegram's callback_data is at most 64 bytes, so a card's
# `created` travels as a fixed-width token (the 8 bytes of the float, base64url: 11 characters, exact) and a reason as
# one letter; the proposal id goes last, so it is never cut or split. Approve: "ok:<token>:<id>"; reject:
# "no:<letter>:<token>:<id>". goldbot.ops.accounts.ACCOUNT_ID_MAX_BYTES bounds the id so every button fits.
CALLBACK_MAX_BYTES = 64
# a proposal id names a file under state/approvals/ (pending, decisions, done): only these characters, no "..", so a
# tap, a dashboard request or a bus call can never reach another path (security review of 2c6e805). The engine's ids
# (`<account>-<epoch s>-<10 hex>`, at most 46 bytes for a 24-byte account name) always match
PROPOSAL_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,47}$"
_PROPOSAL_ID = re.compile(PROPOSAL_ID_PATTERN)
INVALID_PROPOSAL_ID = "invalid proposal id"
_REASON_KEY = {"news": "n", "cost": "c", "discretion": "d", "duplicate": "u", "other": "o"}
_KEY_REASON = {v: k for k, v in _REASON_KEY.items()}


def valid_proposal_id(proposal_id: str) -> bool:
    """`PROPOSAL_ID_PATTERN` and no "..": safe as a file name under state/approvals/."""
    return _PROPOSAL_ID.fullmatch(proposal_id) is not None and ".." not in proposal_id


def card_token(created: float) -> str:
    """The card's `created` as 11 URL-safe characters, exact (no decimal rounding): the float's 8 bytes, base64url."""
    return base64.urlsafe_b64encode(struct.pack(">d", created)).rstrip(b"=").decode("ascii")


def card_created_from(token: str) -> float:
    """The inverse of `card_token`; ValueError for anything that is not exactly one."""
    if len(token) != 11:
        raise ValueError("card token is not 11 characters")
    try:
        raw = base64.urlsafe_b64decode(token + "=")
    except (ValueError, TypeError) as exc:
        raise ValueError("card token is not base64url") from exc
    if len(raw) != 8 or card_token(struct.unpack(">d", raw)[0]) != token:
        raise ValueError("card token is not canonical")
    return float(struct.unpack(">d", raw)[0])


class CardTap(FrozenRecord):
    """One Approve or Reject tap, as its callback data carries it: which proposal, on which card (its `created`)."""
    proposal_id: str
    approve: bool
    reason_code: str | None
    card_created: float


def decision_callbacks(p: Proposal) -> dict[str | None, str]:
    """The callback data of a card's buttons: None -> Approve, each reason code -> Reject with it. Raises ValueError
    when one would exceed Telegram's 64 bytes (never cut: a cut id would decide nothing, or another proposal), and for
    an id `valid_proposal_id` refuses (no tap on it could be decided)."""
    if not valid_proposal_id(p.proposal_id):
        raise ValueError(f"{INVALID_PROPOSAL_ID} ({len(p.proposal_id.encode('utf-8'))} bytes)")
    tok = card_token(p.created)
    out: dict[str | None, str] = {None: f"ok:{tok}:{p.proposal_id}"}
    out.update({r: f"no:{_REASON_KEY[r]}:{tok}:{p.proposal_id}" for r in REASON_CODES})
    if (longest := max(len(d.encode("utf-8")) for d in out.values())) > CALLBACK_MAX_BYTES:
        raise ValueError(f"proposal id too long for Telegram's {CALLBACK_MAX_BYTES}-byte button data ({longest} bytes)")
    return out


def parse_decision_callback(data: str) -> CardTap:
    """An Approve/Reject tap's data back to its decision. A card without the stamp (sent before BACKLOG 63) or a token
    that does not decode is refused with ValueError "card_missing: ..." (fail closed: never an unbound decision); an
    id `valid_proposal_id` refuses (empty, "..", a path) with ValueError "invalid proposal id"."""
    parts = data.split(":", 3 if data.startswith("no:") else 2)
    tap = None
    try:
        if parts[0] == "ok" and len(parts) == 3:
            tap = CardTap(proposal_id=parts[2], approve=True, reason_code=None,
                          card_created=card_created_from(parts[1]))
        elif parts[0] == "no" and len(parts) == 4 and parts[1] in _KEY_REASON:
            tap = CardTap(proposal_id=parts[3], approve=False, reason_code=_KEY_REASON[parts[1]],
                          card_created=card_created_from(parts[2]))
    except ValueError:
        pass
    if tap is None:
        raise ValueError("card_missing: this card is out of date; decide on the current card")
    if not valid_proposal_id(tap.proposal_id):
        raise ValueError(INVALID_PROPOSAL_ID)
    return tap


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
    entry: float                         # the decision price: the quote at the signal, never updated at approval
    stop: float
    target: float
    p: float
    ev_r: float
    spread_points: float                 # the spread at the signal (finance GAP-02: both go on the closed record)
    top_features: list[tuple[str, float]]
    risk_usd: float | None = None        # USD lost if the stop is hit: lots x stop distance x 100 oz (older engines)
    # the approval card's money (owner request), from the RiskGate's sizing; None/False from older engines
    currency: str | None = None          # the broker account's currency
    risk_amount: float | None = None     # account currency lost if the stop is hit (GateDecision.risk_amount)
    risk_fraction: float | None = None   # risk_amount / equity, as the gate sized it
    min_lot_exception: bool = False      # sized at the minimum lot above the normal risk, within the cap (BACKLOG 25)
    risk_normal: float | None = None     # the normal risk per trade (fraction of equity)
    risk_cap: float | None = None        # the minimum-lot cap that applied (fraction of equity; halved in size-down)
    created: float = Field(default_factory=time.time)
    window_s: int = 90
    outcome: Outcome | None = None
    reason_code: str | None = None
    # finance audit GAP-10: when the owner decided (epoch s, the decision's own clock) and through which channel
    # ("telegram" | "dashboard"), kept on the archived proposal; the channel is the word before ":" and never carries
    # an id. None for an expired proposal and on proposals archived before these fields existed. BACKLOG 52: the
    # channel is the only trace of who decided; identity is in the auth audit log, never in an approval file
    decided_ts: float | None = None
    decided_channel: str | None = None
    gate_refusal: list[str] | None = None    # approved, but the RiskGate re-check at approval time refused the order
    # BACKLOG 72: approved and sent, but the broker neither filled nor refused it (`gate_refusal` ["not_confirmed"]):
    # what the engine's look-up at the broker found, for the card and the dashboard; None on every other proposal
    not_confirmed: Confirmation | None = None
    # BACKLOG 72 review (race): set when an approval is archived and cleared once the engine has the broker's answer, so
    # the Telegram outbox never reports a card APPROVED while its order is still in flight (`Outbox.finished`)
    placing: bool = False

    @model_validator(mode="before")
    @classmethod
    def _drop_decider_identity(cls, data: Any) -> Any:
        """BACKLOG 52: "the done files and the API keep only `decided_channel` (with `decided_ts`)". Files archived
        before it carry `decided_by` (a Telegram user id) and `decided_via` ("telegram:<id>" | "dashboard:<email>"):
        they still load (ADR 0010; known history, so not counted as schema skew); the identity is dropped on read and
        only its channel kept when the file has none, so it is never written again or sent to the API."""
        if not isinstance(data, dict) or not ({"decided_by", "decided_via"} & data.keys()):
            return data
        data = dict(data)
        by, via = data.pop("decided_by", None), data.pop("decided_via", None)
        if data.get("decided_channel") is None:
            data["decided_channel"] = (decision_channel(via) if isinstance(via, str) and ":" in via   # never a bare id
                                       else "telegram" if by is not None else None)   # decided_by: the in-process bot
        return data

    @property
    def expired(self) -> bool:
        """Undecided and its window over by the wall clock (`window_over`: a future or non-finite `created` counts as
        over). The engine's ApprovalCenter adds its monotonic deadline on top (`ApprovalCenter.expired`)."""
        return self.outcome is None and window_over(self.created, self.window_s, time.time())

    @property
    def money(self) -> tuple[float, str | None] | None:
        """The stop risk and its currency: the account-currency figure, else an older engine's USD one, else None."""
        if self.risk_amount is not None:
            return ceil_cent(self.risk_amount), self.currency
        return (ceil_cent(self.risk_usd), "USD") if self.risk_usd is not None else None

    @property
    def reward_amount(self) -> float | None:
        """Won at the target, same currency as `money`: the stop risk x target distance / stop distance (linear in
        price; before costs). None without a risk or a stop distance."""
        m, dist = self.money, abs(self.entry - self.stop)
        return None if m is None or dist <= 0 else round(m[0] * abs(self.target - self.entry) / dist, 2)

    def money_lines(self, budget: LossBudget | None = None) -> list[str]:
        """Risk, the minimum-lot note and the loss budget left if this loses (docs/ux/approval-card.md)."""
        m = self.money
        if m is None:
            return ["risk: not reported by this engine version"]
        amount, ccy = m
        share = f" ({pct(self.risk_fraction)} of equity)" if self.risk_amount is not None and self.risk_fraction else ""
        tag = " (USD)" if self.risk_amount is None else ""
        lines = [f"risk {cash(amount, ccy)}{tag}{share} if the stop is hit, before costs"]
        if self.min_lot_exception:
            normal = f" the normal {pct(self.risk_normal)}" if self.risk_normal else " the normal risk"
            cap = f", within your {pct(self.risk_cap)} cap" if self.risk_cap else ""
            lines.append(f"smallest trade size: above{normal}{cap}")
        if budget is None:
            lines.append("loss budget left today: unknown")
        elif budget.cant_check_reason is not None:  # never the account cap's roomier amount
            lines.append(f"loss budget left today: can't check ({budget.cant_check_reason})")
        elif floor_cent(budget.remaining) > 0:      # open positions' stops are not reserved: said, not implied
            lines.append(f"{cash(floor_cent(budget.remaining), ccy)} left {budget.period} if this loses {OPEN_ASIDE}")
        else:
            lines.append(f"no loss budget left {budget.period} if this loses {OPEN_ASIDE}: new entries stop")
        return lines

    def text(self, budget: LossBudget | None = None) -> str:
        """The Telegram message: the same essentials, in the same order, as the dashboard approval card. `budget` is
        the account's loss budget if this trade loses (account_view.proposal_budget); None reads as unknown."""
        side = "🟢 LONG" if self.side > 0 else "🔵 SHORT"
        feats = ", ".join(f"{n} {v:+.2f}" for n, v in self.top_features[:3])
        m, reward = self.money, self.reward_amount
        stop_m = f" (−{cash(m[0], m[1])})" if m is not None else ""
        target_m = f" (+{cash(reward, m[1])})" if m is not None and reward is not None else ""
        money = "".join(line + "\n" for line in self.money_lines(budget))
        return (f"{side} {self.lots:.2f} lots XAUUSD\n"
                f"{money}"
                f"entry {self.entry:.2f}  stop {self.stop:.2f}{stop_m}  target {self.target:.2f}{target_m}\n"
                f"p={self.p:.2f}  EV={self.ev_r:+.2f}R  spread {self.spread_points:.0f}pt\n"
                f"why: {feats or 'no feature importances'}\n"
                f"{self.account_id} · {self.agent_id}\n"
                f"⏱ approve within {self.window_s}s or it expires · exits are automatic")


class ApprovalCenter:
    def __init__(self, allowed_user_ids: set[int], totp_verify: Callable[[str], bool] | None = None,
                 on_decision: Callable[[Proposal], None] | None = None, bus: ApprovalBus | None = None,
                 auto_eligible: Callable[[], bool] | None = None, monotonic: Callable[[], float] = time.monotonic):
        self.allowed = set(allowed_user_ids)
        self.totp_verify = totp_verify or (lambda code: False)
        self.on_decision = on_decision or (lambda p: None)
        self.pending: dict[str, Proposal] = {}
        # BACKLOG 60: proposal id -> monotonic deadline (time.monotonic() at `propose` + window_s), so a backward
        # wall-clock step never stretches the owner's window. In memory only, never persisted: a restart drops it with
        # the pending proposals themselves (the engine then holds none, so a proposal left on disk is never executed)
        self.monotonic = monotonic
        self.deadlines: dict[str, float] = {}
        self.log: list[Proposal] = []
        self.mode = "paper"
        self.halted = False
        self.auto_eligible = auto_eligible or (lambda: False)    # A10 evidence; none given: auto is refused
        self.bus = bus                      # set in production: proposals and decisions cross process boundaries

    # ------------------------------------------------------------- proposals
    def expired(self, p: Proposal) -> bool:
        """The proposal's window is over by the wall clock (`Proposal.expired`) or past the monotonic deadline this
        center set when it raised it, whichever comes first: a decision then counts as EXPIRED_UNAPPROVED."""
        deadline = self.deadlines.get(p.proposal_id)
        return p.expired or (p.outcome is None and deadline is not None and self.monotonic() > deadline)

    def propose(self, p: Proposal) -> Proposal:
        self.pending[p.proposal_id] = p
        self.deadlines[p.proposal_id] = self.monotonic() + p.window_s
        if self.bus is not None:
            self.bus.publish(p)
        return p

    def poll_bus(self) -> list[Proposal]:
        """Apply decisions written to the bus by the dashboard or the Telegram service (already authenticated
        there). A decision arriving after the window counts as expired, as it would in-process. A decision is applied
        only to the card the owner saw (BACKLOG 63: "the engine refuses, and logs with a reason (`card_mismatch`), any
        decision whose `created` differs from the pending proposal's, never approving it"; "a decision without
        `created` is refused, not accepted (fail closed)"): the Telegram button and the dashboard request carry the
        card's `created`, and one that does not match the proposal held here was a click on an earlier card with the
        same id (a proposal id depends only on account, agent and bar close). Such a decision is discarded, never
        applied, and the owner is told, so the card shown stays decidable and the dropped click is not silent."""
        if self.bus is None or not self.pending:
            return []
        done = []
        for d in self.bus.decisions_for(set(self.pending)):
            p = self.pending[d.proposal_id]
            if d.card_created is None or d.card_created != p.created:
                why = "card_missing" if d.card_created is None else "card_mismatch"
                log.warning("decision on %s refused (%s): not made on the card now pending; discarded, not applied",
                            p.proposal_id, why)
                self.bus.discard_decision(d.proposal_id)
                self.bus.notify_owner(f"Your tap on proposal {p.proposal_id} was not applied: it was not made on the "
                                      f"card now pending (the proposal was re-raised, or the card was out of date). "
                                      f"No order was placed from it.")
                continue
            if self.expired(p):
                done.append(self._finish(p, Outcome.EXPIRED))
                continue
            p.decided_ts, p.decided_channel = d.ts, decision_channel(d.by)     # never the id or email (BACKLOG 52)
            p.reason_code = None if d.approve else d.reason_code
            done.append(self._finish(p, Outcome.APPROVED if d.approve else Outcome.REJECTED))
        return done

    def decide(self, proposal_id: str, user_id: int, approve: bool, reason_code: str | None = None) -> Proposal:
        if user_id not in self.allowed:
            raise PermissionError("user not allowed")
        p = self.pending.get(proposal_id)
        if p is None:
            raise KeyError("unknown or already decided proposal")
        if self.expired(p):
            return self._finish(p, Outcome.EXPIRED)
        if not approve and reason_code not in REASON_CODES:
            raise ValueError(f"rejection needs a reason code from {REASON_CODES}")
        p.decided_ts, p.decided_channel = time.time(), "telegram"     # the in-process path is the Telegram bot
        p.reason_code = None if approve else reason_code
        return self._finish(p, Outcome.APPROVED if approve else Outcome.REJECTED)

    def sweep_expired(self) -> list[Proposal]:
        done = [self._finish(p, Outcome.EXPIRED) for p in list(self.pending.values()) if self.expired(p)]
        return done

    def _finish(self, p: Proposal, outcome: Outcome) -> Proposal:
        """Archive the decision, then apply it (`on_decision`: the engine re-checks and sends). An approval is archived
        `placing` and cleared only after `on_decision` returns, so the outbox waits for the broker's answer (BACKLOG 72
        review). One that raised stays `placing`: the outbox reports it NOT CONFIRMED once the engine is silent or
        PLACING_MAX_S has passed, never APPROVED."""
        p.outcome = outcome
        p.placing = outcome == Outcome.APPROVED
        self.pending.pop(p.proposal_id, None)
        self.deadlines.pop(p.proposal_id, None)
        self.log.append(p)
        if self.bus is not None:
            self.bus.archive(p)
        self.on_decision(p)
        if p.placing:
            p.placing = False
            if self.bus is not None:
                self.bus.archive(p)
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
