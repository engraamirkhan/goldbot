# ML + probability team, lane MLEXEC: pre-registration (55 execution variants of 11 trend rules)

ML researcher (staff), 2026-11-02. Written and committed BEFORE any execution-variant outcome is read (no counts, no
outcomes of any variant below). Brief: orchestrator `mlprob-brief.md` (owner directive 2026-11-02), lane key `mlexec`;
shared rules = sprint-200 brief sections 1-6, 8, 9; windows amendment W1 (owner, 2026-11-02). Script (committed after
this file, before any run): `scripts/mlprob/mlexec_screen.py`.

## 0. What is known before this file, and the honest prior

- Known and disclosed: the sprint-200 trend lane's market-entry results (`docs/research/sprint200/trend-results.md`,
  branch `research/sprint200-trend`, PR #272), explore 2010-2017 for all 34 rules and confirm for S200-TREND-03 only
  (market entry: confirm net +0.070 R, t 0.99, gross +0.165 R). The 11 rules used here were chosen by the brief from
  that explore read (gross t >= 2): 01, 03, 04, 10, 15, 19, 20, 24, 28, 31, 32. That selection used explore data, so
  explore numbers below carry it; confirm does not.
- Nothing about limit fills, swap-cut exits, wider stops or a learned chooser on these rules has been read.
- Mechanism under test: the trend premium is about 0.15-0.3 R gross and the ICM round trip + stop overshoot + long swap
  costs 0.10-0.15 R. Execution can (a) buy a better entry price (pullback limit; risk: adverse selection, fills come
  mostly on trades that then fail), (b) skip the swap where the trade is not yet working (risk: cuts the winners'
  first night), (c) make cost smaller in R with a wider stop (risk: gross R per trade shrinks in proportion).
- Literature: limit orders earn the spread but suffer adverse selection and non-execution; the net for uninformed
  traders is about zero or negative (Handa and Schwartz 1996, Journal of Finance 51(5), NYSE equities, gross of
  commissions *(unverified)*; Harris and Hasbrouck 1996, JFQA 31(2), NYSE SuperDOT orders *(unverified)*).
  Single-market findings, not replicated on gold CFDs. In goldbot, H-29/H-30 (limit entry) were never run for lack
  of limit-fill data (`prereg-2026-10-27-scalp-volume.md`, limit-entry caveat). Wider stops were read under the
  constant swap prior as not fixing cost (`next-gen-2026-10-18.md`), on a different family.
- Expected passers at the confirm bar: 0-1. A trigger with confirm market net +0.07 R (t 1) needs about +0.1 R of
  execution uplift to pass; that is about the whole cost line.

## 1. Conventions

Everything not stated here is the trend lane's prereg section 1 verbatim (data, bars, indicators, sessions, FLAT,
one position at a time, exits on the 1m mid path, stop first, gap-through stop fills at the open, time caps, costs
`cor-2026-10-31.2` ICM upper bound + overshoot floor + `SwapSpec.shipped()`, the leak-free control with 4 twins).
Signal code is copied unchanged from `scripts/sprint200/trend_screen.py` at commit `ccaf6b26` (the 11 rule functions
and their helpers).

- **R unit.** R = the rule's registered stop distance (USD/oz) at the market reference price P = open of the first
  1m bar at or after the decision time. All variants except V4 keep the stop DISTANCE (not the level), the target
  multiple, the trail distance, the indicator exit and the absolute time cap of the market version. Net R is always
  the trade's own USD result over its own R, costs charged in full.
- **One position at a time** applies per (rule, variant) on that variant's own exits; an unfilled limit signal does
  not block.
