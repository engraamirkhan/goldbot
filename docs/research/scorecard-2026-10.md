# Research scorecard: October 2026

The first monthly scorecard (research director; format in `.claude/agents/research-director.md`). Written
2026-10-10, base `1bcfe1c`, covering 2026-10-01 .. 2026-10-10. Next issue: early November 2026.

**One line: no strategy has passed, the demo is not trading, and the north-star is not closer in £ terms. This month
built the tools and the plan to measure it honestly. Nothing here is a profit forecast.**

## 1. North-star KPIs (ADR 0004 §6)

| KPI | This month | Last month | Note |
|---|---|---|---|
| Monthly average £/day | **none: no trades** | — | The demo terminal has not run (BACKLOG 1); the engines propose nothing until an agent passes the gates (by design) |
| Monthly % return (time-weighted, deposits excluded) | none | — | Account £1,000 GBP demo, read from MT5 (ADR 0004) |
| Days at £50 or more / best-day histogram | 0 / empty | — | — |
| Opportunity days captured | not measurable | — | The day classifier (H-16) and the base-rate report (BACKLOG 30) do not exist yet |
| Max drawdown % / days lost to the daily cap | 0% / 0 | — | No trades |
| Shadow book (net R by timeframe, family, session) | **empty** | — | No champion or challenger is running; the performance analyst has nothing to report. This scorecard is consistent with that: every figure above is "none" |

## 2. Distance to the next milestone and to the north-star

| Target | Where we are | Distance | What closes it |
|---|---|---|---|
| **M1: first trade possible (15m, £825 at the 1% min-lot cap)** | Equity £1,000 is above £825 | **Equity: reached. In practice: blocked** by (1) BACKLOG 28 currency fix (the gate computes the 15m min lot at 1.09% instead of 0.82% and refuses it), (2) BACKLOG 25 min-lot exception, (3) a Raw-spread account not yet confirmed, (4) the terminal not yet running (BACKLOG 1), and (5) **no strategy that has passed the research gates** | 1–4 are engineering and owner steps; 5 is research, and the Q1/Q2 plans are the route. The earliest a 15m trial can run is Q2 2027 (`preregistration-2027Q2-draft.md`) |
| M2: 1h (£1,650) | £1,000 | +65% | Deposits (≈ 3 months at £250/month) or returns (≈ 17 months at a sustained 3%/month, which no strategy has shown) |
| M3: 4h (£3,270) / M4: 1d (£7,900) | £1,000 | ×3.3 / ×7.9 | As M2 |
| North-star as a monthly average (£50/day ≈ £1,050/month) | £0/day | Needs ~£21k–£53k of equity at a sustained 2–5%/month (ADR 0004 §6) | Not a near-term research target. £50 *days* at £1,000 need a ~6 R runner on a trend or news day (OD §0) |

## 3. What moved this month

