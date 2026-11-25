# 0085: Power-first research gates and alpha-spending confirm (research/demo only)

Status: accepted for research and demo promotion (panel ruling 2026-11-15, quant-reviewer, on the evaluator memo
docs/research/power-first-gates-2026-11.md, "Option A"). Amends ADR 0003 and ADR 0083 for research and demo
promotion only. **The owner's paper -> tiny-live phase gate is unchanged.** n_eff amended 2026-11-25 (label overlap, quant
Q3; rho_day per horizon, quant Q1): see "Amendment 2026-11-25" below.

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

1. **Power-first rule** (`goldbot/research/power_first.py`, settings `research.power_first`). This is condition (a)
   as re-ruled by the quant-reviewer on 2026-11-15, after the first draft's max(floor, goal) was found to let almost
   every card through.
   - **Edge:** delta = **0.10 R net per trade, fixed**, with no max() against the goal's edge. goal_edge_R is the
     edge the GBP 50/day goal needs at the card's frequency and risk:
     `north_star_daily_gbp / (trades a day x lot_fit_equity x risk.risk_per_trade)`. It is recorded as information
     only and **never gates**.
   - **Effective n:** n_eff = n / (1 + (m_day - 1) rho_day) / (1 + (k - 1) rho_inst).
     - n is the expected trades at the gate, from outcome-blind signal counts. On holdout and forward windows it is
       the learn signal rate x the window length; a card states only learn signal counts and window lengths.
     - m_day is the mean trades per (day, instrument) cluster, floored at 1. k is the number of instruments pooled.
   - **Gate power:** power_g = Phi(delta sqrt(n_eff) / sd_tf - z_g). z_g is the gate's actual threshold at its
     batch index and alpha, at Holm rank 1 (alpha_j / m_j).
   - **Start rule:** a card starts only if every power_g >= 0.5, the simulated joint power >= 0.25, and n_eff >=
     n_eff_floor (200) at each fresh-data gate. A card whose expected confirm n_eff is below the floor cannot pass,
     so it is refused before any read. The joint power is simulated with the data shared as the real gates share
     it: gates reading the same window see the same draw.
   - **sd_tf:** max(1.2, the demo-measured sd for that timeframe), taken from `sd_r_measured_by_tf`. Demo measured
     1.10-1.51 R overall but has no split by timeframe yet, so every timeframe uses the 1.2 floor until it is filled
     (follow-up v). The sd is never taken from the card's own outcomes.
   - **Correlation priors:** rho_day 0.25 and rho_inst 0.5 are labelled PRIORS. rho_inst is 0.7 when at least half
     of the pooled instruments are precious metals (XAU, XAG, XPT, XPD). They are to be replaced by a pooled estimate
     from other families' learn outcomes, never the card's own.
   - **Settings can only tighten:** the ruling's values are the lower bounds (code review of 734940c3): delta >=
     0.10, n_eff_floor >= 200, sd floor >= 1.2, rho_day >= 0.25, rho_inst >= 0.5 (metals >= 0.7), and
     trading_days_per_year bounded to [250, 262].
   - **Refusals:** a refused card gets a "NOT STARTED (power)" registry row (status `not_started_power`, in
     `registry.NOT_TRIALS`). It costs no K and takes no trial number, also after a registry merge. The registry
     accepts the status only with `results.power_first.started` False and the card, which is what
     `start_or_refuse` writes. Every refusal is also logged.
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
     - The EVAL-2 TEST-OLD families are a required argument of `run_batch`, so the check can never fail open.
       `eval2_test_old_families` derives them from the reads ledger.
     - Forward replay is frozen before data. Every read carries a `FrozenStamp` (card hash, code SHA, feature version,
       cost-of-record version), and the stamp must equal the one in its card's pre-registration
       (`plan.frozen_stamp`). Each look is a scheduled, charged alpha spend: a `run_batch` call that takes a ledger
       slot.
     - Ledger years are read in UTC, and a year below the highest year already used is never reopened.
     - The holdout is read at most once per config, also within one batch. It is charged, and it is not a routine
       source.
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
     disjoint), so a 1d rule can be scored. A block needs 10 trades for every year it spans (code review of
     734940c3), so a block only pools uneven years and never lowers the rate.
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
  - (iv) Replace the rho priors (0.25 day; 0.5, or 0.7 for metal pools) with a pooled estimate from other families'
    learn outcomes. A suggested estimator is the design effect (t_per_trade / t_day_summed)^2 from the stored
    `DaySummedDsr`.
  - (v) Fill `sd_r_measured_by_tf` from demo trades by timeframe, with the source recorded.
