# 0053: Strategy rankings shown before the read (amends ADR 0050 and ADR 0051)

Status: proposed (product owner, 2026-10-27); final on the quant reviewer's sign-off and cost statement (below)

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
2. Primary key: the lower bound of the 95% CI of net R per trade (clustered by account and opportunity day). Hit rate,
   net £, profit factor, max drawdown in R, n, edge vs random, cost per trade and 7/30-day figures are shown beside
   it, never sorted on.
3. Honesty: n and CI on every row; under 30 fills or 5 opportunity days a row is greyed and unranked; every row says
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

## Consequences
- The pre-registered reads lose blinding. With 5a-5c the nominal error rate of each read holds (looks without action
  do not change it); the residual risk is behavioural (tinkering, selective reporting), checked by the config-hash
  tests and the evaluator. The quant reviewer states the cost and any discount before this is accepted.
- The ADR 0050 withholding (trades_ledger.withheld_reason, daily_review blind() for the ranking, the board's
  progress-only order) is retired in backlog 137-139 rather than left half on: one view hiding what another shows
  only confuses.
- Revisit if the quant reviewer rules that a read cannot stay valid under these commitments: then the ranking covers
  read families and engine strategies only, unread families show progress, and the owner is told why.
