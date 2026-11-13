---
number: 236
rank: 80
title: Re-register S05-S07 after the published holiday calendar
stage: 'Research · registry'
size: S
status: 'proposed (follow-up of R145) · accountable: strategy-researcher · reviews: quant-reviewer, evaluator'
---
- **236. Re-register S05-S07 after the published holiday calendar.** Value: R145 changes the S05-S07 event sets. Explore S05-V0 went from 103 to 199 events, S05-V1 from 70 to 149, S06 lost 2-5 per variant and S07-V0 went from 625 to 692. A changed event set is a new registry row, and the EVAL-2 harness hash changed, so earlier S05-S07 reads are void. `specs.yaml` S05 `impl_notes` still describes the old inference ("weekdays missing from the cash index history"); it is left untouched here because editing specs.yaml re-freezes every unit. Acceptance: S05 impl_notes name the published calendar and its availability rule. New pre-registration rows exist for S05-V0/V1, S06-V0..V2 and S07-V0/V1. S0-S6 are re-run on the new harness hash. The old rows are marked superseded, never deleted.
