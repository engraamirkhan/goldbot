# 0107: SEQ-1 validation stop under obj-v1 and the RL-X1 / size-v1 scope

Status: proposed (principal software engineer, 2026-11-26; for quant and trading-safety sign-off on PR review)

## Context
ADR 0106 (owner, 2026-11-26) asks deep models to "regularise (dropout, early stopping, weight decay) and train several
seeds", split "by time, never randomly, with a gap between train, validation and test", and puts RL (family D) on
"position sizing and order execution, trained in a simulator with realistic costs". ADR 0098 (obj-v1) bans "library
defaults that select on their own metric (CV searches, `*CV` estimators, early stopping, a `scoring=` argument)" from
every fit, threshold, select or gate path, and `objective.GUARDED_MODULES` enforces it by name. SYNTH-ml (2026-11-25)
ruled RL-X1 the one RL item this quarter (R7a) and declined PPO/DQN until RL-X1 passes (R7d).

## Decision
0. **SEQ-1's primary path is outcome-free (quant ruling R-c, 2026-11-26).** `seq1_embed` pretrains the TCN on a
   pretext read from the decision's own window only (the next window bar's close move and range at every position,
   causal), exports an 8-dim bottleneck at the decision bar, and is refit per outer fold on that fold's training
   rows purged by `feature_store.purge` with embargo = TCN window x tf + H, learn 2010-17 only. zoo-v2 may take it
   as one block (Riley charges 8, K 1) with the A8 parity battery on the 8 dims (`seq1_embed.parity`). The
   label-supervised head (`seq1.fit` / `seq1.read`, points 1-2) is a separate shadow-only model that needs its own
   card. RL-X1 fits learn A/A-consistent rows only (R-d); test_old is never fit, tuned or used for stopping.
1. **SEQ-1's stop is not a library default.** `seq1.fit` holds out the last 20 % of its own training span by time,
   purged and embargoed by max(1 d, hold) (`seq1.split`, `windows.label_embargo`), and keeps the epoch with the
   lowest validation value of the **obj-v1 training loss itself** (uniqueness-weighted squared error on clipped gross
   R), patience 3, cap 30 epochs. No AUC, accuracy or log-loss is computed. seq1 is added to `GUARDED_MODULES`, so
   the guard keeps holding for it. Why not "fixed epochs" instead: ADR 0106 is the newer, owner-set rule and asks for
   early stopping; a stop on the objective of record within a time-split fold meets both.
2. **The GBT comparison is part of SEQ-1's read.** `seq1.read(gbt_keep=...)` reports the paired D = S(SEQ-1) − S(GBT)
   per seed with its day-block lower bound (`objective.paired_delta`); `deployable_vs_gbt` needs a READ verdict
   (seed SD < MIN_EDGE/2) and every seed's lower bound > 0 (ADR 0106 3). It is necessary, not sufficient: the card's
   gate legs and K stay as they are.
3. **RL scope.** RL-X1 (fitted-Q exits on top of the T2 template's stop and target, shielded actions hold / close /
   breakeven) counts as execution under ADR 0106 and stays offline, never live. The sizing scaffold size-v1
   (`rl_sim`) is closed form: fraction of equity = min(1 %, 0.5 % / vol ratio), with the 2 % daily stop applied before
   a trade (a trade whose full stop could cross it is skipped) and the 15 % drawdown halt. PPO/SAC/TD3 are allowed by
   ADR 0106 inside the simulator but none is fitted now (R7d); size-v1 is the baseline any learned sizer must beat.
4. **Run log.** Every SEQ-1 and RL-X1 fit can log data version, settings, seed, code sha and results (`run_log`), and
   `rebuild` must reproduce the artifact sha exactly (tested).

## Consequences
- Quant to confirm point 1 on the SEQ-1 card; if refused, set `patience = max_epochs` (no stop) by a spec change
  before any read (K +1 if after a look).
- The simulator's ub95 slippage is 0 until a measured value per broker x session exists (item 6, FillModel); a read
  on W2 point costs alone is not a gate read.
- Nothing in goldbot/engine, execution or risk changes; a serving path for SEQ-1, RL-X1 or agg-v1 needs its own ADR
  and a trading-safety review.
