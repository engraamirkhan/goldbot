- Catalogue screen gates moved to EVAL-1 (evaluation audit 2026-11-07 section (b), quant C1-C6; branch
  `fix/catalogue-eval1-gates`). `goldbot/research/catalogue/stats.py`: the unit is a daily net book marked to market on
  daily closes at the card's one sizing (0.5 % per R), benchmark cash. Explore = E1 Newey-West t >= 2 at
  L = max(5, ceil(1.5 x median hold days)) (smaller t of L and 2L), E2 net > 0, E3 gross >= 2 cost, E4 5/8 net years,
  E6 n floors, E7 learn_pre net same sign, E8 top-1 % trades/days and 10 % single-trade tail, E9 drift guard against
  the financed buy-and-hold book of every side traded (alpha t >= 1, alpha >= 50 % of mean, 5/8 years vs the
  exposure-scaled book), twin as a kill only (t <= -2). CONFIRM: BH on the net book's bootstrap p, 5/7 net years,
  net > 0 at 1.5x, drawdown <= 8 % at 0.5 %; TEST-OLD adds beta > 0.3 => net >= 0; promotion DSR >= 0.95 on the
  CONFIRM series (reported in confirm.json). Reading rule with the stricter readings:
  `docs/research/prereg-2026-11-07-eval1-reading-rule.md`. Nothing was run; K unchanged.
