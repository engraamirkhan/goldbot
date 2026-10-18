# Pre-registration: H-25 precision retest entry with the S-1 cost-bound stop (strategy-researcher, 2026-10-18)

Written 2026-10-18, **before any outcome of this configuration exists**. It answers the research director's priority 2 in
`batch-2026-10-18-readout.md` §5 ("H-25 precision entry with an S-1 regime gate ... ATR floor fixed from the cost
arithmetic, not from returns; event-count estimate per variant; forward-shadow reading plan"), brought forward from
2026-10-25. Nothing in this file computes or reads a trade outcome. The only numbers computed for it are ATR and spread
statistics (section 4.1), which are volatility and cost inputs, the same kind as `atr-measured-2026-10-18.md`.

**Trial cost: 1 registry trial now (in-sample), plus 1 trial charged later if and when the forward read in section 7
happens.** The budget needs **one more 2026Q4 slot: override 55 -> 56** (57 if TVR-3 has already taken 56). The
research director rules on that (section 8). Raising the budget is an owner decision; this file does not assume it.

## 1. What is tested, in one paragraph

The parent signal is the 15m `breakout` specialist at its defaults (the trial #29 configuration: an 8-bar compressed
range broken on a close with tick volume above 1.5x its 20-bar median). Instead of entering at the next open with a
1.0 x ATR15 stop, the order is a **limit at the broken range level** L, valid for 30 one-minute bars. The stop sits
**0.6 x ATR15 beyond L** and the target **2 stops beyond the entry** (reward:risk 2, the family's own ratio). Exits are
barriers only, on 1m bars, with the family's 24-bar (6 h) time limit from the signal bar. The 0.6 is not tuned. It is
the smallest stop at which today's round trip costs at most 0.15 R (section 3). In live and shadow trading the S-1 gate
then skips every signal whose real round trip would cost more than 0.15 R at that stop, or whose stop would not fit a
0.01 lot. **The in-sample trial asks one question with full 2010-2025 power: does this entry/exit geometry have a gross
edge larger than the cost the S-1 gate allows?** If it does not, the high-volatility premise (cost falls in R as ATR
rises) cannot rescue it, and H-25 is retired for this parent with no further trials.

## 2. Evidence and prior (stated before the run)

| Evidence | Source | Sample, instrument | Net of costs? | What it says for H-25 |
|---|---|---|---|---|
| Gross edge does not depend on the entry rule. Signal minus random-side gross is +0.04 to +0.08 R for market, pullback-reclaim, 50% retrace and swing-stop entries | `tight-stop-entry-study-2026-10-18.md` §1 (pre-trial study, ours) | 2024-01..2025-09 design, 2023 check; XAUUSD 1m Dukascopy; 8 families | Both reported (0.71 $/oz) | A retest entry is unlikely to create edge. **Single study, ours** |
| Tight stops lose because cost in R is 0.71 / stop$, not because noise lowers the hit rate: gross R is flat (+0.05 to +0.1) across stop widths | same, §4 | same | Gross and net | The only lever is the stop size in $ relative to cost: hence the S-1 bound |
| In the $3-6.65 stop band (today's band) net R is -0.10 to -0.19, the same as random entry | same, table 5 | design sample, trades whose $ stop matches today's | Net | **This is an in-sample look at the H-25 regime.** It contaminates selection; see K in section 6 |
| breakout/15m with a pullback-reclaim entry: gross +0.08 R on 458 signals; 50% retrace: +0.03 R | same, per-family table | design sample | Gross | The parent's best-looking entry is still about half the 0.15 R bound |
| 15m breakout at a 1.0 x ATR15 market entry: gross +0.008 R (t 0.48) on 3,874 events | registry trial #29, `batch-2026-10-18-readout.md` §1 | 2010-01..2025-09 | Gross and net (-0.524 R) | The parent has no edge as a market-entry rule (R-04 extension, retired) |
| Orders cluster at round numbers and recent extremes; take-profits reverse trends there and stop-losses accelerate breaks | Osler 2003, *J. Finance* 58(5) 1791-1819 | 1999-2000, three USD FX pairs, order book of one dealer | Gross | Mechanism for a retest of a broken level (role reversal). **Single study, FX, not gold** |
| Support and resistance levels published by firms predict intraday trend interruptions | Osler 2000, FRBNY *Economic Policy Review* 6(2) | 1996-1998, three FX pairs, intraday | Gross | Same mechanism; levels interrupt trends more often than chance. **Single study, FX** |

**Prior.** P(continue) is about 5%. The study's evidence says entries do not add edge, and the bound needs a gross edge of
about 0.22 R to clear rule 3 at the expected event count (section 4.3), three to four times anything we have measured at
15m. **Why spend a trial anyway.** It is the one design where the high-volatility premise and the 0.01-lot fit agree
(readout §5, rank 2). A stop read here closes rank 2, and through the rule in section 5.4 it also withdraws the proposed
O-11 (H-25 on H-17, Q1) and H-26 (pyramid, Q2), which depend on it. One trial now saves up to two later.

**Difference from the known-failed S-4 (`sizability-2026-10-18.md`), stated so the overlap is visible.** S-4 retired
pullback-reclaim and 50% retrace entries with stops at 1m structure, 0.25-0.6 x ATR15 (net -0.45/-0.46 R). This design
changes three things, all fixed by arithmetic before any data: (a) the entry is a limit at the broken level, not a
reclaim; (b) the stop is floored at 0.6 x ATR15, which removes the sub-0.5 ATR stops that drove S-4's loss; (c) the S-1
gate trades only where the real cost is within the bound. It is still the same family of idea, which is why it carries
the study's K_eff (section 6).

## 3. The cost arithmetic that fixes every number (no returns used)

Inputs: round trip 0.71 $/oz (cost stress of the batch and the tight-stop study: about 0.30 spread + 0.37 =
2 x `slippage_prior_usd` 0.15 + 2 x `commission_per_lot_side_usd.icm` 3.5 / 100, `config/settings.yaml:120-121`);
ATR15 last-90-day median $8.01 (`atr-measured-2026-10-18.md`); 0.01-lot fit at the default gate $8.00, used here as
$7.98 (`sizability-2026-10-18.md`, playbook).

| Quantity | Value | How |
|---|---|---|
| Today's cost-to-ATR ratio, kappa | **0.09 x ATR15** | 0.71 / 8.01 = 0.0886, rounded up |
| Maximum cost allowed per trade, c* | **0.15 R** | The low end of the "cost 0.15-0.25 R" range that `small-account-playbook-2026-10-18.md` gave for precision entries. It is a cost figure, chosen without reference to any return. Fixed now, not varied |
| Stop in ATR15, k | **0.6** | k = kappa / c* = 0.09 / 0.15. The smallest stop whose cost at today's ratio is within c* |
| Target | **2 x stop = 1.2 x ATR15** | the family's reward:risk 2.0 (`target_atr` 2.0 / `stop_atr` 1.0) |
| Live S-1 gate | trade only if (spread_t0 + 0.37) / (0.6 x ATR15_t0) <= 0.15 **and** 0.6 x ATR15_t0 <= $7.98 | equivalently ATR15 >= (spread + 0.37) / 0.09 (about $7.4-10.2 at spreads of $0.30-0.55) and ATR15 <= $13.30 |

At today's median bar the cost is 0.71 / (0.6 x 8.01) = 0.148 R: the median bar sits on the gate boundary. Lower ATR is
skipped (cost too high in R), higher ATR up to $13.30 is taken (cheaper in R, still fits 0.01 lot).

## 4. Exact configuration and run inputs

### 4.1 Why the in-sample trial charges a scaled cost, not the historical one (outcome-blind count)

Share of 15m bars (07:00-20:00 UTC, bar close spread + 0.37 as the round trip, Wilder ATR14 on mid) that the **live**
S-1 gate would trade, 2010-01..2025-09. Computed 2026-10-18 on release `data-v1` 1m files with the repo's
`resample_bars`, `mid` and `features.technical.atr`; ATR and spread only, no price path after a bar, no outcome:

| Year | 2010 | 2011 | 2012 | 2013 | 2014-15 | 2016 | 2017-19 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 (to Sep) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ATR15 median $ | 1.83 | 2.45 | 2.12 | 2.19 | 1.5 | 1.64 | 1.2-1.3 | 2.92 | 2.30 | 2.48 | 2.20 | 3.10 | 4.54 |
| Round trip median $ | 0.87 | 0.77 | 0.73 | 0.69 | 0.65-0.67 | 0.67 | 0.60-0.66 | 0.76 | 0.72 | 0.72 | 0.70 | 0.74 | 0.92 |
| Bars passing the live gate | 0% | 0.28% | 0% | 0.34% | 0% | 0.19% | 0-0.06% | 1.26% | 0% | 0.23% | 0% | 0.37% | 0.90% |

Total: **440 of 209,640 window bars (0.21%)**. At the parent's rate (3,874 signals in the same window) that is **about
8 gated signals in 15.75 years**. A trial with the live gate and historical costs cannot be read at all. So the
in-sample trial runs **ungated** and charges every trade the cost the live gate guarantees as its worst case: c* =
0.15 R plus the swap actually incurred. This assumes the setup's gross edge in R is scale-free (it does not depend on the
dollar level of ATR). Rule 4 checks that assumption on the two highest-ATR years in the sample. Real historical cost
(about 0.6 R at these stops) is reported and never read.

Reproduce: `resample_bars(read_bar_files(dir, 2010, 2025) cut at research.holdout_from, "15m")`; `A = atr(mid(r), 14)`;
`rt = ask_close - bid_close + 0.37`; gate `A >= rt / 0.09` and `0.6 * A <= 7.98`, hours 7-19 UTC. The scratch script is
not committed.

### 4.2 Configuration (variant keys; integers written as integers per D-1)

| Item | Value |
|---|---|
| Family / parent | `breakout`, `{"timeframe":"15m"}` defaults: `range_bars` 8, `max_range_atr` 2.0, `min_tick_ratio` 1.5, `max_bars` 24 |
| Signal | parent candidate at the close of 15m bar t0 (`visible_at`), side d, level L = the prior 8-bar high (long) or low (short) |
| Entry | limit at L placed at t0, valid for the next **30** one-minute bars; filled on the first 1m bar whose mid low (long) or high (short) trades through L by $0.01; fill price L (never better) |
| Stop | L - d x 0.6 x ATR15(t0) (ATR15 = `atr14` of the decision frame at t0) |
| Target | entry + d x 2 x stop distance |
| Exits | barrier only on 1m mid bars: stop, target, or the time limit at t0 + 24 x 15m; the parent's scale-out and trail are **off**. A 1m bar touching stop and target counts as the stop. On the fill bar the stop counts and the target does not |
| Positions | one at a time; an unfilled order blocks new parent signals until it expires |
| Cost in the rule | `cost_model: scaled`: per trade 0.15 R + swap_i / stop_i (long -0.60 $/oz per 22:00 UTC rollover held, Wednesday x 3; short 0; settings priors) |
| Cost reported only | real: (spread at fill + 0.37) / stop_i + swap_i / stop_i |
| Sample | `from_year=2010`, `to_year=2025`, cut at `research.holdout_from` 2025-10-01 by the pipeline; **holdout 2025-10-01..2026-09-30 never loaded** |
| Reporting | `lot_fit=true` (reporting only, as the batch); random-side control on the same t0s, seed 0 (reporting only); fill rate; per-year table |

`research.yml` inputs (one line, dispatched only when every condition in section 8 holds):

```bash
gh workflow run research.yml --ref main -f specialist=breakout -f from_year=2010 -f to_year=2025 -f lot_fit=true -f variants='[{"timeframe":"15m","entry_mode":"retest_level","entry_tf":"1m","entry_wait_bars":30,"fill_rule":"trade_through_1tick","stop_mode":"level_atr","stop_atr":0.6,"target_rr":2,"exit_mode":"barrier","cost_model":"scaled","cost_r":0.15}]' -f rule_ref='prereg-h25-precision-stop-2026-10-18.md#5-reading-rule' -f rationale='H-25 precision retest entry with the S-1 cost-bound stop on 15m breakout, exactly as pre-registered 2026-10-18; 1 trial'
```

The keys `entry_mode`, `entry_tf`, `entry_wait_bars`, `fill_rule`, `stop_mode`, `target_rr`, `exit_mode`, `cost_model`
and `cost_r` do not exist in code today (section 8, E-H25). Their names may change in the build; the **values and
semantics above may not**. The final command, with the planned agent id, is pasted here as a dated line before
dispatch. `stop_atr` is the existing key, re-used with the meaning "distance from L".

### 4.3 Event count and power (before dispatch, as ruling 1.1 asks)

- Parent signals: **3,874** (trial #29's recorded count; a count, not an outcome).
- Fill: unknown for a first touch of L within 30 minutes. The study's limit-at-midpoint entry filled 75-81% within
  30-60 minutes (pooled) and 78% on breakout/15m. A touch of L is deeper, so the range taken is **35-70%**.
- Expected filled events: **about 1,350-2,700** (central about 2,000). The P4 floor of 1,000 is likely met; about a
  one-in-five chance it is not, in which case rule 5.3 applies.
- Power: barrier outcomes of -1 / +2 give an SD of about 1.41 R. At n = 2,000 the SE is 0.032 R, so rule 2 (gross
  t >= 2) needs gross >= 0.063 R, and rule 3 (scaled net t >= 2) needs gross >= about 0.15 + swap + 0.063 = **0.22 R**.

## 5. Reading rule (written 2026-10-18, before any data of this configuration is seen)

Let n be the filled trades. For each trade, R_i is the gross barrier R at mid and net_i = R_i - 0.15 - swap_i / stop_i.

### 5.1 CONTINUE (the configuration becomes a LEAD), only if all hold
1. n >= 1,000.
2. Gross mean R > 0 with t >= 2.0 (P4).
3. Mean net_i > 0 with t >= 2.0. The t used is the **smaller** of the trade t and the day-clustered t (trades summed
   per entry UTC day).
4. Mean net_i > 0 in at least 9 of the 16 calendar years (2025 = January-September), **including both 2024 and 2025**
   (the scale-free check: the two highest-ATR years in the sample).
5. Fill rate >= 50% of parent signals not blocked by an open position.

### 5.2 STOP (retire H-25 for this parent), if any holds
- n >= 1,000 and gross t < 2.0; or
- mean net_i <= 0, at any n (ruling 1.1 of the readout: no event floor on this part); or
- n >= 1,000, rules 2-3 pass, and rule 4 or 5 fails.

### 5.3 INCONCLUSIVE
n < 1,000 and mean net_i > 0. No re-run, no neighbour, no shadow: the idea waits for new evidence (a parent with a
gross edge >= 0.22 R of its own).

### 5.4 What follows each reading (fixed now)
- **Stop:** row H-25 is retired in `hypotheses.md` §B with "precision retest entry with a cost-bound 0.6 ATR15 stop on
  15m breakout: gross below the S-1 cost bound over 2010-2025". O-11 (H-25 on H-17) and H-26 are **withdrawn before
  they are written into the Q1/Q2 files** (0 trials). Re-testing needs a parent with its own gross >= 0.22 R.
- **Inconclusive:** as stop for O-11 and H-26; the row is recorded inconclusive.
- **Continue:** the configuration is a LEAD. It may shadow under the live S-1 gate (section 7). O-11 may be written into
  the Q1 file. Nothing is promoted, sized or traded: promotion needs the gates (gate 1 at the K of section 6), the
  holdout rule and the owner's live decision.
- Every verdict records: K at the time (section 6), n, fill rate, gross and net t (both t's), the per-year table, the
  real-cost net (reported, not read), the random-side control (reported, not read), DSR at K (reported).
- The real-cost net and the control can never turn a stop into a continue, and the evaluator may not re-read the
  run under another cost model: that would be a new trial.

## 6. K accounting

| Item | Count | Source |
|---|---|---|
| Registry trials in 2026Q4 before H-25 | 55 when TVR-1/2 run (20 + 32 batch + H-02 + 2 TVR) | `preregistration-2026Q4-pullforward.md` §4; TVR ruling (branch `rd-tvr-ruling-2026-10-18`, 53 -> 55) |
| H-25 in-sample | **+1** (trial #56 in budget order) | this file |
| Tight-stop study looks | **K_eff +19**, recorded as `k_eff: 19` on H-25's registry row | that study's §5 recommendation: any later pre-registered entry mechanic from it counts its 19 variants |
| H-02 K_eff | about +10 if H-02's verdict is recorded first | pull-forward §2.6 |
| **K at H-25's verdict** | **registry `n_trials_effective` at that date, never below 75** (56 + 19), about 85 with H-02 | gate-1 DSR bar: t about 4.1 |

Code check owed (E-H25 c): `registry.n_trials_effective` must add a `k_eff` recorded on a non-discovery row. If it
only adds discovery rows today, the evaluator adds 19 by hand in the verdict reason until the code does.

A rule-only row cannot reach gate 1 here (the batch's same finding); the reading is continue/stop only.

## 7. Forward-shadow reading plan (only after a CONTINUE)

- **Who shadows:** the LEAD configuration, with the live S-1 gate of section 3 and real costs, in `engine/shadow.py`.
  A STOP or INCONCLUSIVE configuration never shadows (readout §5: only a LEAD may shadow).
- **Expected rate (estimate, not measured):** parent signals about 250 a year, of which the live gate passes perhaps
  30-50% at today's ATR and spreads, and 35-70% fill: **about 30-90 shadow fills a year**. This forward test is slow;
  it is a kill switch first and a pass route only after years.
- **Kill read** at 100 shadow fills: stop the shadow and record "forward: failed" if the mean real net R <= -0.10 or the
  real-net t <= -1.5. 0 trials (a kill is not selection).
- **Pass read** at 300 shadow fills: continue (eligible for the promotion gates and the owner's live decision) only if
  the mean real net R > 0 with t >= 2.0; otherwise stop. **This read is 1 registry trial**, charged to the quarter in
  which it happens, at that quarter's K.
- **Holdout:** a CONTINUE does not give H-25 the one holdout look. That look is reserved after the G2 repair (due
  2026-11-06), and allocating it is the research director's decision. Flagged, not assumed.

## 8. Conditions before dispatch, budget and owners

| # | Condition | Accountable | Date |
|---|---|---|---|
| E-H25 (a) | 1m entry and barrier simulation for `entry_mode: retest_level`, `stop_mode: level_atr`, `exit_mode: barrier`, as `optional_config` (absent = off; existing agent ids, config hashes and goldens unchanged, with a test) | implementer; quant-reviewer, code-reviewer | 2026-11-20 |
| E-H25 (b) | `cost_model: scaled` / `cost_r` reported as net in the result row; real-cost net, fill rate, per-year table and random-side control reported | implementer; quant-reviewer | 2026-11-20 |
| E-H25 (c) | `k_eff` on a non-discovery row counted by `n_trials_effective` (or confirmed already counted) | implementer; quant-reviewer | 2026-11-20 |
| E-H25 (d) | The same entry, stop and S-1 gate in `engine/shadow.py` (needed only after a CONTINUE) | implementer; trading-safety-reviewer | after the verdict |
| D-1 | Registry repair, or the integer transcription above | implementer + quant-reviewer | 2026-10-25 (readout §3) |
| Q | B05-B12, H-02 and TVR-1/2 finished, nothing queued (one run at a time) | research-director | at dispatch |
| Retired family | `breakout` is in `research.retired_families` (R-04). The batch ran retired families by `rule_ref`; if the director's checks refuse this run, that is raised to the research director, never worked around | research-director | at dispatch |
| Budget | **1 slot: 2026Q4 override 55 -> 56** (57 if TVR-3 took 56). The research director rules whether the owner's standing directive of 2026-10-18 covers it, as for TVR (B4a). Fallback: one of the three unallocated Q1 trials (pull-forward §5) by a dated note | research-director (owner-only if not covered) | 2026-10-25 |

If E-H25 (a)-(c) are not merged by **2026-12-11**, the trial moves to Q1 2027 under this text unchanged (no new choice
is allowed in between), funded by the Q1 fallback above.

## 9. Definition-of-done check

- Config, economic reason, expected edge vs costs in R (break-even 0.15 R + swap; detectable gross about 0.22 R), event
  count (1,350-2,700), reading rule and trial cost (1 + 1 conditional) are written before any data: yes.
- Citations: our own studies by file; Osler 2000 and 2003 by venue, sample and instrument, gross, single studies, FX.
- Known-failed overlap (S-4, S-5) stated, and its K_eff charged.
- Holdout untouched: section 4.1 reads ATR and spread to 2025-09-30 only. Budget not exceeded: the slot is requested,
  not taken.
- Owner-only items flagged: budget (section 8), the holdout allocation (director), live trading and sizing (section 5.4).
