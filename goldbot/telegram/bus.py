"""Cross-process approval bus and owner halt flag (files under state/, all services run on the one VPS).

The engines, the API and the Telegram service are separate processes, so a proposal and its decision travel through
the state directory:

* an engine publishes each proposal to `approvals/pending/<id>.json`;
* the dashboard or the Telegram bot writes `approvals/decisions/<id>.json`, created exclusively, so the first
  decision wins and a second click elsewhere is refused;
* the engine applies decisions on its next tick (re-running the RiskGate at approval time) and archives both files
  in `approvals/done/` with the outcome. A proposal nobody decides within its window expires in the engine.

`control.json` carries the owner's halt: no new entries on any engine until an owner re-arms. Exits are never gated.
An owner re-arm (dashboard, owner role + TOTP) also writes a fresh `rearm_id`; each engine that sees a new id clears
its own 12% drawdown halt (RiskGate.rearm). Nothing else writes a rearm id, so no halt clears without that action.
`control.json` also carries the owner's approval mode (`/mode auto|propose|account`, design: Operating mode, row A10):
None until the owner first sets it, and again after `/mode account` (ADR 0041): each engine then uses its account's own
mode. `auto` is written only after the evidence check and a TOTP (goldbot/telegram/automode.py). An engine's 12% kill switch writes it back to
`propose`. The re-arm lock and the kill switch still force propose-and-approve inside the engine whatever it says.
Writers here are authenticated before they call the bus (API role check, Telegram allow-list); the files are only
reachable on the VPS. Who decided, halted or re-armed is not kept here (A15, the follow-up to BACKLOG 52): a decision
file and the halt and re-arm in control.json record only the channel ("dashboard" | "telegram"); the identity goes to
the audit log (state/aux.db), written by the dashboard and the Telegram service. An engine start sweeps decision files
left behind (`sweep_decisions`).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Literal

from goldbot.base import (
    Record,
    create_exclusive,
    load_record_json,
    note_schema_skew,
    unknown_fields_json,
    write_atomic,
)
from goldbot.telegram.approvals import (
    INVALID_PROPOSAL_ID,
    REASON_CODES,
    Outcome,
    Proposal,
    decision_channel,
    valid_proposal_id,
)

log = logging.getLogger(__name__)
UNCONFIRMED_LISTED_S = 8 * 86400.0      # BACKLOG 72: an unresolved order's card is listed at most this long
UNRESOLVED = ("pending", "still_unknown")


class BusDecision(Record):
    proposal_id: str
    approve: bool
    reason_code: str | None = None
    by: str                         # "dashboard" | "telegram": the channel only (A15); older files: "<channel>:<id>"
    ts: float
    # the `created` of the card the owner decided on, sent by the Telegram button or the dashboard request (BACKLOG 63):
    # the engine applies the decision only to that card, never to a proposal raised again under the same id. None (a
    # file from a release before BACKLOG 60) is refused by the engine, never applied (fail closed)
    card_created: float | None = None


class Control(Record):
    halted: bool = False
    by: str | None = None           # the channel that halted or re-armed (A15); older files: "<channel>:<id>"
    ts: float = 0.0
    reason: str | None = None
    rearm_id: str | None = None     # new on every owner re-arm; engines clear a drawdown halt once per id
    rearm_by: str | None = None
    rearm_ts: float = 0.0
    approval_mode: Literal["propose", "auto"] | None = None    # the owner's /mode; None: each account's own setting
    mode_by: str | None = None
    mode_ts: float = 0.0            # last mode change: the auto-mode evidence counts from here
    mode_reason: str | None = None


DECISION_MAX_AGE_S = 86_400.0      # a decision file this old is long past any proposal's window: swept at engine start
ApprovalMode = Literal["propose", "auto"]
_MODE_FIELDS = ("approval_mode", "mode_by", "mode_ts", "mode_reason")


def _write_atomic(path: Path, text: str) -> None:
    write_atomic(path, text)                    # durable: proposals, outcomes and the owner's halt survive a power cut


def _stored_by(by: str | None) -> str | None:
    """Who acted, as control.json keeps it (A15): the channel for a person, "engine:<account>" for a kill switch."""
    return by if by is None or by.startswith("engine:") else decision_channel(by)


def _without_identity(c: Control) -> Control:
    """Every control.json rewrite: an identity a pre-A15 file carried in `by`, `rearm_by` or `mode_by` is cut to its
    channel rather than copied forward (A15 review L4). `rearm_id` and every other field are kept as they are."""
    return c.model_copy(update={k: _stored_by(getattr(c, k)) for k in ("by", "rearm_by", "mode_by")})


def read_control(state_dir: str | Path) -> Control:
    """state/control.json, read only (no approvals directories created, unlike ApprovalBus()): health's
    entry_status:<account> reads it through goldbot/api/entry_status.py and must never write the state dir."""
    path = Path(state_dir) / "control.json"
    # strict on purpose (ADR 0010 allowlist): a field this release does not know could be a halt condition it
    # cannot honour, so an unknown field reads as unreadable, i.e. halted
    try:
        return Control.model_validate_json(path.read_text()) if path.exists() else Control()
    except ValueError:
        # fail closed for entries, and back to propose-and-approve for any engine that trades again
        return Control(halted=True, reason="unreadable control.json", approval_mode="propose")


class ApprovalBus:
    def __init__(self, state_dir: str | Path):
        self.root = Path(state_dir) / "approvals"
        self.pending_dir, self.decisions_dir, self.done_dir = (self.root / d for d in ("pending", "decisions", "done"))
        for d in (self.pending_dir, self.decisions_dir, self.done_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.control_path = Path(state_dir) / "control.json"

    # ------------------------------------------------------------------ engine side
    def publish(self, p: Proposal) -> None:
        _write_atomic(self.pending_dir / f"{p.proposal_id}.json", p.model_dump_json())

    def decisions_for(self, proposal_ids: set[str]) -> list[BusDecision]:
        out = []
        for pid in sorted(proposal_ids):
            f = self.decisions_dir / f"{pid}.json"
            if f.exists():
                text = ""                 # read_text can raise UnicodeDecodeError (a ValueError) before assigning
                try:
                    text = f.read_text()
                    # strict on purpose (ADR 0010 allowlist): a decision is never applied without a field this release
                    # does not know (a newer condition or size limit); it is skipped and the proposal expires
                    out.append(BusDecision.model_validate_json(text))
                except ValueError:
                    # from a newer release: logged once per proposal and counted, so the click does not vanish
                    # unexplained; otherwise half-written by a crashed writer. Either way the window expires it.
                    if unknown := unknown_fields_json(BusDecision, text):
                        note_schema_skew(f"approvals/decisions:{pid}", "BusDecision", unknown, refused=True)
                    continue
        return out

    def archive(self, p: Proposal) -> None:
        """Called by the engine when a proposal is finished (approved, rejected or expired). The owner's decision time
        and channel are kept on the archived proposal (finance audit GAP-10): taken from the decision file, which is
        deleted here, when the proposal does not already carry them. Only for an approved or rejected proposal: an
        expired one, or a stale one archived on restart (outcome None), was decided by nobody, whatever click came
        late. An unreadable decision file (malformed, newer, or an OSError) is "no decision": the archive and the
        engine start that calls it never fail on it (trading-safety review LOW-2, LOW-3)."""
        if p.decided_ts is None and p.outcome in (Outcome.APPROVED, Outcome.REJECTED):
            try:
                for dec in self.decisions_for({p.proposal_id}):             # strict; malformed or newer: none
                    p = p.model_copy(update={"decided_ts": dec.ts, "decided_channel": decision_channel(dec.by)})
            except OSError:
                pass
        _write_atomic(self.done_dir / f"{p.proposal_id}.json", p.model_dump_json())
        for d in (self.pending_dir, self.decisions_dir):
            (d / f"{p.proposal_id}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------------ decider side (API, Telegram)
    def pending(self) -> list[Proposal]:
        out = []
        for f in sorted(self.pending_dir.glob("*.json")):
            try:
                p = load_record_json(Proposal, f.read_text(), source="approvals/pending")
            except (ValueError, OSError):
                continue
            if not p.expired and not (self.decisions_dir / f.name).exists():
                out.append(p)
        return out

    def get(self, proposal_id: str) -> Proposal | None:
        """None for an unknown id and for one `valid_proposal_id` refuses (never a path outside approvals/)."""
        if not valid_proposal_id(proposal_id):
            return None
        for d in (self.pending_dir, self.done_dir):
            f = d / f"{proposal_id}.json"
            if f.exists():
                return load_record_json(Proposal, f.read_text(), source=f"approvals/{d.name}")
        return None

    def submit(self, proposal_id: str, approve: bool, reason_code: str | None, by: str, *,
               card_created: float | None) -> BusDecision:
        """Record a decision; raises KeyError (unknown, expired, already decided, or `card_mismatch`) or ValueError
        (reason code, `card_missing`, an id `valid_proposal_id` refuses, an approval of a card that is not
        `approvable`: a terminal fault, backlog 103). `by` is the channel; an identity after ":" is dropped, so the
        file never carries it (A15: "the decision file keeps only the channel; the identity is in the audit log").
        `card_created` is the
        `created` of the card the owner decided on (BACKLOG 63: "a tap is bound to the exact card the owner saw"):
        None is refused (an old card or client, fail closed), and one that differs from the pending proposal's is
        refused here and again by the engine, which may hold a proposal re-raised after this check."""
        if not valid_proposal_id(proposal_id):          # a file name under approvals/: never "..", "/" or empty
            raise ValueError(INVALID_PROPOSAL_ID)
        if card_created is None:
            raise ValueError("card_missing: this card is out of date; decide on the current card")
        if not approve and reason_code not in REASON_CODES:
            raise ValueError(f"rejection needs a reason code from {REASON_CODES}")
        f = self.pending_dir / f"{proposal_id}.json"
        if not f.exists():
            raise KeyError("unknown or already decided proposal")
        card = load_record_json(Proposal, f.read_text(), source="approvals/pending")
        if card.expired:
            raise KeyError("proposal expired")
        if card_created != card.created:
            log.warning("decision on %s refused (card_mismatch): made on another card with the same id", proposal_id)
            raise KeyError("card_mismatch: this card was replaced; decide on the current card")
        if approve and (why := card.approve_refusal) is not None:   # backlog 103: real money may be behind it
            raise ValueError(why)
        d = BusDecision(proposal_id=proposal_id, approve=approve, reason_code=None if approve else reason_code,
                        by=decision_channel(by), ts=time.time(),     # never the id or email on disk (A15)
                        card_created=card_created)
        # first decision wins, and it is written whole before it becomes visible (no empty decision after a crash)
        if not create_exclusive(self.decisions_dir / f"{proposal_id}.json", d.model_dump_json()):
            raise KeyError("already decided")
        return d

    def discard_decision(self, proposal_id: str) -> None:
        """Delete one decision file the engine refused to apply (a click on an earlier card with the same id, F60-1),
        so the card shown is listed and decidable again. Safe against a new click: `submit` creates the file
        exclusively, so none can land while the refused one is still there. An OSError leaves it (the window expires
        the proposal). An id `valid_proposal_id` refuses is ignored (never a path outside approvals/)."""
        if not valid_proposal_id(proposal_id):
            return
        try:
            (self.decisions_dir / f"{proposal_id}.json").unlink(missing_ok=True)
        except OSError:
            pass

    def notify_owner(self, text: str) -> None:
        """A Telegram notice to the owner that a tap was not applied, so a dropped click is never silent (F60 re-verify
        finding 3): one "notice" row in state/agent_runs.jsonl, which the Telegram service relays (as the API's
        owner notices). Best effort: a failed write is logged, never raised; the proposal is never applied either way."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        row = {"role": "approvals", "started_utc": now, "finished_utc": now, "status": "notice", "turns": 0,
               "cost_usd": 0.0, "report_path": None, "detail": text}
        try:
            with open(self.root.parent / "agent_runs.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            log.warning("could not queue the owner notice: %s", exc)

    def sweep_decisions(self, max_age_s: float = DECISION_MAX_AGE_S) -> int:
        """Delete decision files left behind: those with no pending file (the proposal was archived, e.g. by a process
        that stopped between the two unlinks in `archive`) and those older than `max_age_s` by file time (long past
        any window; the engine archives the proposal itself). Read by name and age only, so a torn or newer file goes
        too. Called at engine start (A15); returns how many were deleted. A file that cannot be removed is skipped."""
        cutoff, n = time.time() - max_age_s, 0
        for f in self.decisions_dir.glob("*.json"):
            try:
                if not (self.pending_dir / f.name).exists() or f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    n += 1
            except OSError:
                continue
        return n

    def recent(self, max_age_s: float = 600.0, limit: int = 20) -> list[tuple[Proposal, BusDecision | None]]:
        """Proposals decided in the last `max_age_s`, newest first, for the dashboard's decided cards: those the engine
        has finished (archived with an outcome, decision None) and those decided but not yet applied by the engine
        (outcome None, with the decision). BACKLOG 72: an approval whose order the broker has not confirmed yet
        (`not_confirmed` pending or still unknown) stays listed, first, until the engine resolves it (at most
        UNCONFIRMED_LISTED_S, past the engine's 7-day look-up); a resolved one follows the window from its resolution
        (the engine rewrites the file then)."""
        now = time.time()
        cutoff, unresolved_cutoff = now - max_age_s, now - UNCONFIRMED_LISTED_S
        rows: list[tuple[float, Proposal, BusDecision | None]] = []
        for f in self.done_dir.glob("*.json"):
            try:
                mtime = f.stat().st_mtime
                if mtime >= cutoff:
                    rows.append((mtime, load_record_json(Proposal, f.read_text(), source="approvals/done"), None))
                elif mtime >= unresolved_cutoff:
                    p = load_record_json(Proposal, f.read_text(), source="approvals/done")
                    if p.not_confirmed is not None and p.not_confirmed.outcome in UNRESOLVED:
                        rows.append((now, p, None))
            except (ValueError, OSError):
                continue
        for f in self.decisions_dir.glob("*.json"):
            pf = self.pending_dir / f.name
            try:
                d = BusDecision.model_validate_json(f.read_text())          # strict, as decisions_for
                if d.ts >= cutoff and pf.exists():
                    rows.append((d.ts, load_record_json(Proposal, pf.read_text(), source="approvals/pending"), d))
            except (ValueError, OSError):
                continue
        rows.sort(key=lambda r: r[0], reverse=True)
        return [(p, d) for _, p, d in rows[:limit]]

    def outcome(self, proposal_id: str) -> str | None:
        """The archived proposal's outcome; None while undecided, for an unknown id and for one `valid_proposal_id`
        refuses (never a path outside approvals/, as get/submit)."""
        if not valid_proposal_id(proposal_id):
            return None
        f = self.done_dir / f"{proposal_id}.json"
        if not f.exists():
            return None
        o = load_record_json(Proposal, f.read_text(), source="approvals/done").outcome
        return o.value if o is not None else None

    # ------------------------------------------------------------------ owner halt
    def control(self) -> Control:
        return read_control(self.control_path.parent)

    def set_halt(self, halted: bool, by: str, reason: str | None = None) -> Control:
        """`by` is the channel; an identity after ":" is dropped (A15: the identity is in the audit log)."""
        prev = self.control()
        c = _without_identity(Control(halted=halted, by=by, ts=time.time(), reason=reason, rearm_id=prev.rearm_id,
                                      rearm_by=prev.rearm_by, rearm_ts=prev.rearm_ts,
                                      **{k: getattr(prev, k) for k in _MODE_FIELDS}))
        _write_atomic(self.control_path, c.model_dump_json())
        return c

    def owner_rearm(self, by: str) -> Control:
        """The owner's re-arm, called only after the owner role and a TOTP code were verified: clears the owner halt
        and issues a new rearm id, which the engines answer by clearing their drawdown halt. `by` is kept as its
        channel only, as for a halt (A15)."""
        now = time.time()
        prev = self.control()
        by = decision_channel(by)
        c = _without_identity(Control(halted=False, by=by, ts=now, rearm_id=uuid.uuid4().hex, rearm_by=by, rearm_ts=now,
                                      **{k: getattr(prev, k) for k in _MODE_FIELDS}))
        _write_atomic(self.control_path, c.model_dump_json())
        return c

    def set_mode(self, mode: ApprovalMode | None, by: str, reason: str | None = None) -> Control:
        """The owner's approval mode. Callers check authority first: `auto` only after the evidence check and a TOTP
        (goldbot/telegram/automode.py); `propose` lowers authority and needs neither; None (`/mode account`, ADR 0041)
        hands each engine back its account's own mode, which the engine's lock keeps demo-only for auto. The halt and
        re-arm are kept; the mode epoch (`mode_ts`, the auto-mode evidence) restarts on every change.
        `by` is kept as its channel (A15), except an engine's kill switch, which keeps "engine:<account>"."""
        c = _without_identity(self.control().model_copy(update={"approval_mode": mode, "mode_by": by,
                                                                "mode_ts": time.time(), "mode_reason": reason}))
        _write_atomic(self.control_path, c.model_dump_json())
        return c
