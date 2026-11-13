- 2026-11-13 (branch feat/cost-model-series; R150; BACKLOG 90, 255): the nightly `limits_job` now passes ADR 0031's
  20-day measured cost/model ratio (`goldbot/ops/cost_ratio.py`) instead of None, so the frozen cost rule can tighten
  (above 1.25 the floors, 1.10-1.25 one step down). Measured per demo fill: decision spread, entry slippage, exit-leg
  slippage from store `exit_fills`, commission and swap at the account's measured terms; model: the 15m bar spread at
  entry plus the canonical extra cost and model swap. None below 20 measured fills; the result row carries
  `cost_ratio_20d`, `cost_fills`, `cost_unmeasured`. Inert today (no account has adaptive limits on). Tighten only;
  no limit, cap, sizing or order path loosened. Needs quant-reviewer sign-off on the definition before merge.
