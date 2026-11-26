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
4. **Tiers.** A row's tier is the worst r11 tier over the months below.
   - **Usability rule (quant C-3 / R-a on #472 and its re-verify, verbatim).** "A row is usable only if every
     session month its served inputs read, its decision month and its label horizon are usable under the template
     rule. The input lookback is computed at read time from the requested column set: `load_labels(columns=...)` is
     required for judge and train reads. The input lookback is the maximum over the requested columns of each
     column's own lookback: the oldest bar whose perturbation can change the column by ≥ 1 % of the weight an EWM
     puts on it, in decision bars × 7/5 below 1w. For a `d1_` column, the same in daily bars × 7/5 replaces the fixed
     `ctx_lookback_days`. Per-column lookbacks are declared in the registry, and a truncation test proves each one:
     perturbing every bar older than the declared lookback changes the column by less than that bound, and
     perturbing the bar just inside does not. Fixed 2026-11-26, before any label read. Counts are re-run under it.
     The 800-bar fs-v1 window start stays provenance only. The column set is the unit's served or level-order prefix,
     fixed label-blind at Stage 0, so the rule is mechanical. `skeleton` counts are reported per column set (core-6,
     core-12, P30)." If gold P30 is still about 0, that is the answer: the ladder stops before the emptying column,
     and nothing is dropped by hand.
   - **How it is implemented:**
     - `goldbot.features.registry.COLUMN_LOOKBACKS` declares every fs-v1 column's lookback in decision bars, per tf
       where it differs (session-anchored columns). It is additive: the feature-level `lookback` keeps its meaning,
       and the engine reads neither.
     - `goldbot/research/column_lookback.py` defines and measures the lookbacks.
       - Fixture: 8 seeded synthetic series of 4,000 bars per tf, with seed-varied session clocks.
       - Perturbation: the bars older than L are replaced by a 3x-volatility path joined at the junction.
       - Bound: 1 % of the column's own standard deviation over the last 1,000 bars.
     - `tests/test_column_lookbacks.py` proves each declared lookback at every tf (290 cases): the declared lookback
       passes, and one bar less fails on some seed.
     - `tests/test_feature_lookback_ewm.py` keeps the feature-level lookbacks at or above their 1 %-weight EWM
       horizon.
   - **Appended by the quant sign-off of #472 (verbatim).** "The truncation measurement uses two perturbations of
     every bar older than L, and L is the larger of the two lookbacks: (i) the continuous 3×-volatility replacement
     path; (ii) a discontinuous error: every older bar shifted by +5 × the fixture's median bar range, with 1 % of
     those bars replaced by spikes of ±20 × the median range. `measure` declares L on 32 seeds. The CI test
     (`tests/test_column_lookbacks.py`) re-checks pass and tightness on 8 of them. Declared lookbacks never shrink
     without a quant re-review. Counts are re-run after the change."
     - A spike bar is displaced whole, open to close, by a random sign.
     - Tightness looks for the bar-just-inside failure on the 8 CI seeds first, then on the rest of the 32, because
       the seed that binds L can be any of them.
     - The floor is the declaration this ruling signs: `tests/fixtures/column_lookbacks_floor.json`, floor sha256
       b5505ff75cb9f314dd785bb59f30dc6ff214b61a2db05829c63fa874f13f7166. A test pins this sha, so the floor changes
       only together with this ADR.
     - A declared lookback below the floor needs a marker in `registry.COLUMN_LOOKBACK_REREVIEW` (code review LOW-3
       on #478):
       - the marker carries a "PR #N" or "ADR NNNN" reference;
       - it carries the newly signed value for every floor tf, and the declaration must equal it;
       - a marker with no value below the floor is stale and fails.
     - Against the base branch (when the checkout has it), no floor value may go down without such a marker.
   - **Template rule:**
     - intraday: A / A-consistent for every template;
     - 1d / 1w: A / A-consistent for barrier templates; T0 also accepts B.
   - **Rule R-a (quant ruling on PR #486, 2026-11-26), appended verbatim:** "A column declared close-only in the
     registry (`CLOSE_ONLY_COLUMNS`) accepts lookback months graded B. Every other column needs A or A-consistent
     over its lookback. The label window (decision month through horizon end) needs A or A-consistent for barrier
     templates, and also B for T0 at 1d/1w as before. C, F, P and ungraded months are never usable. A row's usability
     is the conjunction over its requested columns."
     - Code: `dense_labels.attach_tiers` checks two spans per row: strict = [decision - the lookback of the columns
       that are not close-only, horizon end] under the template rule, and close = [decision - the lookback of the
       close-only columns, decision] at A / A-consistent / B. `tier` reports the worst month read.
     - Proof (`tests/test_close_only_columns.py`): for every column in `CLOSE_ONLY_COLUMNS`, perturbing open, high,
       low, spread and tick count of every bar with the closes unchanged leaves the value identical (15m, 4h, 1d);
       a column that fails cannot be declared close-only, and the check bites on range, spread and volume columns.
     - R-b (same ruling): "HistData XAUUSD does not lift Dukascopy non-A months (it is their reference; disagreement
       is not arbitrable under r11-v2.1, and source switching breaks A16). GOLD-TIER proceeds via R-a only."
   - **Sources.** The tiers must grade the rows' own source, `<source>:<instrument>` (quant C3 on #462), unless the
     caller passes a `proxy_source`. The D-1 served file (`tiers_served_r11-v2.1.csv`, sha256 ff653ea7..., PR #467)
     grades dukascopy:XAUUSD and histdata:<FX>.
   - **Every store is sealed** (card ruling 5, test 21).
     - Labels are produced and counted outcome-blind (`skeleton`).
     - The label values sit in their own `.values.parquet` file, apart from the skeleton (code review LOW-2).
     - `load_labels` raises `SealedStore` until the tiers sha256 is in `RECORDED_TIERS_SHA256`. A test requires a
       full 64-hex sha on an ADR 0085 line that records a tiers file.
5. **Reads.** `load_labels` reads only the partitions whose key equals `label_key(parts)`: the current fs-v1
   partition key, the minutes' sha256 and `minutes_to_utc` (quant C-2, code review LOW-4). Several candidates with no
   key to choose by raise.
   - Every load under a `booked_read` appends a consumption row (id, columns, label spec hash, keys, ts) to the
     ledger. The first consumption fixes the columns, the spec hash and the keys. A later load under the same id
     with any of them different raises (quant C-4).
     - Columns are compared as a set (`sorted(columns)`), so their order does not matter.
     - The keys bind one (instrument, tf, years) per booked id.
     - Read, check and append run under an exclusive `fcntl.flock`. Each row is one `os.write` on an O_APPEND
       descriptor followed by fsync (code review LOW-A on #472), so two concurrent first loads cannot both be
       first.
     - Read-once fails closed (code review MEDIUM-1 on #478):
       - A write is complete or nothing: a short `os.write` is cut back to the size before it, and it raises.
       - An unterminated final line that parses as a JSON object is a real row (its newline was lost). It counts,
         and the next append gives it its newline.
       - Only an unparsable final fragment is reported and moved to `<ledger>.torn`.
       - A malformed line before the last one refuses the ledger (LOW-B).
       - `windows.record_read_once` uses the same reader, lock and write.
     - The lock is `fcntl.flock` on POSIX and `msvcrt.locking` on byte 0 on Windows, as `data/store.py` does (LOW-1).
       With neither available, a ledger write raises; it is never done unlocked.
   - It refuses:
     - a read without tiers or without `columns`;
     - a sealed store;
     - a judged read with phase != 0;
     - the holdout (any label span);
     - a window touching the unit's confirm or test_old span without a `booked_read` id. The id must be booked in the
       read-once ledger (`windows.record_read_once`) under the frozen spec hash, and it goes into the manifest (quant
       C-1).
   - The spans are per unit: gold/FX confirm 2018-01..2025-09 and test_old 2006-02..2009; index-energy judge
     2022-01..2025-09.
   - It drops and counts:
     - rows outside the window;
     - rows touching a judged span on an unbooked read (learn reads end at the learn end);
     - tier-unusable rows under `columns`;
     - reserve-week rows of a training read (N12).
   - The d0 floor reads only minutes visible by the decision. Decision bars use `drop_late`, as fs-v1 does (code
     review LOW-1).
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
