---
number: 149
rank: 57
title: EVAL-2 Lane 5 runner follow-ups from the Lane 3 quant review
stage: 'Research (EVAL-2 Lane 5 batch runner)'
size: S
status: 'proposed (quant round 2 of Lane 3, 2026-11-10) · accountable: implementer · reviews: quant-reviewer, code-reviewer'
---
- **149. EVAL-2 Lane 5 runner follow-ups from the Lane 3 quant review.** Value: the Lane 3 statistics take inputs whose integrity only the Lane 5 batch runner can check; without these three checks a futility retire, an L3 read or a BatchPRDS batch can rest on an unverified claim. Acceptance: (a) the `registry_row` passed to `screen.futility_terms` is looked up in the registry before the screen reads it: an id with no pre-registered row (same config hash, written before the read) raises, so "counted in K" is checked against the registry, not only that the id is non-empty; (b) the runner cuts the L3 units at decision + the registered maximum hold before 2025-10-01 (an instance whose registered hold would reach the holdout is not read), on top of `l3_read`'s decision-instant guard; (c) the runner writes every family's `confirm.family_cv_p` for batch j (m_j of them, `len(batch_p)` equal to the card's m_j) to `state/catalogue_reads.jsonl` before any `l3_read` of that batch, and `l3_read` reads them back from there. Tests: one per item, each failing without it.
