- **H-27 lot-fit overlay in research (implementer, 2026-10-18; opt-in, not yet reviewed by quant).** Playbook
  small-account-playbook-2026-10-18 section 3: research no longer claims £ from trades the account cannot take.
  `goldbot/research/lot_fit.py` sizes every trade with the gate's own `risk.sizing.size_lots` (equity, risk per trade,
  0.01 min/step, 100 oz, account_fx conversion; stop = label `risk` x `entry`); trades the gate would refuse
  `min_lot_exceeds_risk` are `unfit` and excluded; fitting ones are scaled by the actual lot. `run_specialist(...,
  lot_fit=LotFitSpec)` / `evaluate` / `_walk_forward` add `metrics.lot_fit` (model_filtered and all_candidates: n, fit
  fraction, mean lots, £ P&L, R at lots, net R of the fit subset). Default (`lot_fit=None`): no key, outputs and
  registry rows unchanged (tested). New `research.lot_fit_equity` (1000) and `lot_fit_acct_per_usd` (0.75, a research
  assumption). Not done: routing branch before RiskGate (ATR stop, else H-25, else skip), research_pass.py flag/report
  columns, pooled path; min-lot exception, size-down stage and model multiplier not modelled. Tests:
  tests/test_lot_fit_overlay.py.
