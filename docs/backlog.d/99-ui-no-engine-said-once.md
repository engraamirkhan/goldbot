---
number: 99
title: No-engine state said once, consistently, across pill, banner and safety tiles
stage: 'UI (status) · UX'
size: S
status: 'done (triage 2026-11-13): PR #156 (aa368faa)'
---
- **99. No-engine state said once, consistently, across pill, banner and safety tiles.** Value (found in the shots, not in the review list): with no engine the Trades screen says it four ways at once: pill "No engine reporting" (`web/src/shell/StatusPill.tsx:55`), banner "No engines reporting" (`web/src/screens/SafetyStrip.tsx:39`), Drawdown tile "no engines" (`:74`) and an amber Account tile showing only "—"; Limits says "No engines reporting yet." Singular and plural vary. Acceptance: one canonical phrase ("No engine reporting") from one constant; the banner is the only sentence; tiles that depend on the engine read "not reported" in neutral grey (amber only on the banner); the Account tile reads "not reported", never a bare dash. Vitest on SafetyStrip idle state.
