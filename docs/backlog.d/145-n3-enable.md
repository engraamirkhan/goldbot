---
number: 145
rank: 6
title: 'Enable N-3 HAR abstention: refill 2026 Duka holes, overlap check, K row'
stage: 'research ops (N-3, prereg-2026-11-07-n3-har.md)'
size: S
status: 'ready (N-3 shipped off, PR #342) · accountable: data-engineer (holes, overlap); research-director writes the K row and enables · reviews: quant-reviewer · due 2026-11-18 (triage 2026-11-13)'
---
- **145. Enable N-3 HAR abstention: refill 2026 Duka holes, overlap check, K row.** Value: N-3 ships with `research.har_abstention.enabled: false` (quant R7); it starts only when its inputs are whole and its feed cut-over is measured. Acceptance: (1) the 2026 Dukascopy holes inside the trailing window are refilled (in progress: `scripts/local_bars.sh refresh 2026` then `push`; on the Mac copy 2026-04-01..04-06, 04-10..04-16, 06-09..06-16, 07-09..07-21 and 07-29..08-03 exceed `max_gap_days` 4, so `har_days` would fail every night) and a dry run of `har_days` on the brain writes an `ok` row; (2) the data-engineer's Duka/ICM overlap measurement (median ln(ICM/Duka) of daily TR, W and w) is recorded and |median ln ratio of w| <= 0.02, else an amendment first; (3) the research director writes the registry row (family novel:2026-11, unit N-3, K+1); then `enabled: true` in config/settings.yaml with the three results cited.
