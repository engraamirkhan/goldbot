# 0085: Power-first research gates and alpha-spending confirm (research/demo only)

Status: accepted for research and demo promotion (panel ruling 2026-11-15, quant-reviewer, on the evaluator memo
docs/research/power-first-gates-2026-11.md, "Option A"). Amends ADR 0003 and ADR 0083 for research and demo
promotion only. **The owner's paper -> tiny-live phase gate is unchanged.**

## Context
The evaluator's memo (base 9d1abbc7, K +0) re-derived the gate audit. A true 0.10 R edge (sd 1.2 R) has 0.000-0.008
power at the L5 hurdle (deflated Sharpe at the registry's K 21,333, t 5.69) on every timeframe slower than 5m. The
count minimums (1,500 candidates, 60 a fold, 200 trades, 10 trades in each of 3 positive years) fail at 1h, 4h and 1d
whatever the edge. The DSR at registry K is far stricter than a 5 % family-wise test: its FWER over 21,333 nulls is
about 1.3e-4. Tests that cannot pass are also the main thing feeding K. The memo recommended option D: pre-registered
alpha-spending on fresh data, with the registry-K DSR kept as a reported number only.

## Decision
Scope: research and demo promotion ONLY. The phase gate, live unlock, real-money confirmation and RiskGate are not
touched.

1. **Power-first rule** (`goldbot/research/power_first.py`, settings `research.power_first`). A card starts only
   with **>= 0.5 power at every gate it must pass and >= 0.25 joint power**. Otherwise it gets a "NOT STARTED
   (power)" registry row (status `not_started_power`, in `registry.NOT_TRIALS`): no K and no data read. Conditions:
   - (a) min edge = max(0.10 R, the edge the GBP 50/day goal needs at the card's trade rate). The goal's edge uses
     `north_star_daily_gbp / (trades a day x lot_fit_equity x risk.risk_per_trade)`. The sd comes from a pre-set
     per-timeframe table (`sd_r_by_tf`, 1.2 R: the gate audit's pooled cross-family figure), never from the card's own
     outcomes. A card has no sd field.
   - (b) Expected trades on holdout and forward gates are extrapolated from the learn signal rate, never counted on
     holdout bars. A card states only its outcome-blind learn signal count and each window's length.
   - (c) Clustered effective n: n_eff = n / ((1 + (c - 1) rho_day)(1 + (m - 1) rho_instrument)), where c is learn
     signals per (instrument, session day) and m is the number of instruments of a pooled family.
   - (d) Joint power is simulated with the data shared as the real gates share it. A gate reads the union of its
     windows, and gates reading the same window see the same draw.
   - (e) Every refusal is logged: the registry row plus a warning.
   - A gate's z is the threshold at the batch index and alpha it will actually receive: Holm's first step,
     alpha_j / m_j, for the confirm.
2. **Confirm = option D with HOLM, not BH** (`goldbot/research/promotion_confirm.py`).
   - **Holm:** per-batch FWER <= alpha_j under any dependence. It reuses `eval2.confirm.holm` and the EVAL-2
     stationary day-block draws.
   - **Fixed convergent schedule:** q_y = q·2^-y with q = 0.05, and **BATCHES_PER_YEAR = 4**, fixed here before any
     batch is read. alpha_j = q·2^-y / 4 for every batch in ledger year y, and the sum over all years is q. Ledger
     year 1 runs from 2026-11-15 to its first anniversary. Unused slots expire and are never carried over.
     - Year 1: alpha_j 0.00625, z 2.87 at m_j = 3 (about 1,180 trades for 50 % power at 0.10 R).
     - Year 2: alpha_j 0.003125, z 3.08.
   - **One global never-reset ledger:** the registry's `promotion_confirm` trial rows (append-only and synced, and
     each read is charged to K), with ledger id `adr0085:holm:q0.05:2^-y:4/yr`. It may not coexist with EVAL-2's
     q = 0.10 BatchPRDS ledger as a second pot. `research_promotion_refusal` accepts only a confirm stamped with this
     ledger id. An EVAL-2 rejection, or an autodiscovery monthly Holm pass, is never research or demo promotion
     evidence.
   - **Fresh data** means data untouched for that family:
     - TEST-OLD once per family. A family already read on TEST-OLD under EVAL-2 cannot reuse it, and neither can two
       configs of one family in one batch. The ADR 0067 embargo applies.
     - Forward replay is frozen before data. Every read carries a `FrozenStamp` (card hash, code SHA, feature version,
       cost-of-record version), and each look is a scheduled, charged alpha spend: a `run_batch` call that takes a
       ledger slot.
     - The holdout is read at most once per config. It is charged, and it is not a routine source.
   - **P-values and costs:**
     - P-values are clustered: one-sided basic p from the existing stationary day-block bootstrap. The units' day is
       the session day across instruments, so a pooled family is resampled in cross-instrument day clusters.
     - The read is net of measured costs at the upper CI (`cost_basis` must be `measured_upper_ci`).
     - Size comes from the fresh-data effect (`size_basis_mean_r`).
   - **DSR:** the registry-K DSR stays REPORTED, with K = `n_trials_effective` (K_eff included). It is report-only
     for research and demo promotion.
   - CPCV/PBO stay veto-only (ADR 0003, ADR 0083 amendment) wherever they are read.
3. **Count minimums, rule-only path only** (`gates.rule_only_promotion_gates`).
   - The 1,500 candidate and 60-per-fold minimums are dropped for rule-only cards and kept for model cards. A model
     card's confirm is refused unless its learn row passed them.
   - The 200-trade minimum becomes **MIN_FRESH_N_EFF = 200 clustered effective observations** on the fresh-data test.
     Justification: 200 was the floor below which the asymptotic statistic is not trusted (`MIN_TRADES_FOR_DSR`). The
     same floor counted on clustered observations rather than trades is at least as strict per unit of evidence, and
     it lets a pooled family reach it honestly. Power is a separate guarantee (rule 1), so the floor need not carry it.
   - The regime-robustness intent of "10 trades/yr and 3 positive years incl. 2021-22" is kept. Years are taken from
     the family's pooled trades, or from pre-registered multi-year blocks (each spanning at least 2 years, in order,
     disjoint), so a 1d rule can be scored.
4. **The phase gate is pinned.**
   - Gate 1 (`gates_phase.evaluate_gates`) and the tournament's shadow -> live check (`TrialRegistry.passed_gates`)
     read `registry.phase_gate_refusal`. This predicate was called `promotion_refusal` until now, and its body is
     unchanged.
   - Both keep today's criteria: the legacy `results.gates` verdict (DSR at the registry's K pass/fail, the count
     minimums), the rerun rule and the discovery-holdout rule.
   - `tests/test_phase_gate_pin.py` proves the decisions byte-identical to those of 9d1abbc7 on 12 representative
     registries: gate-1 items and verdict, every row's refusal, `passed_gates` and the CPCV gate's candidate list.

