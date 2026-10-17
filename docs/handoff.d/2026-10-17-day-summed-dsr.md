- **E-6 day-summed DSR coded (BACKLOG 76, implementer, 2026-10-17; quant review and code review pending; not
  pushed).** The discovery survivors' reading rule (pre-registration 2027Q1 O-5/O-6: "the day-summed DSR is also
  >= 0.95") is now a coded check. `metrics.day_summed_dsr` sums net R per UTC entry day over days with a trade only
  (G days), with var(SR) = 1/G and the skew and kurtosis of the daily sums. Every walk-forward stores it with the
  trial as `results.model_filtered.day_summed`, beside the per-trade DSR of the same net-R series and both effective
  ts (`DsrInputs.t`, the t each DSR deflates). `gates.day_summed_check(row, rows)` is the evaluator's reading. It
  re-reads the stored inputs on `registry.n_trials_effective(rows)`, the phase gate's K (both discoveries' K_eff
  included), and fails closed without them. The research report has a "Day-summed DSR" section.
  - Below 200 trades or 200 trading days the check reports nothing and fails closed (the same asymptotic guard as the
    per-trade DSR, applied to the days). This is the implementer's choice; the quant-reviewer rules on it.
  - The day sums use net R (ret / risk), as the rule says. The gate's per-trade DSR uses `ret`. The record's
    per-trade side is therefore on R, so the two ts compare like with like.
  - Not changed: `research_gates`, the phase gate, stored Q4 results, any frozen reading. `summarize` builds the same
    `DsrInputs` through `DsrInputs.of` (same numbers). Rows written before this have no `day_summed` and read as
    "re-run the trial".
  - Tests: `tests/test_day_summed_dsr.py` (15). On a synthetic sample of 1-3 trades a day with a shared day shock,
    the median fall in t over 20 seeds is in 5-12%.
