# Research plan: the agenda against the north-star

Written 2026-10-10 by the research director (`.claude/agents/research-director.md`), base `1bcfe1c`. Refreshed every
quarter after the evaluator's verdicts, and whenever the account crosses a milestone of ADR 0004 section 4.

Inputs: ADR 0004 (account, milestones, north-star KPIs), ADR 0006 (evidence across feature versions),
`state-of-the-art.md` ("SoA"), `indicator-survey.md` ("survey"), `xauusd-trader-playbook.md` ("playbook"),
`opportunity-days.md` ("OD"), `hypotheses.md` (H-nn, R-nn), `preregistration-2027Q1.md` (frozen queue, not altered
here), `docs/BACKLOG.md`.

**Nothing in this plan runs a trial, spends or reserves budget, reads the holdout (2025-10-01 .. 2026-09-30) or
changes the Q1 2027 pre-registration.** Every number about the account is ADR 0004's estimate (gold $4,150, GBPUSD
1.33, ATR from ~22% annualised volatility), not a measurement.

---

## 1. Where we stand (honest baseline)

| Fact | Value | Source |
|---|---|---|
| Strategies that passed the research gates | **0** | hypotheses.md B (R-01..R-07): every Q4 2026 family had no gross edge or a gross edge smaller than its costs |
| Best gross edge found | tsmom 1h/4h, +0.06 R (t 2.6 / 2.4); net −0.079 R (1h), −0.014 R (4h, before swap) | SoA header, R-06 |
| Trial budget | Q4 2026 **closed at 55/55** (owner override; #88 H-48 STOP and #89 H-50 STOP were the last two). Q1 2027: **6 reserved** (program decision 2026-10-20: H-02 + C4H queued + 4 contingent H-02 survivor slots; H-01 and H-04 ran in the Q4 batch as #48 and #44, H-06 withdrawn 2026-10-16), **14 open** under the E-5 cap | hypotheses.md C; settings still say `"2027Q1": 8` until the reviewed change owed by 2026-10-27 (section 11.1) |
| True trial count K | **111** (2026-10-20): 89 trial rows + 21 screens of discovery #69 = 110 by the registry's own count, + 1 for H-49 stage 1's 0-trial look (K_eff +1, pre-registered, never written to the registry). H-53 is read at K ≥ 112; O-2/O-3 at K ≥ 113 | evaluator audit s.1 (audit file dated 10-23, read 10-20), ml-leakage-audit F5, strategy-researcher 2026-10-20 |
| Forward (shadow/live) evidence | **No H-52-admissible number exists.** The parity job (rollout 5b) is not on main and `perf_report` applies neither the 13:49 UTC quarantine nor parity; demo fills are cost and execution evidence only, never edge | evaluator audit s.3, performance-analyst 2026-10-20 |
| Account | £1,000 GBP demo (IC Markets SC), read from MT5 | ADR 0004 |
| What can trade at £1,000 | **15m only**, one 0.01 lot (£8.20 = 0.82% per R), and only after BACKLOG 28 (currency fix) and 25 (min-lot exception), on a Raw-spread account, while ATR(15m) ≤ ~$8.87 | ADR 0004 §2, OD §0 |
| Positions at once | 1 (combined-notional cap) | ADR 0004 §2 |
| Worst day at £1,000 | ~£17 (two full losses, then the 1.5% supervisor cap) | ADR 0004 §3 |
| Demo trading | not running yet (terminal smoke test BACKLOG 1 pending; no shadow or live trades recorded) | BACKLOG 1 |

The honest reading: **the system has no edge yet, and the only timeframe it can trade at £1,000 (15m) is the one
where our own trials and the literature are most negative** (R-01, R-02, R-05; SoA C3: small-tick contracts lost
short-term trend after 2009). The research agenda has to face that directly, not around it.

## 2. The north-star and how research can move it

The owner's goal (ADR 0004 §6): **£50 or more on days the market offers it, less or nothing when it does not.** At
£1,000 a £50 day is ~6.1 R at the 15m minimum lot: a runner on a trend or news day, never an average. As a monthly
average £50/day needs ~£21k–£53k of equity (ADR 0004 §6). Research never targets a daily £ figure and never proposes
sizing up to reach one; the rails (RiskGate only path, 1% clamp, daily caps, owner confirms entries, exits never gated)
are not research variables.

| KPI (ADR 0004 §6) | What research can do for it | What research cannot do |
|---|---|---|
| Monthly average £/day and % | Find a net-positive edge (nothing else raises the mean); cut costs per trade (H-13, H-19) | Raise the £ figure at a given equity: £ per R is fixed by the minimum lot until the account grows |
| Opportunity days captured | Forecast which days have range (H-16, R-graded evidence) so the system is in the market on them; enter trend days early (H-17, H-18) | Forecast direction from the range forecast; a range score with no entry edge only widens the outcome distribution (OD §1, "most important caveat") |
| Best-day distribution (£50+ days, best-day histogram) | Runner exits on flagged days (H-17 V1, BACKLOG 31) so a correct trade can reach 6 R | Make 6 R days frequent; they are a few a month at best, unmeasured (BACKLOG 30 base-rate report) |
| Max drawdown % | Stand aside on low-score days (H-19), where fixed USD costs are the largest share of R (SoA A7); drawdown expectation from the ruin tool (G-5) | Avoid the 8% stage at £1,000 without an edge: with E[R] = 0, P(8% stage) is 86% within 3 months (ADR 0004 §5) |

## 3. How the agenda is ranked

Each hypothesis is scored on five questions, in this order of weight. The scores are judgement, written down so they
can be argued with; no data was read to make them.

1. **KPI reach now:** can its trades be taken at the current milestone (M1, 15m only)? A hypothesis on a timeframe the
   account cannot trade contributes nothing to the KPIs until the milestone that unlocks it.
2. **Evidence grade** (R / S / P / F, as the playbook): replicated mechanisms first.
3. **Cost to beat:** plausible gross edge as a multiple of the round trip in R (0.14–0.39 R per trade measured).
4. **Information value:** does its verdict decide other items (a gate for several hypotheses ranks higher)?
5. **Trial cost**, including the multiple-testing cost it adds to every later trial (K_eff).

### Research priority follows the milestone ladder

| Milestone (ADR 0004 §4) | Equity (1% min-lot cap) | Unlocks | Distance from £1,000 | Research stance |
|---|---|---|---|---|
| M1 | £825 | 15m (Raw account) | reached by equity; blocked by BACKLOG 28 + 25, Raw account unconfirmed, terminal not running | **Primary.** Every Q2 trial that can produce a live trade is 15m |
| M2 | £1,650 | + 1h | +65% | **Lead-time research only.** Research to paper takes 2–3 quarters (pre-register, run, shadow record, paper gate), so 1h work starts in earnest when M2 is expected within ~3 quarters. Without deposits that is years away (ADR 0004 §6: at 3%/month, +65% takes ~17 months); with £250/month deposits it is ~3 months. The owner's deposit plan therefore decides when 1h moves up |
| M3 | £3,270 | + 4h | ×3.3 | Evidence-gathering: Q1's H-02 (4h discovery) and the 4h continuity trial are already frozen. A 4h pass would sit in shadow until M3 |
| M4 | £7,900 | + 1d (needs 1d promoted to a decision timeframe) | ×7.9 | Evidence only: H-01 (slow TSMOM) is the best-supported idea in the literature, which is why it stays in Q1, but it is shadow-only until M4 (ADR 0004 decision 7) |
| — | — | 1w | context only, never a decision timeframe | No trials. 1w enters only as context features (trend state, ranges) |

The rule this table encodes: **spend trials where the next live trade can come from (M1), keep a small evidence lane
on the timeframes the literature supports (4h, 1d) because their payoff arrives as the account grows, and start 1h
research when the deposit plan brings M2 within reach.** Deposits, not returns, move the ladder fastest at this size.

## 4. The ranked agenda

Status as of 2026-10-10; **re-ranked 2026-10-20 in section 11.4** (1-5: H-16, H-13, H-19, H-17, H-14; H-28 withdrawn, H-24 lapsed, H-54 added). "Rank" is the unified north-star rank now used in `hypotheses.md` (section A3 gives the
reasons for each change). Q1 2027's running order is fixed by its pre-registration and is not changed by this rank.

| Rank | ID | Item | TF | Tradeable at | KPI it serves | Evidence | Trial cost | Where it runs |
|---|---|---|---|---|---|---|---|---|
| 1 | H-16 | Opportunity score = day-range forecast (HAR + GVZ + events + compression + gap) | daily, applied to 15m | M1 (as a selector) | opportunity days, drawdown | R (range) | 1 | Q2 draft |
| 2 | H-19 | Stand-aside overlay: net R by score band on every trial | all | M1 | drawdown, £/day | R (fixed USD costs) | 0 | Q2 overlay |
| 3 | H-13 | Volatility-forecast cost filter | all | M1 | £/day (net R) | R (costs) | 0 | Q1 overlay (frozen), continued in Q2 |
| 4 | H-17 | Trend-day continuation with a runner, on high-range days | 15m | **M1** | best days, opportunity days | S (equities, net) · N for gold | 2 (V0, V1) | Q2 draft, after a quant ruling |
| 5 | H-14 | 15m scalping discovery (sweeps, levels, session flow, news jumps), cost-gated | 15m | **M1** | £/day | P/S (Osler, microstructure) | 1 + ≤ 2 survivors | Q2 draft |
| 6 | H-02 | Feature discovery, 4h (whole indicator universe in one trial) | 4h | M3 | gate for H-03/05/07/08/10 | — | 1 | Q1 (frozen) |
| 7 | H-01 | Slow TSMOM, 1d signal, 4h execution | 1d/4h | M4 | £/day (later) | R | 1 | Q1 (frozen) |
| 8 | H-18 | Post-blackout event continuation (NFP/CPI/FOMC/PCE) | 15m | **M1** | best days, opportunity days | R (event range) · S (FOMC drift) | 1 | Q2 draft, only if the event floor is ruled and a historical calendar exists |
| 9 | H-04 | Asia-session drift | 1h | M2 | £/day | S (weak) | 1 | Q1 (frozen) |
| 10 | H-06 | Macro-conditioned slow TSMOM | 1d/4h | M4 | £/day (later) | R contemporaneous, broke 2022 | 1 | Q1 (frozen) |
| 11 | H-08 | Liquidity-sweep reversal | 15m–1h | M1 (15m) | £/day | P/S | via H-02 / H-14 | Q1 survivor rows; inside H-14 at 15m |
| 12 | H-05 | Stop-cluster levels ($10/$50/$100, prior extremes) | 15m–4h | M1 (15m) | £/day | S (FX) | via H-02 / H-14 | as H-08 |
| 13 | H-10 | Post-jump behaviour (news vs no-news jumps) | 15m–4h | M1 (15m) | best days | — | via H-02 / H-14 | as H-08 |
| 14 | H-03 | Gold variance risk premium | 1d/4h | M4 | £/day (later) | S (monthly horizon) | via H-02 | Q1 survivor row |
| 15 | H-20 | Compression → expansion (NR4/NR7, inside day) | daily | inside H-16 | opportunity days | P/F | 0 | inside H-16 |
| 16 | H-21 | Early-session noise ratio | 15m–1h | inside H-16/H-17 | opportunity days | S | 0 | H-16 refresh diagnostic, H-17 mechanics |
| 17 | H-15 | Liquidation rebound after a dash for cash | 1d | M4 | best days (later) | episodes only | 0 until it survives | waits for D-8 and ruling A |
| 18 | H-07 | Equity-stress haven | 1d | M4 | — | R (haven ~15 days) | via H-02 | Q1 survivor row; ruling A |
| 19 | H-11 | Post-release continuation with standardised surprise | 15m–4h | M1 | best days | none for drift | 1 | blocked: consensus feed (owner C) |
| 20 | H-09 | COT speculative pressure | 1d | M4 | — | S, mixed | 0 (feature) | feature in H-02 |
| 21 | H-12 | GLD flow pressure | 1d | M4 | — | thesis-level | 0 (feature) | feature |
| 22 | H-23 | Overnight USD / real-yield shock → day range | daily | — | opportunity days | W | 0 until data | blocked: intraday USD (D+, owner) |
| 23 | H-22 | COT extremes predict volatility | daily | — | — | F | 0 (feature) | lowest |

**Why range-first leads.** Range is the one thing about gold's day that is forecastable with replicated evidence
(Corsi 2009; Cai et al. 2001; OD §1). It serves two KPIs directly (opportunity days captured, drawdown) even before an
entry edge exists, because fixed USD costs take a larger share of R on quiet days. It is also the selector every
15m idea on this list needs. It cannot create an entry edge, so it is ranked first as an enabler, not as a source of
profit.

**Why 15m ideas rank above better-evidenced slow ideas.** Only 15m can trade at £1,000. A slow-trend pass (H-01)
would be the best news research could deliver, but its trades are refused by the gate until ~£7,900. H-17, H-14 and
H-18 are low-probability (the 15m record is negative) but they are the only research that can put a trade on the
demo this year. Both lanes are kept: Q1 already funds the slow lane; Q2 funds the 15m lane.

## 5. Per-timeframe plan

| TF | Tradeable from | Our evidence | Literature | Plan for the next 12 months | Deliberately not doing |
|---|---|---|---|---|---|
| **15m** | M1 (now, after 28 + 25, Raw account) | Null: R-01 mean reversion, R-02 session-open breakout (+0.042 R, t 1.31), R-05 intraday momentum (gross ≤ 0). Costs 0.15–0.39 R per trade | Short-horizon trend gone on small-tick contracts since ~2009 (Kurth et al. 2026); stop-cluster microstructure (Osler) is the one mechanism with a reason | Q2: H-17 (range-conditioned trend day with runner), H-14 (cost-gated discovery, survivor unit = mechanism group), H-18 if ruled. Every 15m trial reports the £1,000-tradeable share (stop ≤ ~0.32% of price) | Unconditional 15m trend, breakout, momentum or reversion variants; meta-models over primaries with no gross edge (BACKLOG Rejected) |
| **1h** | M2 (£1,650) | Null: R-03 trend pullback, R-04 range breakout, R-06 tsmom 1h net −0.079 R | As 15m for trend; Asia drift is thesis/working-paper only | Q1: H-04 (frozen). 1h variants of 15m survivors become Q3+ neighbours. **Raise 1h priority when the deposit plan puts M2 within ~3 quarters** | New 1h trend/breakout variants |
| **4h** | M3 (£3,270) | tsmom 4h net −0.014 R before swap | Slow trend only at multi-week horizons | Q1: H-02 discovery and the frozen continuity trial (tsmom 4h, max_bars 12). Q2: only follow-ups of Q1 survivors (reserve) | Further fast-tsmom horizons (R-06) |
| **1d** | M4 (£7,900), and 1d must be promoted to a decision timeframe. **Conditional on volatility** (ADR 0004 §4, amended 2026-10-17): the £50 projected check refuses a 1.5 × ATR(1d) stop while ATR(1d) > ~$43, so 1d is research + shadow only | 1d tsmom had 454 events; H-01 counted 154–2,154 | Slow TSMOM is the only replicated trend horizon (MOP 2012, Hurst et al. 2017) | Q1: H-01, H-06 (frozen). Q2: H-01 model research or exit variant (C-5) only if H-01 continues (reserve). H-15/H-07/H-03 wait for ruling A | Promoting 1d to a decision timeframe before an H-01-family pass and M4 |
| **1w** | never (context only) | — | Monthly-horizon VRP and COT are better as features | Context features only (weekly ranges, trend state) | Any 1w trial |

## 6. Quarterly roadmap

| Quarter | Trials | Content | Status |
|---|---|---|---|
| Q4 2026 | 20/20 | Primary-signal screens of the original families | spent; all retired or failed (R-01..R-07) |
| Q1 2027 | 6 reserved (2 queued: H-02, C4H + 4 H-02 survivor slots; H-01/H-04 ran in Q4, H-06 withdrawn; amended 2026-10-20, section 11.1) + 14 open (E-5 cap: 7 live conditional rows + H-53 + 6 free reserve; O-7/O-8 impossible after H-04's STOP) | Queued: H-02, tsmom 4h continuity (H-01 and H-04 ran in Q4 as #48 and #44); Q1 survivor rows. **Open-budget round (2026-10-17):** H-16 (1), H-17 V0/V1 (2), H-14 (1) + ≤ 2 survivors, each only if its inputs land in time; H-24 and H-04 model research only if H-04 continues; a holdout scoring; 6 free reserve (was 3; section 11.5). H-13/H-19 overlays; H-A/H-B/H-C 0-trial. **Lane asks (2026-10-17):** scalping O-4 only, with its cost withdrawal rule (0.35 R); intraday 0 trials (H-24 pinned at the 09:00 New York bar close); swing 0 trials | draft, freeze by 2027-01-01 (`preregistration-2027Q1.md`, "Q1 open-budget round") |
| Q2 2027 | 4 planned + 3 conditional + 6 Q1 follow-up reserve + 7 free reserve | H-16, H-17 V0/V1, H-14 (+ ≤ 2 survivors) **only for the rows that did not run in Q1** (2026-10-17); H-18 if ruled; follow-ups of Q1 verdicts; H-13 and H-19 overlays. **Lane asks (2026-10-17), each from the free reserve by a written note and conditional:** IC-5 Asia→London continuation (1h; only if H-04 or H-24 continues) and SW-0 1d swing feature discovery (K_eff ≈ 16; swing is context features and retirements only, forward shadow keeps or retires a group and never promotes). K ≈ 77 + Q2 trials (≈ 83 on the H-14 ≈ 16 base), gate-1 t ≈ 4.1 | DRAFT (`preregistration-2027Q2-draft.md`), freeze by 2027-03-31 |
| Q3 2027 (outline) | ≤ 20 | Neighbours of Q2 passers only: H-17 with the full H-16 score (if H-16 passes), V2/V3 exits if V1 beats V0, 1h versions of 15m survivors if M2 is near; H-18 with tier-2 events if the calendar archive exists | not drafted |
| Q4 2027 (outline) | ≤ 20 | Quarterly SoA refresh decides; if every 15m trial through Q3 is null, see section 7 | not drafted |

## 7. What would change the plan (decided now)

- **H-16 fails** (no score beats HAR-only by 0.05 ρ): the day factor ships on HAR alone (OD §4) and GVZ, events and
  compression are retired as range inputs. H-17 is unaffected (its conditioning is HAR-only by design, Q2 draft).
- **H-16 passes but no 15m entry has a gross edge** (H-17, H-14, H-18 all null or fail): range-first stays as a
  stand-aside and risk tool, and the honest conclusion is recorded: **at £1,000 goldbot has no research-backed trade.**
  The options then go to the owner through the program director: stay in shadow and grow by deposits toward M2/M3,
  where 1h/4h evidence (H-04, H-02 survivors, H-01) can apply; or consider other instruments (owner decision D, SoA:
  large-tick contracts kept trend). Raising risk is not an option.
- **A Q1 survivor appears** (H-01 continue, H-04 continue, H-02 survivors): the Q2 Q1-follow-up reserve funds its
  next step first; Q2's new hypotheses do not displace it.
- **The account reaches M2** (deposit or growth): 1h variants of any 15m passer move ahead of new 15m discovery.

## 8. Dependencies for the agenda

| Kind | Item | Needed by |
|---|---|---|
| Engineering | BACKLOG 28 (currency-correct sizing), 25 (min-lot exception) | any 15m trade at £1,000 (not the research runs) |
| Engineering | BACKLOG 30a (`day_type` opt-in features with a fold-fit HAR hook) | H-16, H-17 |
| Engineering | BACKLOG 31 (`flat_at_local` clock exit through `policy_step`) | H-17 V0/V1, H-18 |
| Engineering | BACKLOG 32 (gate EV uses the measured mean winner) | H-17 V1 / H-18 **before shadow or live**, not before the research run |
| Engineering | BACKLOG 30b (day-score allocator input) | using H-16 live, only after H-16 passes |
| Engineering | BACKLOG 33 (opportunity-day evaluation of trials) | the £/day and capture metrics in Q2 reports (reported, not in any rule) |
| Data (new) | **E-1: historical tier-1 release schedule 2010-01 .. 2025-09** (scheduled times only, from the BLS, BEA and Fed published calendars). Today the calendar archive is forward-only (`goldbot/data/econ_calendar.py`, weekly feed archived since deployment) and research passes no events (`calendar_events` is opt-in, `goldbot/research/pipeline.py:27`), so tier-1 inputs would be NaN on 2010–2025 | H-16's event inputs, H-18 entirely |
| Engineering (new) | E-2: a 15m two-sided discovery candidate spec (screening specialist) with the H-13 cost gate as the candidate filter | H-14 |
| Engineering (new) | E-3: screening specialists `trend_day` (H-17) and `event_continuation` (H-18), rule-only like `asia_drift`. `trend_day` implements H-17's literal blackout rule: only the day's first breaching bar is a candidate, and if its close is inside the tier-1 blackout [−15, +30) min (on the bar close) the day has no entry | H-17, H-18 |
| Quant ruling | H-17's overlap with retired R-02/R-05; H-16's evaluation scheme; H-14's survivor unit | Q2 freeze |
| Owner ruling | H-18 event floor; ruling A (daily floor, still open for Q1); Raw account; deposit plan | see section 9 |

## 9. Needs the owner (options and a recommendation; nothing decided here)

**Decided 2026-10-11 (owner approved the research director's recommendations, 2026-10-11):** item 4 (ruling A) = a daily-signal floor of 150, retire-only, committed in
settings; item 6 (budget) = 20, unchanged. Also decided: no consensus feed (H-11 stays blocked); no other
instruments; the conservative K_eff default confirmed; `costs-v1` published by 2026-12-11 if the demo terminal runs,
otherwise "priors accepted" on 2026-12-14; the owner's trade history exported in full, analysed locally, summary
only. Items 1-3, 5 and 7 stay open.


1. **Event floor for H-18** (~690 scheduled events in 15.75 years before the move filter, below the 1,000 floor).
   Options: (a) a 500-event retire-only floor for scheduled-event families; (b) pool with tier-2 events once BACKLOG
   18 / D-4 and E-1 exist; (c) drop H-18. **Recommendation: (b); until then H-18's Q2 slot stays conditional and
   returns to reserve.** Note that even (a) may not be enough after the move filter; the count is owed from data.
2. **Deposit plan** (ADR 0004): it decides when 1h research rises (section 3). Recommendation: decide it before Q2's
   freeze so the Q3 outline can be set.
3. **Raw-spread account confirmation**: on Standard, 15m is off except session-open, so nothing the Q2 plan finds
   could trade at £1,000.
4. **Ruling A** (daily-signal event floor, Q1): still open; it also governs H-15, H-07 and H-03 later.
5. **Projected percentage daily caps** (BACKLOG 29 addition): at £1,000 it means at most one full loss a day; it
   changes the £/day distribution H-17 reports, not its reading rule.
6. **Trial budget**: 20 a quarter is enough for this plan; raising it is not recommended.
7. **Material feature version** (ADR 0006, BACKLOG 34): decides whether Q2 evidence carries across the `day_type`
   and later feature bumps.

## 10. Not verified / left out

- No market data was read; every event count here is a calendar estimate or quoted from earlier docs. The exact counts
  are owed from `data-v1` before the Q2 freeze (listed per trial in the Q2 draft).
- The ranking scores are judgement, not measurements. ATR, gold price and GBPUSD are ADR 0004's estimates.
- The research-to-paper lead time (2–3 quarters) is an estimate from the Q1 calendar, not a measured figure.
- No code, settings, registry rows, budget, holdout or the Q1 pre-registration were touched.

---

## 11. Research cycle 1 (2026-10-20): decisions and re-plan

Integrates the eight researcher hand-backs of 2026-10-20 and the evaluator audit (audit file dated 10-23, read
10-20; checked on origin/main `9a44bdcb`). Nothing here runs a trial, reads an outcome, the holdout or market data.

### 11.1 Program decisions applied (taken by the program; not re-opened)

| Decision | What changes | Owner, date |
|---|---|---|
| **Q1 2027 reserve = 6, not 8.** H-01 (#48, INCONCLUSIVE, held) and H-04 (#44, STOP) already ran in the Q4 batch, so the Q1 queue is H-02 and C4H plus H-02's 4 contingent survivor slots | Q1 open budget 12 → 14. The 2 freed trials go to the **free reserve** (section 11.5 gives the full count: 6 free after H-53 and the O-7/O-8 returns), spent only by a dated note written before the pre-registration. `preregistration-2027Q1.md` carries the amendment; `config/settings.yaml` `reserved_trials_quarter."2027Q1"`, `preregistration-2027Q1.yaml` (`reservation: 6`, H-01/H-04 marked moved) and `tests/test_preregister_quarter.py` change in **one** reviewed change | implementer, quant-reviewer checks; **2026-10-27** |
| **True K = 111.** Counted as 89 registry trial rows + 21 (discovery #69's `k_eff`) = 110, the figure `registry.n_trials_effective` returns, plus 1 for H-49 stage 1, a pre-registered 0-trial look (K_eff +1, which retired R-13) that has no registry row. Not yet counted and therefore a floor, not an estimate: `k_eff` on non-discovery rows and other 0-trial looks (BACKLOG 107 on the ML branch). D-KB1's pre-registered 0-trial look is not in the 111 because `prereg-kb-2026-10-18.md` s.4 charges its +1 to H-25's row, so it enters K with H-25 (as `k_eff` 19 + 1) if H-25 is ever written, and until then it gates only H-25's own prior. No registry row is added for the H-49 look (it would push 2026Q4 to 56/55); `registry.n_trials_effective` and the coded DSR gate return 111 until BACKLOG 107 lands | Every reading quotes `n_trials_effective` at its verdict date **and** this +1 until BACKLOG 107 lands: H-53 at **K ≥ 112** (was 111), O-2/O-3 at K ≥ 113 (exits pre-registration). Gate-1 DSR t stays about 4.1. **BACKLOG 107 is merged before H-53 dispatch; otherwise the verdict row records `k_used: 112`** | evaluator (every verdict); implementer (BACKLOG 107) |
| **No FRED API key is needed.** ALFRED serves release dates and first-release vintages without a key; BLS/BEA archives are cross-checks | E-1 (historical tier-1 calendar) is built from ALFRED: loader by **2026-10-30**, `calendar-v1` published by **2026-11-06**. The `FRED_API_KEY` blocker is removed from every readiness row; the blocker for H-16's event inputs, H-17's blackout and H-18 is now `calendar-v1`, not the owner | data-engineer |
| **H-02 dollar-index revision** (macro point-in-time audit, PR #162, `macro-pit-audit-2026-10.md` A1/A2): DTWEXBGS in `macro-v1` is latest-vintage (20-day change ρ 0.994 with first release, 1.2% sign flips) and is a 2019 construction back-cast before 2019-03 | **Not re-pinned, file not changed** (it is H-02's reference digest). H-02's verdict record carries "A1/A2: DTWEXBGS values are latest-vintage and pre-2019 back-cast (macro-pit-audit-2026-10.md s.3)". Any H-02 survivor slot whose group contains `macro_dollar_chg20` is re-checked before its slot trial (0 trials). **Pass rule, written now, before H-02's pin:** the slot's group is re-scored with the dollar column rebuilt from true first releases (ALFRED DTWEXBGS from its first vintage 2019-03; before that, legacy DTWEXB first releases re-based at the splice date), everything else as pinned; the slot stays live only if it still meets its derivation rule (stability ≥ 0.6, OOF AUC ≥ 0.53, lower 95% bound ≥ 0.5) **and** its OOF AUC falls by no more than 0.01; otherwise the slot is void (not run, not re-derived, not replaced). Of the two issues the pre-2019 back-cast (A2) is the larger look-ahead: about 9 of H-02's 15.75 years use an index construction that did not exist at the time, against 1.2% sign flips from the vintage issue (A1); the splice is what the re-check exists for. SW-0 builds first releases from ALFRED (P-1) | evaluator (verdict note); data-engineer (re-check, only if needed) |
| **#88 / #89 verdicts** | Recorded here and in `hypotheses.md` C: **#88 H-48 `sweep_absorb` STOP → R-14; #89 H-50 `box_break_close` 15m STOP → R-15** (gross +0.030 R, t 1.61, net −0.297 R). The registry has no verdict row after #52: `record_verdict` for both and a `research-v1` sync | evaluator via release-manager, **2026-10-23** |

### 11.2 Decision: demo probes on retired non-roster families

**Yes, they keep probes, as cost probes only, and no `retired_families` check is added now.** A probe measures
spread, slippage, stop-exit and GBP commission at a moment; none of that depends on whether the family that chose the
moment has an edge. Its signal times are **not** a neutral sampling frame, though: slippage depends on entry timing
(breakout bars cost more), so M0 fills are tagged by the protocol's time/news bucket and by source, and are never
pooled with M1 or across sources in a cost reading. The owner has asked for hundreds of demo fills this week for
execution learning. The integrity risk is not the fill but its P&L
leaking into an edge or reinstatement reading; the evaluator confirmed probes never reach the shadow book,
`closed_trades`, attribution or promotion (ADR 0040 tests), and H-52 rule c excludes them from every edge number.
Exposure is demo only, 0.01 lot, about £4 a probe, £10 a day, under the RiskGate. Two conditions: (1) the roster
ruling and ADR 0040 text are corrected to say mirroring is **additive** to the ADR 0037 base (any non-shadow-only
agent), owner trading-safety-reviewer with the program-director, by 2026-10-27; (2) family-mirrored probes (M0)
are a stop-gap: they sample signal times, not the seven time/news buckets, so when the scheduled market probe M1 has
30 fills in each of its non-news cells (expected about 15 trading days after it starts), M0 mirroring of retired
non-roster families (today `mean_reversion`, R-01) is switched off by a reviewed change, review date 2026-11-13.
Retired families' probe fills are never reported per family.

### 11.3 Decision: ML items 110 and 111

**BACKLOG 110 (E-2 tercile cut, O-4): proceed with option (a)**, the cut from fold k's own model on a purged inner
time split, recorded as an amendment under O-4 before its group map is pinned, due **2026-12-04** (implementer,
quant-reviewer). The cross-model quantile is not a leak, but it lets scale drift between fold models decide whether
O-4 reaches its 1,000-trade floor; a floor that reads noise is a reading rule that cannot be trusted either way.
Option (b)'s retention guard ([0.15, 0.55] per fold, outside it INCONCLUSIVE) is written in **now as the fallback**:
if (a) is not merged and reviewed by 2026-12-04, (b) applies automatically, so the choice is not made after seeing
anything. Neither costs a trial; (a) changes the config hash (a new pin). F1 (BACKLOG 105) must land first, since
without it O-4 has no opt-in groups at all. **BACKLOG 111 (group-importance week buffer, H-02): proceed**, size S,
merged and reviewed before H-02's config pin on **2026-12-11**. H-02 ranks its four survivor slots by group
stability, and the audit shows the interleaved scoring favours exactly the slow macro/regime groups that also carry
the A1/A2 dollar deviation; fixing the ranking before dispatch is cheap, fixing it after is a new trial. Fallback, also
written now: if not merged by 2026-12-11, H-02 runs as pinned and its verdict records F4 as a known bias. Order stays
as the ML researcher proposed: 105 → 106 → 107 → 108 → 109, then 110/111, then 112/113.

### 11.4 Re-rank (adopted from the strategy lead, 2026-10-20)

Ranks 1-5 are **H-16, H-13, H-19, H-17, H-14**. H-13 moves above H-19 and H-17 because it is the one item that
changes net R on every later trial for 0 trials, and the costs lane now has a measurement protocol. Changes:
**H-28 withdrawn** (it needed a Q4 slot that does not exist and a 1h trade the account cannot place); **H-37 and
H-41 folded into H-53's verdict** (a STOP there extends R-15 to every box timeframe and retires both at 0 trials; a CONTINUE does not reopen them);
**H-24 lapsed** (O-7 needed H-04 to continue); **H-54** (forward-only opportunity log, 0 K) added beside H-16 as its
forward check. The honest statement stands: **no queued hypothesis has a credible £50/day path at £1,000.** The
route, if any, is H-16 + H-17 V1u on 15m (a correct runner on a flagged day reaching about 6 R); 4h needs about
£6,900 equity on measured ATR (macro, against ADR 0004's £3,270 estimate) and 1d cannot be placed while ATR(1d)
> ~$44. Those two sizing numbers go to the CFO and program-director for an ADR 0004 note; they are not research
variables.

### 11.5 Budget and slots after this cycle

| Quarter | Reserved | Open | Allocation |
|---|---|---|---|
| 2026Q4 | — | — | **closed 55/55**. H-28, H-25 and H-49 stage 2 assumed Q4 slots that do not exist: H-28 withdrawn; H-49 stage 2 void (stage 1 retired R-13); H-25 **lapsed in Q4**, eligible to bid for a Q1 free-reserve slot by a new dated note before 2026-12-18, with its `k_eff` 19 stated |
| 2027Q1 | 6 (H-02, C4H, 4 slots) | 14 | O-1..O-6, O-10 (7 conditional rows; O-7/O-8 impossible, O-9 a 0-trial diagnostic that also lapses), H-53 (the retired-family exploration trial, written as a queued row before 2027-01-01: evaluator M2), **free reserve 6** (the original 3, + 2 from the reserve change, + 2 returned by O-7/O-8, − 1 for H-53) |
| 2027Q2 | per draft | free reserve 7, or 10 by a dated note moving the 3 slots H-04's STOP and H-01's INCONCLUSIVE made impossible | the note is adopted in principle; written in the Q2 draft by the strategy-researcher by 2026-10-27 |

Note on the Q1 free reserve: 14 open = 7 live conditional rows (O-1..O-6, O-10) + H-53 + 6 free. None of the 6 is spent without a
dated note in `preregistration-2027Q1.md` written before that row's pre-registration, and the E-5 cap still binds.

### 11.6 Demo fills this week

The owner wants hundreds of demo fills by Fri 2026-10-23. They serve the cost-measurement protocol
(`prereg-cost-measurement-2026-10.md`: slippage, limit fills, stop exits, GBP commission and swap) and execution
learning. They are **never** edge evidence: probes are excluded by H-52 rule c, and research costs stay at
max(0.15, measured) per leg, so demo slippage never lowers a research cost.

### 11.7 Time-structure programme (research director, 2026-10-26)

Source: `time-structure-2026-10-26.md` (synthesis of slices A-H), pre-registration
`prereg-2026-10-26-time-structure.md`.

**Ranking.** H-73, the range feature group, joins H-16/H-19 at the top as a priority-1 enabler (0 trials, K_eff +1).
H-70 (F-1, 15m Hurst-switched band breakout) is the first new 15m candidate since H-17 that survived its 0-trial
checks. The order now reads: H-16, H-73, H-13, H-19, H-17, H-14, H-70, then the rest unchanged. Below them sit H-71
(G-1, 1h level break) and H-72 (Friday long), both arm-only. H-74 (slow-trend swing group) goes into SW-0. H-75 and H-77
are held for reconciliation; H-76 (A-1) is closed.

**Slots.**
- The 2027Q1 free reserve gives 2 conditional rows, O-11 (H-70 confirmation) and O-12 (H-71 confirmation). 4 stay free.
- The 5 new arms need the 2026Q4 override 69 -> 74: **approved 2026-10-26** by PD + PO, on the quant's conditions:
  the pre-registration is on main before the first fill, the settings comment cites the directive, #34 is told the
  same day, K +5, and the fills are never evidence.

**K.** Registry 145. The programme's looks are charged at about **187 effective** (G at its 51 axes), so later
gate-1 reads use K ≈ 332, or 337 with the arms (t ≈ 4.6).

**Contamination** (slices launched before the frame landed):
- A read the holdout for session-clock statistics. Those configurations lose holdout scoring and confirmation credit.
- B read 2018-2025-09, and D read part of it for 1d rows: no confirmation credit for those.
All three are reported to the evaluator.

**Closed at 0 trials, in the form tested:** Fibonacci levels and time, wavelet/Ehlers/Goertzel/EMD direction,
30/60/90-day direction, calendar direction, oscillator fades, ADX gate, OBV, VWAP fades, value area, OB retest, FVG,
sweeps and magnets, level retests, 15m ORB (all 24 variants), band fades and unconditional band breaks.

## 12. Team feedback log

Every item raised since the last cycle, with the answer. A = adopted, D = deferred (date), X = declined (reason).

| # | From | Item | Answer | What changed in the plan |
|---|---|---|---|---|
| 1 | opportunity | H-54 forward opportunity log (fixed equal-weight score, two jobs, hash-chained, one read at n ≥ 120 days ≈ 2027-05-28; 0 K) | **A.** Pre-registration ready for quant review | H-54 row in `hypotheses.md` A13; 11.4 |
| 2 | opportunity | E-1 without a key (ALFRED) | **A** (program decision) | 11.1; data-engineer 10-30 / 11-06 |
| 3 | opportunity | Stop waiting on the FRED key | **A** | blocker removed from the plan and Q1 readiness |
| 4 | opportunity | Backlog items for the two forward jobs and the ALFRED loader | **A**, routed to product-owner; numbers via `fix-ids` against origin/main (the ML branch holds 105-113) | none (backlog) |
| 5 | performance-analyst | No admissible edge numbers (parity 5b missing, shadow unreadable, 3/30 probes) | **A** as the baseline | section 1 row "Forward evidence" |
| 6 | performance-analyst | box 1h/4h refuse every close with `history_gap` (Labor Day hole older than backfill 30 d) | **A**, urgent, data-engineer on `fix/holiday-history-gap` with trading-safety review; escalated to the program-director today | none (ops) |
| 7 | performance-analyst | 10 engine restarts; restart counter owed | **A**, implementer with the parity job, 2026-10-23 (H-52) | — |
| 8 | performance-analyst | Feed reconcile FAIL (12.16% of closes off broker M1 by > $0.02) | **A, escalated:** if the engine feed differs, H-52 parity can fail on feed alone and no forward number ever becomes admissible. data-engineer diagnoses the source by 2026-10-27 before the parity threshold is read | risk added to H-52 dependencies |
| 9 | performance-analyst | Spread medians by session | **A** as context only (not a cost reading: below 30 fills, no CI) | — |
| 10 | performance-analyst | mean_reversion probe mirrored against the roster ruling | **A**, decided | 11.2 |
| 11 | performance-analyst | H-BR1 bar-close spread excess (Wilcoxon, ≥ 300 closes) | **A** as a 0-K cost diagnostic, written into the cost-measurement protocol by the microstructure-researcher by 2026-10-27, before any bar-close spread is read | — |
| 12 | performance-analyst | H-BR2 feed parity ≥ 95% both ways, 10 weekdays | **A** as the acceptance of parity job 5b (it is H-52 rule b) | — |
| 13 | performance-analyst | H-BR3 guard availability after the gap fix | **A** as an ops check after item 6 merges | — |
| 14 | performance-analyst | Read-only shadow-report and probe-ledger command | **A, done:** `goldbot run shadow-report` is on main (lane `feat/shadow-report-cmd`) | — |
| 15 | exits | V1 cannot run at £1,000 (0.01 lot skips the 50% partial); V1u pre-registered | **A.** The trial reads the exit the account runs; V1u's K_eff +1 on O-3 is accepted (the bar moves by < 0.01 t at K ≈ 113) | K ≥ 113 at O-2/O-3 |
| 16 | exits | No follow-up exit trial for H-01, C4H or H-04 | **A** | no exit slots in Q1/Q2 for them |
| 17 | exits | Copy section 2 under O-2/O-3 as a dated amendment before any event count | **A**, strategy-researcher, by 2026-10-30 | — |
| 18 | exits | BACKLOG 32 `win_atr` on the exit the account runs | **A**, product-owner amends 32 | — |
| 19 | exits | Stop V2/V3 diagnostics; split BACKLOG 31, no swing trail until a trial needs it | **A** | Q3 outline keeps V2/V3 only as neighbours of a V1u pass |
| 20 | exits | Live closes around tier-1 releases; early-close flat rule | **A**, routed to trading-safety (same as item 28) | — |
| 21 | exits | 0.02 lot ≈ 1.8% risk | **X as a research item:** sizing above the 1% cap is not a research variable; noted for the CFO | — |
| 22 | microstructure | Measured-cost protocol M0/M1/L1, 7 buckets × 30 fills, floor max(0.15, measured), O-4 withdrawal at 0.10 R on 2027-01-29 | **A** (already on main with amendments 1-2) | 11.6 |
| 23 | microstructure | Blockers K7-K12 | **A**, product-owner turns them into backlog items; K8 (limit/scheduled path through the RiskGate) needs trading-safety review | — |
| 24 | microstructure | ADR 0037 amendment 5 (scheduled probe type) + trading-safety review | **A**, in flight on `dv/scheduled-probes`; not merged without trading-safety | — |
| 25 | microstructure | icm-probe credentials and GitHub token flagged as owner items | **X as an owner ask:** terminal logins are the program's (owner 2026-10-20); routed to the program-director | — |
| 26 | microstructure | Staff spec/citation check (af81d871: citations, GBP 5.50 RT cross-check, K12) | **A**, no reading rule changed; already merged equivalent | — |
| 27 | trading-safety | Retired non-roster families mirrorable (cost_probe.py:301) | **A, decided:** keep, additive text fix, M0 switch-off review 2026-11-13 | 11.2 |
| 28 | trading-safety | No flat-before-close on early-close/holiday days | **A**, queued after `fix/holiday-history-gap` (program-director, trading-safety) | — |
| 29 | trading-safety | Probes skip the tier-1 news re-score | **A:** document it for M1 (30 s hold); mirrored probes get the re-score, implementer by 2026-11-06 with trading-safety review | — |
| 30 | strategy lead | P-3/P-6/P-7 ruled; P-4/P-8 drafted for the quant ruling 10-25 | **A**; P-4/P-8 counts wait for `calendar-v1` (11-06) | — |
| 31 | strategy lead | True K is 111 | **A** (program decision) | 11.1, section 1, `hypotheses.md` C |
| 32 | strategy lead | Re-rank 1-5; withdraw H-28; fold H-37/H-41 into H-53; H-24 lapsed | **A** | 11.4 |
| 33 | strategy lead | Q2 free reserve 7, or 10 by a note | **A** in principle; note by 2026-10-27 | 11.5 |
| 34 | strategy lead | W1-5 (H-13 cost filter) late: rule text 10-23, code 11-06, quant review 11-13 | **A**, re-dated and escalated to the program-director today | — |
| 35 | strategy lead | Q1 reserve 6 not 8, due 10-27 | **A** (program decision) | 11.1 |
| 36 | strategy lead | FRED key blocks ranks 1, 4, 5 (conflict with item 2) | **Reconciled, X:** ALFRED is keyless; the blocker is `calendar-v1` (11-06) | 11.1 |
| 37 | H-13 staff | `costfilter.py` built; waits for the rule text (HAR horizon map, spread charged, planned hold, whether 3.0 is final, report layout) | **A.** The rule text (strategy-researcher, 10-23) answers all five; constraint from the director: the spread charged must be known at the decision bar close (no same-bar realised spread). Quant review only after the rule text | — |
| 38 | ml | F1 high: `--discover` drops O-4's opt-in groups | **A**, BACKLOG 105 first; O-4 cannot be pinned before it | — |
| 39 | ml | F2 real leak in cross-fitted calibration | **A**, `fix/crossfit-calibration-purge` with quant review before any Q1 dispatch | — |
| 40 | ml | F3 / F4 (110 / 111) | **A, decided** | 11.3 |
| 41 | ml | F5 K honesty; F6-F9 | **A**, 107/108/109/112/113 in the stated order | 11.1 |
| 42 | ml | Stop hand-typed `shuffle_auc`; stop quoting K without recomputing | **A** | every K in this plan names its count |
| 43 | ml | Backlog 105-113 may clash | **A**, main session runs `fix-ids` against origin/main when merging | — |
| 44 | macro | DTWEXBGS revision / back-cast (A1/A2) | **A** (program decision) | 11.1 |
| 45 | macro | COT pre-2009-09 stamped early; no data-v1 bar affected | **A**, fix after H-02 dispatch (0 trials) | — |
| 46 | macro | Not yet audited: SP500 revisions, pre-2018 revisions, CFTC corrections | **D to 2027-02-26** (macro-researcher), before SW-0's Q2 freeze; H-02 does not use them beyond what A1/A2 covers | — |
| 47 | macro | 4h needs ~£6,900; 1d unplaceable while ATR(1d) > ~$44 | **A**, to CFO/program-director for an ADR 0004 note | 11.4 |
| 48 | evaluator | M1 no verdict rows for #88/#89 | **A**, evaluator via release-manager, 10-23 | 11.1 |
| 49 | evaluator | M2 H-53's Q1 charge exists only in docs | **A**: H-53 written as a queued row before 2027-01-01 (strategy-researcher, 12-18); pending retired-family rows count against `RETIRED_EXPLORATION` (BACKLOG 91, implementer, 12-18) | 11.5 |
| 50 | evaluator | M3 contradictory section C rows; H-28/H-25/H-49 stage 2 assume Q4 slots | **A**: the "not dispatched" batch row is marked superseded; H-28 withdrawn, H-49 stage 2 void, H-25 lapsed (may bid for Q1 free reserve) | 11.5, `hypotheses.md` C |
| 51 | evaluator | M4 Q1 "Order and budget" heading stale | **A**, corrected with the reserve amendment (2026-10-20) | `preregistration-2027Q1.md` |
| 52 | evaluator | Attribution ignores the 13:49 UTC quarantine | **A**, `fix/attribution-evidence-start`, implementer + quant-reviewer | — |
| 53 | evaluator | `perf_report` drawdown omits probe losses | **A**, finance-auditor reconciles to the broker | — |
| 54 | evaluator | H-52 admissibility a-f | **A** as the checklist every forward number is tagged against (performance-analyst) | section 1 |
| 55 | performance-analyst (2026-10-25) | 0 own entries: every agent shadows at cw 0 and promotion counts only taken trades (`population.py:111`) | **A.** H2 counterfactual fixed-threshold arms (pre-registered, counted in K, never pooled with the roster) plus a learning demo account. Untaken outcomes are **never promotion evidence**. Owner: strategy-researcher (H2 text), program-director (account) | H2 arms in K |
| 56 | performance-analyst (2026-10-25) | Shared broker login: icm-probe = icm-demo | **A.** Owner is storing a separate account; until then probe and demo fills are not separable evidence. Owner: program-director | — |
| 57 | performance-analyst (2026-10-25) | Probe fills are not in the cost table | **A.** H1 refit takes `probe_fills` as an input. Owner: microstructure-researcher | H1 inputs |
| 58 | performance-analyst (2026-10-25) | Mirror probes at 0.3-0.5x the stop are useless as evidence | **A.** Mirrors use their own stop on the learning account, otherwise skipped. Mirror rows are tagged `exploration` and carry 0 trials. Owner: microstructure-researcher, implementer | — |
| 59 | performance-analyst (2026-10-25) | 4h history gap from the August Dukascopy hole | **A.** Owner: data-engineer | — |
| 60 | performance-analyst (2026-10-25) | Rule families p = 1.0, calibration undefined | **A.** Isotonic calibration once n >= 50; it must be cross-fitted and include untaken candidates (n=50 overfits otherwise). Owner: ml-researcher | — |
| 61 | performance-analyst (2026-10-25) | Retrain skipped while a challenger is in shadow (`jobs.py:448`) | **A.** Retrain triggers per 25 closed outcomes; a challenger stuck > 30 days is retired or reset. Owner: implementer with ml-researcher | — |
| 62 | performance-analyst (2026-10-25) | Weekly evaluator verdicts | **A.** Owner: evaluator via release-manager | — |
| 63 | slice A (sessions) | A-1 failed Asia-range fade as an arm; A-2 NY pit continuation as a feature; A-3 cost-window rule; A-4 local-clock audit; read 2020-26 including the holdout | **A-1 deferred** (reconcile with H's flipped rebuild, MS-2, by 2026-11-13; holdout seen). **A-2 A** as a feature (not reproduced by H, so low weight). **A-3 A** as a 0-trial arm-fill diagnostic (microstructure). **A-4 A** (L-3, new arms local by construction). **Holdout breach recorded** (synthesis 7.2) | H-76 held; 7.2 contamination table |
| 64 | slice B (cycles) | B-1 Friday slot; B-2 event-day range feature; B-3 rv ratio; B-4/B-5 drop; festival and expiry date tables | **B-1 A as a forward-only arm** (WFRI; 2018+ seen, so no slot now). **B-2/B-3 A** into H-73. **B-4/B-5 A** (dropped). **Date tables A** (macro-researcher-staff) | H-72, H-73 |
| 65 | slice C (Fibonacci/wavelets) | Close Fibonacci, cycles, Ehlers, Hurst gate, EMD; C-1 30-day-low continuation; C-2/C-3 features | **Closures A.** **C-1 A merged** into H-74 (SW-0), no standalone slot (115 events < 150). **C-2/C-3 A** into H-73 (≤ 5 columns) | H-73, H-74 |
| 66 | slice D (oscillators/trend/volume) | D-1 arm; D-2 arm; D-3 diagnostic; osc_trend_vol feature group (16 columns); tick volume is not real volume | **D-1 deferred** (H's rebuild null; reconcile by 2026-11-13). **D-2 deferred** (redundancy check vs MA regime first). **D-3 X** (bear-market regime control likely explains it; 2-3 events a month). **Group A** for H-62 v2 under the 40-cap (ml-researcher picks). **Volume A** (data survey decides COMEX volume) | H-75, H-77 held |
| 67 | slice E (SMC) | 0 trials; OB/FVG/value area closed against controls; #88 cost resolved as real | **A.** The #88 explanation matches the quant audit | section 4 table |
| 68 | slice F (bands) | F-1 slot candidate; F-2 one-or-the-other; F-3 feature; features list | **F-1 A**: arm F1A/F1C now, O-11 conditional. **F-2 A as a recorded flag inside F1A**, not a second slot. **F-3 A** into H-74. **Features A** (≤ 5 columns into H-62 v2) | H-70 |
| 69 | slice G (key levels) | G-1 arm with a random-level control; G-2/G-3/G-4 features; close fades/retests | **A.** G-1 as arm G1A/G1C with **fixed 2R** (0.01 lot cannot split for the partial exit); O-12 conditional | H-71 |
| 70 | slice H (combinations) | H-1 NY-pit split; H-2 book on arm fills; allocator unvalidated; meta-labelling no lift; A1/D1/C1 did not reproduce | **A.** H-1 as a pre-specified stratum of F1A; H-2 as H-78 (daily_review); allocator test as backlog 111; reproduction failures held (H-75/H-76) | H-78, H-79 |
| 71 | architect | H's allocator result undetermined, probably an artefact | **A**; backlog 111 is the proper test | — |
| 72 | quant-reviewer (cost audit) | Prior about 5-7x measured, conservative but defensible; no bug; no retired family flips; judge measured-cost candidates on the worse of two measured costs | **A.** Stage-3 requirement written; corrected the wording to the worse of quiet-time probe cost (+overshoot) and signal-time cost; the research prior stays the cost of record | learning-loop.md stage 3 |
| 73 | quant-reviewer (local clocks) | S0L/S4L/S5AL as new ids, each a trial, paired reading rule | **D to the Q2 draft (by 2027-03-26)**: new arms are local by construction, and 3 slots for a scheduling answer is poor value | — |
| 74 | data survey (pending) | longest history, volume, COT, ETF, options | **D until it lands:** H-74 (pre-2010 history), D volume (COMEX volume), B-5 (exact OG dates) wait on it | dependency list in synthesis 9 |
| 75 | owner via coordinator | continuous learning loop; multi-models | **A**: `time-structure/learning-loop.md`, backlog 114 and 106-110 (L-1..L-6; L-1 renumbered 105->114 at merge) | learning loop |
| 76 | quant-reviewer (synthesis review) | C1 swap in the measured cost; C2 freeze ny_pit and the WFRI clock; C3 the arm cannot cancel O-11/O-12; C4 charge G at 51 axes, eigenvalue K_eff in L-1; C5 override conditions | **A, all five.** ny_pit 08:00-13:30 NY; WFRI Thursday 19:00 NY; K 332 (337 with arms); L-1 acceptance amended | prereg 0, 1, 2.3, 3.3; synthesis 7.1 |
| 77 | evaluator (synthesis review) | C6 key-level break-close died on signal | **A** | synthesis section 4 |
| 78 | evaluator | C7 the 4h Asia cell | **A** as a pre-specified secondary in O-12 (not in its verdict) | prereg 3.3 |
| 79 | evaluator | C8 close A-1 | **A**: H-76 closed | hypotheses A15 |
| 80 | evaluator | C9 ban ny_pit from holdout criteria; G-1's 8/8 is the partial + trail exit | **A** | prereg 2.3, 3.1 |
| 81 | evaluator | C10 power and stop rules for every arm; Friday read needs about 170; O-11 on the armed stop exit; minimum n on the net conditions | **A**: F1A n≈1,000 / 2,000 paired with futility at 300 and 1,000; G1A a cost/safety arm; WFRI safety at 26, read at 170; O-11 on the stop exit; n ≥ 1,000 | synthesis 6.3; prereg 2-4 |
| 82 | program director + product owner | 2026Q4 override 69 -> 74 approved under the quant's conditions | **A**: settings and `config/exploration_arms.yaml` + `accounts.yaml` blocks prepared; arms blocked until their generators land (backlog 112) | config |
