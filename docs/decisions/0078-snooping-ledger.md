# 0078: Explore views go to an append-only snooping ledger, not the trial budget

Status: accepted, **signed by the quant-reviewer 2026-11-09** (PD ruling 2026-11-09, PD + PO with quant input; recorded by the
research director). Amends how ADR 0073 is read on views. Part of EVAL-2 (ADR 0076).

## Context
On 2026-11-09 the EVAL-2 review looked at explore 2010-17 descriptively across the catalogue: 17,862 de-duplicated
cell views (16,680 real-only, excluding NULL-RAND, per the quant ruling of 2026-11-09). ADR 0073 defines K as
"pre-registration rows plus registry K at run time". The views had no registered rule, no pass/fail and promoted
nothing, so they are not trials under 0073. They are still data snooping: a card shaped by them has been selected,
and its multiplicity must say so. Booking them as trials would give 14.2k-21.3k rows, above the 5,000 hard ceiling
that even the owner cannot lift, so the quant's override route has nothing valid to ask for.

## Decision
- **Views are not trials. They are booked, not dropped.** The data-engineer builds an append-only snooping ledger on
  the brain, one row per (de-dup hash, source, harness). The 11-09 views are booked at the upper count, **17,862**,
  with **16,680** (real-only) reported beside it as a sensitivity, never as the headline.
- The snooping count feeds every reported multiplicity number: **DSR K = registry `n_trials_effective` + snooping K**
  (quant-verified 2026-11-09: 3,471 + 17,862 = 21,333; sensitivity 3,471 + 16,680 = 20,151), the EB prior's `n_configs`, and the
  Harvey-Liu haircut. It does **not** draw on the 3,500 registration budget or the 5,000 ceiling (ADR 0073), which
  govern registrations. The bar gets stricter, not looser.
- Any future descriptive look is appended to the ledger **before** it is computed.
- A card shaped by ledger views cites their hashes and cannot pass on explore alone: it needs TEST-OLD plus 2018-25
  (BatchPRDS).
- Registration freeze until the registry is reconciled and the unlinked-prereg guard is in. Reconciliation: merge
  applied 2026-11-09 (3,455 rows), then the sprint-3000 result backfill applied and quant-verified the same day
  (registry 6,961 rows, sha 73fc51e1...; `n_trials_effective` 3,471; quarter_trials(2026Q4) 3,395; **headroom 105**
  under the 3,500 budget and the ADR 0029 loop exclusion). Backfilled rows carry ts = 2026-11-09; anything that
  orders reads by time (BatchPRDS numbering included) uses `results.backfill.true_read_ts`. The freeze lifts only
  when Lane 0's `tests/test_registry_unlinked_prereg.py` is green on CI and merged. Then rows go in this
  order: (1) futility ADR 0077 (0 rows); (2) EVAL-2 batch-1 family rows (about 5); (3) KoG/G card (+11,
  `prereg-2026-11-09-kog-box-ladder`); (4) the L2 block, only what fits in the remainder minus a reserve of 5.
  Everything else moves to 2027Q1 (fresh budget 2027-01-01). If fewer rows remain than needed, cut from the bottom.
- Owners: the data-engineer owns the ledger table (due 2026-11-11); the quant-reviewer owns the K wiring into DSR,
  the EB prior and the haircut; the data-engineer (SRE for brain access) owns the reconciliation; the PD chases it at
  the due time and re-dates it the same day if it slips, with the freeze kept.

## Consequences
- Reported DSR K rises from 3,471 to 21,333; the t a family must show for DSR rises with it. Reported,
  not gated, before L5; at L5 the K basis is the owner's (ADR 0076).
- The Q4 registration budget is unchanged at 3,500. Nothing goes to the owner as a question: no money, live,
  RiskGate, credential or legal item is involved.
- Owner information line for #34: "11-09 explore looks (17,862) booked in a separate snooping ledger that raises K
  for DSR/EB/haircut; Q4 budget unchanged at 3,500; remaining Q4 rows go to EVAL-2 batch 1 and KoG."
