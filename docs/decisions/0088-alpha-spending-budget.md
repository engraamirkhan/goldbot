# 0088: Alpha-spending ledger replaces the quarterly trial cap (amends 0073, extends 0085)

Status: Proposed (research-director, 2026-11-18). Needs quant-reviewer sign-off; trading-safety-reviewer to confirm the
phase gate is untouched.

**The owner's written acceptance of Decision 1**, dated 2026-11-18, verbatim: "the system should be autonomous on
finding new strategies, retire strategies etc, and there is no rules restrictions from my side, the system should be
able to become the system i want no matter how it achieves it". The owner may revoke this. K still applies in full at
the phase gate. Amends ADR 0073 and extends ADR 0085, for **research and demo only**. Takes effect only after
the red-box week ends (2026-11-22 22:00 UTC) and after it is implemented through the normal lane.

## Context
- **The owner's directive (2026-11-18, firm):** the system finds, tests and retires strategies itself, with no rule
  restrictions from the owner.
- **ADR 0073** set the 2026Q4 trial budget at 3,500, with a 5,000 hard ceiling, enforced by
  `research.trial_budget_quarter`, its overrides and the preregistration validator.
  - The brief gives the quarter at 3,429 of 3,500. That is the brain's registry count; it is not re-derived here.
  - With about 71 trials left and six weeks of the quarter to run, an autonomous search would stop within days.
- **A trial cap does not control what matters.** It counts trials. It does not control the false-discovery error of
  promotions. The why-fail audit (section 3) shows that the program's K, 21,333 in the registry, drove the
  deflated-Sharpe hurdle to t 5.69. That is about 6,100 trades at 0.10 R, so no 1h-1d rule could ever pass. Tests with
  no power were also what fed K.
- **ADR 0085** already moved research and demo promotion to a fresh-data confirm under one global, never-reset
  alpha-spending ledger:
  - q = 0.05;
  - q_y = q 2^-y;
  - 4 Holm batches a year;
  - ledger id `adr0085:holm:q0.05:2^-y:4/yr`.
  The DSR at registry K is kept as a reported number for research, and stays pass/fail for the owner's phase gate.
  The step left is to let that ledger, and not a trial count, bound promotions. That includes the forward reads of
  ADR 0086 and the machine arms of ADR 0087.

## Decision
1. **The quarterly trial cap stops refusing research and demo trials.** `trial_budget_quarter`, the overrides and the
   5,000 ceiling become reported figures. They no longer refuse a pre-registration. Trials already run stay counted.
2. **K is still counted and reported.** Every trial, every look and every forward read is a registry row, as today,
   and `n_trials_effective` is unchanged. Twin sets are controls: they are `NOT_TRIALS` and not K (quant ruling
   2026-11-18). K appears in every read and as the footnote of
   the weekly owner report. The power-first rule (ADR 0085 section 1) still refuses cards that cannot pass, at
   no K. Compute and token budgets stay operational limits (research-director rule 2), not statistical ones.
3. **One ledger, extended to online spends.** The ADR 0085 ledger stays the only pot. Its yearly budget q_y = 0.05
   2^-y is unchanged, so the total over all years stays 0.05, a family-wise error rate (FWER) under arbitrary
   dependence. From the effective date, each future ledger year is split before any of its slots is read:
   - **History batches:** three quarters of q_y, over ADR 0085's 4 batches a year, Holm within each batch. Each
     batch's alpha_j becomes 3 q_y / 16. In year 1 that is about 0.0047, z about 2.95 at m = 3.
   - **Forward reads (ADR 0086):** one quarter of q_y, split equally over the 12 ledger months, so each month's
     pot is q_y / 48. The j-th forward read registered in a month gets alpha_i = (q_y / 48) / (j (j + 1)),
     j = 1, 2, .... Since sum 1/(j(j+1)) = 1, the month's pot is never exceeded. This is online Bonferroni, valid
     under any dependence. Unused alpha expires, never carries over, and no year below the highest used is
     reopened (as in ADR 0085).
   - **Why 75/25, not 50/50** (quant ruling 2026-11-18). Forward reads have about 0 power at 1h-1d, and those
     timeframes depend on history. The cost of a smaller alpha is quadratic in z for a fixed-n history test, but
     only logarithmic, through ln(1/alpha), for an e-process.
   - **Already-read batches.** Batches of year 1 already read under ADR 0085 keep their alpha. Only slots not yet
     opened are re-split, and the re-split happens under a new ledger id.
   - **FWER, not FDR.** The ledger controls the family-wise error rate, not the false discovery rate (e-LOND was
     considered and not chosen), so it stays one pot.
