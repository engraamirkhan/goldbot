# Pre-registration amendment: D-4 freezes the exit templates T0-T7 and h_ref as `xt-v1` (`sr-v3` rev 9 -> rev 10)

Amends `docs/research/prereg-2026-11-26-sr-v3.md` (H-103) rev 9, sections 2.1 ("Templates T0-T7"), 3.6 and 9a
(work item D-4), and ADR 0104 point 1 (d0). Written by the principal trade-management (exits) researcher on 2026-11-27,
pulled forward from the 12-02 due date by the owner directive of 2026-11-27.

**Label-blind.** The inputs are the card's own text (2.1 table, H table, "Stop = k × ATR14_tf × sqrt(hold/h_ref), the
W2-2 formula, floored at 1.5 × the session-median TR"), ADR 0104, the cost-per-R figures already in
`docs/research/prereg-2026-11-25-multi-tf-learning.md` (Mechanism / Cost per trade rows) and closed-form barrier
geometry. No label value, R, barrier-hit share, gross, net or Stage 0/L statistic was read to make any choice here. The
author read only the `gate_block` line of the 2026-11-27 fit-only reports, to find the refusal text. K 0: no template
value changes, and every label value dl-v1 produces is numerically unchanged (proof in 4). D-12's control-grid and
family cells (gross T2/T6 R under h_ref = 1 bar, learn 2010-17) existed at the time of this choice. The author did not
read them for this choice. The choice keeps the pre-existing dl-v1 placeholder, so it adds no new outcome-dependent
degree of freedom.

Quant-reviewer signs (required, D-4 owner pair). The barrier labels are shared with RL-1 and the engine's exit path is
not touched; trading-safety review is not required for this change (no engine, risk or execution file changes).

## 1. What was open

- The card names W3-1 as the freeze of T0-T7 shared with RL-1. W3-1 was never written anywhere in the repo. The card
  rule governs: "If W3-1 has not frozen them by 12-02, the definitions below govern."
