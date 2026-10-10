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
Writers here are authenticated before they call the bus (API role check, Telegram allow-list); the files are only
reachable on the VPS.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from goldbot.base import Record, create_exclusive, write_atomic
from goldbot.telegram.approvals import REASON_CODES, Proposal


class BusDecision(Record):
    proposal_id: str
    approve: bool
    reason_code: str | None = None
    by: str                         # "dashboard:<email>" | "telegram:<user id>"
    ts: float


class Control(Record):
    halted: bool = False
    by: str | None = None
    ts: float = 0.0
    reason: str | None = None
    rearm_id: str | None = None     # new on every owner re-arm; engines clear a drawdown halt once per id
    rearm_by: str | None = None
    rearm_ts: float = 0.0


def _write_atomic(path: Path, text: str) -> None:
    write_atomic(path, text)                    # durable: proposals, outcomes and the owner's halt survive a power cut


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
                try:
                    out.append(BusDecision.model_validate_json(f.read_text()))
                except ValueError:
                    continue          # half-written by a crashed writer; the window will expire it
        return out

    def archive(self, p: Proposal) -> None:
        """Called by the engine when a proposal is finished (approved, rejected or expired)."""
        _write_atomic(self.done_dir / f"{p.proposal_id}.json", p.model_dump_json())
        for d in (self.pending_dir, self.decisions_dir):
            (d / f"{p.proposal_id}.json").unlink(missing_ok=True)

    # ------------------------------------------------------------------ decider side (API, Telegram)
    def pending(self) -> list[Proposal]:
        out = []
        for f in sorted(self.pending_dir.glob("*.json")):
            try:
                p = Proposal.model_validate_json(f.read_text())
            except (ValueError, OSError):
                continue
            if not p.expired and not (self.decisions_dir / f.name).exists():
                out.append(p)
        return out

    def get(self, proposal_id: str) -> Proposal | None:
        for d in (self.pending_dir, self.done_dir):
            f = d / f"{proposal_id}.json"
            if f.exists():
                return Proposal.model_validate_json(f.read_text())
        return None

    def submit(self, proposal_id: str, approve: bool, reason_code: str | None, by: str) -> BusDecision:
        """Record a decision; raises KeyError (unknown, expired or already decided) or ValueError (reason code)."""
        if not approve and reason_code not in REASON_CODES:
            raise ValueError(f"rejection needs a reason code from {REASON_CODES}")
        f = self.pending_dir / f"{proposal_id}.json"
        if not f.exists():
            raise KeyError("unknown or already decided proposal")
        if Proposal.model_validate_json(f.read_text()).expired:
            raise KeyError("proposal expired")
        d = BusDecision(proposal_id=proposal_id, approve=approve, reason_code=None if approve else reason_code, by=by,
                        ts=time.time())
        # first decision wins, and it is written whole before it becomes visible (no empty decision after a crash)
        if not create_exclusive(self.decisions_dir / f"{proposal_id}.json", d.model_dump_json()):
            raise KeyError("already decided")
        return d

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
                    rows.append((mtime, Proposal.model_validate_json(f.read_text()), None))
            except (ValueError, OSError):
                continue
        for f in self.decisions_dir.glob("*.json"):
            pf = self.pending_dir / f.name
            try:
                d = BusDecision.model_validate_json(f.read_text())
                if d.ts >= cutoff and pf.exists():
                    rows.append((d.ts, Proposal.model_validate_json(pf.read_text()), d))
            except (ValueError, OSError):
                continue
        rows.sort(key=lambda r: r[0], reverse=True)
        return [(p, d) for _, p, d in rows[:limit]]

    def outcome(self, proposal_id: str) -> str | None:
        f = self.done_dir / f"{proposal_id}.json"
        if not f.exists():
            return None
        o = Proposal.model_validate_json(f.read_text()).outcome
        return o.value if o is not None else None

    # ------------------------------------------------------------------ owner halt
    def control(self) -> Control:
        try:
            return Control.model_validate_json(self.control_path.read_text()) if self.control_path.exists() else Control()
        except ValueError:
            return Control(halted=True, reason="unreadable control.json")    # fail closed for entries

    def set_halt(self, halted: bool, by: str, reason: str | None = None) -> Control:
        prev = self.control()
        c = Control(halted=halted, by=by, ts=time.time(), reason=reason, rearm_id=prev.rearm_id, rearm_by=prev.rearm_by,
                    rearm_ts=prev.rearm_ts)
        _write_atomic(self.control_path, c.model_dump_json())
        return c

    def owner_rearm(self, by: str) -> Control:
        """The owner's re-arm, called only after the owner role and a TOTP code were verified: clears the owner halt
        and issues a new rearm id, which the engines answer by clearing their drawdown halt."""
        now = time.time()
        c = Control(halted=False, by=by, ts=now, rearm_id=uuid.uuid4().hex, rearm_by=by, rearm_ts=now)
        _write_atomic(self.control_path, c.model_dump_json())
        return c
