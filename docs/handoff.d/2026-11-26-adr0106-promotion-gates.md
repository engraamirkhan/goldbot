- 2026-11-26 ADR 0106 promotion and money gates (lane 2, R185, R186; finding ADR0106-PROMOTION). Stricter only.
  - Promotion (`research/promotion.py`): a challenger must beat the live model by session day, after costs, on as-if
    real-money P&L. Fewer than 30 paired days means not ready. The read stops at the first 60 days. Looks happen only
    at paired days 30, 35, ..., 60; any other day is "waiting" with no bound. At each look the one-sided day-block
    bootstrap bound must be above 0, at the pinned O'Brien-Fleming alpha (c = 1.8117). The mean block is
    max(5, ceil(1.5 x the longer mean holding episode)). Challenger and live run as two as-if accounts from the same
    start and equity. The challenger is a procedure: every row carries its scoring sha, and its read is booked as one
    registry trial (K +1) at the first look. Shadow Sharpe must be above 1.0 (was 0.8).
    The as-if replay uses the stricter of ADR 0106 and the risk settings (today 0.5 %/trade, 2 % day, 5 % week latch
    and breach, 12 % drawdown, 1 % ADV); a breach is a final refusal. Trades below the minimum lot are not counted toward
    the 40 clusters. Missing evidence means "waiting": nothing is retired for evidence it was not given.
  - `ops/jobs.py` does not build the paired and as-if reads yet, so every challenger now waits and then expires.
    The specialist retrain is retired, so this changes nothing on the brain today; the wiring is a follow-up.
  - Paper gate (`ops/gates_phase.py`): four new required items: `as_if_real_money`, `paper_sharpe` (the lower of naive and Lo 2002 lags 1-5; a floor, not evidence of an edge),
    `beats_buy_and_hold` (research/benchmarks.py: one round trip plus the signed long swap) and `paper_cost_share`
    (at most 1/3 in as-if USD; fails closed when more than 5 % of as-if sized trades are unmeasured, by count or by
    notional). Every existing item is unchanged and the
    182-day rule stays (settings refuse less). `as_if_equity_usd` is 1,250 USD (PD + CFO: a simulation parameter, not live sizing).
  - Until lane 1's reference ADV is wired into `run.py gates`, `as_if_real_money` is unmet (fail closed).
  - Confirm reads (`promotion_confirm.run_batch`) write buy-and-hold beside the random twin. This is report only:
    the frozen cards, G3 and N10 do not move.
