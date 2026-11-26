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
   - **Labels (quant C5).** Every label starts at its row's `decision_utc`: entry is the first tradable 1m minute
     strictly after it, at the side-aware price. A 1d row's close-to-close label runs from feature-day close to
     feature-day close (13:30 NY). raw/daily 21:00 UTC bars never label fs-v1 rows.
   - **Daily arms.** fs-v1 1d/1w rows do not describe the deployed 1d/1w arms until those arms decide on the same
     bar. The alternative is that fs-v1 builds an arm-anchored 1d grid (`resample_bars(feature_day_anchor=...)`) as
     a new spec id.
2. **Daily context comes from the whole history**: closed daily and weekly bars from `goldbot/data/htf_bars.py`,
   using the same `resample_bars` as the engine.
   - This is the one intended difference from the engine as deployed. The engine must read the same store before
     these columns are served live (D-7 / W3-3).
   - The parity oracle compares against the engine frame on the same, complete, minutes.
3. **Point in time**: a minute counts from max(visible_at, available_utc).
   - A bar holding a minute visible after its close is no decision point and no context bar (`drop_late`), so a
     revised value never enters any window.
   - `features_for` serves the last available row, or NaN when that row is more than 2 bars old (counted).
4. **Cache keys**: sha256 of spec, code, instrument, tf, phase, year and the data the partition reads (minutes plus
   daily bars).
   - The code part is the static import closure of the store (holdout guard, config and calendars included) plus
     every feature module.
   - Decisions never enter a key.
   - `load` reads only the partition whose key the current data gives, never the newest file, and runs the holdout
     guard on decisions and window starts.
5. **Tiers are applied at load, not in the cache** (r11-v2.1, ADR 0085 A4):
   - intraday: A and A-consistent;
   - 1d and 1w: B as well;
   - C, F, P and ungraded months are never loaded;
   - the tiers file sha256 and r11 id are returned with the rows;
   - the tiers must grade the rows' own source (`<source>:<instrument>`). Dukascopy gold read through the HistData
     grade needs `proxy_source="histdata:XAUUSD"`, recorded as `tier_proxy`;
   - judge and train reads always load with tiers; only `purpose="parity"` may skip them.
6. **Offset phases are for training only.** Rows carry `phase` and `group_id` (the phase-0 bar they belong to). A
   judged read (`purpose="judge"`, the default) refuses phase != 0. A phase adds rows but no n_eff.
   - **Folds (quant C6).** Folds purge every training row whose [window_from_utc, label_end_utc] overlaps a test
     row's [decision_utc, label_end_utc], plus an embargo of one label horizon (`feature_store.purge`).
   - `group_id` is for counting n_eff. It is not a substitute for purging.
7. **One source per instrument** (A16): Dukascopy for XAUUSD, HistData (bid only) for the others. On a bid-only
   source, the spread and tick columns stay NaN with `bid_only`. They are never filled from another source.
   - At serve time they are masked to NaN, and the model card says so.
   - A pooled model drops them (`columns_for`) or trains within one source (quant L3).
8. **Data-sufficiency contract** (`goldbot/research/data_sufficiency.py`, `config/data/sufficiency.yaml` ->
   `docs/data/sufficiency.md` + `sufficiency_status.json`, one health line per cell):
   - The requirement is stated by the ML side (sr-v3 card: Riley 430 n_eff per input; the A22 power row).
   - Supply is counted by the card's n_eff method, so there is one method, not two. Rows count after the loader's
     tier rule: B-only units are n/a below 1d, and gold is UNMEASURED until D-1.
   - A cell that no pooling can reach (bound = learn / rho_inst) is INFEASIBLE. Its plan names the data remedies
     (D-13 independent instruments, 2003-09 if D-1 grades it, measured rho from D-12) before the input cap or the
     rule-only fallback.
   - A cell is SUFFICIENT only when the numbers meet the requirement and no blocking data item is open.
   - Every SHORT cell names its gap, plan, owner and date.

## Consequences
- Gold 15m 2010-17: 189,064 rows in 21 s of compute (26 s wall including load), against about 6 h per worker with the
  per-decision path (about 1,000x).
- An arm change recomputes 0 feature years.
- On real gold 15m 2015, max abs diff against the engine frame and the unbounded snapshot is 4e-13, with identical NaN
  masks.
- The deployed 90k-memory snapshot differs on all three d1 columns (2 NaN, `d1_slope_ema20` from a short window). At
  4h it also differs on decision columns. That is the train/serve skew that D-7 closes.
- **Serving gate (quant C4).** These models are not promoted to serve:
  - any model trained on fs-v1 rows at 4h, 1d or 1w;
  - any model using d1_* columns, at any tf.

  The gate lifts only when the engine's memory holds feature_window + max lookback decision bars and D-7 serves d1
  from htf_bars. The parity script's `snapshot_as_deployed_90k` block must then show max abs diff <= 1e-9 on every
  served column at that tf. `feature_store.serve_ready` is that check, and the promotion of any fs-v1-trained model
  calls it (none exists yet; the sr-v3 promotion path must).
- New features need a new spec id, and a spec column read from a context timeframe needs that timeframe in
  `context_tfs`.
- The cache is local (~/tmp/feature-store), with about 3 GB peak memory for gold 2003-17 minutes. A backup is D-11.
- The r11 tiers grade HistData against Dukascopy, so Dukascopy gold has no grade of its own until D-1. Gold rows read
  through the tiers are a proxy until then.
