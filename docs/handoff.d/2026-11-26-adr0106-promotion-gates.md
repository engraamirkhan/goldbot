- 2026-11-26 ADR 0106 promotion and money gates (lane 2, R185, R186; finding ADR0106-PROMOTION). Stricter only.
  - Promotion (`research/promotion.py`): a challenger must beat the live model by session day, after costs, on as-if
    real-money P&L. Fewer than 30 paired days means not ready. The read stops at the first 60 days. The one-sided
    day-block bootstrap bound must be above 0, at alpha 0.05/7 looks. Shadow Sharpe must be above 1.0 (was 0.8).
    An as-if breach of 1 %/2 %/15 %/1 % ADV is a final refusal. Trades below the minimum lot are not counted toward
    the 40 clusters. Missing evidence means "waiting": nothing is retired for evidence it was not given.
  - `ops/jobs.py` does not build the paired and as-if reads yet, so every challenger now waits and then expires.
    The specialist retrain is retired, so this changes nothing on the brain today; the wiring is a follow-up.
  - Paper gate (`ops/gates_phase.py`): four new required items: `as_if_real_money`, `paper_sharpe`,
    `beats_buy_and_hold` (research/benchmarks.py: one round trip plus the signed long swap) and `paper_cost_share`
    (at most 1/3; fails closed when more than 5 % of trades are unmeasured). Every existing item is unchanged and the
    182-day rule stays (settings refuse less). `as_if_equity_usd` is 1,250 USD (PD + CFO: a simulation parameter, not live sizing).
  - Until lane 1's reference ADV is wired into `run.py gates`, `as_if_real_money` is unmet (fail closed).
  - Confirm reads (`promotion_confirm.run_batch`) write buy-and-hold beside the random twin. This is report only:
    the frozen cards, G3 and N10 do not move.
