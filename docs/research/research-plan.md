# Research plan: live lines, kill/scale table, next bets

Rewritten 2026-10-30 by the research director (role replaced 2026-10-30, owner decision), base `660d2db2`. The
previous plan (cycle 1, 2026-10-20: milestone ladder, 23-row agenda, team-feedback items 1-83) is in git history at
`660d2db2:docs/research/research-plan.md`; its answers stand unless a row below changes them. Kill rules here that
need an amendment cite `prereg-amendment-2026-10-30-kill-rules.md` (A1-A4, drafts until signed).

Safety rails are not research variables: RiskGate only path, 1% per-trade cap, daily loss limit, owner confirms
real-money entries, exits never gated, holdout sealed, every id counted in K.

## 1. Where we stand (numbers first)

| Fact | Value | Source |
|---|---|---|
| Families with an edge over the random control | **0.** Every family with >= 30 trades a side is "not distinguishable" on the matched read | perf-report 2026-10-19..10-30, learning section, all 3 accounts (read 2026-10-30 17:24 UTC) |
| Random control, icm-probe | n 354, +0.025 R net, +8.84 R | perf-report; **re-derived** from aux.db `exploration_trades` (read-only), identical |
| Exploration + control closes | 1,468 in 2026-10-26..30 (probe 875, icm 389, vantage 204); peak 420 on 10-29, mean ~367 a session day 10-27..30; cost probes on top | aux.db, re-derived |
| Why the raw book is green | Direction drift, not skill: icm-probe strategy shorts +0.216 R (n 333, t 3.4), longs -0.164 R (n 176, t -1.95); control shorts +0.058 R, longs -0.025 R; strategies 65% short in a falling week. The matched read (by side) removes it | performance-analyst 2026-10-30; **re-derived** exactly from aux.db |
| Costs | ICM ~0.05 R round trip at $3-4.50 stops (spread 0.025 + commission 0.024), Vantage 0.021 R; engine signal-time cost reads ~2x (0.116 R vs 0.053 R) | performance-analyst; ruling owed (line 8) |
| Exits (descriptive) | 93% of stopped trades never reach +1 R (median MFE 0.19-0.27 R). Strategy mean R by target: 0.5 R -0.070, 1 R +0.032, 2 R +0.133, none +0.213; control flat (+0.014 -> +0.007). Confounded by the short drift | performance-analyst sim, not yet history-tested |
| Power | Per-trade SD ~1.1 R. At 300 a side the matched 90% half-width is ~0.15 R; a +0.05 R edge needs ~6,000 a side. **Demo can only kill or find large effects; small edges must be found on history** | re-derived, A1 |
| Discovery (ADR 0058), 2010-2017 | 288 specs a TF, ~5.3k null specs, **0 survivors**. 1h 0.841 vs bar 1.337; 4h 0.692 vs 1.019 (RC p 0.64, PBO 0.62); 15m beats its null (RC p 0.001) but top specs have negative own mean R (-0.03..-0.08 R, stressed ~-0.19 R): it beats a cost-crushed null, not zero | `~/tmp/autodisc-run-*/`; 15m ledger re-read |
| North-star | £50+ days: none attributable to an edge. Account balances 26-30 Oct: icm-probe +£46, icm-demo -£44, vantage +£30 (exploration + probes, demo) | performance-analyst |
| K | ~360 by the arm-id rule (Q4 budget 145, all spent on arm ids) + 864 discovery specs this run | settings.yaml `trial_budget_overrides`; discovery ledgers |

**Read:** after ~2,000 demo trades the system has no edge distinguishable from random entries, and the demo cannot
detect the size of edge that intraday gold plausibly offers (0.05-0.10 R). The cheapest information now is on
history (free n, measured costs ~3-7x below the old research prior) and in *when* to trade and *how to exit*, not in
more entry patterns. The demo's job is narrowed to: random controls, cost measurement, and reading the 5-6 families
that can reach n = 300 this quarter.

## 2. Live lines (at most 8)

