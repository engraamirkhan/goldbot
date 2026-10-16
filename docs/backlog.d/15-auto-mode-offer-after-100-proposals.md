---
number: 15
title: Auto-mode offer after 100 proposals
stage: Entry timing, governance · RUN
size: S–M
status: 'done (PR #63, main 5f7da74; af441e8)'
---

### 15. Auto-mode offer
Value: the owner's one-click approval stays the default, and `/mode auto` becomes available only on evidence (RUN).
Acceptance: `/mode auto` is refused until 100 proposals with no RiskGate breach and no statistically
distinguishable difference between approved and rejected outcomes. The test covers 99 vs 100 and the difference
test on both sides. The offer is a message. Switching still needs TOTP. Row: A10.

Sweep 2026-10-16 (product owner): Done (PR #63, main 5f7da74; af441e8). Acceptance record: merged on green CI into main; checked against git and the named tests exist; the criteria were not re-run one by one in this sweep.
