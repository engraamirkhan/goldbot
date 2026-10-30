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
