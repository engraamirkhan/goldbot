- 2026-11-09 (branch fix/registry-unlinked-prereg; quant ruling 2026-11-09, item 3(b)): an ad-hoc (`queue: false`)
  pre-registration that is not withdrawn and has had no linked result row for more than 7 days
  (`registry.STALE_PREREG_DAYS`, `stale_preregistrations`) now counts as one trial of the quarter of its `ts` in both
  `quarter_trials` (the budget) and `n_trials_effective` (the DSR's K). Once a result row links to it only the result
  counts; queued rows stay under `director.reserved_trials`; loop rows stay out of the quarter budget (ADR 0029) but in
  K. Both functions take `now=` (default the registry clock). Exemption (quant ruling 2026-11-09): only a `withdrawn`
  row naming the pre-registration by (ts, config_hash) with `reason` "unevaluable: <cause>" or "never formed: <cause>"
  and `evidence` (run output files with sha256; top-level `evidence` [{path, sha256}] or the applied sprint-3000
  backfill's `results.evidence` [{source_file, source_sha256}]), written before any read of the spec (`exempting_withdrawal`),
  exempts it, and only when written before the prereg's stale point (ts + 7 d; unstamped = late), so the counts never
  fall as time passes; `evidence_ok` checks format only (content stays with the writer plus quant).
  `TrialRegistry.withdraw(..., evidence=)` records it. Brain registry copy (sha 73fc51e1) at 2026-11-20:
  quarter_trials(2026Q4) 3,395, n_trials_effective 3,471, 0 stale, 389 backfill withdrawals exempt. Any other withdrawal, or a "not a trial" note,
  exempts nothing. Tests: `tests/test_registry_unlinked_prereg.py`.