- **Condition (a) was re-ruled before merge.** The first draft took max(0.10 R, goal edge). On today's basis (GBP
  1,000 x 0.5 % = GBP 5 at risk a trade) the goal edge is 4.2 R a trade at 5m and up to 420 R at 1d, so the deciding
  power was about 1 everywhere. The quant re-ruled to a fixed delta of 0.10 R. goal_edge_R is now recorded only.
  `tests/research/test_power_first.py::test_changing_the_goal_edge_never_changes_the_verdict` holds that.
- **Worked example (closing test).** A 1h card pooled over 10 metals has 740 TEST-OLD trades and n_eff 740 / 7.3,
  about 101. It is refused at delta 0.10. At delta 0.20 it is still refused, on the n_eff floor; with 6 TEST-OLD
  years (n_eff about 304) it starts.
- **What would change this:** a quant ruling that TEST-OLD or the forward replay is not independent of learn (regime
  overlap). D would then rest on the holdout alone, which has too little power for anything slower than 15m.

## Amendment 2026-11-25 (code, W1-B): label overlap (quant Q3) and rho_day per horizon (quant Q1)
Status: implemented (branch fix/power-overlap-design-effect, plan 2026-11-25 S3), for quant-reviewer re-verify.
The phase gate, live unlock and RiskGate are not touched.

**Context.** n_eff above prices overlap within a day only (m_day). A daily card has m_day = 1, so a decision every
day held H = 20 days was credited n_eff = n. Its overlapping labels have lag-j autocorrelation 1 - j/H, so the true
n_eff is about n / H (Hansen & Hodrick 1980): power was overstated by about sqrt(H), and such a card could start where
it cannot pass (daily-timeframe researcher, 2026-11-25). Clone arms and the same bet on two timeframes were also
counted twice.

**Decision.**
1. Q3, as ruled: "n_eff = sum(u_i)/DEFF_day; u_i = average uniqueness over label horizon pooled across tfs/arms per
   instrument; DEFF_day = 1+(m_w-1)*rho_day, m_w = sum M_d^2/sum M_d, M_d = sum u_i in day; then rho_inst factor".
   - `power_first.uniqueness(events)` computes it exactly from the learn labels' entry and exit times (outcome-blind).
     m_w is floored at 1, as m_day is. Labels on different instruments never share uniqueness (rho_inst prices that).
   - A card states it as `PowerCard.learn_uniqueness`.
