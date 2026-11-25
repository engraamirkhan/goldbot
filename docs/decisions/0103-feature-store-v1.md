# 0103: fs-v1 point-in-time feature store and data-sufficiency contract

Status: proposed (data-engineer, 2026-11-25); quant sign-off on parity and tiers, architect on the build path

## Context
- Features for history pretraining are built one decision at a time (`history_replay.features_at` /
  `feature_snapshots.decision_features`). That runs at 8.5 decisions/s on the Mac (measured, gold 15m 2015). Gold 15m
  2010-17 alone takes about 6 h per worker, dense 1m/5m labels take days to weeks, and the cache keys hash the
  decision set, so an arm change recomputes whole years.
- No test held the history features equal to the engine's frame.
- The engine's 90,000-minute memory leaves `d1_atr_ratio_14_100` and `d1_rv_ratio` NaN on every decision
  (backlog 271).
- Owner 2026-11-25: models train on history, and the data team provides the necessary and sufficient data.

## Decision
1. **fs-v1** (`goldbot/research/feature_store.py`) computes the sr-v2 input set (39 market columns; `side` is added
   per candidate) for every closed bar of an instrument x tf in one pass from 1m bars. A decision reads the row with
   the latest `available_utc` at or before it (`asof_join`).
   - Decision frame: the engine's rules (800 bars, at least 120 bars).
   - Two columns depend on where the window starts (Wilder-100 ATR ratio, pandas rolling std in `bb_z_20`). They are
     rebuilt per 800-bar window in batches, bit for bit.
   - `features_at` / `decision_features` stay the reference.
2. **Daily context comes from the whole history**: closed daily and weekly bars from `goldbot/data/htf_bars.py`,
   using the same `resample_bars` as the engine.
   - This is the one intended difference from the engine as deployed. The engine must read the same store before
     these columns are served live (D-7 / W3-3).
   - The parity oracle compares against the engine frame on the same, complete, minutes.
3. **Point in time**: a minute counts from max(visible_at, available_utc). A row is available only once every bar its
   window or context reads is available, so a revision is never served before it became available.
4. **Cache keys**: sha256 of spec, code (the feature, resample and store modules), instrument, tf, phase, year and the
   data the partition reads (minutes plus daily bars). Decisions never enter a key.
5. **Tiers are applied at load, not in the cache** (r11-v2.1, ADR 0085 A4):
   - intraday: A and A-consistent;
   - 1d and 1w: B as well;
   - C, F, P and ungraded months are never loaded;
   - the tiers file sha256 and r11 id are returned with the rows;
   - loading without tiers needs `untiered_ok` (parity and throughput only).
6. **Offset phases are for training only.** Rows carry `phase` and `group_id` (the phase-0 bar they belong to). A
   judged read (`purpose="judge"`, the default) refuses phase != 0. A phase adds rows but no n_eff.
7. **One source per instrument** (A16): Dukascopy for XAUUSD, HistData (bid only) for the others. On a bid-only
   source, the spread and tick columns stay NaN with `bid_only`. They are never filled from another source.
8. **Data-sufficiency contract** (`goldbot/research/data_sufficiency.py`, `config/data/sufficiency.yaml` ->
   `docs/data/sufficiency.md` + `sufficiency_status.json`, one health line per cell):
   - The requirement is stated by the ML side (sr-v3 card: Riley 430 n_eff per input; the A22 power row).
   - Supply is counted by the card's n_eff method, so there is one method, not two.
   - A cell is SUFFICIENT only when the numbers meet the requirement and no blocking data item is open.
   - Every SHORT cell names its gap, plan, owner and date.

## Consequences
- Gold 15m 2010-17: 189,064 rows in 21 s of compute (26 s wall including load), against about 6 h per worker with the
  per-decision path (about 1,000x).
- An arm change recomputes 0 feature years.
- On real gold 15m 2015, max abs diff against the engine frame and the unbounded snapshot is 4e-13, with identical NaN
  masks. The deployed 90k-memory snapshot differs on all three d1 columns. That is the train/serve skew that D-7
  closes.
- New features need a new spec id, and a spec column read from a context timeframe needs that timeframe in
  `context_tfs`.
- The cache is local (~/tmp/feature-store), with about 3 GB peak memory for gold 2003-17 minutes. A backup is D-11.
- The r11 tiers grade HistData against Dukascopy, so Dukascopy gold has no grade of its own until D-1. Gold rows read
  through the tiers are a proxy until then.
