---
number: 280
rank: 101
title: 'Cost of record: measured FX10/XAG classes and a stale era-spread refusal'
stage: 'Research (costs)'
size: M
status: proposed
---
- **280. Cost of record: measured FX10/XAG classes and a stale era-spread refusal.** Value: (a) `srv3-cost-2b3628372a9b3bfa` measures gold only; FX10 and XAGUSD are `basis: prior` and `srv3_cost_record.apply` keeps the D-3 block on them, so no pooled-body gate read (H-106 U2, precondition P3) can happen; (b) `gold_row_bp` joins the gold spread table as of `available_utc`, and the table stops at 2025-09, so a row decided in 2027 silently takes the 2025-09 era spread (finite, no refusal): any forward or live use of the record charges a stale era term (H-105 U1 section 3). Owners: microstructure-researcher (a), implementer (b); quant countersigns; due 2026-12-11. Acceptance: (a) a new recorded version (ADR 0098 R-H line) with per-instrument ECR ub95 from demo fills per broker x session (>= `ecr_min_fills` 30 per cell, else the session `all` cell), an era term from Dukascopy bid+ask FX/XAG quoted spreads (HistData is bid-only), each broker's commission and slip_x, W2 floor, worse broker; an instrument short of fills stays `prior` and is listed; no label read; (b) failing test first: a gold row whose as-of spread month is more than one month older than its decision month raises `CostRecordRefused`; holdout months stay refused; the existing gold charges on 2003-01..2025-09 rows are byte-identical.
