"""What the Telegram service has to send, decided without the network (so it is testable).

* proposals published on the approval bus that have not been sent yet; once a proposal is finished (approved,
  rejected or expired, on any channel) its messages are edited to show the outcome;
* staff-agent reports: new rows of aux.db table agent_runs, each as a short phone-sized message, and owner notices
  (status "notice"), which the API appends to state/agent_runs.jsonl. The first run starts at the end of both, so a
  fresh install does not replay history. Reports are at least once: `pending_reports` reads, and the cursor moves
  only when the service calls `ack_reports` after every message went out.

Progress is kept in aux.db (tables outbox_sent, outbox_cursor; state store step 3) and every change commits under
BEGIN IMMEDIATE, so a restart neither resends a proposal nor drops a report. state/telegram_outbox.json is imported
once on first start (its line offset becomes the agent_runs id cursor; the original is kept in state/pre-db/), and
for one release it is rewritten in its old format after every commit, so a rolled-back release neither re-sends
proposals nor replays reports; what that release writes there is merged back on the next start (`dual_read`).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from goldbot.base import FrozenRecord, Record, load_record_json
from goldbot.db import Database
from goldbot.db.bookkeeping import (
    OUTBOX_FILE,
    RUNS_FILE,
    AgentRunLog,
    complete_lines,
    cursor_from_line_offset,
    dual_read,
    export_latest,
    max_run_id,
    outbox_cursor,
    outbox_document,
    outbox_forget,
    outbox_mark_sent,
    outbox_sent,
    runs_after,
    set_outbox_cursor,
    table_empty,
)
from goldbot.db.import_state import ImportSpec
from goldbot.telegram.approvals import Proposal
from goldbot.telegram.bus import ApprovalBus

log = logging.getLogger(__name__)

MAX_MESSAGE = 3900                 # Telegram's limit is 4096 characters
MARK_RETRY_S = 2.0                 # first retry of a failed mark_sent; doubles up to MARK_RETRY_MAX_S
MARK_RETRY_MAX_S = 300.0


class OutboxState(Record):
    """The pre-database file format (state/telegram_outbox.json): imported once, then exported for a rollback."""
    sent: dict[str, list[tuple[int, int]]] = Field(default_factory=dict)    # proposal id -> [(chat id, message id)]
    reports_offset: int | None = None                                        # lines of agent_runs.jsonl already handled


class ReportsAck(FrozenRecord):
    """Where the report cursor moves once the messages from `pending_reports` were all sent."""
    reports_id: int
    notices_line: int


def import_outbox_json(path: Path, conn: sqlite3.Connection) -> int:
    """telegram_outbox.json -> outbox_sent + outbox_cursor, only while both are empty. Runs after the agent_runs
    import: the old line offset becomes the id of the last imported run already relayed, and stays the line from
    which owner notices are read."""
    if not (table_empty(conn, "outbox_sent") and table_empty(conn, "outbox_cursor")):
        return 0
    return merge_outbox_json(path, conn)


def merge_outbox_json(path: Path, conn: sqlite3.Connection, pending: set[str] | None = None,
                      archived: Callable[[str], bool] | None = None) -> int:
    """Merge a file an older release wrote after a rollback, as a union: every proposal it lists is sent (its
    messages win) unless the bus has already archived it with an outcome (`archived`: its outcome edit went out, so
    a stale file must not bring it back and repeat the edit); a proposal only the DB lists is kept while it is still
    pending on the bus (`pending`; None keeps all), so neither side's send is repeated and an outcome that release
    already reported is not reported again. Each report cursor takes the larger of the two positions (nothing is
    replayed twice)."""
    st = load_record_json(OutboxState, path.read_text(), source="telegram_outbox.json")
    st_sent = {pid: m for pid, m in st.sent.items() if archived is None or not archived(pid)}
    keep = set(st_sent) | {pid for pid in outbox_sent(conn) if pending is None or pid in pending}
    for pid in outbox_sent(conn):
        if pid not in keep:
            outbox_forget(conn, pid)
    for pid, msgs in st_sent.items():
        outbox_mark_sent(conn, pid, msgs)
    if st.reports_offset is not None:
        rid, line = cursor_from_line_offset(conn, st.reports_offset), st.reports_offset
        cur = outbox_cursor(conn)
        if cur is not None:
            rid, line = max(rid, cur[0]), max(line, cur[1])
        set_outbox_cursor(conn, rid, line)
    return len(st_sent) + (st.reports_offset is not None)


OUTBOX_IMPORT = ImportSpec(source=OUTBOX_FILE, kind="aux", loader=import_outbox_json, once=True, backup=True)


class Outbox:
    def __init__(self, state_dir: str | Path, bus: ApprovalBus | None = None):
        self.dir = Path(state_dir)
        self.bus = bus or ApprovalBus(self.dir)
        AgentRunLog(self.dir)                     # imports/adopts agent_runs.jsonl first (the cursor maps onto it)
        self.db = Database.open(self.dir, "aux")
        pending = {p.proposal_id for p in self.bus.pending()}
        def archived(pid: str) -> bool:
            return self.bus.outcome(pid) is not None
        if dual_read(self.db, OUTBOX_IMPORT, self.dir,
                     lambda path, conn: merge_outbox_json(path, conn, pending, archived), outbox_document):
            self._export()

    def _export(self) -> None:
        """The old-format telegram_outbox.json for a rollback, after the commit; a failure is logged, never raised."""
        export_latest(self.db, OUTBOX_FILE, self.dir / OUTBOX_FILE, outbox_document)

    @property
    def sent(self) -> dict[str, list[tuple[int, int]]]:
        with self.db.connection() as conn:
            return outbox_sent(conn)

    # ------------------------------------------------------------------ proposals
    def unsent_proposals(self) -> list[Proposal]:
        sent = self.sent
        return [p for p in self.bus.pending() if p.proposal_id not in sent]

    def mark_sent(self, proposal_id: str, messages: list[tuple[int, int]]) -> None:
        with self.db.write() as conn:
            outbox_mark_sent(conn, proposal_id, messages)
        self._export()

    def finished(self) -> list[tuple[str, str, list[tuple[int, int]]]]:
        """(proposal id, outcome, messages) for sent proposals that are finished, forgotten once returned. A proposal
        that vanished without an archive (engine restarted before its window ended) is reported as expired, and so is
        one archived with no outcome (a restarted engine withdrew it; it is never raised again): its card loses its
        buttons at once rather than at the end of its window (F60 re-verify finding 1). Each is returned once: the
        row is deleted in the same commit that decides to report it."""
        out = []
        for pid, msgs in self.sent.items():
            outcome = self.bus.outcome(pid)
            if outcome is None:
                p = self.bus.get(pid)
                if p is not None and not p.expired and (self.bus.pending_dir / f"{pid}.json").exists():
                    continue                                 # still open
                outcome = "EXPIRED_UNAPPROVED"
            elif outcome == "APPROVED":
                p = self.bus.get(pid)
                if p is not None and p.gate_refusal:         # approved, but the RiskGate re-check refused the order
                    outcome = "GATE_REFUSED: " + ", ".join(p.gate_refusal)
            with self.db.write() as conn:
                if outbox_forget(conn, pid):
                    out.append((pid, outcome, msgs))
        if out:
            self._export()
        return out

    # ------------------------------------------------------------------ agent reports
    def pending_reports(self) -> tuple[list[str], ReportsAck | None]:
        """Messages for staff-agent runs recorded since the last ack, then owner notices appended since, and the ack
        that moves past them. Nothing moves until `ack_reports`: a failed send is retried on the next pass."""
        lines = complete_lines(self.dir / RUNS_FILE)
        with self.db.connection() as conn:
            cur = outbox_cursor(conn)
            if cur is None:
                start = ReportsAck(reports_id=max_run_id(conn), notices_line=len(lines))
            else:
                runs = runs_after(conn, cur[0])
        if cur is None:                                     # first start: no replay
            self.ack_reports(start)
            return [], None
        reports_id, line = cur
        line = min(line, len(lines))                        # the mirror log was truncated: start at its end
        notices = []
        for raw in lines[line:]:
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("status") == "notice":
                notices.append(row)
        ack = ReportsAck(reports_id=runs[-1][0] if runs else reports_id, notices_line=len(lines))
        if not runs and not notices:
            if ack.notices_line != cur[1]:
                self.ack_reports(ack)                       # only non-notice lines passed: nothing to send
            return [], None
        return [self._report_message(r) for _, r in runs] + [self._report_message(r) for r in notices], ack

    def ack_reports(self, ack: ReportsAck) -> None:
        """Move the report cursor after every message was sent (never backwards past a newer ack)."""
        with self.db.write() as conn:
            cur = outbox_cursor(conn)
            if cur is None or ack.reports_id >= cur[0]:
                set_outbox_cursor(conn, ack.reports_id, ack.notices_line)
        self._export()

    def new_reports(self) -> list[str]:
        """pending_reports + ack_reports in one call, for callers that treat the hand-over as delivery."""
        msgs, ack = self.pending_reports()
        if ack is not None:
            self.ack_reports(ack)
        return msgs

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


class SendGuard:
    """In-memory guard for the Telegram pump: a proposal that went out but whose `mark_sent` failed (aux.db busy or
    broken) is not sent again every poll; the mark is retried with exponential backoff until it commits."""

    def __init__(self, first_s: float = MARK_RETRY_S, max_s: float = MARK_RETRY_MAX_S):
        self.first_s, self.max_s = first_s, max_s
        self.unmarked: dict[str, tuple[list[tuple[int, int]], float, float]] = {}   # pid -> (messages, due, delay)

    def should_send(self, proposal_id: str) -> bool:
        return proposal_id not in self.unmarked

    def record(self, outbox: Outbox | None, proposal_id: str, messages: list[tuple[int, int]], now: float) -> bool:
        """After a send: mark it; on failure remember it and retry later. True when the mark committed."""
        try:
            if outbox is None:
                raise RuntimeError("outbox unavailable")
            outbox.mark_sent(proposal_id, messages)
        except Exception as exc:                           # noqa: BLE001 - kept and retried, never re-sent
            prev = self.unmarked.get(proposal_id)
            delay = self.first_s if prev is None else min(prev[2] * 2, self.max_s)
            self.unmarked[proposal_id] = (messages, now + delay, delay)
            log.warning("could not record proposal %s as sent (retry in %.0f s): %s", proposal_id, delay, exc)
            return False
        self.unmarked.pop(proposal_id, None)
        return True

    def retry(self, outbox: Outbox | None, now: float) -> None:
        for pid, (msgs, due, _) in list(self.unmarked.items()):
            if now >= due:
                self.record(outbox, pid, msgs, now)


REPORT_ATTEMPTS = 3                # a report batch is retried this many passes, then acknowledged with a warning


class ReportRetry:
    """Bounds the at-least-once report delivery: a batch whose send failed for some recipient is retried on the next
    passes, and after REPORT_ATTEMPTS failed passes it is acknowledged with a logged warning, so one recipient that
    always fails (blocked the bot, wrong id) does not resend the batch to everyone every 30 s forever."""

    def __init__(self, attempts: int = REPORT_ATTEMPTS):
        self.attempts = attempts
        self.failures = 0                           # consecutive passes with a failed send (the batch may grow)

    def should_ack(self, ack: ReportsAck, delivered: bool) -> bool:
        if delivered:
            self.failures = 0
            return True
        self.failures += 1
        if self.failures >= self.attempts:
            log.warning("agent reports up to run %s / line %s failed for some recipient %d passes in a row: "
                        "acknowledged anyway", ack.reports_id, ack.notices_line, self.failures)
            self.failures = 0
            return True
        return False
