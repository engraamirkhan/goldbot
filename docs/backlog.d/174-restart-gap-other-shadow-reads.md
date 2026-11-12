---
number: 174
rank: 69
title: CUSUM model watch and shadow reports leave restart_gap shadow trades out
stage: 'engine / research (follow-up of backlog 165, R129, 2026-11-12)'
size: S
status: 'proposed · accountable: implementer · reviews: quant-reviewer, trading-safety-reviewer'
---
- **174. CUSUM model watch and shadow reports leave restart_gap shadow trades out.** Value: backlog 165 replays shadow trades across a restart and leaves the ones it could not replay (`restart_gap`) out of promotion stats, shadow_z, recalibration, the re-arm read and the throttle's shadow win, but the daily model watch (`ops/jobs.py` CUSUM over every closed shadow trade), `telegram/automode.py`, `ops/perf_report.py` and `research/ruin.py` still read them, so a path with missing bars can move a CUSUM alarm or a report. Separately, the exploration 1m/5m minute pass keeps its own per-minute state and is not covered by the restored `closed_through` (backlog 163): check whether a restart re-decides its last minute. Acceptance, tests first: each read excludes or flags `restart_gap` trades (a test per read with one gap trade); the exploration minute pass after a restart is tested with a stale quote and either never re-decides the last processed minute or the finding is recorded.
