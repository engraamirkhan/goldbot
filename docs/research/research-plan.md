# Research plan: live lines, kill/scale table, next bets

Rewritten 2026-10-30 (cycle 2, same day) by the research director, base `48768fda`. Cycle 1 of today is at
`48768fda:docs/research/research-plan.md`; the 2026-10-20 plan (team-feedback items 1-83) at
`660d2db2:docs/research/research-plan.md`. Their answers stand unless a row below changes them. Kill-rule amendments
cite `prereg-amendment-2026-10-30-kill-rules.md` (A1-A4 signed with conditions 10-30; A5 draft).

Safety rails are not research variables: RiskGate only path, 1% per-trade cap, daily loss limit, owner confirms
real-money entries, exits never gated, holdout sealed, every id counted in K.

## 1. Where we stand (numbers first)

| Fact | Value | Source |
|---|---|---|
| Families with an edge over the random control (demo) | **0.** Every family with >= 30 trades a side "not distinguishable" | perf-report 2026-10-19..10-30, learning section (cycle 1) |
| Scalp arms' own entries on history (S5A/B, Z4D/P, Z6D, Z7D/P), 337,341 entries 2010-2017 | Gross within +/-0.010 R at **every** exit (1 R..time-only); net -0.04..-0.06 R at the 0.05 R broker cost, -0.11..-0.13 R at the 0.116 R signal-time cost of record. Share reaching +1 R: 4.1-12.7%; median MFE 0.18-0.30 R. DiD of wider exits vs random entries -0.0017..+0.0009 R (bar +0.05) | `hb-exits-screen-2026-11.md`; **re-derived** by the director from `~/tmp/hb_exits/pairs_*.parquet`: +1 R share S5A 0.127, S5B 0.085, Z4D 0.101, Z4P 0.086, Z6D 0.044, Z7D 0.042, Z7P 0.041; Z7D time-only gross +0.0095, Z6D -0.0091; S5 n 55,442 + 55,264 = 110,706. All match |
| Random entries by session x range tercile x trend side, 2010-2017 (493,694 trades) | **0 of 24 cells** net > 0 (net -0.058..-0.109 R; best gross +0.025 R); H-A trend side -0.008 R (t -2.7, wrong sign). Session changes cost (Asia/NY late ~2x the London spread add-on), not gross | `when-map-screen-2026-11.md` |
| Box family (owner's box break) R2, 2018-2025-09, n 5,355 | Runner - fixed gross -0.018 R (t -1.12); levels gross/net E0 +0.012/-0.199, E4 -0.006/-0.288, E8 +0.011/-0.206 R. The +0.039 R discovery-half result did not replicate | `prereg-exits-adaptive-2026-10-26.md` s.8 |
| H-16 range forecast, 2010-2017, 1,506 OOS days | FAIL by the frozen rule: rho gain +0.201 (95% 0.145..0.260; positive in all 6 years) passed, quintile ratio 1.437 < 1.5 failed (HAR alone 1.165). Every quintile's median day moves 14-18x the round-trip cost | `opportunity-days-screen-2026-11.md`; **re-derived** 1.0744 / 0.7478 = 1.437 from `~/tmp/h16-screen/result.json` |
| Discovery v1 (ADR 0058) | 0 survivors on 15m/1h/4h; phase 3 (confirm/holdout) still being built | cycle 1 |
| TradingView parser | Audit 2 (seed 20261031) FAIL: direction 83.3% (bar 95%), levels 76.7% (bar 90%). Second failed regex iteration | `tv-ideas-parser-audit-2026-10-31.md` |
| Data integrity | icm-probe's terminal ran on icm-demo's login 10-19..10-25: its balances, daily loss and 137 probes in that window are icm-demo's | demo-reset PR (68 A2 ids retired, duplicate-login guard, specialist label fix) |
| K | ~1,256 = ~360 arm ids + 864 discovery v1 specs + 31 screen looks (H-16 1, H-82 5, H-83 25) + 1 registry trial (H-80 R2, local-only, owed to the brain registry as a 2026Q4 trial); +2 weekend controls WKS0/WKI0 (2026-10-31) -> ~1,258 | this file; screen documents; `prereg-2026-10-31-weekend-247.md` s.5 |

**Read.** Three independent history tests this week say the same thing: on 1m-4h gold, with the entries we have,
the gross edge is zero to within 0.01-0.03 R, and the cost (0.05-0.3 R) is the whole result. Exits do not rescue it
(H-82, H-80 R2), timing does not rescue it (H-83), and the day-type forecast predicts range, not money (H-16). The
remaining expected information is where cost per R is small and a peer-reviewed mechanism exists: 4h/1d holds
driven by trend and macro, and the one intraday "when + exit" idea with published support (H-17 trend day).

## 2. Live lines (at most 8)

| # | Line | Mechanism (why gold pays) | Owner | Kill rule (sample, bar, date) | Next measurable step |
|---|---|---|---|---|---|
| 1 | **Macro-driven 4h/1d direction (H-84)**, reopened from "parked" | Gold is priced against real rates and the dollar (opportunity cost; Erb & Harvey 2013; Baur & Lucey 2010, R) and trends at 1-12 months (MOP 2012; Hurst et al. 2017, R). At 5-20-day holds cost is ~0.02-0.05 R + swap, so a gross 0.1 R edge can survive | macro-researcher | Screen 2010-2017 by **2026-11-10**: per variant gross t >= 2 and net > 0 at the worse measured cost + swap, and >= 5 of 8 years positive, else retired; survivors confirm once on 2018-2025-09 (the 2022 real-rate break is the falsification test) | pre-registration (<= 4 variants, frozen by commit) by **11-03**; bet 1 |
| 2 | **Trend day + runner (H-17)**; H-16 selector = HAR alone | Volatility clusters (R); a trend day caught early pays only if held (Zarattini et al. 2024, S) | opportunity-researcher | Frozen H-17 rule (A3) on 2010-2017 by **2026-11-06**: gross t < 2 on its events, or V1 net <= 0, or < 3 positive net years -> line retired with H-16 | bet 2 |
| 3 | **Discovery grammar v2, last run** | Search over when/side/exit primitives with an absolute net-R floor | strategy-researcher (+ ml-researcher for null/leakage) | A4: 0 survivors on every TF run by **2026-11-20** freezes generation until the Q1 SoA refresh | K_v2 declared, one run per TF; bet 3 |
| 4 | **Demo book: controls, cost, and the reads that remain** (table 3) | Kill machine for large effects; cost and parity evidence | performance-analyst (reads), evaluator (verdicts) | A1/A2 (signed with conditions), A5 (draft) and each unit's own read; 11-13 A2 bar; reads from **2026-11-23** | A5 signed by 11-04; per-stratum signal-time cost stop applied now |
| 5 | **Costs: the figure every rule reads** | Fixed USD costs are the largest share of R at small stops | microstructure-researcher | S0R overshoot read at 100 stop exits (~**11-10**); data-engineer confirms `cost` is the measured table, not the config fallback, by **11-06** (evaluator R2) | then cost-probe volume set to what the protocol needs |
| 6 | **TradingView author ideas, direction only with abstain (amendment T-1)** | Rare public skill (Kakhbod et al. 2023, WP); prior 10-15% | strategy-researcher (staff audit) | Re-audit on a fresh seed by **2026-11-13**: precision >= 95% on non-abstained ideas and coverage >= 50%, else the line is **parked** to the Q1 SoA refresh; no fourth parser iteration | T-1 written in `prereg-2026-10-28-tv-author-ideas.md` s.7 before the parser change |
| 7 | **Book integrity** (money leaks, account mixing) | Losses that are not edge distort every read | performance-analyst + data-engineer | Closes when items 89, 90 and 101 are answered | icm-probe 10-19..10-25 excluded from every read and balance figure |

**Retired this cycle, with the reason:**
- *Exits* (was line 2): H-80 R2 FAIL (runner -0.018 R gross, t -1.12; 2 of 4 TF) and H-82 FAIL on all 5 variants
  (DiD |x| <= 0.0017 R, CIs +/-0.001 R). Fixed-$ scalp brackets rarely reach a target, so exits cannot matter there;
  box-family exits do not replicate. Not reopened: H-82's volatility-scaled replication (item 97).
- *When-to-trade at the scalp clock* (was line 3): H-83 0/24 cells, H-A wrong sign. Not reopened: the 1h/4h
  when-map (item 98) and the post-hoc "continuation on narrow days" pattern (item 99).
- *H-16 as a separate line*: FAIL; GVZ/events/compression retired as range inputs; O-1 cancelled; no new money-tied
  pre-registration (item 95).

**Parked:** ML meta-labelling (no primary with a gross edge); hand-written intraday entry families (0 of ~30 beat
the control, and history now bounds the scalp entries' gross at +/-0.01 R); the owner's box break + MFI as a coded
rule (R-15 at 15m, H-80 R2 at 15m-4h; H-53 1h stays the 2027Q1 retired-family trial by its own text).

## 3. Kill/scale table, every demo family

Matched read: perf-report 2026-10-19..10-30 (cycle 1). Rates: admissible fills a session day 10-27..10-30. **No
family qualifies for scale.** icm-probe fills and balances 10-19..10-25 are excluded (login mix, item 101).

| Family (arms) | n, matched excess vs control (90%) | Rate | Rule | Decision this cycle |
|---|---|---|---|---|
| random controls (S0, S0R, I0, W0, VI0, C1H, VC1H, ZCD, ZCV) | +0.025 R (probe 354) | ~90 | never read | **keep** (the null) |
| weekend controls WKS0, WKI0 (vantage-247, XAUUSD247, ADR 0062; not enabled yet) | none | — | `prereg-2026-10-31-weekend-247.md` s.4: cost/plumbing read after 4 weekends or WKS0 300 fills; default stop | **keep as measurement only**: never read for edge, never a null for weekday families, no promotion path |
| XC15 (dedicated control of the specialist arms) | — | <2 | A2 | **kill** (evaluator R4 objection accepted: its arms are retired) |
| cost_probe | -0.05 R | ~120 | cost protocol | **keep** to the 11-06 ruling, then cut to the protocol's need |
| halfhour_persist (Z7D/Z7V/Z7P) | probe -0.02 (103), icm +0.25 (107), vantage +0.08 (98) | 78 | H-81 read 300 + A1 | **kill on A5 sign-off** (history: gross +0.002..+0.010 R across the 5 exits on 168,705 entries, below cost at every exit); until then the H-81 s.4 signal-time cost stop applies per stratum |
| ema_pullback (S5A, S5B) | probe -0.03 (191) | 53 | H-61 read 300 + A1 | **kill on A5 sign-off** (gross -0.009..+0.005 R across exits on 110,706 entries) |
| round_reject (Z4D, Z4P) | probe -0.15 (76), icm +0.34 (85) | 40 | H-81 read 300 + A1 | **kill on A5 sign-off** (gross -0.001..+0.008 R on 30,079) |
| nr4_break (Z6D, Z6V) | icm +0.35 (47), vantage +0.05 (43) | 23 | H-81 read 300 + A1 | **kill on A5 sign-off** (gross -0.011..-0.009 R on 27,851) |
| round_break (Z3D, Z3V) | icm +0.15 (62) | 15 | H-81 + A2 | **kill at < 100, 2026-11-13** (A2), else read 11-23 with A1 |
| session_vwap_fade (S1A, S1B) | probe +0.21 (54, -0.09..+0.51) | ~8 an arm | H-61 per arm + A1/A2 (evaluator R3: not void) | **no A2 exemption**: retired 11-13 if < 100 an arm; else read at 300 an arm with A1. VWAP fades grossed <= 0 on 2010-2017 |
| box family H-80 R1 (BXR*, virtual BXT*/BXF*) | no admissible fills | <1 | A2 exemption ended with the R2 FAIL | **kill now** (A2: "retired if R2 does not adopt the runner") |
| hurst_band_break (F1A/F1C), level_break (G1A/G1C) | small n | <4 | cost arms for O-11/O-12 | **keep as cost arms** (A2 exempt) |
| run_fade Z2P, three_horizon Z10D, extreme_break Z5P/Z5V, tick_surge Z1P, htf_reclaim Z9D | n 12-27 | <8 | H-81 + A2 | **kill at < 100, 2026-11-13** (A2) |
| A2 "kill now" list (evaluator R4: S2*, S3*, MSA15, VMS15, S4, I2*, VI2*, W1*, VW1*, W2, VW2, MB*, VMB*, WFRI, specialists X*/VX*, Z8P, KA15, OBR15, KSQ1/4, DON4/DOND, C30A, PDC1/4, F2A/F2C, C30C, S6, I1, I3, W3, VS*) | < 2 a day | — | A2 | **killed** (A2 effective; the demo-reset PR retires the 68 ids; rates recorded with each retirement) |

**Volume after these decisions:** ~245 admissible fills a session day (controls ~90, cost probes ~120, Z3 ~15, S1
~16, cost arms ~4), down from ~440; after 11-13 and the cost ruling, ~150-200. This follows the owner's own 10-27
rule "anneal down on measured confidence": history measured these arms at zero gross on 337k entries. Owner
options are in section 6.

## 4. Next week's three bets (2026-11-02..11-06), each screened on 2010-2017 locally first

Chosen by expected information per trial, given that entry timing, exits and when-to-trade failed intraday.

1. **H-84 macro-driven 4h/1d direction** (line 1, macro-researcher). <= 4 pre-registered variants (real-yield
   20-day change, broad-USD 20-day change, H-01 slow TSMOM gated by both (H-06), GVZ-RV variance premium (H-03)),
   1d signal on `macro-v1` PIT data (`~/tmp/macro_dl/macro_fred.parquet`, `asof_join` on `available_utc`), 4h
   execution, 5-20-day hold, measured cost + swap. Prior: H-01's gross +0.166 R (t 2.04) on 294 events (trial #48,
   2010-2025-09, so H-01 itself is already seen; the gates are the new part, disclosed). P(any variant passes the
   screen) ~0.25; P(confirm on 2018-2025-09) ~0.1. K +4. Tradeability flag: a 1.5 x ATR(1d) stop is not sizable on
   the live pilot (0.01 lot, $4 stop): demo/shadow only unless the owner changes sizing (owner-only).
2. **H-17 trend day + runner** (line 2, opportunity-researcher). The frozen A3 rule on 2010-2017, conditioned on
   HAR (already frozen). The only intraday idea left with peer-reviewed support and the £50-day shape. P(pass)
   ~0.1-0.15 (R-05 and H-80 R2 cut against it). K +2 (V0, V1).
3. **Discovery grammar v2** (line 3, strategy-researcher). One run per TF on 1h, 4h and 1d (15m dropped before the
   first run: every 1m-15m test so far is cost-bound with gross ~0; quant to acknowledge as a v2 definition, not a
   v2.x), with the macro PIT drivers and the HAR tercile as gate primitives and the absolute net-R floor. K_v2
   declared before the run; survivors pay the DSR at ~1,256 + K_v2. P(>= 1 survivor) ~0.1. It decides whether
   automated generation continues past 11-20.

## 5. Team feedback log (items 1-94: see the cycle-1 file at `48768fda`)

A = adopted, D = deferred (date), X = declined (reason).

| # | From | Item | Answer | What changed |
|---|---|---|---|---|
| 95 | opportunity-researcher (H-16) | New pre-registration on a money-tied target (net R of a random bracket by score quintile, or USD range in cost multiples)? | **X.** (1) Random brackets have ~0 gross in every range tercile (H-83: -0.025..+0.025 R), so a random-bracket target only measures cost per R, which is mechanical. (2) The cost-multiple target is already answered: every quintile's median day moves 14-18x the round trip, so no cost-based stand-aside rule exists to find. (3) The +0.20 rho is real and recorded; GVZ/events return only as a conditioning input inside the pre-registration of a primary that has passed a 2010-2017 screen (bets 1-2), under a new id, K counted | line 2; H-16 row |
| 96 | evaluator (H-80 R2) | BXR arms stop; registry row local-only | **A**: BXR killed now (A2); evaluator syncs the row to the brain registry as the 2026Q4 trial (PD + PO raise 145 -> 146) by 11-04 | table 3 |
| 97 | exits-researcher (H-82 limits) | Volatility-scaled replication at today's stop/ATR | **X**: the demo's median MFE (0.19-0.27 R) matches history's (0.18-0.30 R), so the geometry is the same; the gross bound (+/-0.01 R) leaves nothing for any exit to amplify | — |
| 98 | microstructure-researcher (H-83) | When-map at the 1h/4h random-control geometry | **X** for now: bet 1 and bet 3 already test 4h/1d conditioning with a mechanism; revisit only if bet 1 finds a gross edge to condition | — |
| 99 | microstructure-researcher (H-83) | Post-hoc "continuation after narrow days" (London low +0.050 R) | **X** as a trial (selected from 12 strata, below cost); recorded as a descriptive note for H-17's pre-registration review only | — |
| 100 | microstructure-researcher (H-83) | Keep 1m/5m arms inside London + NY overlap (thin hours cost ~0.01 R more) | **A** as a cost rule for any surviving 1m/5m arm (Z3, S1 until 11-13) | table 3 |
| 101 | data-engineer / demo-reset PR | icm-probe ran on icm-demo's login 10-19..10-25 | **A**: that window is excluded from every read and balance figure; performance-analyst re-runs the matched reads without it and states whether any cycle-1 number changes | line 7 |
| 102 | strategy-researcher (TV audit 2) | Third regex, LLM classifier, abstain amendment, or park | **A: amendment T-1** (direction only, 3 classes long/short/abstain, precision >= 95% on non-abstained, coverage >= 50%). **X LLM**: a model trained on later data knows gold's path after each idea, so its reads of ambiguous text are not outcome-blind (look-ahead), and it costs ~3-4M tokens for the back catalogue. **X fourth regex** chasing full agreement. Park on a T-1 fail | line 6 |
| 103 | evaluator R4 | XC15 is not A2-exempt | **A** | table 3 |
| 104 | quant-reviewer A2 | S1A/S1B under the 11-13 bar unless exempted by 11-04 | **A**: no exemption | table 3 |
| 105 | owner directive 2026-10-31 / ADR 0062 | Use Vantage XAUUSD247 at weekends to keep learning and testing | **A, as cost and plumbing measurement only**: the weekend quote is broker-made (gold is shut), so no edge claim is possible; two random controls (WKS0, WKI0), 2 registry rows, Q4 budget 147 -> 149 (PD + PO), no strategy arm under the owner's history-qualified rule | table 3; `prereg-2026-10-31-weekend-247.md` |

## 6. Owner-only items (options and a recommendation; nothing decided here)

1. **Live pilot (0.01 lot, stops <= $4, £3.12 a trade, owner-confirmed entries; not yet started).** At that geometry
   history says every current entry loses 0.05-0.12 R a trade: about £0.16-£0.37 a trade, £5-£11 over the pilot's
   30-trade cap. Options: (a) **defer real money until a family passes its history screen and confirm**
   (recommended); (b) run the pilot as a plumbing and execution test only, inside its £25 / 30-trade stop, with the
   expected small loss stated up front; (c) run it as a profit test (not recommended: no research supports it).
2. **Demo volume vs the 10-19/10-25/10-27 volume directives** ("hundreds of demo trades a day", 500+ total). Options:
   (a) **~150-250 a day of controls, cost probes and the readable families** (recommended; applied within the
   owner's own "anneal down on measured confidence" clause, reversible); (b) keep 500+ by adding random-control
   volume (pure nulls; useful for cost measurement only until the 11-06 ruling); (c) keep the scalp arms to their
   11-23 reads (pays ~0.05-0.12 R a fill, notional, to learn what history already measured on 337k entries).
3. **Sizing for 4h/1d** (only if bet 1 survives its confirm): a 1.5 x ATR(1d) stop does not fit 0.01 lot at £50 or
   1% of £1,000. Options when it arises: demo/shadow only (default), or a larger live balance. No decision needed
   this week.
4. **Owner's own trade history** (memory "owner manual results"): the analyser exists (`owner-method-study.md`), but
   no export is on the Mac. It is the one source of a demonstrated edge; optional, one export, aggregates only.
