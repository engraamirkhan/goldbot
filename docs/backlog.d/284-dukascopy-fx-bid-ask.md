---
number: 284
rank: 103
title: 'Data: Dukascopy FX10 1m bid+ask pull 2003-01..2025-09 for the FX era spread'
stage: 'Data'
size: S
status: proposed
---
- **284. Data: Dukascopy FX10 1m bid+ask pull 2003-01..2025-09 for the FX era spread.** Value: HistData FX is bid-only, so the measured FX cost of record (backlog 280a, U2 P3) has no era spread term without a bid+ask source. Owner: data-engineer; due 2026-12-18. Acceptance: `raw/dukascopy_fx/<inst>_1m_dukascopy_<year>.parquet` for the 10 pairs with `ts_utc`, `spread_mean`, `dq_flag` (the schema `fx_cost_cells.fx_spread_table` reads); a sha256 manifest; nothing from 2025-10 on (holdout sealed); data local only, never published; no label or outcome read.
