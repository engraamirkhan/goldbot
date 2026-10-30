# Amendment drafts A1-A4, 2026-10-30 (research director): demo kill rules, two-window screens, discovery grammar v2

Status: **DRAFT.** Written 2026-10-30 by the research director. A1/A2 take effect when the evaluator and the
quant-reviewer sign them (target 2026-11-04); A3 when the quant-reviewer signs it, before any 2010-2017 outcome of
H-16/H-17 is computed; A4 when it is merged as an ADR 0058 amendment, before the next nightly search.

**Disclosure (integrity).** A1 and A2 were written **after** interim matched reads were seen (perf-report learning
section, 2026-10-19..10-30, all three accounts). Both are **kill-only**: they add ways for an arm to retire and none
for it to graduate, continue or be promoted. The bias they can introduce is a false kill, never a false discovery.
Every retired id stays counted in K. No graduation rule, no promotion rule, no holdout and no reserved slot changes.

## A1. Matched-control futility at the existing read (amends H-61 "Graduation", W1, W2, H-81 section 4)

At each unit's **existing** pre-registered read (unchanged: the 300th admissible trade or family fill on >= 20
session days, first 300 only), add one retirement condition to the existing stops (-30 R / -15 R / -10 R and the
UB < +0.03 R futility):

- retire if the unit's **matched excess over the random control** (net mean R minus the control's on the same risk
  day x session x side cell; `goldbot run perf-report`, learning section) has a point estimate <= 0, **or** its 90%
  upper bound < +0.10 R.

Why +0.10 R: per-trade SD is about 1.1 R (re-derived 2026-10-30 from aux.db `exploration_trades`, icm-probe: 0.97 to
1.16 by side). At 300 a side, the matched 90% half-width is about 1.645 x 1.1 x sqrt(2/300) = 0.15 R. Detecting a
+0.05 R excess at 80% power needs about 6,000 trades a side; that is not a demo-scale question. Demo reads can only
find large effects (>= ~0.15 R, about 670 a side), so a unit that cannot show one at 300 is retired and, if its
mechanism is still believed, goes to a history screen where n is free.

## A2. Readability (amends the same texts)

An arm or read unit that cannot reach its read in time has no demo read and only spends slots.

- **Retire now** (on sign-off): every unit that made **< 2 admissible fills a session day** over 2026-10-27..10-30
  and is not exempt below.
