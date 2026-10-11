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
| Trial budget | Q4 2026 spent 20/20; Q1 2027: 13 reserved for the frozen queue, 7 reserve | hypotheses.md C, settings `research.reserved_trials_quarter: 13` |
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

Status as of 2026-10-10. "Rank" is the unified north-star rank now used in `hypotheses.md` (section A3 gives the
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
| **1d** | M4 (£7,900), and 1d must be promoted to a decision timeframe | 1d tsmom had 454 events; H-01 counted 154–2,154 | Slow TSMOM is the only replicated trend horizon (MOP 2012, Hurst et al. 2017) | Q1: H-01, H-06 (frozen). Q2: H-01 model research or exit variant (C-5) only if H-01 continues (reserve). H-15/H-07/H-03 wait for ruling A | Promoting 1d to a decision timeframe before an H-01-family pass and M4 |
| **1w** | never (context only) | — | Monthly-horizon VRP and COT are better as features | Context features only (weekly ranges, trend state) | Any 1w trial |

## 6. Quarterly roadmap

| Quarter | Trials | Content | Status |
|---|---|---|---|
| Q4 2026 | 20/20 | Primary-signal screens of the original families | spent; all retired or failed (R-01..R-07) |
| Q1 2027 | 9 reserved (5 queued + 4 H-02 survivor slots; ADR 0007) + 11 open | H-02, H-01, H-06, H-04, Q1 survivor rows, tsmom 4h continuity; H-13 overlay | frozen draft, freeze by 2027-01-01 (`preregistration-2027Q1.md`; **not altered here**) |
| Q2 2027 | 4 planned + 3 conditional + 6 Q1 follow-up reserve + 7 free reserve | H-16, H-17 V0/V1, H-14 (+ ≤ 2 survivors), H-18 if ruled; H-13 and H-19 overlays | DRAFT (`preregistration-2027Q2-draft.md`), freeze by 2027-03-31 |
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