| # | Line | Mechanism (why gold pays) | Owner | Kill rule (sample, bar, date) | Next measurable step |
|---|---|---|---|---|---|
| 1 | **Opportunity days: H-16 range forecast, then H-17 trend day + runner** | Volatility persists and clusters on event days (Corsi 2009; Cai et al. 2001; R-grade); a trend day caught early pays only if held | opportunity-researcher | H-16 frozen rule on 2010-2017 screen: OOS Spearman < HAR-only + 0.05 or quintile ratio < 1.5 -> GVZ/events/compression retired as inputs; H-17 screen: gross t < 2 or net <= 0 at the measured cost -> trend-day line retired. Screen result by **2026-11-13** | A3 signed (11-03); H-16 screen on 2010-2017 by 11-06 (bet 1) |
| 2 | **Exits: runner vs fixed, wider targets / time exits** | Stopped trades rarely reach +1 R; if intraday gold trends have positive serial correlation, cutting winners at 1-1.5 R forfeits the payoff (Kaminski & Lo 2014, S) | exits-researcher | H-80 R2 frozen rule (pooled paired t >= 2 Holm, 3 of 4 TF same sign, net >= fixed) on 2018-2025-09, once, by **2026-11-13**; H-B screen: if no target beats 1 R by >= 0.05 R **over the random control under the same exit** on 2010-2017, the line closes | bet 2 |
| 3 | **When-to-trade map on random entries (incl. the 4h-trend side filter, H-A)** | If any session x range x trend-side cell makes even random brackets net-positive, the edge is in timing, and every family's bar moves | microstructure-researcher | Pre-registered 24 cells, DSR at cumulative K: no cell with net mean > 0, day-clustered t >= 3 and >= 6 of 8 years positive on 2010-2017 by **2026-11-13** -> line retired | bet 3 |
| 4 | **Discovery funnel, grammar v2** | Search over when/exit/side primitives, not only entries; selection on 2010-2017 only | strategy-researcher (+ ml-researcher for the null and leakage) | ADR 0058 stop (48 confirms, no pass); A4: grammar v2 with 0 explore survivors on 15m/1h/4h by **2026-11-20** freezes generation until the Q1 SoA refresh | A4 signed; grammar v2 frozen with the absolute net-R floor and measured costs; one run per TF |
| 5 | **Demo reads of the readable families** (table 3) | Kill machine for large effects; cost and parity evidence | performance-analyst (reads), evaluator (verdicts) | Each unit's pre-registered read + A1 + A2; first reads **2026-11-23** | Friday read every week; A1/A2 signed by 11-04 |
| 6 | **Costs: the figure every rule reads** | Fixed USD costs are the largest share of R at small stops | microstructure-researcher | S0R overshoot read at 100 stop exits (~**2026-11-10**) by its rule; cost-figure ruling (measured vs signal-time) by **2026-11-06** | evaluator ruling; then re-score retired families' recorded gross edges at the worse measured cost (R-15 re-entry condition: cost below half the prior) |
| 7 | **TradingView author ideas** | Rare public skill (Kakhbod et al. 2023, WP); prior 10-15% | strategy-researcher | Parser re-audit (outcome-blind, new seed) must reach direction >= 95%, levels >= 90% by **2026-11-13**, else the line is retired: 0 of 60 sampled ideas carry entry + stop, so only direction-plus-target ideas can ever be scored | parser fix in flight; re-audit |
| 8 | **Money-leak analysis of the demo book** | Finds losses that are not edge (sizing, specialist arms, stale accounts, unrecorded balances) | performance-analyst | One-off: closes when its findings are answered in section 5 | data issues 89-90 below |

**Parked, with the reason:** macro 4h/1d (H-01/H-02/H-06 frozen in Q1; untradeable before M3/M4; no new work this
month); ML meta-labelling (no primary has a gross edge to label: BACKLOG "Rejected"); hand-written new entry
families (0 of ~30 beat the control; new entries come only through line 4).

## 3. Kill/scale table, every demo family (matched read: perf-report 2026-10-19..10-30)

Rates are admissible fills a session day over 10-27..10-30 (aux.db). Reads: H-61 (arm, 300, >= 20 session days),
W1/W2/H-81 (family pooled, 300, >= 20 session days; -30 R stop; futility UB < +0.03 R). 20 session days from 10-27
= **2026-11-23**. **No family qualifies for scale**: exploration fills are never promotion evidence (H-61, H-81
section 4); a read can at most earn a new pre-registration and a history trial.

