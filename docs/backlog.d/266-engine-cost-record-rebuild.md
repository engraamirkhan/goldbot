---
number: 266
rank: 91
title: 'Engine cost of record: next version from quote-measured exits (P6 coverage >= 95%)'
stage: Data · costs
size: S
status: 'proposed 2026-11-20 (data-engineer, ADR 0092) · accountable: data-engineer · reviews: quant-reviewer'
---
- **266. Engine cost of record: next version from quote-measured exits.** Value: `ecr-2026-11-20.1` imputes every
  Vantage exit and every ICM time exit from the probes. Since ADR 0092 each exploration exit records its quote, so
  the next version can measure exit slip on every exit. Acceptance:
  1. a weekly read reports the share of closed gold exploration exits per account that carry `exit_bid` and
     `exit_ask`, against the 95% P6 target;
  2. once the target holds for 14 session days, `python -m goldbot.research.realised_costs` writes a new version
     file (never an edit of the old one);
  3. the report sets that version's cells beside `ecr-2026-11-20.1`, cell by cell. `costs.engine_record` moves only
     after a quant review;
  4. a drift alert fires when a full cell's 14-day mean leaves the charged version's 95% interval.
