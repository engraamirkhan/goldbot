# 0104: dl-v1 dense label factory

Status: proposed (data-engineer, 2026-11-25); quant sign-off on the label definition, the weights and the purge

## Context
- The sr-v3 card (2.1, 2.2, 9a D-5) trains on every closed bar of every (instrument, tf), on both sides, under eight
  pre-named exit templates (T0-T7), with uniqueness weights, phase rows at 1/P and n_eff per cell measured, not
  projected.
- ADR 0102 (point 3) and ADR 0098 R-H / R-I: cost(x) is the ub95 cost of record known at decision time. It is never
  the realised leg r_mid - r.
- Quant review of PR #462 is binding here:
  - C5: entry is strictly after `decision_utc`, and a 1d close-to-close label runs feature-day close to feature-day
    close.
  - C6: purge on [window_from_utc, label end], plus an embargo; `group_id` counts n_eff only.
  - C2: supply is counted after tier survival.
- Card ruling 5: gold labels may be produced before D-1, but not read.
- Some inputs are not frozen yet:
  - D-4 has not frozen the templates.
  - D-3 has not frozen the r3 side-aware labels or the ub95 cost of record per class.
  - The W2-2 `h_ref` is defined nowhere in the repo.

## Decision
1. **`goldbot/research/dense_labels.py`, spec dl-v1.** Every fs-v1 row gets 16 heads (2 sides x T0-T7), in long
   format.
   - Entry is the open of the first 1m minute strictly after `decision_utc` (C5).
   - Barriers are side-aware. A long is judged on the bid, a short on the ask. Gross R is the exit-side quote's move
     from its own entry value, so with a constant spread it equals the mid move. The spread is charged once, in
     cost(x).
   - d0 = Wilder ATR14 of the tf's mid bars at the decision, x sqrt(H/h_ref), with **h_ref = H until D-4 freezes it**.
     d0 is floored at 1.5 x the median 1m true range over the 1,440 minutes up to the decision.
   - R is the template's own initial stop (T0: d0).
   - Fills are conservative:
     - a gapped stop fills at the open;
     - a gapped target fills at the target;
     - a minute that touches both levels is a stop;
     - trail and break-even move the stop from the next minute.
   - Time barrier H follows card 2.1. On 1d and 1w the time exit is on fs-v1's own decision grid (feature-day
     closes). raw/daily bars are never read.
   - Labels are clipped at +-10 R. The raw value is kept for ADR 0102's 2x-cost leg.
   - A horizon past the loaded minutes is `truncated` (`label_ok` false), never shortened. Each window is built from
     minutes that end at the window's end, so a learn label never reads a confirm-window price.
2. **Labels are gross. The cost inputs are persisted.**
   - Persisted: `LABEL_COST_COLS` (entry, risk_usd, barrier_hit), plus `max_hold_s`, `cost_rt_bp` and
     `cost_version`.
   - `net_r` = clip(gross_raw - cost(x)), where cost(x) is `decision_cost.cost_x` (arm = instrument:tf:template) with
     the round trip replaced by the caller's per-cell `rt_bp`, so ADR 0102's max(W2, ecr ub95) is computed per row.
   - The factory does not compute net R. p_stop and the night share are label statistics, fitted by a booked stage on
     learn rows before its judged block.
   - Rows built now carry `cost_version` "w2-point". A judged read on them alone is not a gate read (ADR 0102 3).
3. **Weights.** One event per bar (all 16 heads).
   - The interval is [entry minute, horizon end] on the instrument's market-minute grid.
   - The interval comes from the horizon, not the realised exit. A realised-exit interval would make a stopped label
     weigh more because of its outcome.
   - `u_tf` = mean 1/concurrency within the instrument x tf, over phase-0 events only.
   - `u_inst` uses the same formula across all of the instrument's tfs. It is for cross-unit claims.
   - Phase rows weigh u/P and add no concurrency and no n_eff.
   - n_eff follows card 2.2 on the measured u:
     - holds under a day: sum over session days of S_d / (1 + (max(S_d, 1) - 1) x 0.25);
     - holds of a day or more: sum of u.
4. **Tiers.** A row's tier is the worst r11 tier over the session months from (decision − feature lookback) to the
   horizon end.
   - The feature lookback is 100 decision bars (x 7/5 below 1w). It is not the 800-bar fs-v1 window, whose start is
     provenance only (fs-v1 `tier_window`).
   - Measured on the first run: checking the whole 800-bar window cut EURUSD 1d/1w n_eff to 56/0, against 277/192
     from the decision month to the horizon. **Quant ruling requested on which lookback the check reads.**
   - Intraday: A / A-consistent for every template.
   - 1d / 1w: A / A-consistent for barrier templates; T0 also accepts B.
   - The tiers must grade the rows' own source, `<source>:<instrument>` (quant C3 on #462), unless the caller passes
     a `proxy_source`. The D-1 served file (`tiers_served_r11-v2.1.csv`, sha256 ff653ea7..., PR #467) grades
     dukascopy:XAUUSD and histdata:<FX>.
   - **Every store is sealed** (card ruling 5, test 21). Labels are produced and counted outcome-blind (`skeleton`).
     `load_labels` raises `SealedStore` until the tiers sha256 is in `RECORDED_TIERS_SHA256`, and a test holds every
     member of that set to ADR 0085's text.
5. **Reads.** `load_labels` behaves as follows:
   - refuses: the holdout (any label span); a judged read with phase != 0; a read with no tiers; a sealed source;
   - drops and counts: rows outside the window (decision before its start, or horizon after its end); tier-unusable
     rows; reserve-week rows of a training read (N12).
6. **Folds (C6).** `purge` is `feature_store.purge`, one rule repo-wide. A training row goes when its
   [window_from_utc, horizon_end_utc] overlaps a test row's [decision_utc, horizon_end_utc + embargo], whatever its
   phase.
   - Embargo = max(1 d, H, feature lookback), which is at least the one label horizon C6 asks.

## Consequences
- n_eff per cell is measured. The first-run numbers are in `config/data/sufficiency.yaml` (`measured`) and the
  handoff.
  - Supply is counted after tier survival under the D-1 served tiers (gold and EURUSD), on the judged head T2.
- Changes that need a new spec id, which re-keys the cache:
  - D-4 freezing different templates or `h_ref`;
  - D-3's r3 definition differing from point 1's side-aware rule;
  - a synthetic ask for bid-only sources (N7, >= 0.98 sign agreement).
- Bid-only (HistData) rows have the bid on both sides (`quote_basis` "bid_only"). Their barrier labels need N7 before
  a judged read.
- The 1d / 1w embargo is long (140 d and about 2 years), and the C6 window-overlap purge on 800-bar windows removes
  up to 800 decision bars after a test block. Expect 1d / 1w training folds to shrink sharply.
- The cache is local (~/tmp/dense-labels). Gold 1m is about 45 M heads (about 1.6 GB zstd). Backup is D-11.