- `h_ref` in the W2-2 formula "is defined nowhere in the repo" (ADR 0104, Context). dl-v1 carried the placeholder
  `h_ref = "H"` (factor 1), and `dense_labels.gate_status` refused every read with
  "h_ref = H: the exit templates' h_ref is not frozen (D-4)" (quant A4 on #482).

## 2. Decision: `xt-v1`

| Template | Exit (k in d0) |
|---|---|
| T0 | time exit at H; R = d0 |
| T1 | stop 1, target 1, time exit at H |
| T2 | stop 1, target 2, time exit at H (the judged head) |
| T3 | stop 1, target 3, time exit at H |
| T4 | stop 1.5, target 1.5, time exit at H |
| T5 | stop 1, trail 1 from the best exit-side quote, time exit at H |
| T6 | stop 2, time exit at H |
| T7 | stop 1, stop to entry once the best quote reaches +1 R, target 3, time exit at H |

- d0 = ATR14_tf (Wilder, mid, decision bars) × sqrt(H / h_ref), floored at 1.5 × the median 1m true range of the
  1,440 minutes up to the decision.
- **h_ref = H of the tf** (card 2.1 table, in seconds): 1m 60 min, 5m 4 h, 15m 8 h, 30m 16 h, 1h 1 d, 2h 2 d,
  4h 5 d, 1d 10 d, 1w 20 d. The factor is exactly 1.
- Unchanged from ADR 0104 and pinned by the existing tests: entry at the first minute strictly after the decision,
  side-aware quotes, a gapped stop fills at the open, a gapped target fills at the target, a both-touch minute is a
  stop, trail and break-even move the stop from the next minute, clip ±10 R.
- Code: `goldbot.research.dense_labels.EXIT_FREEZE` (record `ExitFreeze`, id `xt-v1`).
  **xt-v1 sha256 559739c72fc720a2d27035c14011dc7dc9a12e62da3121496bd16b270478979c** (`exit_freeze_hash`: canonical
  JSON of the templates, H, h_ref, atr_n 14, floor 1.5 × 1,440-minute median TR, clip 10, sides, the grid time-exit
  tfs {1d, 1w}, the close-to-close set {T0}, and `behaviour_sha`). RL-1 reads its templates from the same record: one
  definition repo-wide.
- **The mechanics are frozen with the parameters (quant 1 on #514).** `behaviour_sha` is the sha256 of the outputs of
  `first_passage` (all 8 templates, both sides through `_quotes`), `decision_bars` and `floor_at` (15m, 1d) and
  `entry_and_horizon` (15m, 1d) on a fixed synthetic minute set (`_FP_BLOCKS`: a both-touch minute, a gapped stop, a
  gapped target, a trail trigger, a break-even trigger, a time exit; `_path_frame`: three weeks with the weekend gaps),
  rounded to 6 decimals, computed lazily and cached. A changed fill rule or grid rule with the same parameters is not
  xt-v1.

## 3. Why h_ref = H (principal ruling, reasons written before any read)

1. **It keeps the card's table literal.** The card leaves h_ref undefined. h_ref = H is chosen, label-blind, as the
   value under which k is the ATR14_tf multiple in the table. Every template's hold is the tf's H, so sqrt(hold/h_ref)
   is one constant per tf, and any h_ref ≠ H multiplies every stop, target and trail at that tf by that constant.
2. **The alternative (h_ref = one bar) collapses the templates.** It multiplies d0 by sqrt(H/bar): 1m 7.7, 5m 6.9,
   15m and 30m 5.7, 1h and 2h 4.9, 4h 5.5, 1d 3.2, 1w 1.7 (clock time). Under a driftless random walk the chance a
   level a away is touched within H is 2(1 − Φ(a / σ_H)) (reflection principle; Karatzas & Shreve 1991, *Brownian
   Motion and Stochastic Calculus*, Springer, 2.6). With the stop at 1 σ_H, the target-2 level is touched with
   probability 0.046 and target-3 with 0.003, so T2, T3 and T7 become time exits in all but a few percent of rows. RL-1
   would choose among near-identical actions. With h_ref = H, ATR14 ≈ 1.6 σ_bar (expected range of a Brownian bar,
   sqrt(8/π) σ; Parkinson 1980, *Journal of Business* 53(1), 61-65), so at 15m (H = 32 bars) a 1-ATR level is
   ≈ 0.28 σ_H (touch ≈ 0.78), 2 ATR ≈ 0.57 σ_H (≈ 0.57) and 3 ATR ≈ 0.85 σ_H (≈ 0.40). The templates stay distinct
   and T0 is the time-exit contrast. This is geometry on a random walk, not a label statistic.
3. **Cost and deployability are sized on 1 ATR_tf.** The card's sibling (multi-tf learning, Cost per trade row) put
   cost at 0.06-0.09 R (1h) and 0.032-0.046 R (4h) on a 1-ATR stop, W2 bps, 2014-17, gross of nothing else (single
   internal measurement, not replicated). A stop 5-8 times wider cuts cost per R by the same factor, but risk per
   0.01 lot rises by it: a 15m stop near $8 (order of magnitude from the 2026-10-18 sizing note, not re-measured
   here) becomes about $45 per 0.01 lot. That cannot be served at the minimum lot inside the account loss limits, and
   a label the engine cannot trade is not a label of record.
4. **No rebuild of values.** Under h_ref = H the factor is exactly 1.0, so every gross R, barrier and exit time is the
   same number dl-v1 wrote. The spec record changes (h_ref becomes a per-tf seconds map), and the module changes, so
   `spec_hash` and `code_hash` change and every partition re-keys. dl-v1 stays the spec id (ADR 0104 asks a new id
   only for *different* templates or h_ref). The label factory re-runs mechanically; skeleton counts and n_eff are
   unchanged, because the weights use the horizon, not the stop.

**The one earlier reading differs.** D-12's control grid (`scripts/research/measure_rho.py`,
`docs/research/rho-measurement-2026-11.md`, PR #466) used h_ref = 1 bar: stop = k × ATR14_tf × sqrt(hold_bars), on
holds of 2-30 bars. That was a measurement geometry for ρ, not a label freeze, and its holds are not the card's H.
xt-v1 does not adopt it for the reasons above.

**Quant ruling on #514 (D-12 under xt-v1), recorded:**
- The D-12 bindings do NOT carry over to xt-v1 automatically.
- Work item **D-12-xt** (ml-researcher; quant; due before Stage 0): the control grid and the 3 replayed families are
  re-run at xt-v1 geometry (h_ref = H, T2/T6 at the card's k, hold = H, the 1.5 × 1,440-minute TR floor, the xt-v1
  fill rules, learn 2010-17, the same buckets, families and upper-95 % method) before any Stage 0, power or gate read
  that uses ρ_u,inst, the D-13 bound, or τ_a/λ_a.
- ρ_u,day and ρ_u,inst bind at max(D-12 recorded, re-run) per cell. τ_a/λ_a take the re-run value only (the R units
  must match). Both are reported.
- Learn-window fit-only reads may proceed now with the recorded values.
- The ρ JSON gains `exit_freeze_sha`. The `rho_day_measurement` validator refuses a file without the xt-v1 sha for any
  gate or power read: `goldbot.config.check_rho_measurement(..., gate_read=True)` and
  `PowerFirstSettings.require_gate_read_rho()`; test
  `tests/research/test_rho_measurement.py::test_a_rho_file_without_the_xt_v1_exit_freeze_sha_is_refused_for_a_gate_read`.
  Settings load (fit-only) is unchanged.

Rejected: h_ref = one decision bar (reason 2 and 3), h_ref = a fixed clock span across tfs such as 1 h (it rescales
every tf by a different constant and breaks the "one table" reading of 2.1), and per-tf tuning of k or h_ref (it needs
outcomes, so it is a trial with K, not a freeze).

## 4. The gate check (quant A4 on #482, D-4 half)

- Before: `gate_status` blocked when `spec.h_ref == "H"`.
- After: it blocks when `exit_freeze_hash(spec) != EXIT_FREEZE_SHA256`, with the text "exit templates / h_ref are
  not the D-4 freeze xt-v1 (sha256 559739c72fc7): not a frozen exit geometry (D-4)". The old placeholder `"H"`, any
  other string, another h_ref, a changed template, ATR length or floor, and a changed fill or grid mechanic all keep
  the block. The shipped `SPEC` clears it.
- **Provenance (quant 2 on #514).** `label_rows` stamps `exit_freeze_sha` on every row (a `SKELETON` column) and
  `write_partition` records it in the manifest. `gate_status` also blocks unless every returned row carries
  `EXIT_FREEZE_SHA256`: rows built under another geometry stay blocked even when the caller passes the frozen spec.
- The D-3 half is unchanged: rows with `cost_version == "w2-point"` still block until the ub95 cost of record per cell
  is frozen.
- Tests: `tests/research/test_exit_freeze_d4.py` (the table, the pinned sha and its presence in the card, the block
  for every other geometry, the monkeypatched mechanics (both-touch to target-first, gapped stop filled at the stop,
  a changed grid time-exit set), the row stamp and manifest, d0 = max(ATR × sqrt(H/h_ref), floor));
  `tests/research/test_rho_measurement.py` (item 7); `tests/research/test_dense_labels.py` A4 test updated (the
  placeholder now blocks).

## 5. What it does not do

- It does not make a run gate eligible by itself: the D-3 block stays until the cost lane freezes the ub95 cost of
  record per cell and the labels carry it.
- It does not change H, the judged head (T2), K, folds, windows or the holdout. No trial is dispatched.
- No new hypothesis row: this freezes an existing card input (H-103), K 0.
