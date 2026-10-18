- Speed-up (CTO docs/process/speedup-2026-10-18.md items 1-2, implementer, 2026-10-18): `f_order_blocks` is vectorised
  (`_order_block_report`, pure numpy, no new dependency) and byte-identical to the old loop (golden test); feature VERSION
  unchanged. `lookahead_check(full=...)` reuses the frame `research_pass` already built: two feature builds per run, not three.
  1h 2024 trial 7.0 s -> 4.2 s; registry row identical. Quant review: features feed live models.
