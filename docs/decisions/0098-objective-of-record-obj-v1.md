# 0098: Objective of record obj-v1: one money measure for every model

Status: proposed (research-director, 2026-11-25). The quant-reviewer signs (it owns gates and amends its own R14 by
D1-D3 below); the trading-safety-reviewer confirms the execution and sizing rows touch no RiskGate rule. Research and
demo only. Real money, sizing and live unlock stay owner-only.

## Context
- Owner rule A5 (2026-11-25): weights are fitted to an explicit objective chosen by the experts. The owner's addition:
  one money measure everywhere.
- Today each learner optimises something different: sr-v2 and H-101 a plain logistic on 1[net R > 0], the H-102 draft
  a weighted logistic with a 3 R weight cap and a keep floor of one third, older meta-models AUC. The gates judge
  net R. A model trained on one quantity and judged on another can pass training and fail the gate by construction.
- The money facts: labelled candidates are gross +0.038 R, cost 0.074 R, net −0.036 R (execution plan 2026-11-25).
  Cost is about twice gross, so any objective that ignores cost, or forces trading where the model scores negative,
  loses money.
- Rulings it records: execution plan 2026-11-25 §1d and D1-D3; quant-reviewer R14 as amended by D1-D3.

## Decision
**1. The money measure of record (`objective_id = obj-v1`).**
- net R = (exit − entry) x side / initial risk − (spread + commission + slippage including the latency leg + swap or
  financing + the rollover cell) / initial risk.
- Costs come from the cost of record: the measured upper 95 % bound per broker x session x leg, in bps or ATR units.
  Never fixed dollars; never the Dukascopy spread or tick columns as a cost leg.
- **Clip at ±10 R in every use** (D1). Rows beyond ±10 R are quarantined for label audit and counted, never silently
  dropped. A −3 R clip is refused: gap-through-stop losses are real money.
- £ = net R x £ per R at the decision size.
- The **same** measure is used for training, threshold, selection and the gate.
- AUC, accuracy, log-loss and win rate are **reported diagnostics only**. They never train, threshold, select or gate.

**2. The five models.**

| Model | Training objective | Decision rule | Selection between candidates | Gate (judged) |
|---|---|---|---|---|
| Trade filter (sr-v3, H-102, OMB-1, zoo-v2) | weighted logistic, y = 1[net R > 0], w = uniqueness x min(abs(net R), 10); its Bayes rule keeps iff E[net R \| x] > 0. GBT via `sample_weight`; calibrators weighted the same | keep iff p_w ≥ 0.5. **No keep floor** (D2). Kept < MIN_KEPT gives ABSTAIN: 0 K, not a pass | out-of-fold S = mean(net R x keep) − max(0, mean(net R)), per candidate, day-block bootstrap | S ≥ +0.05 R with lower bound > 0, kept mean > 0, gross ≥ 0, a side-matched control leg, deflated at the K of record; then two consecutive judged reads of ≥ 100 unseen rows each. £/day reported beside it (D3) |
| RL exit choice (RL-1 contextual bandit over exit templates T0-T7; RL-2 FQI, conditional) | per-action regression of net R under each template (full information on history replay, exact at 0.01 lot); FQI: per-bar reward Δ net R, exit cost charged at the close action | argmax predicted net R; ties go to the cheapest template. Shield: only close earlier or tighten; never widen or remove the broker SL | paired mean(net R of the policy − net R of the best fixed template chosen on learn) | ≥ +0.03 R paired (session-day bootstrap) and net > 0, at FillModel p75 costs |
| Allocation (top-two Thompson or AdaHedge over frozen models, incl. "keep all" and "abstain") | Hedge loss = −net R per candidate (exact replay for counterfactual arms); prior warm-started from history (learn 2010-17 only) | weights from the posterior or Hedge, never above the account profile's caps | off-policy exact-replay cumulative net R vs equal weight | anytime-valid confidence sequence (Howard et al. 2021) on the net R difference > 0; read only when ≥ 2 gated arms per cell |
| Sizing (fractional Kelly with shrinkage) | maximise E[log(1 + f x net £ / equity)] on the calibrated net-R distribution, subject to the margin floor | f = 0.25 x shrunk Kelly, rounded down to the volume step | replay on history vs a fixed 0.01 lot at matched risk | demo only, after a judged filter pass, on the size ladder (CFO + RD). **Real money is owner-only** |
| Execution (dispatch, order type, netting, passive variant) | minimise implementation shortfall in R: (fill − decision price) x side + spread + commission + latency drift + **missed-trade net R** | market at signal (default); a passive variant only under its own card | paired vs market-at-signal on the same signals, randomised assignment; controls get identical execution | Δ net R ≥ 0 with lower bound > 0 (passive card, K 1). Netting: £ saved by leg count net of re-materialisation, per-arm labels unchanged (parity test). No execution RL |

**3. Abstention and learning (D2).** Full abstention is allowed: there is no keep floor, and the deployed rule equals
the judged rule. On demo, learning does not starve: every abstained candidate is still replay-labelled, and a
**randomised 10 % sample** of abstained candidates is traded with its propensity logged. That sample is excluded from
the model's kept-money read and used only for off-policy evaluation.

**4. What carries the id.** Every model artifact records `objective_id`. Loading refuses an artifact without it, or
with an id its gate does not name.

## Consequences
- **Tests (acceptance for A5):**
  - a guard test fails any training, threshold, selection or gate call that names AUC, accuracy, log-loss or win rate;
  - a calibration test: per score bin, p_w ≥ 0.5 iff the out-of-fold mean net R ≥ 0;
  - the ±10 R clip and the quarantine count are pinned;
  - an artifact without `objective_id` does not load.
- **sr-v2 and H-101 are unchanged in flight.** They keep their signed objectives. sr-v3 and every new filter card use
  obj-v1. Changing a recipe or loss after a look costs K +1 (quant Q4).
- **Ruled against the quant's own R14 draft, for its signature:** the weight cap is 10 R, not 3 R (D1: a 3 R cap
  flips the sign of runner payoffs; brackets never reach 10 R, so it costs nothing there). The rest of R14 stands:
  no keep floor, ABSTAIN below MIN_KEPT, the statistic S.
- **Expected effect, honest:** obj-v1 makes a model abstain where cost exceeds gross. With today's labels that means
  fewer trades and, most likely, no model passing. That is the point: it stops paying spread for noise.
- **Out of scope:** PPO, DQN, end-to-end deep RL, RL on live fills and execution RL are declined this quarter (D12).

## Owner rules served
A5 (objective), A2 (singles and fusions judged on the same measure), A8 (demo learning continues under abstention).
