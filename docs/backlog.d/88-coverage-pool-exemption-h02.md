---
number: 88
rank: 24
title: 'H-02: full-coverage columns are exempt from the 80% pool rule; survivors carry the mask; field in the dump'
stage: Research (discovery pipeline, pre-registration H-02) · LEARN
size: S
status: 'ready (research-director pin 3a0d25d, 2026-10-17; H-02 does not run without it) · accountable: implementer · reviews: quant-reviewer, code-reviewer · due 2026-12-11'
---
- **88. H-02: full-coverage columns are exempt from the 80% pool rule; survivors carry the mask; field in the dump.** Value: `macro_spx_dd20` covers ~57% of candidates, so the global >80% pool rule (`pipeline.py` `eligible_columns`) drops it and H-07 would silently become VIX-only in every fold; the pre-registration now pins that columns in `full_coverage_columns` are exempt from the 80% rule and governed by the per-fold mask, that survivor slots 6-9 carry the mask, and that CPCV/pooled are out of scope. Acceptance: the pool exemption with a test that `discovery_pool` keeps a named column at 57% coverage and drops an unnamed one; `run_specialist` passes `full_coverage_columns` to `evaluate` (test); the field written into H-02's dump and YAML with the verbatim-dump test normalised (tuple vs list); default hashes/goldens unchanged.