**Research evidence (the Q4 2026 budget, spent 20/20 by 2026-10-09; reports on issues #37–#53, summaries on #34):**
- No gross edge: mean reversion (15m), session-open breakout (15m), trend pullback (1h), range breakout (1h),
  intraday momentum (London, NY). All retired (R-01..R-05, `config/settings.yaml` `research.retired_families`).
- tsmom 1h/4h: gross +0.06 R (t 2.6 / 2.4), net −0.079 R (1h) and −0.014 R (4h, before swap). Failed; only slow TSMOM
  (H-01) stays alive (R-06).
- Meta-models: OOF AUC ~0.50 (R-07). Lookahead check clean on every run; holdout untouched.
- **Verdict count this month: 20 trials, 0 passes.** This is the expected result for these families (SoA C3).

**Research documents (no trials, no budget):** state of the art (SoA), indicator survey, XAUUSD trader playbook with
a ranked gap list, opportunity days and runner exits, hypotheses H-01..H-23 with the retired list and the
multiple-testing ledger, the Q1 2027 pre-registration draft (H-01 and H-04 parameters frozen before any outcome,
event recounts), ADR 0004 (account, daily limit, north-star, milestone ladder; amended to £1,000 / £50), ADR 0006
(evidence across feature versions), and this month's research plan, Q2 2027 draft and scorecard.

**Research infrastructure (merged; tools, not results):**
- Trial integrity: reservation of the pre-registered Q1 queue in code (and in every runner), queued rows that cannot
  be backdated or moved between quarters, screen event floor with an "inconclusive" outcome, CPCV and PBO as
  veto-only (ADR 0003), CUSUM calibrated to a 5% quarterly false-alarm rate.
- Discovery: one-trial feature discovery with fold-internal stability selection (`research_pass.py --discover`).
- Specialists ready for Q1: slow TSMOM preset (H-01), `asia_drift` (H-04).
- Data and features: FRED macro pipeline (real yields, dollar, GVZ), COT and GLD positioning (opt-in), survey and
  trader-toolkit features, measured broker costs as a release asset (`costs-v1` once the VPS publishes), DST-aware
  sessions (feature version `f-079deacc61`), broker clock and sessions.
- Risk evidence tools (need data): drawdown Monte Carlo (G-5), minimum-lot feasibility (G-7), owner trade-history
  analyser (private; research seeds only, in-sample).

**Delivery that research depends on (program director's domain, listed for context):** live exit policies with
label/shadow parity, approval card, alerts, drift and health, backups, SQLite state store, hosting on Oracle VMs.

## 4. What did not move

- No strategy is net positive after costs. The only gross edge (fast tsmom) is smaller than its costs.
- The demo has not placed a trade or recorded a shadow trade. There is no performance-analyst report.
- The currency bug that blocks the only trade £1,000 can take (BACKLOG 28, HIGH) is not fixed yet.
- Owner rulings still open from earlier reviews: ruling A (daily-signal event floor), the H-18 event floor, the
  Raw-spread account, the deposit plan, the projected percentage caps, the material feature version (ADR 0006).
- The base rate of £50-sized days (a ~$66 directional move) at today's prices is unmeasured (BACKLOG 30).

## 5. Pipeline and budget

| Quarter | Budget | State |
|---|---|---|
| Q4 2026 | 20/20 used | closed: 0 passes |
| Q1 2027 | 20: 9 reserved (5 queued rows + 4 contingent H-02 survivor slots; 8 if H-06 is withdrawn), 11 open under the E-5 cap | draft, freeze commit 2026-12-14, rows 2026-12-18..23; nothing may run before 2027-01-01 (updated 2026-10-11; was "13 + 7", superseded by ADR 0007) |
| Q2 2027 | 20: 4 planned, ≤ 3 conditional, 6 for Q1 follow-ups (H-24 added to that list, conditional on H-04), 7 free reserve | draft written this month; nothing reserved in code |

Multiple-testing ledger: unchanged (`hypotheses.md` C). DSR trial count for the next trial: 20, plus Q1 trials and
H-02's K_eff as they are recorded.

## 6. Risks raised this month

1. **The tradeable timeframe has the worst evidence.** At £1,000 only 15m can trade, and every 15m family so far had
   no gross edge. The Q2 draft spends its new trials there anyway (H-17, H-14, H-18), because nothing else can reach
   the demo this year; if all fail, the plan records that £1,000 has no research-backed trade and puts the options
   to the owner (`research-plan.md` §7).
2. **High-opportunity days are the ones £1,000 cannot take.** A higher ATR raises the minimum lot's risk; above
   ATR(15m) ≈ $8.87 the gate refuses the trade. Every Q2 trial reports the £1,000-tradeable share so this is seen,
   not discovered later.
3. **Historical event calendar missing.** The tier-1 archive is forward-only, and research loads no events, so
   event-based inputs (H-16) and H-18 cannot run on 2010–2025 until a historical schedule exists (E-1).
4. **Reservation arithmetic.** Rows written once a quarter has started cannot draw on the reservation, so a setting
   above the number of queued rows locks the excess slots for the quarter. Resolved for Q1 by ADR 0007 (13 → 9 with
   contingent slots); the same rule sets Q2's value at its freeze.

## 7. Next month (no trials; the Q4 budget is spent)

- Program director: BACKLOG 28 and 25 (first trade possible at £1,000), BACKLOG 1 (terminal running, so the shadow
  book and `costs-v1` start).
- Data engineer: E-1 historical tier-1 schedule; BACKLOG 30 base-rate report (frequency of ≥ 1 and ≥ 1.5 × ATR(1d)
  days by weekday and session).
- Quant reviewer: rulings Q-1 (H-17 overlap with R-02/R-05), Q-2 (H-16 evaluation scheme), Q-3 (H-14 survivor unit).
- Strategy researcher: Q1 freeze checklist (event counts owed from data, `macro-v1` present, rulings recorded).
- Performance analyst: first report once the demo terminal runs.

## 8. Needs the owner

Listed with options and recommendations in `research-plan.md` §9: the H-18 event floor (recommend: pool with tier-2
events once they exist), ruling A (Q1), the deposit plan (decides when 1h research rises), confirming a Raw-spread
account (without it nothing can trade at £1,000), the projected percentage caps, and the material feature version.
The trial budget stays at 20; raising it is not recommended.

## 9. Mid-month update (research director, 2026-10-11)

KPIs unchanged: no trades, no shadow book, no performance-analyst report, so every figure in section 1 is still
"none". What changed is the readiness picture, checked against the repo, the settings and the shared registry:
- **Q1 2027 readiness list** written into `preregistration-2027Q1.md`: 5 queued rows (H-02; H-01, H-04 and the 4h
  continuity trial with `rule_id` H-01 / H-04 / C4H; H-06), 4 survivor slots on H-02's row, no Q1 follow-up or
  holdout slots, the owed counts, and the freeze checklist, each with an owner and a date.
- **Found missing:** (1) `research-v1` holds no `preregistered` row or slot yet (20 Q4 rows only); (2) releases
  `macro-v1`, `calendar-v1` and `costs-v1` do not exist; the macro and calendar workflows have never run; (3) no
  command writes queued rows or slots (BACKLOG 47); (4) H-06 has no implementation (BACKLOG 48). It now has a written
  withdrawal rule decided before any outcome: not merged and counted by 2026-12-04, or below the daily floor, means
  it is withdrawn and the reservation becomes 8; (5) the retired lists for R-01 and R-03 name one trial each, where
  the registry holds four each (BACKLOG 49; the total of 20 and the DSR count are unaffected).
- **Q2 draft:** the three quant rulings are written into the rules (H-17 literal blackout; [−15, +30) on the bar
  close; H-14 cost prior = Dukascopy bar spread + settings extra cost). Ten pins (P-1..P-10) are owed before the
  first count-owed-events run on `main`. P-1 matters most: the count script in review counts H-17's non-literal
  entries.
- **Session coverage** (`hypotheses.md` A4): London and New York are covered at 15m by H-17, H-14 and H-18. Tokyo is
  covered only by H-04 at 1h and by multi-day holds, none tradeable at £1,000. The Tokyo 15m gap is recorded without
  a trial (owner's click at UK night, widest spreads, H-04 already holds the one mechanism). The Western leg of the
  Asia/West mechanism becomes H-24, conditional on H-04 continuing.
- **Owner's manual method:** a study brief (`owner-method-study.md`) turns the MT5 export into seeds in R and $/oz,
  never in sizing. Owner-seeded configurations are confirmed by forward shadow, not by the holdout (the owner's
  trades probably fall inside it). At most two seeds a quarter become trials.
- **Multiple-testing ledger:** unchanged (20). H-24 adds one trial only if it runs.

## Owner rulings recorded (2026-10-11; source: owner approved the research director's recommendations, 2026-10-11)

- **Ruling A:** daily-signal event floor 150, retire-only (`research.screen_min_events_daily: 150`). H-01 can now
  retire slow TSMOM at 150+ events; it cannot promote it, because the gates need >= 1,500 candidates. Code check: a
  daily screen at >= 150 events can still read `pass`, and that permits model research only (Q1 pre-registration,
  "Owner rulings").
- **Budget 20** unchanged; **no consensus feed** (H-11 blocked); **no other instruments**; the **conservative K_eff
  default** confirmed.
- **costs-v1:** published by 2026-12-11 if the demo terminal runs, otherwise "priors accepted" on 2026-12-14.
- **Owner's trade history:** complete export, run locally, summary only.
- **H-06:** reading-rule pins G1-G4 recorded. Withdrawal is likely even at 150 (quant estimate of the lower bound:
  about 80-130), so the Q1 reservation is planned at 8.
- **Q2 draft:** P-1 resolved; P-2 (literal first breach) and P-5 (cost source pinned at the freeze, the same for
  gates and labels) adopted.
- Open owner items now: the H-18 event floor (O-1), the deposit plan, the Raw-spread account, the projected
  percentage caps and the material feature version. KPIs, budget and ledger unchanged.

## Research director decision: daily-signal rows under the strict floor (2026-10-11)

Under the strict retire-only floor (150 retires, 1,000 passes), H-01 (lower bound 154) and H-06 (about 80-130) can
never read "pass". **Decision (c):** keep H-01 as the slow-trend family's retire test, written only if its recount is
>= 150. Withdraw H-06 if it is below 150, which is the expected case. Q1 reservation: 8, or 7 if H-01 is withdrawn too.
Reasons:
- H-06 can only be inconclusive.
- H-01 can end a lane and measures net R after costs for the best-supported hypothesis, for one trial that displaces
  nothing (Q1's open budget is capped by E-5).
- Stated before any outcome: H-01's screen has low power at 154-400 events, so a "stop" is recorded as "retired at low
  power", reinstatable by out-of-sample evidence.
No owner confirmation is needed (delegated, within budget, no rail touched). Full reasoning: `preregistration-2027Q1.md`,
"Research director decision". Ledger: unchanged until a trial runs.

## Not verified / left out

- "What moved" is read from the git history and HANDOFF on base `1bcfe1c`, not from CI or the dashboard.
- The trial results are quoted from HANDOFF and `hypotheses.md`, not re-run.
- Distances use ADR 0004's estimated ATR, gold price and GBPUSD; the M2 timings are arithmetic, not forecasts.
- No code, settings, trials, budget, holdout or the Q1 pre-registration were touched.

## Weekly research read, 2026-10-30 (research director, first cycle)

Sources: `goldbot run perf-report --from 2026-10-19 --to 2026-10-30` on all three accounts (17:24 UTC); aux.db
`exploration_trades` re-derivation (read-only); performance-analyst demo read 2026-10-30; discovery ledgers
`~/tmp/autodisc-run-*`.

- **Winners: none.** Matched excess vs the random control (n >= 30 a side): probe session_vwap_fade +0.21 R (54,
  -0.09..+0.51), icm nr4_break +0.35 (47, -0.27..+0.96), icm round_reject +0.34 (85), icm halfhour_persist +0.25
  (107), all not distinguishable.
- **Losers beside them:** probe round_reject -0.15 (76), probe ema_pullback -0.03 (191), probe halfhour_persist
  -0.02 (103); small-n: specialist arms -£44 on icm-demo, asia_drift -£48.65 (3).
- Control: probe random_bracket +0.025 R (354), re-derived exactly. Raw strategy P&L is a short tilt in a falling
  week (shorts +0.216 R, longs -0.164 R), not skill.
- **Discovery:** K_search 288 a TF (~5.3k null specs), 0 survivors on 15m/1h/4h; grammar v2 ordered (A4).
- **Kills this week (on A1/A2 sign-off):** ~50 arm ids that cannot reach a read this year or whose read is void
  (research-plan section 3). **Scales:** none. K ~360 by the arm-id rule.
- **Bets next week:** H-16 range forecast screen; exits (H-80 R2 confirm + wider-target screen vs control); the
  when-to-trade map on random entries.

## Weekly research read, Friday 2026-10-30 (research director, cycle 2)

Sources: the four result documents below, re-derived where marked; demo figures unchanged from cycle 1 (perf-report
2026-10-19..10-30; icm-probe 10-19..10-25 now excluded, login mix).

**History screens this week (2010-2017 unless stated), 4 bets, 4 fails:**

| Bet | Result | Verdict | Source |
|---|---|---|---|
| H-16 range forecast | rho gain +0.201 (95% 0.145..0.260), quintile ratio 1.437 < 1.5 (re-derived 1.0744/0.7478) | FAIL; GVZ/events/compression retired as range inputs; O-1 cancelled | `opportunity-days-screen-2026-11.md` |
| H-80 R2 box runner (2018-2025-09, n 5,355) | runner - fixed gross -0.018 R (t -1.12); net E0 -0.199, E4 -0.288, E8 -0.206 R | FAIL; BXR killed | `prereg-exits-adaptive-2026-10-26.md` s.8 |
| H-82 exits on scalp entries (337,341 entries) | DiD -0.0017..+0.0009 R (bar +0.05); arm gross -0.011..+0.010 R; net -0.04..-0.06 R (broker cost), -0.11..-0.13 R (cost of record) | FAIL, all 5 variants; re-derived +1 R shares 4.1-12.7% from the pairs files | `hb-exits-screen-2026-11.md` |
| H-83 when-map (493,694 random trades) | 0/24 cells net > 0 (-0.058..-0.109 R); H-A -0.008 R (t -2.7) | FAIL; timing at the scalp clock retired | `when-map-screen-2026-11.md` |

- **Winners: none**, on history or demo. Demo matched excess (cycle 1, all not distinguishable): probe
  session_vwap_fade +0.21 R (54), icm nr4_break +0.35 (47), icm round_reject +0.34 (85), icm halfhour_persist +0.25
  (107). **Losers beside them:** probe round_reject -0.15 (76), probe ema_pullback -0.03 (191), probe
  halfhour_persist -0.02 (103). History now says the "winners" are drift: the same entries gross +/-0.011 R on 2010-2017.
- **Kills:** BXR* (A2 after R2 FAIL); the A2 "kill now" list (~68 ids, demo-reset PR) plus XC15; S5/Z4/Z6/Z7 on A5
  sign-off (draft, 11-04). **Scales:** none.
- **Discovery:** v1 0 survivors on 15m/1h/4h; v2 last run on 1h/4h/1d by 11-20 or generation freezes.
- **TradingView:** parser audit 2 FAIL (direction 83.3%, levels 76.7%); one last attempt under amendment T-1
  (direction only, abstain class), else parked 11-13.
- **K ~1,256** (360 arm ids + 864 discovery specs + 31 screen looks + 1 registry trial).
- **Bets next week:** H-84 macro 4h/1d direction (K +4), H-17 trend day + runner (K +2), discovery v2 on 1h/4h/1d
  (K_v2 declared). All 2010-2017 first.

## Weekly research read, week 2026-10-26..10-30 (research director, written 2026-10-31)

Sources: `goldbot run perf-report --from 2026-10-26 --to 2026-10-30` on the brain (run 2026-10-31 13:23 UTC; learning
section, matched by entry day x session x side); `state/aux/daily_review/2026-10-26..30.json` (costs, volume);
`state/scheduler.json` (tournament, research_director, autodisc_nightly, agents_weekly); the history verdict documents
named below; discovery logs `~/tmp/autodisc-run-{15m,1h,4h}.log`. **Re-derived from raw data:** icm-probe random
control from aux.db `exploration_trades` less `evidence_exclusions`: n 358 (S0 270, S0R 88), net +£19.69, +8.83 R,
mean +0.025 R, win 52%. This matches the report exactly; 49 voided rows were left out.

**One line: 0 families beat the random control on demo, and all 6 history bets this week failed. Since 31 Oct
07:15 the demo runs only the controls and the cost arms (ADR 0061).**

### 1. Demo book: matched excess net R vs the random control (90% interval, n), 26-30 Oct

| Account | Family | n | Mean net R | Excess vs control | Reading |
|---|---|---|---|---|---|
| icm-probe | session_vwap_fade | 54 | +0.18 | +0.22 (-0.08..+0.52) | not distinguishable |
| icm-probe | ema_pullback | 195 | +0.17 | -0.02 (-0.21..+0.17) | not distinguishable |
| icm-probe | halfhour_persist | 105 | +0.15 | -0.03 (-0.34..+0.28) | not distinguishable |
| icm-probe | round_reject | 77 | -0.05 | -0.14 (-0.40..+0.12) | not distinguishable |
| icm-demo | nr4_break | 47 | +0.08 | +0.35 (-0.27..+0.96) | not distinguishable |
| icm-demo | round_reject | 87 | +0.05 | +0.27 (-0.31..+0.85) | not distinguishable |
| icm-demo | halfhour_persist | 107 | +0.06 | +0.25 (-0.27..+0.77) | not distinguishable |
| icm-demo | round_break | 62 | -0.15 | +0.15 (-0.39..+0.68) | not distinguishable |
| vantage-demo | halfhour_persist | 98 | +0.04 | +0.08 (-0.37..+0.53) | not distinguishable |
| vantage-demo | nr4_break | 44 | +0.20 | +0.01 (-0.43..+0.45) | not distinguishable |
| **Losers under 30** | probe hurst_band_break -0.64 (13), opening_range_break -0.62 (6), sweep_absorb -0.68 (6), absorb_fade -0.52 (4), run_fade -0.25 (28); icm-demo asia_drift -0.36 (3, -£48.65), level_break -0.83 (2), mean_reversion -0.88 (2); vantage tsmom -2.97 (1, -£29.39) | | | | insufficient sample |
| **Small-n "winners"** | probe extreme_break +0.58 (13), clock_anchor +0.83 (4), tick_surge +0.21 (8 cells); icm-demo htf_reclaim +1.27 (12), three_horizon +0.19 (24) | | | | insufficient sample |

Controls (the null): icm-probe random_bracket +0.02 R (358); vantage-demo scalp_control -0.02 R (31); icm-demo
random_bracket -0.14 R (10) and scalp_control -0.21 R (24). Book net per account (exploration + control + cost
probe): icm-probe +£104.48, vantage-demo +£35.78, icm-demo -£37.71, all at 0.01 lot. **That is about +£20.5 a day,
and £39.16 of it came from the random controls, so it is drift and not skill.** Each account's exploration book
had positive net R (+50.5 / +8.5 / +15.2 R), but no family separates from the control in its own cells.

### 2. Kills and retirements this week (none scaled)

- **A2 kill-now list: 67 arm ids retired** at the 10-30 demo reset (entries refused, exits kept; `ed64463f`). The
  list covers S2/S3/MSA/I1-3/W1-3/MB, the specialists X*/VX*, KA15, OBR15, KSQ, DON, C30, PDC, F2 and S6. **XC15**
  was killed with its retired arms.
- **BXR15/30/1H/4H (box runner)** were killed after the H-80 R2 confirm FAIL (A2).
- **A5 (signed 10-30):** S5A/S5B (ema_pullback) and Z4D/Z4P (round_reject) retired. History showed the same entries
  had a gross of -0.009..+0.008 R across exits on 140k entries.
- **ADR 0061 (owner rule, live from 31 Oct 07:15):** every strategy arm without a passing screen and confirm on
  record is refused new entries. None has one, so Z3/Z5/Z6/Z7 (halfhour_persist, nr4_break, round_break,
  extreme_break), S1A/S1B (session_vwap_fade), Z1P/Z2P/Z9D/Z10D and the BX* twins all stop. I record them as
  **retired on demo**: the 11-13 A2 bar and the 11-23 reads are moot. Only a history pass reopens a family, under a
  new pre-registration. Still running: the random controls (S0, S0R, I0, W0, VI0, C1H, VC1H, ZCD, ZCV), the cost
  probes and the cost arms F1A/G1A. WKS0/WKI0 (weekend 24/7) are pre-registered as cost measurement only and are
  not yet enabled.

### 3. History verdicts this week (2010-2017 screens unless stated): 6 bets, 0 passes

| Bet | Key number | Verdict |
|---|---|---|
| H-16 range forecast | quintile ratio 1.437 < 1.5 (rho gain +0.20) | FAIL (`opportunity-days-screen-2026-11.md`) |
| H-80 R2 box runner, confirm 2018-2025-09, n 5,355 | runner minus fixed gross -0.018 R (t -1.12) | FAIL, BXR killed |
| H-82 wider exits on scalp entries, 337k entries | difference-in-differences vs random within +/-0.002 R | FAIL, 5/5 variants |
| H-83 when-map, 494k random trades | 0/24 cells net > 0; trend side -0.008 R (t -2.7) | FAIL |
| H-84 macro 4h/1d direction (4 variants) | gross t -0.59 / 0.47 / 0.84 / 0.62; net -0.22..-0.05 R | FAIL, retired R-16 (`h84-macro-screen-2026-11.md`) |
| H-17 trend day + runner, 671 events | V0 gross -0.003 R (t -0.05), net -0.35 R, 0/6 years | FAIL, retired R-17 (`h17-screen-2026-11.md`; the H-17 row's "R-16" is corrected to R-17 in this PR) |

**Discovery:** v1 searched 288 specs per timeframe on 15m/1h/4h (K 864) and found **0 survivors**. Its SPA p and PBO
were 0.000/0.00 on 15m, 0.002/0.09 on 1h and 0.570/0.62 on 4h. The explore-only v2 run on 1h/4h/1d has not run yet.
The brain's nightly job is off by design (Mac only, ADR 0058). **K ~1,264** = ~360 arm ids + 864 v1 specs + 37 screen
looks (H-16 1, H-82 5, H-83 25, H-84 4, H-17 2) + 1 registry trial + 2 weekend controls.

### 4. Costs: measured cells vs the 10-25 prior (`cost-model-1m-2026-10-25.md`)

| Cell (week) | Fills | Spread | Entry slip | Round trip | Stop overshoot | Prior |
|---|---|---|---|---|---|---|
| ICM all | 1,373 | $0.085 | -$0.017 | **$0.208** | **$0.084** (618 stop exits) | spread $0.079, slip -$0.055, RT $0.144 central / $0.23 conservative, overshoot $0.70 (n 14) |
| ICM Asia / London / NY | 317 / 436 / 620 | 0.088 / 0.083 / 0.086 | +0.013 / -0.032 / -0.023 | 0.237 / 0.200 / 0.198 | 0.049 / 0.068 / 0.112 | as above |
| Vantage all | 195 | $0.105 | +$0.054 | **$0.226** (London 0.306) | ~0 | none (first week) |

- Round trips cost more than the central prior: ICM is +44%, but still inside the conservative case.
- Cost rose over the week. ICM went from $0.163 (10-26) to $0.250 (10-30) as entry slippage turned from favourable
  to adverse. Vantage went from $0.173 to $0.317.
- **Stop overshoot is 8x smaller than the prior** ($0.084 vs $0.70). Resting broker stops settle the 10-25 worry.
- The session slippage refits (ICM Asia, London and NY) were set to $0.15.
- **Blind spots:**
  - perf-report's own Costs, Days and North-star sections read only the closed-trade record (0 trades), so they show
    "not recorded" for the learning book.
  - `registry_sync` returns HTTP 403 on both the tournament and research_director jobs, so the H-80 R2 row is still
    not in the brain registry.
  - The weekly agents job skips because there is no API key.

### 5. Three bets for next week (2026-11-02..11-06), with expected information

1. **Discovery v2, one run each on 1h, 4h and 1d** (strategy-researcher, ml-researcher for the null). Macro and HAR
   enter as gate primitives, with an absolute net-R floor, and K_v2 is declared before the run. P(at least 1 survivor)
   ~0.1. Either result decides whether automated generation continues past 11-20 (A4); 0 survivors freezes it.
2. **Cost of record from measured cells** (microstructure-researcher with the data-engineer, by 11-06). Freeze the
   per-broker, per-session round trip and overshoot above as the figure every screen reads. Explain why the round
   trip rose about 50% in 5 days (session mix vs slippage drift). Then cut the cost probes to what the protocol
   needs. Every remaining edge question is cost-bound, so this changes the bar for every future screen.
3. **TradingView author ideas, T-1 re-audit** on a fresh seed by 11-13 (strategy-researcher staff): direction only,
   with an abstain class; precision >= 95% and coverage >= 50%, else the line is parked to the Q1 SoA refresh. It is
   the one untested information source, human discretionary calls, and costs no trial until the parser passes.
   Prior ~10-15%.

Alongside the bets (engineering, not research): fix `registry_sync` 403; make perf-report's Days, North-star and
Costs read the learning ledger; the weekly agents job needs an API key in the brain keyring (owner-only: a secret and a spend; until then the director writes this read by hand).
