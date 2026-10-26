---
number: 105
rank: 38
title: Record a rule-code version on each exploration arm row
stage: 'Research integrity (exploration arms)'
size: S
status: 'proposed · accountable: implementer · reviews: quant-reviewer, code-reviewer'
---
- **105. Record a rule-code version on each exploration arm row.** Value: `arm_hash` (goldbot/research/arms.py) covers id, rule, timeframe, params and max_open, not the rule code in `goldbot/research/arm_rules.py` (or the specialist code a rule calls). A code change to a frozen rule (e.g. the S1 midnight cut correction, commit 8d777955, voided after the fact as `spec-bug-midnight`) leaves the hash unchanged, so nothing in the evidence says which rows came from which code; voiding needed a hand-written criterion. Acceptance: (1) every rule function in `arm_rules.RULE_FUNCS` carries an explicit rule-code version (constant per rule, bumped by hand with a dated note in arms-2026-10-25.md), and a test fails when a rule's source changes without a version bump (hash of the function source pinned in the test); (2) `exploration_obs` gets an additive column `arm_rule_version` (aux migration, older readers unaffected), written by the engine for every candidate, live and shadow; the H-60 history run writes it to its output too; (3) the learner and the n/posterior readers can filter by rule version, and a version bump inside an arm's life is reported in the daily review (count of rows per version per arm); (4) `arm_hash` and the frozen arm ids are unchanged (no re-freeze of the 37 arms); (5) tests: a row records the version in force at its decision; a bumped version shows up in the review line; an unbumped source change fails CI.
