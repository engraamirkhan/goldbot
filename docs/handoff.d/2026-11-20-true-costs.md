- 2026-11-20 true costs (ADR 0092; finance audit N10, P5 and P6). The engine's cost charged the $0.15/side slippage
  prior on every cell: its slippage cells read only main-engine fills, and there are none while every engine is
  `probe_only`. So it charged about $0.46/oz where the fills paid $0.19-0.26 (4,302 gold exploration fills,
  2026-10-26..11-19). Changes:
  - `goldbot/research/realised_costs.py` measures the realised round trip per account x session x timeframe: spread,
    signed entry slip, exit slip (from the exit quote, else the stop or target level, else the broker's probe p75,
    else the prior), commission, and swap reported apart. Each cell has a day-clustered 95% CI and p50/p75/p90. It
    writes an immutable versioned record, `config/engine_costs/ecr-2026-11-20.1.yaml`.
  - The engine charges that record first (`engine/cost_record.py`, settings `costs.engine_record`). A cell charges
    max(point, 95% upper bound). A sparse cell charges at least its pooled cell. Without a cell the engine uses the
    legacy table. An unreadable record falls back to the dearer legacy table. Each exploration row stamps
    `cost_version`.
  - P6: every exploration exit records its quote (`exit_bid`, `exit_ask`, `exit_quote_utc`). That is the quote a
    close was sent on, or the first tick through the broker's stop or target. The engine-store golden gains these
    three null fields (192 cases, no other change).
  - Most cells now charge 25-50% less. ICM 15m cells charge more ($0.72-2.00), because their stop exits slip.
    Research v3 (ADR 0084) is unchanged. Rollback: `costs.engine_record: null`.