4. **Only ledger-stamped reads promote.** `research_promotion_refusal` accepts a forward pass only with the new
   ledger id (proposed `adr0088:holm+online:q0.05:2^-y:75/25:4/yr+12/mo`). An unstamped read, an EVAL-2 read or a
   learning read is never promotion evidence.
5. **The phase gate is pinned.** Real money still needs ADR 0061's history qualification and the phase gate's
   deflated Sharpe at the **full** registry K (ADR 0085 section 4, `tests/test_phase_gate_pin.py`). Removing the
   cap lets K grow, so that bar can only get stricter. `phase_gate_refusal` does not read the online ledger. The
   quarterly cap and the K count feed the phase gate unchanged.

## Consequences
- The machine search is no longer stopped by a count. The price of each extra registration is a smaller alpha_i,
  so it needs more evidence:

  | forward read in its month | alpha_i (year 1) | 1/alpha_i | trades for an oracle e-process at 0.10 R / 0.20 R (sd 1.2) |
  |---|---|---|---|
  | 1st | 2.6e-4 | about 3,840 | about 2,380 / 590 |
  | 10th | 4.7e-6 | about 211,200 | about 3,530 / 880 |

  The trade counts use n of about 2 sd^2 ln(1/alpha) / delta^2. Real e-processes need more, and ADR 0086 requires
  the net e-process to pass as well.
- History batches lose some power: their alpha falls to three quarters (z 2.87 to about 2.95 at m = 3). That is the
  cost of funding forward reads from the same FWER budget.
- K will rise faster. The DSR reported for research will rise with it, and the real-money phase gate gets stricter.
  That is intended: the owner's gate is where K bites.
- Implementation:
  - the preregistration validator stops refusing on the quarter budget and keeps the reporting;
  - `promotion_confirm` gains the online slot and the ledger id;
  - tests pin the split, the convergent series and the expiry.

## Risks and guards
1. **Alpha leaking through unregistered looks.** *Guard:* only ledger-stamped reads promote. The ledger is
   append-only, hash-chained, synced and backed up. Each read is charged to K.
2. **Dependence between correlated arms.** *Guard:* Bonferroni and Holm are valid under arbitrary dependence, and
   the twins are matched by day.
3. **K inflation hiding a real edge from real money.** This is the accepted cost of an honest K. *Guard:* the
   history route plus fresh-data alpha-spending gives each family its own read, and the phase gate is unchanged.
   Any change to the phase gate is the owner's decision.
4. **The gap between live fills and the replay.** *Guard:* ADR 0086's parity monitor voids a forward read. It never
   passes it.

## Review rulings (2026-11-18)
- quant-reviewer:
  - FWER, not e-LOND.
  - A 75/25 history/forward split.
  - Unopened slots are re-split under a new ledger id.
  - Twin sets are NOT_TRIALS.
  - The owner's acceptance of Decision 1 is recorded in Status.
- trading-safety-reviewer: `phase_gate_refusal` does not read the online ledger.

## Still open
- How many ADR 0085 year-1 batches have already been read on the brain? Their alpha stays as spent. The count is
  needed to fix the year-1 re-split.
