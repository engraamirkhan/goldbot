---
number: 52
rank: 5
title: Approvals keep only the decision channel on disk and in the API
stage: Execution and risk (approvals) · SECURE
size: S
status: 'in review (04f6266, 3817db7, c7ea0e3, eba16e3 in PR #79; security re-review ran 2026-10-16: two LOWs, fixed in d2ecb8f on the integration branch, not in PR #79; security re-verify 2026-10-16 PASSED: d2ecb8f fixes both LOWs, tests/test_automode.py + tests/test_runbook_rollback_scrub.py 4 passed, branch scan 0 secrets, PUSH-OK; trading-safety sign-off in progress) · accountable: trading-safety-reviewer · due 2026-10-17'
---
- **52. Approvals keep only the decision channel on disk and in the API (trading-safety review of the finance audit
  record, 2026-10-11).** Value: an archived proposal in `approvals/done/` still carries the owner's identity:
  `Proposal.decided_by` (the Telegram user id) and `decided_via` ("telegram:<id>" or "dashboard:<email>"). The
  finance audit needs only when and through which channel the owner decided, which `decided_ts` and
  `decided_channel` now record (TRACEABILITY X18). Do: the done files and the API keep only `decided_channel`
  (with `decided_ts`); drop `decided_by` and `decided_via` from the files written to disk (the in-memory
  allow-list check is unchanged); if an identity is ever needed, it lives in the auth audit log, not in the
  approval files. The dashboard's decided cards (`DecidedProposal.decided_by`, goldbot/api/app.py
  `recent_proposals`, web/src/screens/ProposalCard.tsx) switch to the channel. This is an API contract change:
  regenerate the OpenAPI export and the web types, and keep reading older done files that still carry the fields
  (ADR 0010). Acceptance: a test that a decided proposal archived by the bus or the engine has no Telegram id or
  email anywhere in its file; a test that the decided card shows the channel for a new file and for an older file
  with `decided_via`; the API contract regenerated; trading-safety and security review.
  Sweep 2026-10-16 (product owner): accountable `security-reviewer`, due 2026-10-17. Built (TRACEABILITY A14, A15); the re-review asked on 2026-10-12 has not started. Acceptance: both reviews signed off, the OpenAPI export and web types regenerated, then it ships with the reviewed batch.
