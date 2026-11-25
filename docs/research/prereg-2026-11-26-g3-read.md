# Pre-registration: the G3 read of 2027-01-15 (kill or continue)

Research director, drafted 2026-11-25 for the 2026-11-26 deadline. **Written before any forward outcome of the rows it
judges was read.** Only counts and calendars were used: no `sim_r`, no P&L and no model score of an eligible row.

**Status: draft.** The evaluator checks it; the quant-reviewer signs it on 2026-11-27; it merges in the 11-27 docs
train. **After 2026-11-27 no route is added and no threshold moves** (G3-D). Sources: execution plan 2026-11-25 §8
and rulings D19-D21; rd-7 (G3 spec, 2026-11-25); sr-v2 card `prereg-2026-11-24-self-retrain.md`; GD-1 card
`prereg-amendment-2026-11-20-gd1-asia-learning.md`.

K of this card: one row per route-A eligible arm, booked at the 01-15 read (expected 0-2). Route B adds 0 (its runs
are already booked). The holdout 2025-10-01..2026-09-30 is not read.

## G3-0 The read

| Item | Value |
|---|---|
| Reader | the evaluator, independent and read-only |
| Read | **2027-01-15 12:00 UTC**, once |
| Data cutoff | **2027-01-08 21:00 UTC** (the sr-v2 01-10 run's cutoff). Only labels resolved by then count |
| Script | `scripts/research/g3_read.py`, deterministic, committed by **2026-12-06** |
| Dry run | **2026-12-13**, counts and eligibility only; no outcome field is computed or printed |
| Eligibility snapshot | **2027-01-10**, counts only, committed as the read's input |
| Verdict | exactly one of `CONTINUE-A`, `CONTINUE-B`, `MAINTENANCE`; posted to #34 the same day. If both routes pass, the verdict is CONTINUE-B |

## G3-A Route A: a carded arm beats its matched replay twin, net of costs (ruling D20)

- **Eligible** (all of them):
  - the arm is not retired (no `retired_day`) and its family is not on the retired list;
  - its card was pre-registered and merged on or before **2026-12-06**;
  - it records r2 replay labels (net R under the measured broker cost of record);
  - its card names a **side- and hour-matched replay twin** (a `control_mode: replay_only` row on the same timeframe,
    drawn in the arm's UTC hour cells and on the arm's side).
- **The eligible set is closed on 2026-12-06** (arm_id, card path and merge SHA, twin id). An arm retired after that
  stays in the Holm family and reads as fail. The 01-10 snapshot records counts only.
- **Not eligible:** the 10 retired cohort arms (ADR 0095) and any retired family read raw against a twin. A retired
  arm counts only as "arm + filter" through route B. A raw re-read would be a re-test.
- **Unit:** the candidate, not the fill. Each candidate's r2 `sim_r` (net R). Clipped at ±10 R and kept in (ADR 0098
  D1).
- **Statistic:** arm and twin are each averaged per session day; d_t = arm_t − twin_t on days where both exist. The
  statistic is mean(d_t).
- **Inference:** resample the ordered paired-day series d_t (pairs kept together) with a stationary bootstrap, mean
  block 5, B = 10,000, one numpy default_rng(20270115) stream, arms in arm_id order.
  p = (1 + #{boot mean <= 0})/(1 + B). Holm across eligible arms at alpha = 0.025 one-sided. Session day =
  exploration_obs.session_day. The arm's own mean net R is per candidate.
- **Pass needs all of:**
  1. ≥ 30 paired days and ≥ 100 arm candidates (else `insufficient`, which is not a pass). `insufficient` is decided
     from the 01-10 counts snapshot before any outcome is computed, and books 0 K;
  2. the Holm-adjusted one-sided test passes (p at most the Holm level of alpha = 0.025);
  3. the arm's own mean net R > 0 (losing less than a losing twin is not a pass).

**Known now (counts only, so the owner hears it before the read):**
- GD-1 is not eligible for route A (quant 2026-11-27): its controls are not hour-matched (D20). It books 0 K at the
  G3 read. Its own card's read on 2027-02-16 is unchanged. (GD-1's card runs `until` 2027-01-04: a renewal is needed
  for it to run past that date.)
- Route A needs forward candidates from 2026-11-26 or earlier (>= 30 paired days by 01-08, less 12-25 and 01-01). No
  arm carded after 11-25 can reach 30 paired days, and the only one that started earlier (GD-1) is not eligible.
  Route A is therefore expected to be empty, and G3 rests on route B.

## G3-B Route B: a self-retrain model is ACTIVE under its signed gate