- **Retire on 2026-11-13** every non-exempt unit with **< 100 admissible fills** by the close of that day.
- **Exempt:** random controls (S0, S0R, I0, W0, VI0, VW0, C1H, VC1H, ZCD, ZCV); cost arms whose fills are a
  registered input to a confirmation read (F1A/F1C for O-11, G1A/G1C for O-12; S0R's overshoot read); H-80 R1 (BXR*)
  until the R2 verdict, then retired if R2 does not adopt the runner.
- A retired unit keeps its K, its registry row and its arm id (never reused). Its mechanism may return only as a
  history screen under a new pre-registration.

## A3. Two-window protocol for H-16 and H-17 (amends `preregistration-2027Q1.md` O-1, O-2/O-3)

H-16 and H-17 are pre-registered on 2010-01..2025-09 as single reads. Neither has been run. To screen them locally
now without spending their trial data:

- the **screen** uses 2010-01-01..2017-12-31 only (the ADR 0058 explore window), read by the frozen rules unchanged
  (H-16: OOS Spearman >= HAR-only + 0.05 and top/bottom quintile range ratio >= 1.5; H-17: as `hypotheses.md` row 4);
- the **trial** (O-1, O-2/O-3) then reads 2018-01-01..2025-09-30 only, once, by the same rules, with the event floors
  scaled to the window (H-17: >= 500 strict events on 2018-2025-09, count events only before freezing);
- H-17's screen runs only after H-16's screen has a recorded result (H-17 conditions on the HAR-only baseline, so it
  does not wait for H-16's verdict, only for the HAR model's code to be frozen);
- K: each screen +1 (counted, not a registry trial); each trial unchanged.

## A4. Discovery grammar v2 (to become an ADR 0058 amendment; strategy-researcher drafts the ADR text)

Measured 2026-10-30 (`~/tmp/autodisc-run-{15m,1h,4h}/`, 288 specs a timeframe, about 5.3k null specs, 0 survivors):
1h best 0.841 vs null bar 1.337; 4h best 0.692 vs 1.019 (RC p 0.64, PBO 0.62). 15m: batch RC p 0.001, best 3.866 vs
bar 3.757, but the top specs have **negative own mean R** (re-read from the 15m ledger: excess over the random-side
twin +0.30 to +0.50 R, own mean R -0.03 to -0.08 R, cost-stressed -0.18 to -0.20 R). At 15m with priors-only costs the
search beats a cost-crushed null, not zero. Decisions:

1. **No further nightly runs on grammar v1.** Each night adds K with near-zero new information. One run per grammar
   version.
2. **Fitness floor:** a spec can advance only if its own net mean R > 0 at the **worse of** the measured probe cost and
   the signal-time cost (quant item 72), as well as beating the search null. Excess over random alone never advances.
3. **Cost table:** the search reads the measured cost table (`costs_measured.json`), not the priors. Until the
   release has it, the report states "priors only" and nothing advances.
4. **Grammar v2 primitives, frozen before its first run:** when-to-trade gates (session window, HAR range-forecast
   tercile, NR4/NR7, event day), the 4h-return side filter (performance-analyst H-A), and exit primitives (time exit
   only, fixed 1/2/3 R, BE at +1 R with an ATR trail). Exits are drawn from a closed list of 6, so K grows by a known
   factor.
5. The ADR 0058 stop stands: 48 confirms with no pass sends discovery back to the quarterly state-of-the-art refresh.
   Added: **grammar v2 with 0 explore survivors on all three timeframes by 2026-11-20 freezes generation** until the
   Q1 state-of-the-art refresh.

## Sign-off

| Amendment | Signs | Due | Status |
|---|---|---|---|
| A1, A2 | evaluator, quant-reviewer | 2026-11-04 | draft |
| A3 | quant-reviewer | 2026-11-03, before any H-16/H-17 screen output | draft |
| A4 | quant-reviewer; strategy-researcher writes the ADR 0058 amendment | 2026-11-04, before grammar v2's first run | draft |

## Evaluator rulings, 2026-10-30

The full text, with the id list, is in the comments at the end of `config/arm_reads.yaml` (R1-R4).

1. **A1, A2: signed with conditions** (the quant-reviewer's signature is still needed before either takes effect).
   They are valid kill-only amendments even though they were written after the interim reads. Neither adds a route to
   continue, graduate or be promoted. A2 reads fill rates only, so it never looks at outcomes. A1 acts only at each
   unit's existing single read. Conditions:
   - A1's estimator is frozen as the perf-report learning section at `fe045043`.
   - A1 is never applied at an interim look, and passing it adds nothing to a "continue".
   - A2's rates are taken per read unit and recorded with each retirement.
   - At n 300, the "UB < +0.10 R" clause implies a point estimate below -0.05 R, so the "point <= 0" clause is the one
     that binds. False-kill rate: about 29% for a true +0.05 R unit, about 13% for +0.10 R. We accept that.
2. **Cost figure.** The Z section 4 cost stop reads the engine's signal-time cost (about 0.116 R). The rules that say
   "measured cost" read the **worse** of $0.23/oz + $0.70 per stop exit and the arm's own signal-time fill cost
   (time-structure prereg 2.2(b), which includes slippage and overshoot). The 0.053 R broker figure leaves both of
   those out and is not the figure of record. Condition: the data-engineer confirms by 11-06 that `cost` comes from
   the measured nightly table and not from the config fallback.
3. **S1A/S1B are not VOID.** S1 is not on the closed neighbour list, and H-76 is the Asia-range fade, not a VWAP rule.
   S1 reads at its own read (about 2026-12-16), with A1 applied there, and is subject to A2's 11-13 count.
4. **Kill now**, on A2 alone (VOID, "closed on history" and "hypothesis retired" are not registered kill grounds):
   - the 51 ids the plan names;
   - plus XBB15 (if it is under 2 fills a session day), F2C, C30C and XC15 (objection: XC15 is not A2-exempt), S6,
     I1, I3, W3 and the nine blocked VS twins.