2. A card that states counts only is read with Q3's expectation on even arrivals of signals within and across days
   (quant C1 on PR #450). `PowerCard.hold_days` is required: the label horizon, its time barrier.
   - c = max(1, signals per instrument per trading day x hold_days) (Little's law on signals).
   - m_eff = max(1, m_day / c).
   - n_eff = n / [(1 + (m_eff - 1) rho_day) x c x (1 + (k - 1) rho_inst)].
   - This is n / H for a decision every day held H days, within 10 % of the measured Q3 at 77 labels a day (60 and
     240 min holds, rho_day 0.25 and 0.05), and unchanged while under one label is open on average.
3. `PowerVerdict.overlap_effect` (n / sum u, or c) and `rho_day` record what was used. A given verdict (H-101) is
   refused by `start_or_refuse` when it credits a lower rho_day than its card implies, or less overlap than the
   card's holds across days (`across_day_overlap`). Within-day overlap of a given verdict stays priced by its own
   rho_day, as signed before C1.
4. Q1: labels of 60 min or less take `rho_day_le_60m` = max(0.05, upper 95 % CI of the day-clustered ICC, week-block
   bootstrap, other families' learn candidates); floor 0.05. Longer labels keep rho_day, floor 0.25. Shipped at 0.25
   until the measurement (RD lane) lands, so no verdict changes today.
5. Callers: autodiscovery states its specs' time barrier (MAX_BARS bars: 15m 1 day, 1h 2, 4h 4, 1d 10); H-101 states
   `model_zoo.HOLD_DAYS = 1.0` (longest arm RBR15, 96 x 15m; a test holds every replayed arm to it).

**Acceptance tests (Q3).** Daily H = 20 within +-5 % of n / 20; max_open = 1 unchanged; 15m + 30m pooled <= 15m alone
x (1 + unique share); a clone pair counts once; count-only matches measured within 10 % on even arrivals
(tests/research/test_power_first.py, `test_q3_*`).

**Consequences.**
- H-101: its signed stage 0 verdict stands (across-day overlap 1, rho_day 0.25). Its within-day overlap is priced by
  rho_day 0.25 alone; under C1's count-only formula it would be higher. Open for the quant (N2: H-102 onward states
  measured uniqueness).
- Autodiscovery (stopped by plan S6 in the same change): with each spec's real time barrier, a spec that signals
  many times a day is about n / (signals a day x hold) independent labels. An every-bar 1h spec (48-bar hold) and
  4h are no longer reachable; 1d was already unreachable.
- The horizon overstates the hold of target exits (1R/2R/3R), so the count-only path is conservative for them. A card
  that wants the credit states its measured uniqueness.
- H-102's power must be recomputed with Q3 before it starts (W1-F card revision).

## Amendment 2026-11-25 (quant-reviewer rulings Q1, Q3, Q5, R13; written by the research director)

Status: proposed; the quant-reviewer signs. Research and demo promotion only; the phase gate, live unlock, real-money
confirmation and RiskGate are untouched. Settings can still only tighten, except where Q1 replaces a prior with a
measured bound (below). Source: quant-reviewer plan 2026-11-25, section 0. Closes follow-up (iv) for rho_day at
horizons of 60 minutes or less and for rho_inst; follow-up (iv) stays open for rho_day above 60 minutes until Q3 is
fully implemented.

**Q1. rho_day for short horizons.**
- For label horizons ≤ 60 min: rho_day = max(0.05, the upper 95 % CI of the ICC).
  - The ICC is day-clustered, estimated with a week-block bootstrap on learn 2010-17, from **other families'**
    replayed candidates with the same stop and hold geometry. Never from the card's own arms or outcomes.
- Horizons > 60 min keep the 0.25 prior until Q3 lands in full.
- Until the ICC measurement is committed (script, data sha256, output file), rho_day stays at 0.25 for every horizon.
  The validator accepts rho_day < 0.25 only with `rho_day_measurement` naming that file.
- Every read still uses the day-block bootstrap. No p-value is ever taken from an analytic n_eff (a test pins it).
- Unchanged: n_eff floor 200, delta 0.10 R, sd floor 1.2 R.
- The validator floor `rho_day ≥ 0.25` (config.py, `PowerFirstSettings`) becomes a per-horizon floor: 0.05 for
  ≤ 60 min, 0.25 above.
- Effect, re-derived here from the ADR's own formula: at 77 candidates a day, n_eff/day = 77 / (1 + 76 x 0.25) = 3.9
  under the prior and 77 / (1 + 76 x 0.05) = 16.0 at the floor.
- This is the one place a bound moves down. It is a measured upper confidence bound replacing a prior, and the reads
  stay on the bootstrap, so the false-pass rate is held by the read and not by n_eff.

**Q3. n_eff counts label overlap (design effect).**
- n_eff = sum(u_i) / DEFF_day, then divided by the rho_inst factor.
  - u_i: average uniqueness of row i over its label horizon, pooled across timeframes and arms per instrument
    (López de Prado 2018, ch. 4).
  - DEFF_day = 1 + (m_w − 1) rho_day, with m_w = sum(M_d^2) / sum(M_d) and M_d = sum of u_i in day d.
- `power_first.design_effect` gets a concurrency input. ADR 0096 implements the cluster-level part (a stated hold,
  Little's law). The label-level pooling across timeframes and arms is still to build.
- Tests: a daily decision held H = 20 gives n_eff within ±5 % of n/20; max_open = 1 is unchanged; 15m and 30m pooled
  give at most the 15m n_eff x (1 + the unique share); clone pairs count once.

**Q5. Daily-P&L (position) reads.**
- n_eff = trading days, then the rho_inst factor.
- Inference: Newey-West with lag max(5, ceil(1.5 x mean episode length)), or the stationary block bootstrap.
- Mandatory excess over an episode-sign-randomised control **and** over a drift- and beta-matched book.
- About 1.03 annual net Sharpe on one instrument is needed for 50 % power.
- This opens one honest 1d judged route (position reads). 1d **bracket** reads stay never judged.

**R13. Multi-asset pooling.**
- n_eff_pool = sum of n_eff per instrument / (1 + (k − 1) rho_inst).
- rho_inst: prior 0.5 (0.7 for metals), unless replaced by the upper 95 % CI of the within-class same-day ICC (the Q1
  method).
- One joint day bootstrap across instruments.
- An instrument enters only with a cost of record and Tier A/B history (quant R11).
- Brokers are pooled for gross labels only, with a broker indicator and each broker's own cost legs. Judging is per
  broker.

**What this does not change:** delta, the n_eff floor, the sd floor, Holm, the alpha-spending ledger, the phase gate.

## Amendment 2026-11-27 (ML lifecycle plan of 2026-11-25: N18, N26, N12, A2, A4; written by the research director)

Status: proposed; the quant-reviewer signs, the evaluator owns the confirm calendar. Research and demo only; the phase
gate, live unlock and RiskGate are untouched. Rulings R-O, R-Q and R-J of the lifecycle plan.

**N18. Procedure-level K and the forward-read version rule (R-O).**
- For history-pretrained, live-updated models (sr-v3, H-102, zoo-v2 and later), the judged unit for K is the
  **procedure**: pretrain + frozen a0 + head scope + update cadence. K 1 per procedure per judged unit (inside D9's
  ≤ 5 for gold 1h+4h). sr-v2 keeps its signed per-run K.
- Every forward row is tagged with the scoring model's sha. A forward read pools rows scored by retrained children
  only if each child's update stayed inside the pre-registered head and max_shift (checked from lineage); otherwise
  the child is a new version with its own read. The body is frozen for the whole read. Fit-only retrains are K 0 and
  are never judged mid-read.
- Test: a forward read spanning a child outside max_shift is refused by the registry, and a pooled read whose rows
  carry a sha with no lineage record raises.

**N26. 2012-17 is a SEEN development set; reads happen once; one confirm date (R-Q).**
- The 2012-17 out-of-fold window is declared **seen** (H-101's per-arm and per-year tables have been read, and the
  next cards are designed after them). A Stage L on it is a screen: it may only send a card on to confirm. **No go
  decision** (serving, influence, promotion) is taken from it. eval-v1 runs on it as descriptive or veto output only.
- Go decisions come only from test_old (where tiered), the batched confirm read, or forward rows.
- **Read once:** `window_reads.jsonl` and the registry refuse a second judged read of the same window for the same
  question. Test: a second judged read raises.
- **One batched confirm date** for sr-v3, H-102 and zoo-v2 (and OMB-1 if it continues), owned by the evaluator. The
  evaluator fixes it in docs/ROADMAP.md by 2026-12-06, before any Stage L read of the batch, and it never moves after
  a Stage L read. No per-card confirm read. Multiplicity: **Holm within the batch on the section 2 ledger** (quant
  ruling, review of #458; N26's BH proposal is not adopted, because the ledger is a family-wise pot). Each card's own
  confirm threshold still applies; Holm adjusts its p within the batch.
- No confirm read runs before the K-of-record reconciliation (ruling D5, due 2026-11-27).

**N12. Forward reserve weeks.**
- From 2026-10-01, forward weeks with an **odd ISO week number** are reserve weeks: their rows are judged-only and
  never used to train, update or calibrate any model until the read that uses them is recorded. Even ISO weeks may
  train (2026-10-01 is in ISO week 40, a training week).
- Scope: sr-v3, H-102, OMB-1, zoo-v2 and every card signed after this amendment. sr-v2 runs as signed.
- Training rows whose label horizon ends at or after the start of the next reserve week are purged (full horizon,
  calendar time). A judged reserve-week row whose features use a row trained on is fine; its label is never trained
  on.
- Test: a training loader given a reserve-week row raises.

**A2. K rules.**
- An objective or loss swap on the **same arms and inputs** carries the cumulative K of that series and books K +1.
  It is not a new series and does not restart the count.
- Fit-only and record-only runs are 0 K **only if** their out-of-fold and outcome statistics are blind to whoever
  picks the spec (quant Q2, Q4). Otherwise they are looks and count.
- Each D-40 variant and each dense-head choice counts as one trial per variant.
- A tier change after any labelled count costs K.

**A4. OHLC quality tiers frozen by hash (R-J, quant R11).**
- The tier rule is r11-v2.1 (`R11_V21_ID = "r11-v2.1-2026-11-25"`, `goldbot/data/era_quality.py`, PR #453). It is
  frozen by hash. **Signed by the quant (review of #458):** git blob c6f283d763718bcf32f1c3971acd812a868fce63, sha256
  ff1cb0c62c096e47dee684a117962856c7cf40395ef7c6172924731093a7db2a (the same at 1b125c7f and 810acc60).
- A: barrier labels and intraday features. A-consistent: used as A, and every read is also reported without it.
- B: close-to-close labels and daily features only, at every timeframe. C, F and P: excluded in every window.
- The vol-tercile mix is reported before and after the filter. The sha256 of the produced tiers file is recorded here
  before any labelled count. The same filter applies in learn, test_old and confirm.
- Any change to the rule after a labelled count is a new id and costs K (A2).

**N26 addition (quant issue 12 on PR #460, 2026-11-26).** "Learning-class demo serving at K 0 is not a go decision,
provided deployment is unconditional on any Stage L/C outcome (every cell with a merged report and a Stage 0 pass
deploys)."