| Family (arms) | n, matched excess vs control (90%) | Rate | Pre-registered rule | Decision |
|---|---|---|---|---|
| random_bracket (S0, S0R, I0, W0, VI0, C1H, VC1H), scalp_control (ZCD, ZCV), F1C, XC15 | control, +0.025 R (probe 354) | ~90 | controls never read | **keep** (the null) |
| cost_probe | -0.05 R, cost measurement | ~120 | cost protocol | **keep** to the 11-06 cost ruling; microstructure proposes a lower volume then |
| halfhour_persist (Z7D/Z7V/Z7P) | probe -0.02 (103), icm +0.25 (107, -0.27..+0.77), vantage +0.08 (98) | 78 | H-81 read 300 | **kill at read 2026-11-23** unless it passes the -30 R stop, futility and A1 |
| ema_pullback (S5A, S5B) | probe -0.03 (191, -0.23..+0.16) | 53 | H-61 read 300 per arm; suspended if S0R reads "real" | **kill at read 2026-11-23** by H-61 + A1 (or earlier by S0R) |
| round_reject (Z4D, Z4P) | probe -0.15 (76, -0.41..+0.11), icm +0.34 (85, -0.23..+0.91) | 40 | H-81 read 300 | **kill at read 2026-11-23** unless it passes A1; watch (performance-analyst) |
| nr4_break (Z6D, Z6V) | icm +0.35 (47), vantage +0.05 (43) | 23 | H-81 read 300 | **kill at read 2026-11-23** unless it passes A1 |
| round_break (Z3D, Z3V at V2) | icm +0.15 (62, -0.39..+0.68) | 15 | H-81 read 300; -9.5 R of -30 R | **kill at <100, 2026-11-13** (A2), else read 11-23 |
| session_vwap_fade (S1A, S1B) | probe +0.21 (54, -0.09..+0.51), best point estimate in the book | 16 pooled, 8 per arm | H-61 read 300 per arm (~2026-12-16) | **keep to its read ~2026-12-16**; evaluator rules by 11-06 whether S1 is a neighbour of the closed session-VWAP fades (H-76); if VOID, **kill now** |
| run_fade (Z2P) | -0.31 (27); prior 0.02, "expect <= 0" | 7 | H-81 read 300 | **kill at <100, 2026-11-13** (A2) |
| three_horizon (Z10D) | +0.19 (24); prior 0.02, "expect <= 0" | 6 | H-81 read 300 | **kill at <100, 2026-11-13** (A2) |
| extreme_break (Z5P, Z5V at V2) | +0.60 (13) | 3 | H-81 read 300 | **kill at <100, 2026-11-13** (A2) |
| tick_surge (Z1P) | +0.21 (12) | 3 | H-81 read 300 | **kill at <100, 2026-11-13** (A2) |
| htf_reclaim (Z9D) | +1.27 (12); prior "expect <= 0" | 3 | H-81 read 300 | **kill at <100, 2026-11-13** (A2) |
| hurst_band_break (F1A) | -0.64 (13); -5.3 R | 3 | futility at 300/1,000; cost input to O-11 | **keep as cost arm** (A2 exempt) until 200 fills for O-11 (~2027-01-25); no demo edge read |
| level_break (G1A) | -0.83 (2) | 0.5 | cost/safety arm for O-12 | **keep as cost arm** (A2 exempt); O-12 moves to Q2 by its own rule if 100 fills are not reached by 2027-03-12 |
| box family H-80 R1 (BXR*, virtual BXT*/BXF*) | no admissible fills in the window | <1 | kill-only, no adoption before 3,000 | **keep until R2 verdict (bet 2), then kill** unless R2 adopts the runner |
| sweep_absorb (S3A, S3B: VOID R-14; MSA15, VMS15) | n 11 | 3 | H-61; read void | **kill now** (A2; read void by H-61) |
| opening_range_break (S2A, S2B) | -0.62 (6) | 2.5 | H-61 read 300 | **kill now** (A2: cannot read this year; ORB closed on history, R-02/H-76) |
| specialist / wave 1 (XIMNY, XIMLN, XMR15, XMR1H, XAD1H, XSO15, XEQ15, XTV1, VXBO1H, VXBO15, VXTR1H, VXTV2, VXRIP1H, VXRT1H, VXFK1H, VXTL1H) | -0.16 R raw (13); icm-demo -£44 | 3 across 16 ids | W1 read 300, -30 R | **kill now** (A2) |
| absorb_fade (Z8P), clock_anchor (KA15), ob_retest (OBR15), squeeze_release (KSQ1/4), DON4/DOND, C30A, PDC1/PDC4, F2A | n 1-4 each | < 2 | W2 / H-81 read 300 | **kill now** (A2) |
| tsmom swing (W1A, W1B, VW1A, VW1B: VOID R-06/R-10), W2/VW2 (VOID R-15), I2A/I2B/VI2A/VI2B (VOID R-15), S4 (VOID R-15) | n 0-2 | < 1 | H-61; read void | **kill now** (read void; A2) |
| asia_drift (icm-demo, 3 trades, -£48.65) | -0.36 (3) | < 1 | H-04 STOP (#44) on history | **kill now** (its hypothesis is already retired) |
| MB15/MB30/MB1H, VMB15/30/1H, WFRI, other ids with no fills in the window | 0 | 0 | H-61 / time-structure 4 | **kill now** (A2); WFRI's 170-Friday read cannot land before 2030 |

## 4. Next week's three bets (2026-11-02..11-06), screened on history locally

Chosen by expected information: each decides a whole line and the bar for every family, at 0 or 1 registry trial.

1. **H-16 range forecast on 2010-2017** (line 1). Not a return test. If GVZ/events/compression add >= 0.05 rho over
   HAR, the system gets a selector that concentrates risk on opportunity days for every family and makes H-17
   testable; if not, the selector ships on HAR alone and three inputs retire. P(pass) ~0.4. K +1 (screen).
2. **Exits** (line 2): (a) H-80 R2 on 2018-2025-09, evaluator runs it once by the frozen rule (discovery half: runner
   - fixed +0.039 R, t 2.4); **1 registry trial**, PD + PO raise 2026Q4 145 -> 146 under the owner's "backtest as much
   as needed"; P(adopt) ~0.3. (b) H-B pre-registered first, then screened on 2010-2017: the S5/Z4/Z6/Z7 entry rules
   with targets {1 R, 2 R, time-only} paired against random entries under the same exits, at the worse measured cost.
   It separates "wider exits pay" from "the week fell". K +3.
3. **When-to-trade map on random entries** (line 3): S0/ZC bracket geometry on 2010-2017 1m, net at the worse
   measured cost, 4 sessions x 3 HAR range terciles x 2 trend sides (with / against the 4h return) = 24 cells,
   pre-registered before any cell is computed, DSR at cumulative K. A positive cell would be the first net-positive
   evidence in the program and would move every family's bar; a clean null closes timing-only ideas. K +24.

## 5. Team feedback log (items 1-83: see `660d2db2`)

A = adopted, D = deferred (date), X = declined (reason).

| # | From | Item | Answer | What changed |
|---|---|---|---|---|
| 84 | performance-analyst 10-30 | Net by account; closed-trade record 0 on all accounts | **A** as the baseline (exploration is in the ledger by design) | section 1 |
| 85 | performance-analyst | Loss is entry direction, not costs or exits; strategies 65% short in a falling week | **A, verified** from aux.db (shorts +0.216 R t 3.4, longs -0.164 R) | raw demo P&L is not quoted as skill anywhere; matched read only |
| 86 | performance-analyst | No arm at its stop; signal-time cost ~2x measured, evaluator must rule | **A**: evaluator rules by 11-06; until then every history screen uses the worse figure (item 72) | line 6 |
| 87 | performance-analyst | H-A trend filter on S5/Z4/Z7 | **A, reshaped**: tested first on random entries (bet 3), so it is a timing/side mechanism, not three more arm variants; arms only if a cell passes | line 3, bet 3 |
| 88 | performance-analyst | H-B wider targets / time exit vs control under the same exit | **A** as bet 2b, pre-registered before the screen | line 2 |
| 89 | performance-analyst | icm-probe balance snapshots 20-23 Oct equal icm-demo's (account mixing) | **A**: data-engineer checks by 11-04; fills from 20-23 Oct stay out of every read until cleared (matches item 56) | — |
| 90 | performance-analyst | icm-demo lost £39 pre-reset (19-23 Oct), in no readable record | **A**: finance-auditor reconciles to the broker by 11-06 | — |
| 91 | performance-analyst | Watch Z4P (-0.15 R, 76) and Z2P (-0.20 R, 27) | **A**: Z2 under A2 (11-13), Z4 at its 11-23 read | table 3 |
| 92 | coordinator (ADR 0058 run) | 0 survivors; change grammar or search space? | **A**: grammar v2 (when/side/exit primitives), absolute net-R floor, measured costs, no nightly reruns on v1, freeze at 11-20 if still 0 | A4, line 4 |
| 93 | strategy-researcher (TV parser audit 10-30) | Parser fails: direction 53%, levels 67%; 0 of 60 ideas have entry + stop | **A**: no scoring until a re-audit passes; retire the line at 11-13 if it does not | line 7 |
| 94 | research director (self) | Kill-only futility and readability rules (A1, A2), disclosed as written after interim reads | evaluator + quant sign by 11-04 | table 3 |

## 6. Owner-only items

None this cycle. The +1 registry trial (bet 2a) is a PD + PO budget decision under the owner's standing directive.
