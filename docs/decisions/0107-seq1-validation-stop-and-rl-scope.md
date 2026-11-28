# 0107: SEQ-1 validation stop under obj-v1 and the RL-X1 / size-v1 scope

Status: accepted (quant review of PR #489, 2026-11-28; point 1 restated by the quant reviewer; proposed by the principal
software engineer 2026-11-26)

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
1. **SEQ-1's stop (quant ruling, 2026-11-28).** "A hand-written stop is permitted only on the objective-of-record
   training loss (obj-v1 weighted squared error), evaluated on an inner time split inside the outer training fold
   (last val_share of its span, purged by windows.label_embargo), with patience, max_epochs and val_share fixed in
   the card before any read and best_epoch recorded per seed and fold. Library early stopping, any other metric, or a
   validation set touching an outer test row stays banned." `seq1.fit` implements it (`seq1.inner_split` /
   `seq1.split`; validation rows are a strict subset of the fit's input, after its fit rows: tested), and seq1 stays
   in `GUARDED_MODULES`. For `seq1_embed` (outcome-free) the stop is allowed without conditions; its validation rows
   also start their windows after the last fit decision (`seq1_embed.embed_split`).
2. **The GBT comparison is a serving verdict.** `seq1.read` returns the bundle's keep on the judged rows;
   `history_runs.seq1_quality` reads `model_quality.beats_baseline` (paired, after costs, day-block bootstrap) against
   the GBT run manifest on the same rows (ADR 0106 3). It is necessary, not sufficient: the card's obj-v1 gate legs,
   the seed-SD rule and K stay as they are.
3. **RL scope.** RL-X1 (fitted-Q exits on top of the T2 template's stop and target, shielded actions hold / close /
   breakeven) counts as execution under ADR 0106 and stays offline, never live. The sizing scaffold size-v1
   (`rl_sim`) is closed form: fraction of equity = min(1 %, 0.5 % / vol ratio), with the 2 % daily stop applied before
   a trade (a trade whose full stop could cross it is skipped) and the 15 % drawdown halt. PPO/SAC/TD3 are allowed by
   ADR 0106 inside the simulator but none is fitted now (R7d); size-v1 is the baseline any learned sizer must beat.
4. **Run record: one format, ADR 0109's run manifest.** Every SEQ-1 bundle, embedding and RL-X1 fit goes through a
   builder in `history_runs` and `run_manifest.fit_and_record`; `run_manifest.rebuild` reproduces the sha (tested on
   a clean checkout). SEQ-1's served form is the 5-seed bundle; its manifest carries `model_quality.seed_stability`
   and `beats_baseline` against the GBT manifest (`history_runs.seq1_quality`). The embedding (an input block) and
   RL-X1 (never live) record no serving verdict, so `require_servable` refuses them.

## Consequences
- Point 1 is accepted as restated above; the SEQ-1 card fixes patience, max_epochs and val_share before any read.
- Every learn read that computes outcome statistics (`seq1.read`, `rl_exits.run`) is a gate read: booked in the
  read-once ledger under a family registered with the signed study plan, charged at one D-3 cost of record, and
  refused otherwise (quant on #489). Fit-only paths book nothing.
- The simulator's ub95 slippage is 0 until a measured value per broker x session exists (item 6, FillModel); a read
  on W2 point costs alone is not a gate read.
- Nothing in goldbot/engine, execution or risk changes; a serving path for SEQ-1, RL-X1 or agg-v1 needs its own ADR
  and a trading-safety review.