- **Uplift vs market** (the lane's score): mean net R per trade of the variant minus mean net R per trade of V0 on
  the same signal set and window (each with its own one-at-a-time filter). Also reported, not gated: per-signal
  uplift (paired over signals both variants took, standalone outcomes, unfilled = 0 R).
- **Control** for every variant: the trend lane's leak-free twins (same side, same R, same exits including the
  variant's exit rule, fill minute uniform over the real fill minute to the end of its session instance), entering at
  market at the twin minute's open. For limit variants the twin window starts at the limit fill minute.

## 2. The five variants (applied to each of the 11 rules: K = 55)

- **V0 MKT** (baseline, not a new trial; already counted by the trend lane): market entry at P.
- **V1 LIM30**: at the decision, a limit at L = P - side x 0.30 R, good for 60 minutes (and never later than the
  deadline - 15 minutes or the time cap). Fill rule (conservative, mid data): a long fills in the first minute whose
  mid low <= L - $0.10 (the ask must trade through; $0.10 covers half the ICM spread plus queue); fill price =
  min(L, that minute's open) for a long (mirrored for a short). Unfilled = skipped. The stop is fill - side x R. In
  the fill minute only the stop is checked (the path after the fill inside that minute is unknown, so stop first;
  that minute's high neither hits the target nor lifts the trail). Full round trip charged (no maker rebate).
- **V2 LIM15**: as V1 with 0.15 R and 30 minutes.
- **V3 SWAPCUT**: market entry; at every swap-charging rollover (server midnight Europe/Athens ending a weekday) a
  LONG still open is checked at the first minute at or after rollover - 10 minutes: if (that minute's open - entry)
  < +0.5 R it exits at that open (exit kind "swapcut", no overshoot charge); else it holds to the next check. Shorts
  (no swap) are unchanged.
- **V4 WIDE**: market entry, initial stop distance 1.5 x the registered R (R unit = the wider distance), trail
  distance unchanged in USD, target unchanged in USD (multiple / 1.5), time cap and indicator exit unchanged.
- **V5 LEARN** (learned entry timing): per signal, a fixed LightGBM regressor predicts u = net R(V1, unfilled = 0)
  - net R(V0), both computed per signal on their own (no one-at-a-time); V1 is used when the prediction is > 0, else
  V0. One model pooled over the 11 rules. Features (11, all point in time, from bars whose decision time <= the
  signal's decision time, joined with `goldbot.data.store.asof_join` on available_utc = bar decision time): rule id
  (categorical), side, decision hour UTC, weekday, R / ATRd(20), ATR(1h,14) / ATR(1h,100), side-adjusted close
  location in the last 1h bar, last 1h bar range / R, side x (1h close - EMA20(1h)) / R, side x 6-bar 4h return / R,
  side x D1 trend gate. Hyperparameters fixed here, no search anywhere: n_estimators 200, learning_rate 0.03,
  num_leaves 7, min_child_samples 100, subsample 0.8 (bagging_freq 1), colsample_bytree 0.8, seed 0, 1 thread.
  Walk-forward by calendar year: the model scoring year Y trains on signals decided before Y-01-01 whose V0 and V1
  exits are both before Y-01-01 (purged). Explore scores 2012-2017 (training from 2010); V5's explore read and its
  V0 comparator use 2012-2017 signals only. Confirm scores 2018-2025 with training on all signals before Y (explore
  2010-2017 plus confirm years < Y; no year is trained on while it is read; no confirm-side tuning). TEST-OLD uses
  the model trained on every signal 2010-01-01..2025-09-30, frozen (no TEST-OLD row is ever in training).

## 3. Pass rules (frozen)

- **Explore** (2010-2017): n >= 100 AND mean net R > 0 AND t >= 2.0 AND control excess >= +0.05 R (brief rule 4) AND
  uplift vs market >= +0.02 R per trade (lane rule).
- **Confirm** (2018-01-01..2025-09-30), ONCE, explore passers only (the script refuses others): mean net R > 0 AND
  t >= 2.0 AND DSR probability (`DsrInputs.of(r).dsr(K)`) >= 0.5 at K = 3,400 (programme ~3,233 + this lane's 55 +
  an allowance for the other team lanes; recomputed at the final K the orchestrator reports; not computed below 200
  trades = FAIL) AND >= 6 of 8 confirm years with mean net R > 0 AND uplift vs market > 0.
- **TEST-OLD** (W1: 2004, 2006, 2008; trade year = entry year; warm-up from the preceding year), ONCE, confirm
  passers only: n >= 50, net mean R > 0, t >= 1.5, control excess >= 0. Until read: "confirm-passed, old-test
  pending", no deploy. **Qualified** = confirm AND TEST-OLD.
- **No tuning.** One spec per (rule, variant). A bug found after a look is fixed for all, re-run in full and
  disclosed as a dated amendment. K added: 55.
- **Deploy** (qualified only): `goldbot/research/arm_rules_mlprob_mlexec.py` + append-only
  `config/exploration_arms.yaml` row with a paired control. A limit-entry survivor also needs limit-order support in
  the engine (flagged, not built here).

## 4. Run order

prereg commit -> script + tests commit -> engine parity (V0 must reproduce the trend lane's explore n and net R for
the 11 rules; those numbers are already public) -> one explore run of V1-V5 -> one confirm run of explore passers ->
TEST-OLD for confirm passers when that data is on disk. Max 2 worker processes. Aggregates only leave the process
(JSON under ~/tmp, not committed).
