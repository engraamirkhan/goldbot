- **H-27 lot-fit overlay in research (implementer, 2026-10-18; opt-in, not yet reviewed by quant).** Playbook
  small-account-playbook-2026-10-18 section 3: research no longer claims £ from trades the account cannot take.
  `goldbot/research/lot_fit.py` sizes every trade with the gate's own `risk.sizing.size_lots`, the gate's exception cap
  (`RiskGate.min_lot_cap`) and its stop floor (stops level plus spread); the min-lot exception (`risk.min_lot_risk_cap`,
  off by default, never tiny-live), the 8% size-down stage and the model multiplier are LotFitSpec fields. Unfit = the gate
  would refuse `min_lot_exceeds_risk`: excluded from P&L, counted; fitting trades are scaled by the actual lot.
  tests/test_lot_fit_overlay.py runs `RiskGate.check` over a 1,500-case grid and requires the same verdict.
  `run_specialist`/`evaluate`/`run_pool(..., lot_fit=LotFitSpec)` add `metrics.lot_fit` (model_filtered and
  all_candidates: n, fit fraction, unfit, mean lots, £ P&L, R at lots, net R of the fit subset) and the report a
  "Lot fit" table. `research_pass.py --lot-fit` (account from `research.lot_fit_equity` 1000 and `lot_fit_acct_per_usd`
  0.75, a research assumption); research.yml optional boolean input `lot_fit` (and a batch key) -> env LOT_FIT -> plan
  validation -> `--lot-fit`. Default off: no key, outputs and registry rows unchanged (tested). Not done: routing before
  RiskGate (ATR stop, else H-25, else skip); `--lot-fit` with --discover/--cpcv/--h16 is refused; margin and the other
  gate checks are not part of "unfit".
