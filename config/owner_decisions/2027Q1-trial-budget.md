# 0099: Owner trial budget 2027Q1: ceiling 250, plan 120
<!-- Copy of docs/decisions/0099 kept under config/ so the deploy settings check (which archives goldbot/ and config/ only) can verify the owner decision. -->

Status: accepted (owner decision 2026-11-25; recorded by the research-director, 2026-11-25). Amends ADR 0073 for
2027Q1. The quant-reviewer confirms the K accounting. Research and demo only: the phase gate still deflates at the full
registry K.

## Context
- **The owner's decision, 2026-11-25:** "allow absolutely anything", as recorded in the program director's execution
  plan of 2026-11-25. The plan records it as the owner decision for (a) the 2027Q1 trial-budget override and (b)
  demo no-limits. This ADR records (a) only. Statistical gates are not limits and stay.
- **Why a record is needed** (quant-reviewer issue 1, 2026-11-25): the 2027Q1 budget is 20 (`trial_budget_quarter`)
  with 8 reserved. sr-v2 as signed books about 55-120 K in 2027Q1 (one row per judged broker x arm per weekly run, up
  to 10 a run). At 20 or 40 it fails closed in early January, which silently switches off core learning (owner red
  line: learning is never silently off).
- ADR 0088 (proposed 2026-11-18) would make the quarterly cap report-only. It is not implemented, so the cap still
  refuses pre-registrations; this record is needed either way.

## Decision
- **2027Q1 trial budget ceiling: 250** (`research.trial_budget_overrides` "2027Q1": 250). 250 is under the 500 the
  validator allows without an owner ADR; the record exists so the override rests on the owner's decision and not on
  a settings edit.
- **Plan: 120.**

  | 2027Q1 item | K |
  |---|---|
  | sr-v2 as signed | ≤ 86 |
  | sr-v3 weekly | 13 |
  | zoo-v2 core (conditional) | 4 |
  | H-102 class units (conditional) | ≤ 4 |
  | agg-v1 | 3 |
  | abstain-v1, RL-2 (conditional), allocator read, augmentation ablation (conditional) | 4 |
  | O-11, O-12, FX-FIX, WFRI2 | 4 |
  | G3 read | 2 |
  | **planned** | **120** |

- **Checkpoints:** program director + quant at 60, 120 and 180 used. Discretionary items freeze at 180. Above 250
  needs a new owner decision.
- Every trial still counts in K whether it fires or not. Control arms are not trials. Fit-only runs are 0 K only
  under quant Q2. Changing a recipe or loss after a look costs K +1.

## Consequences
- **Nothing loosens.** Deflation runs at the real K (the K of record is the larger figure until the quant reconciles
  it, ruling D5), and the phase gate deflates at the full registry K. More trials make the real-money bar stricter.
- **Settings change, applied by the release-manager** in the integration commit after the W1-B lane merges (W1-B owns
  `config/settings.yaml` this wave): `trial_budget_overrides` gains `"2027Q1": 250` and `trial_budget_owner_decisions`
  gains `"2027Q1": "config/owner_decisions/2027Q1-trial-budget.md"` (the copy beside this file).
- **The stale 2027Q1 reservation** (8 slots for H-02 and C4H, a tsmom 4h row from a retired family) should be
  withdrawn before the 2027-01-01 freeze. That is a separate pre-registration edit, not this record.
- **Reversal:** a later owner decision may lower the quarter; trials already run stay counted.