## Consequences
- **One explicit loosening, research and demo only.** The registry-K DSR stops being pass/fail for research and demo
  promotion. In exchange, the promotion confirm runs on fresh data at a family-wise level: FWER <= 0.05 over all
  promotions ever, valid under arbitrary dependence. The owner's phase gate still requires the DSR at the full
  registry K, so nothing reaches real money on this path.
- **Real readings.**
  - "NOT STARTED (power)" rows are not trials. An older build that does not know the status would count each one as
    one trial. That is the conservative direction (a higher K), so a mixed deploy cannot loosen anything.
  - Each confirm read is a trial row (K +1 per config read), as every look is.
- **Not done in this change; follow-up lanes, each needs its own review:**
  - (i) Rewire autodiscovery's confirm (`autodisc/confirm.py`, a fixed 5 % Holm each month, which is a second pot
    under this ruling) and its demo registration to `promotion_confirm.run_batch` and `research_promotion_refusal`.
  - (ii) Retire EVAL-2's confirm-window reads as a promotion source.
  - (iii) Have `scripts/research_pass.py` record `rule_only_promotion_gates` on pooled family trades.
  - (iv) Refresh `rho_day` and `rho_instrument` from pooled other-family registry rows. Their current values (0.25
    and 0.5) are PRIORS, not derived. A suggested estimator is the design effect (t_per_trade / t_day_summed)^2 from
    the stored `DaySummedDsr`.
- **Open item 1 (quant-reviewer): condition (a) makes the goal's edge bind everywhere.** On today's basis (GBP 1,000
  x 0.5 % = GBP 5 at risk a trade), the GBP 50/day edge is 4.2 R a trade at 5m, 25 R at 15m, 68 R at 1h, 210 R at 4h
  and 420 R at 1d. Every one is above the 0.10 R floor, so the deciding power is about 1 and the rule refuses almost
  nothing on edge grounds. Each verdict therefore also records `power_at_floor`, which is report-only. Recommendation:
  re-rule (a) with the goal shared across the concurrent cards of a portfolio, or with the floor alone deciding. This
  is a threshold decision for the panel, so it was coded as ruled.
- **What would change this:** a quant ruling that TEST-OLD or the forward replay is not independent of learn (regime
  overlap). D would then rest on the holdout alone, which has too little power for anything slower than 15m.
