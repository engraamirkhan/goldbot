---
number: 65
rank: 15
title: Review follow-ups from 2026-10-16 (hygiene)
stage: Dashboard, Telegram and research tooling (review residuals) · RUN and FIND
size: M
status: 'ready (decided 2026-10-16; low priority, parts can land separately) · accountable: implementer (N3: research-director) · due 2026-10-30'
---
- **65. Review follow-ups from 2026-10-16 (hygiene).** Decided 2026-10-16 by program director + product owner
  (owner delegated). Value: none of these can lose money; each makes a screen or a setting say the truth, so the
  owner and the research loop act on the right reading. One item so the follow-ups are not lost, ranked last.
  TRACEABILITY: R42-adjacent loss-budget rows for (a); none for the rest. Depends on: 61 (merged at 921a38a) for (a);
  ROADMAP W2-3 for (c). Each part touches different files and may ship on its own.
  Acceptance, part by part:
  - (a) BACKLOG 61 L1: a stale engine state file no longer keeps the loss budget amber once the account is single
    currency again (test with an old file for a removed engine); L2: during a halt that is not a mixed-currency halt
    the budget tone is red, not amber (test);
  - (b) the rollover and weekend entry-block reasons show their times (rollover window start and end, the Friday cut
    and the reopen) in the owner's time zone, on the dashboard and Telegram (tests on the reason text);
  - (c) the W2-3 API work carries the quant review's F1 (each breakdown row shows its own `t_bar`) and F2 (the exit
    target and stop rows are labelled "not a test"); this is acceptance on W2-3, checked by the quant-reviewer there;
  - (d) quant N1: its text is not on record in the repo (E1 gap); the quant-reviewer records N1 here by 2026-10-19
    with a testable criterion, then it is built; N2: a settings validator rejects `holdout_from` without
    `holdout_to` (test in the settings suite); N3: an ADR by the research-director on which `feature_version`
    governs a trial's evidence, by 2026-10-30, cross-referenced from item 34;
  - code-reviewer APPROVE for (a), (b), (d N2); quant-reviewer for (c) and (d).
