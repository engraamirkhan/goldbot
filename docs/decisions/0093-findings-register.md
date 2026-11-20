# 0093: Findings register: measured closure, daily re-check

Status: proposed (implementer, 2026-11-20; owner directive "feedback must close itself")

## Context
The system audit of 2026-11-20 graded the reviewers well and the follow-through as failing: A4 (controls at most 1:1,
150-250 trades a day) failed for a month, issues #392 and #394 sat without an owner, P5/P6 stayed open and no
postmortems were written. Findings lived in audit files, issue comments and memory, each with its own wording and no
owner, due date or test. Nothing re-read them, so a finding was "closed" when people stopped talking about it.

Options considered:
1. A tracker (GitHub issues/project per finding). Owned and dated, but closure is still a human click, and the brain
   cannot judge it; the audit shows clicks did not happen.
2. A register whose rows are closed only by a measurement the brain re-runs daily (chosen).
3. Extend the health checks with one check per finding. Health runs every 60 s and alerts on change; most findings are
   slow (days), many are not service health, and 50 more standing warns would repeat the alert noise the SRE audit
   measured (63 % self-inflicted).

## Decision
- `config/findings.yaml` (versioned, public) holds one row per finding or directive: id, title, owner (an agent role in
  .claude/agents/), severity, due date, source, and a check naming a function registered in
  goldbot/ops/finding_checks.py with parameters validated at load. Rows carrying a login, host, email or money figure
  are refused at load.
- A row is closed only when its check passes. `manual` exists for steps with no data trail and closes only with a
  dated entry and evidence in a reviewed PR. "Can't check" is never a pass; past the due date it is overdue.
- The daily `findings_check` job stores every evaluation in aux.db (`findings_history`; no new JSON state) and
  announces what changed (new, regressed, closed, past due, and a reminder every 7 days while overdue) in at most one
  comment a day on #34, plus one Telegram line for critical/high events. Unposted events stay pending.
- Health `findings` WARNs while a critical/high row is past due; it is not on NOTIFY_ON_WARN (the job announces).

## Consequences
- Feedback has an owner and a date by construction, and the system reports its own backlog of unfixed findings.
- Each new finding must come with a measurable check or an honest `manual` row; the share of manual rows (29 of 51 at
  seeding) is itself a signal for the CTO: findings without a data trail should get one.
- Due dates and owners at seeding are proposals from the audits; PD + PO confirm them. Moving a date or retiring a row
  is a reviewed edit with its reason, never a silent deletion.
- The GitHub security check needs an admin token; on the brain it reads "can't check" until one exists or the row is
  converted to a dated manual entry.
- Reporting only: nothing in this path touches an order, a limit, an arm, a halt or a setting.
