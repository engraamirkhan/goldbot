"""What the Telegram service has to send, decided without the network (so it is testable).

* proposals published on the approval bus that have not been sent yet; once a proposal is finished (approved,
  rejected or expired, on any channel) its messages are edited to show the outcome;
* staff-agent reports: new rows of state/agent_runs.jsonl, each as a short phone-sized message. The first run starts
  at the end of the log, so a fresh install does not replay history.

Progress is kept in state/telegram_outbox.json so a restart neither resends nor drops anything.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field

from goldbot.base import Record
from goldbot.telegram.approvals import Proposal
from goldbot.telegram.bus import ApprovalBus

MAX_MESSAGE = 3900                 # Telegram's limit is 4096 characters


class OutboxState(Record):
    sent: dict[str, list[tuple[int, int]]] = Field(default_factory=dict)    # proposal id -> [(chat id, message id)]
    reports_offset: int | None = None                                        # lines of agent_runs.jsonl already handled


class Outbox:
    def __init__(self, state_dir: str | Path, bus: ApprovalBus | None = None):
        self.dir = Path(state_dir)
        self.bus = bus or ApprovalBus(self.dir)
        self.path = self.dir / "telegram_outbox.json"
        self.state = OutboxState.model_validate_json(self.path.read_text()) if self.path.exists() else OutboxState()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(self.state.model_dump_json())
        tmp.replace(self.path)

    # ------------------------------------------------------------------ proposals
    def unsent_proposals(self) -> list[Proposal]:
        return [p for p in self.bus.pending() if p.proposal_id not in self.state.sent]

    def mark_sent(self, proposal_id: str, messages: list[tuple[int, int]]) -> None:
        self.state.sent[proposal_id] = messages
        self.save()

    def finished(self) -> list[tuple[str, str, list[tuple[int, int]]]]:
        """(proposal id, outcome, messages) for sent proposals that are finished, forgotten once returned. A proposal
        that vanished without an archive (engine restarted before its window ended) is reported as expired."""
        out = []
        for pid, msgs in list(self.state.sent.items()):
            outcome = self.bus.outcome(pid)
            if outcome is None:
                p = self.bus.get(pid)
                if p is not None and not p.expired:
                    continue                                 # still open
                outcome = "EXPIRED_UNAPPROVED"
            out.append((pid, outcome, msgs))
            del self.state.sent[pid]
        if out:
            self.save()
        return out

    # ------------------------------------------------------------------ agent reports
    def new_reports(self) -> list[str]:
        log = self.dir / "agent_runs.jsonl"
        lines = log.read_text().splitlines() if log.exists() else []
        if self.state.reports_offset is None or self.state.reports_offset > len(lines):
            self.state.reports_offset = len(lines)          # first start (or a truncated log): no replay
            self.save()
            return []
        out = []
        for line in lines[self.state.reports_offset:]:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.append(self._report_message(row))
        self.state.reports_offset = len(lines)
        self.save()
        return out

    def _report_message(self, row: dict) -> str:
        title = str(row.get("role", "agent")).replace("_", " ")
        head = f"📋 {title} · {row.get('status')} · ${float(row.get('cost_usd') or 0):.2f}"
        body = ""
        rp = row.get("report_path")
        p = Path(rp) if rp else None
        if p is not None and p.exists() and self.dir.resolve() in p.resolve().parents:   # only reports in the state dir
            body = "\n".join(x for x in p.read_text().splitlines() if not x.startswith("<!--")).strip()
        elif row.get("detail"):
            body = str(row["detail"])
        text = f"{head}\n\n{body}" if body else head
        if len(text) > MAX_MESSAGE:
            more = "\n\n… full report on the dashboard (Agents)"
            text = text[:MAX_MESSAGE - len(more)].rstrip() + more
        return text
