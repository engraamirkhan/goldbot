---
number: 113
rank: 46
title: 'Disaggregated COT before Sept 2009: availability floor'
stage: 'Data · PIT fix'
size: S
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer'
---
- **113. 'Disaggregated COT before Sept 2009: availability floor'.** Value: the CFTC first published the disaggregated report (managed money) on 2009-09-04, with history back to 2006-06. Rows of positioning-v1 dated before then carry the weekly Friday stamp, so features on 2006-2009 see managed-money data years before it existed (macro-pit-audit B1 called it a caveat; it is look-ahead). Acceptance: report dates before 2009-09-04 get available_utc no earlier than 2009-09-04 15:30 ET (a floor like SHUTDOWN_FLOORS), applied as new rows (merge_releases keeps the published ones; research reads the latest available_utc per value_date only if first_release is changed accordingly, quant rules which); the legacy report (source cftc_legacy, 1986 on, real-time since then) is the pre-2009 positioning input instead. Seeded test: a 2007 bar never sees a 2007 managed-money value. Done = verified first date per series, the PIT stamp rule (available_utc) written and tested with a seeded future row, and the quality checks (coverage per month, gaps, price-scale sanity against known levels, spikes, weekend rows) run and reported.
