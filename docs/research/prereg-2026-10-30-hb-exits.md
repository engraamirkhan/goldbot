# Pre-registration H-82 ("H-B" of the 2026-10-30 plan, bet 2b): wider targets / time-only exits on the scalp arms' own entries, paired against random entries under the same exits (2026-10-30)

Trade management researcher. Written **before any history outcome of these exits is computed**: the only numbers seen
are the demo's descriptive sim (performance-analyst 2026-10-30, icm-probe, one falling week: strategy mean R by target
0.5 R -0.070, 1 R +0.032, actual +0.086, 2 R +0.133, 3 R +0.187, none +0.213; random control +0.014 -> +0.007). Git
history is the proof (this file is committed alone, before the screen script runs). Window **2010-01-01..2017-12-31
only**; nothing from 2018 on, no holdout bar. Nothing here changes an engine exit, a label or a config row.

Id: the plan calls this "H-B"; `hypotheses.md` already has an H-B (THIN days, line ~170), so it is registered as
**H-82**, the next free id (highest in the file: H-81).

## 1. Configuration (frozen)

- **Arms in scope (entries unchanged, the live rule code, rows of `config/exploration_arms.yaml` loaded with their
  hashes):** family S5 `ema_pullback` 1m: S5A ($3.00 stop, 1.5 R, hold 30), S5B ($3.75, 1.5 R, 30); family Z4
  `round_reject` 1m: Z4D ($3.00, 1 R, 15), Z4P ($3.00, 1 R, 15, $5-offset handles); family Z6 `nr4_break` 5m: Z6D
  ($4.50, 1.5 R, 6); family Z7 `halfhour_persist` 5m: Z7D ($4.50, 1.5 R, 6, :00/:30), Z7P ($4.50, 1.5 R, 6, :15/:45).
  Vantage strata (Z6V, Z7V, $7) are out of scope (same entries, different stop; a second read would double-count).
  Entries via `goldbot.research.arm_rules.candidates` (window, common clock filters, spread gate on Dukascopy
  `spread_mean`), entry at the open of the first 1m bar at or after the decision close + `arms.send_delay_s`, exactly as
  `scripts/run_arm_backtest.py`. Mid prices (cost carried separately). **No tier-1 blackout**: the calendar files are not
  on this machine; arms and controls are treated identically, disclosed in the read.
- **Fixed entry set:** the arm's candidates kept by `limit_open(max_open)` under its **current** exit. Every variant
  is walked on that same set, so a variant never changes which trades exist (the exit is isolated).
- **Exit variants V** (stop = the arm's fixed $ stop, unmoved; hold = the arm's own `hold_bars`; no trail, no
  break-even, no partials): target **1 R, 1.5 R, 2 R, 3 R, none** (time exit at the close of the hold's last decision
  bar). Baseline B = the arm's current target (S5/Z6/Z7 1.5 R, Z4 1 R). A variant equal to a family's baseline is not
  read for that family. Stop first when stop and target fall in one minute (the engine labeller's rule).
- **Random control, same sessions, same exits:** for each arm entry at decision close t, M = 4 control decisions at
  t + o_k, o_k drawn by `sha256("hb-2026-10-30|<arm>|<t>|<k>")` uniformly from the arm's decision grid in
  [-60, +60] min excluding 0 (1m arms: 120 offsets; 5m arms: 24); a control must exist as a decision bar, lie on the
  same trading day (New York 17:00 day) and pass the arm's own window and common clock filters (S5: `window_utc`
  07:00-20:00; Z: window W). Side: `sha256("hb-2026-10-30|<arm>|<t>|<k>|side")` 50/50, independent of the arm. Same $
  stop, hold and variants, same entry convention. An arm entry with no valid control is dropped and counted.
- **Censoring:** a trade (arm or control) whose time exit lies beyond the last 2017 bar is dropped with its pair.

## 2. Economic reason
If an entry rule carries direction that persists beyond 1-1.5 R inside its hold, a fixed near target forfeits the tail;
trailing/wide exits pay only with positive serial correlation (Kaminski & Lo 2014, *Journal of Financial Markets* 18,
234-254; US equity/bond portfolios, monthly, 1950-2004, gross of costs *(not re-checked)*; single paper for this
mechanism, not intraday gold). Against it: 93% of stopped demo trades never reach +1 R (median MFE 0.19-0.27 R), and
the demo's gain from wider targets coincided with a falling week in which the strategies were 65% short (direction
drift, not exit skill). On random-side entries a wider target with a fixed stop and hold changes the gross mean by
about zero in expectation (no drift sign), so the control absorbs path, volatility and session effects; what remains
is whether the arm's **own** direction keeps running.

