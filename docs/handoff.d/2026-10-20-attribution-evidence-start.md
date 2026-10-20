- H-52 evidence start now cuts every shadow-evidence input that feeds a decision (evaluator finding, MEDIUM). Trades
  OPENED (signal bar open) before 2026-10-19 13:49 UTC are quarantined in: the attribution report (`trade_rows` /
  `build_report(evidence_start=)`, counted in its notes), the research director's attribution cells (budget tilt, the
  retired families' exploration pick and reinstatement; `AttributionEvidence.excluded_quarantined`; rows without a
  timeframe are cut on entry less 1w, fail closed) and the shadow record `state/shadow_<version>.json` that the
  promotion gate and director shadow_z read (`ShadowBook(evidence_start=)` defaults to EVIDENCE_START_UTC). So R-14/R-15
  trades of 2026-10-19 00:00-13:49 can no longer count towards reinstatement. The shadow-record cut takes effect when
  the engine restarts; the report/plan cut on the next attribution / research_director job. Not cut (monitoring or
  calibration, listed for quant review): CUSUM model_watch, drift, recalibrate, perf_report, automode eligibility.
