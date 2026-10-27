# 0053: Strategy rankings shown before the read (amends ADR 0050 and ADR 0051)

Status: accepted with the quant reviewer's binding conditions (2026-10-27; decision 6), built in backlog 136-139.

## Context
ADR 0050 withholds every per-arm aggregate (mean, hit rate, CI, rank, comparison with the control) until the arm's
pre-registered read, to keep the reads free of interim looks. The owner decided otherwise on 2026-10-27: "in each
review of trades, the strategies should be ranked based on accuracy for profit and other metrics product owner can
decide", and, the same day, "don't show me ranking design, use expert agents who will decide based on what is best
for the system to learn, improve and apply". Showing results is the owner's decision; how to show them, and how to
keep the pre-registered reads worth something, is ours.

## Decision
1. Every daily review (Telegram and dashboard), the strategy board and the weekly digest show a ranking of strategy
   families after costs, built by one builder (docs/design/trades-ux-2026-10-27.md §7; backlog 136-139).
2. Primary key: the Bonferroni-adjusted lower bound of net R per trade after costs, `mean - t(1 - 0.025/K, G-1) x
   SE`, with K the number of families ranked in that ranking and SE clustered by (account, opportunity day), G the
   number of clusters (about 3.0 standard errors at K = 30). The unadjusted 95% CI (t with G-1 df) is shown beside it.
   Hit rate, net £ (total and per trade), profit factor, max drawdown in R, n, edge vs random, cost per trade and
   7/30-day figures are shown beside it, never sorted on.
3. Honesty: n and CI on every row; under 30 fills or 10 opportunity days a row is greyed and unranked; every row says
   "early, not proven" until its read or gate.
4. Superseded: ADR 0050 decisions 2-4 (no pre-read aggregate, blinded narrowed lists and totals, board never ordered
   by result) and the daily review's banned rank/R vocabulary for the ranking section. ADR 0051 decision 4 (the
   board computes no difference vs control) becomes "computes a descriptive one, labelled so; the evaluator's
   recorded comparison replaces it at the read". ADR 0050 decision 1 (money always visible) stands.
5. The reads stay as pre-registered (same n, same rule, same alpha), under these commitments, binding on every
   researcher, the evaluator and the allocator:
   a. The ranking never changes trading: allocator, budgets, stops, annealing and reads do not read it (import test).
   b. No rule, parameter, stop, `read_at` or budget edit to an unread arm. A change makes a new arm (new
      `config_hash`, n from 0); the old arm is read as registered or retired by its own rule.
   c. No early stop or early graduation on the ranking; only the pre-registered rules stop or promote.
   d. A hypothesis or variant suggested by the ranking is tagged `ranking-seeded` in docs/research/hypotheses.md and
      judged only on fills after its registration date.
   e. Every read verdict from now on records "results visible since 2026-10-27" and the fills seen before that date.
6. Quant reviewer's binding conditions (2026-10-27):
   a. The sort key is the Bonferroni-adjusted lower bound of decision 2 (one-sided alpha 0.025 / K_ranked), never the
      unadjusted bound: the top of a 30-row table is the maximum of 30 noisy estimates, and an unadjusted bound puts a
      null family first about half the time.
   b. Every interval uses t with G-1 degrees of freedom, G the (account, opportunity day) clusters; a family is ranked
      only with at least 30 fills on at least 10 opportunity days.
   c. The ranking's key is NOT what a read or a gate checks. Reads and gates use their pre-registered rule and the
      deflated Sharpe with the real trial count K; the ranking is descriptive and orders a table, nothing more.
   d. Every new `config_hash` row (a rule edit under a new arm id, shown as "v2") is one more registry trial: it is
      pre-registered and charged to the quarter's budget like any trial.
   e. An amendment registered after 2026-10-27 (results visible) costs one more K and loses holdout credit: its
      evidence is forward fills only, after its registration.
   f. Promotion reads only the recorded verdict (config/arm_reads.yaml), never the ranking. Enforced by an import
      test: only the API, the strategy board, the daily review, the digests and the ranking's own tests may import
      goldbot/api/strategy_ranking.py (tests/test_strategy_ranking.py), so no engine, allocator, stop, annealing,
      evaluator or promotion module can.
## Consequences
- The pre-registered reads lose blinding. With 5a-5c the nominal error rate of each read holds (looks without action
  do not change it); the residual risk is behavioural (tinkering, selective reporting), checked by the config-hash
  tests and the evaluator. The quant reviewer's cost is decision 6: the adjusted key, the 10-day minimum, +1 K per
  new `config_hash` and per post-unblinding amendment, and promotion read from the recorded verdict only.
- The ADR 0050 withholding (trades_ledger.withheld_reason, daily_review blind() for the ranking, the board's
  progress-only order) is retired in backlog 137-139 rather than left half on: one view hiding what another shows
  only confuses.
- Revisit if the quant reviewer rules that a read cannot stay valid under these commitments: then the ranking covers
  read families and engine strategies only, unread families show progress, and the owner is told why.