- **Pass:** after the **2027-01-10** run, at least one `self_retrain_models` row has `status = active`, under one of:
  - **sr-v2** as signed on 2026-11-24 (unchanged in flight);
  - **sr-v3**, only if its promotion rule is quant-signed by **2026-12-06** and its spec is frozen and merged by
    **2026-12-18**, with its first counted run no later than **2026-12-20**. Otherwise sr-v3 cannot count.
- **The signed gate, restated (not changed):** edge vs no-model ≥ +0.05 R and vs the control ≥ +0.05 R (sr-v3:
  the ADR 0098 statistic of record), kept mean net R > 0, Holm-adjusted max(p) ≤ 0.10 across the run's judged pairs,
  **two consecutive passes** with cutoffs exactly 7 days apart, and a **frozen forward edge ≥ 0** for the first
  passer on rows decided after its cutoff. The forward check binds once there are >= 30 rows.
- This restatement is a summary. The signed sr-v2 card and its SPEC_VERSION code decide, including n >= 200/100/10,
  controls >= 50, gross sanity (kept minus all on label_mid_r >= 0) and serve.
- sr-v3 counts only if its signed gate also needs both edges, Holm and two consecutive passes.
- **A history winner** (H-101, H-102, OMB-1) counts only if it is ACTIVE under sr-v3 and has met its own card's
  forward rule. A history-only result never counts.
- **The evaluator re-derives** the passing run by re-running the frozen module at the same cutoff; the decision must
  reproduce.
- **Not a pass:** a model active at an earlier run but demoted or superseded at 01-10; a run with a failed broker
  read (fail-closed); a model whose K rows were not written.

## G3-C Integrity

- Not read: the holdout (2025-10-01..2026-09-30), the confirm window (2018-01..2025-09) and test_old.
- No synthetic, augmented, dense or phase≠0 row in any judged set (quant R7-R9).
- Quarantined, void (`exploration_label_void`) and capped-day rows are excluded as in sr-v2. "Quarantined" here
  means sr-v2 label quarantine only; rows beyond ±10 R are clipped and kept in (ADR 0098 D1). Holiday days are not
  excluded. No day is dropped by hand.
- Demo £, cohort fill P&L and netting savings are **reported next to** the verdict. They never count for it.

## G3-D No moving goalposts

- The date, routes and thresholds are fixed at signing (2026-11-27). No extension.
- Evidence that cannot be produced (missed runs, a broken chain, a snapshot outage, a script failure) gives
  `MAINTENANCE`, never "postponed".
- A later edit to this card is void; the signed text decides.

## G3-E Maintenance (if neither route passes; effective 2027-01-16 00:00 UTC)

- **Runs:** a daily cycle; health and broker/ledger reconcile; safety and incident fixes; the weekly self-retrain at
  its frozen spec (including any history-pretrained retrain merged by 01-15); scheduled data jobs; the finance
  monthly; GD-1 to its own read on 2027-02-16; the Friday report, P&L first. Token budget ≤ $100 a month.
- **Stops:** new cards, generators, sprints and instruments; platform and UI work; H-101/H-102/OMB-1 stages not yet
  merged; cohort renewal (the cohort ends 01-15 by its card); every order from an arm with no evidence.
- **Real money:** unchanged and owner-only.

## G3-F Automatic exit from maintenance

Full research resumes the day after any one of:
1. a weekly self-retrain run at the frozen spec yields an ACTIVE model by the G3-B test;
2. a GD-1 promotion card, quant-signed and counted in K, passes its read (GD-1's own card has no promote rule: its
   section 5.3 defers it);
3. a new card with **new mechanism evidence** (not a re-parameterised closed line), quant-signed and counted in K.
   One such card at a time is allowed in maintenance (<= $300). Maintenance ends only when that card's
   pre-registered read passes.

An owner override is recorded on #34 as an override. The published verdict stays `MAINTENANCE`.

## Honest odds, published now

P(continue) **3-6 %** (restated by the evaluator and quant on 2026-11-25; ruling D19 published 5-10 %). Route A: expected
empty (above). Route B: an sr-v2 pair (RBR15, Z3D, Z2P) about 3 %; an H-102/OMB-1 winner forward-ACTIVE by 01-10:
about 0 % (its forward rule needs >= 200 unseen rows); an sr-v3 model on sr-v2's arms about 1-3 %.

Evaluator's measurement (2026-11-25, counts only): the realistic route-B pairs are RBR15, Z3D and Z2P. Snapshot
coverage is 52-67 % (not 85 %), so the first judged runs slip about a week. RBR15 stops adding rows on 12-31. Vantage's
0 feature rows fail closed and block icm activations in the same run.

## Precondition

No Stage C or confirm read of any card runs before the K-of-record reconciliation (quant, due 2026-11-27, ruling D5).

## Signatures

- Evaluator check: pending.
- Quant-reviewer (route A eligibility incl. GD-1; route B restatement; integrity): pending, due 2026-11-27.