## 3. `research.yml` inputs
None (local screen, 0 registry trials). Script: `docs/research/candidates/hb_exits/hb_exits_screen.py`; inputs:
`/Users/aamirkhan/myproject/goldbot/raw/xauusd_1m_dukascopy_{2010..2017}.parquet` (local only, never committed),
`config/exploration_arms.yaml`. Outputs to `~/tmp/hb_exits/` (no market data in the repo).

## 4. Statistic and costs
- Per arm entry i and variant V: `d_i(V) = [R_arm,i(V) - R_arm,i(B)] - mean_k [R_ctl,ik(V) - R_ctl,ik(B)]`, net R.
- **Costs:** measured broker round trip **0.05 R** per trade (ICM, performance-analyst 2026-10-30: spread 0.025 +
  commission 0.024 R at $3-4.50 stops). Because the entry set is fixed, a per-trade round trip cancels inside every
  difference; the only cost that does not cancel is the stop-exit overshoot, so the DiD is reported at overshoot $0
  (primary) and $0.70 per stop exit (the frozen sensitivity, `config/arm_costs.yaml`). Absolute arm net R per variant
  is also reported at 0.05 R and at the worse signal-time 0.116 R (plan item 86), descriptive.
- **Pooling:** per variant, the family estimate is the trade-weighted mean of `d_i` over its arms; the pooled estimate
  is the equal-weight mean of the contributing families (S5, Z4, Z6, Z7 minus any family whose baseline equals V).
- **Inference:** block bootstrap by trading day (New York 17:00 day), days resampled jointly across all arms, 10,000
  resamples, seed 20261030; percentile intervals.

## 5. Reading rule (written before any data it reads)
Per variant V (K = 5 variants: 1 R, 1.5 R, 2 R, 3 R, none):
- **PASS** only if all hold: pooled DiD >= **+0.05 R**; the two-sided 90% bootstrap lower bound > 0; the
  multiplicity-adjusted lower bound (Bonferroni over the 5 variants: one-sided 2%, i.e. the 2nd percentile) > 0; the
  family DiDs have the pooled sign in all contributing families but at most one; and >= 5 of the 8 calendar years have
  a positive pooled DiD. All five at overshoot $0, and the first two still at overshoot $0.70.
- **INCONCLUSIVE:** pooled DiD >= +0.05 R and 90% lower bound > 0 but an adjusted, family or year condition fails.
  No change is made; it can at most earn a new pre-registration with a 2018-2025-09 confirmation.
- **FAIL:** anything else.
- **Line consequence (research-plan line 2):** if no variant PASSes, the "wider targets / time exits" line closes for
  these fixed-$ scalp entries; the demo exits stay as they are; the demo's +0.18 R is recorded as direction drift. If
  a variant PASSes, it is **not** adopted from this screen: it earns one confirmation read on 2018-01..2025-09 (one
  registry trial, research director's decision) and, because it changes the exit path, review by the quant-reviewer and
  the trading-safety-reviewer before any arm row changes (a new arm id, never an edited row).
- Not read, reported only: the arm's absolute net R per variant (no entry-edge credit: these are forward-only demo
  arms, and the screen gives no promotion evidence), exit mix, MFE distribution, a **side-matched control diagnostic**
  (the same control times with the arm's side; not point in time, never in the verdict) that shows how much of any
  gain is the day's drift rather than the entry's timing.

## 6. P4 screen expectation and prior
Expected pairs: several thousand per 1m arm-year are possible for S5 (one per side per 10 bars), fewer for Z4/Z6/Z7
(hundreds per year). Prior: pooled DiD for "none" between -0.03 and +0.03 R; P(any variant PASS) about 0.12. The
demo's +0.18 R would need the arm entries to carry a short-horizon trend that the 2010-2017 tape does not show for
1m/5m entries at $3-4.50 stops (S0 history: gross ~0 at every target).

## 7. Gates and trial cost
- **K +5** (one per variant; the plan's "K +3" assumed three variants, the brief fixed five). 0 registry trials, no
  budget change, no holdout. The confirmation read, if earned, is a separate decision (1 registry trial).
- No arm row, exit path or barrier label changes from this file.

## 8. Known-failed ideas in this area, not re-tested
H-80 discovery (2010-2017): first-box-at->=2 R target worse than fixed 2 R in all 4 TF (-0.016 R); partials halve the
runner's gain; break-even adds nothing over the trail (-0.001 R); structural box-far-side stops gross -0.16..-0.25 R.
R-03/R-04 trails (1.5 ATR after 1.25 ATR, half at 1 ATR). Exits as a rescue of a primary with gross t < 2. Trailing
runners are H-80's question (R2 on 2018-2025-09) and are not part of this screen.
