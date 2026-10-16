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
`control.json` also carries the owner's approval mode (`/mode auto|propose`, design: Operating mode, row A10): None
until the owner first sets it (the engine then uses its configured mode), and only the Telegram service writes `auto`,
after the evidence check and a TOTP (goldbot/telegram/automode.py). An engine's 12% kill switch writes it back to
`propose`. The re-arm lock and the kill switch still force propose-and-approve inside the engine whatever it says.
Writers here are authenticated before they call the bus (API role check, Telegram allow-list); the files are only
reachable on the VPS. Who decided, halted or re-armed is not kept here (A15, the follow-up to BACKLOG 52): a decision
file and the halt and re-arm in control.json record only the channel ("dashboard" | "telegram"); the identity goes to
the audit log (state/aux.db), written by the dashboard and the Telegram service. An engine start sweeps decision files
left behind (`sweep_decisions`).
"""
from __future__ import annotations

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
from goldbot.telegram.approvals import REASON_CODES, Outcome, Proposal, decision_channel


class BusDecision(Record):
    proposal_id: str
    approve: bool
    reason_code: str | None = None
    by: str                         # "dashboard" | "telegram": the channel only (A15); older files: "<channel>:<id>"
    ts: float


class Control(Record):
    halted: bool = False
    by: str | None = None           # the channel that halted or re-armed (A15); older files: "<channel>:<id>"
    ts: float = 0.0
    reason: str | None = None
    rearm_id: str | None = None     # new on every owner re-arm; engines clear a drawdown halt once per id
    rearm_by: str | None = None
    rearm_ts: float = 0.0
    approval_mode: Literal["propose", "auto"] | None = None    # the owner's /mode; None: never set (engine config)
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
        for d in (self.pending_dir, self.done_dir):
            f = d / f"{proposal_id}.json"
            if f.exists():
                return load_record_json(Proposal, f.read_text(), source=f"approvals/{d.name}")
        return None

    def submit(self, proposal_id: str, approve: bool, reason_code: str | None, by: str) -> BusDecision:
        """Record a decision; raises KeyError (unknown, expired or already decided) or ValueError (reason code). `by`
        is the channel; an identity after ":" is dropped, so the file never carries it (A15: "the decision file keeps
        only the channel; the identity is in the audit log")."""
        if not approve and reason_code not in REASON_CODES:
            raise ValueError(f"rejection needs a reason code from {REASON_CODES}")
        f = self.pending_dir / f"{proposal_id}.json"
        if not f.exists():
            raise KeyError("unknown or already decided proposal")
        if load_record_json(Proposal, f.read_text(), source="approvals/pending").expired:
            raise KeyError("proposal expired")
        d = BusDecision(proposal_id=proposal_id, approve=approve, reason_code=None if approve else reason_code,
                        by=decision_channel(by), ts=time.time())     # never the id or email on disk (A15)
        # first decision wins, and it is written whole before it becomes visible (no empty decision after a crash)
        if not create_exclusive(self.decisions_dir / f"{proposal_id}.json", d.model_dump_json()):
            raise KeyError("already decided")
        return d

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
        (outcome None, with the decision)."""
        cutoff = time.time() - max_age_s
        rows: list[tuple[float, Proposal, BusDecision | None]] = []
        for f in self.done_dir.glob("*.json"):
            try:
                mtime = f.stat().st_mtime
                if mtime >= cutoff:
                    rows.append((mtime, load_record_json(Proposal, f.read_text(), source="approvals/done"), None))
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
        f = self.done_dir / f"{proposal_id}.json"
        if not f.exists():
            return None
        o = load_record_json(Proposal, f.read_text(), source="approvals/done").outcome
        return o.value if o is not None else None

    # ------------------------------------------------------------------ owner halt
    def control(self) -> Control:
        # strict on purpose (ADR 0010 allowlist): a field this release does not know could be a halt condition it
        # cannot honour, so an unknown field reads as unreadable, i.e. halted
        try:
            return Control.model_validate_json(self.control_path.read_text()) if self.control_path.exists() else Control()
        except ValueError:
            # fail closed for entries, and back to propose-and-approve for any engine that trades again
            return Control(halted=True, reason="unreadable control.json", approval_mode="propose")

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

    def set_mode(self, mode: ApprovalMode, by: str, reason: str | None = None) -> Control:
        """The owner's approval mode. Callers check authority first: `auto` only after the evidence check and a TOTP
        (goldbot/telegram/automode.py); `propose` lowers authority and needs neither. The halt and re-arm are kept.
        `by` is kept as its channel (A15), except an engine's kill switch, which keeps "engine:<account>"."""
        c = _without_identity(self.control().model_copy(update={"approval_mode": mode, "mode_by": by,
                                                                "mode_ts": time.time(), "mode_reason": reason}))
        _write_atomic(self.control_path, c.model_dump_json())
        return c
