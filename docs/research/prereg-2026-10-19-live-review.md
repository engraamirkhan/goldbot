# Live review of the first shadow day and pre-registrations H-50..H-52 (2026-10-19)

Strategy researcher, 2026-10-19, after the owner's request: "the strategy researcher should be looking at the data
captured so far and analyse". Read-only on code. Input: the brain snapshot of 2026-10-19 12:37 UTC (the shadow book,
the decision journal, the engine state and the cost-probe ledger). It covers about 8.5 live hours (founders from
04:04 UTC, box agents from 07:44 UTC, 30m box and sweep_absorb from 12:05 UTC).

**Discipline.** No edge is read from the live P&L. Nothing here selects, tunes or ranks by a shadow outcome. The
research comparisons below are **counts only** (signal rates and event counts on 2010-01..2025-09, the frozen rules,
no label, return or outcome computed). They come from scratch scripts that are not committed. No holdout bar
(2025-10-01..2026-09-30) was read. The only post-holdout bars read were the last rows of the `data-v1` 2026 file
(ending 2026-10-05 23:58 UTC), to check what a restarted engine sees (section 1.1). That is a data check, not an
outcome read.

## 1. What the live data says

### 1.1 Blocking finding: the live decision frames have a two-week hole after every engine restart

The 4 breakout-1h shadow entries are all **long**, at 4183.60 (04:00 bar), 4172.96 (07:00), 4167.37 (09:00) and
4169.70 (10:00), while price fell. The founder's rule (`specialists/breakout.py`) needs the close above the high of
the previous 8 one-hour bars. The book itself records a high of at least 4186.12 on the 05:00 bar (the 04:00 trade's
MFE). That bar is inside the 8-bar window of the 07:00, 09:00 and 10:00 bars, and all three closes are below 4186.
On contiguous bars, **3 of these 4 signals cannot happen**. They can happen only if the 05:00 bar was missing from
the frame the engine decided on.

Mechanism (from the code, to be confirmed on the brain):
- `Engine.warm_start` reads only the store's `bars_1m` (release Dukascopy bars). On the release, those end
  **2026-10-05 23:58 UTC** (high 4166.0, last close 4134.1).
- Live minutes exist only from the moment of the restart. Nothing in the engine reads the broker M1 in
  `bars_1m_broker` or the terminal history.
- `_frame` asks for at least 120 bars. It does not check that they are contiguous.
- The decision journal shows **12 engine starts** between 04:04 and 12:05 UTC (`limits_change` rows with
  `previous: null`).

So after each start every rolling window joins 5 October to the last few live minutes. A close above the stale
5 October high (4166) reads as a **long breakout**. The Bollinger z of the 15m fade reads as overbought, which gives
**short** mean-reversion fades: all 3 of those are short. Every one of the 10 shadow entries was decided 1 to 61
minutes after a start. `box_break_close` carries a replica state that is bootstrapped from the same store
(`attach_replica`), so its boxes and money flow also span the hole. ATR, `tick_vol_ratio_20` and all h4/d1 context
features are affected in the same way.

Consequences:
1. **No shadow trade recorded so far is admissible evidence.** That covers the 10 entries of every agent and the
   cost probe's signal. Their features, p values and signals come from a frame the research labels never contain.
2. 1h/4h agents and the d1 context need 8 h to several weeks of clean history. While the engine restarts about
   every 40 minutes, they never get clean frames. Today the restart rate alone stops the program from learning.
3. The `data-v1` release ends 2026-10-05. The daily data pull seems to have stopped two weeks ago. That also starves
   the forward research data.

Owed by others (flagged, not done here; the research director routes them):
- **data-engineer / implementer, with the trading-safety review:** warm start from the broker M1
  (`bars_1m_broker`, or a terminal history pull) up to the start time.
- A **contiguity guard**: no decision and no shadow entry on a frame whose last `max(window)` bars have a gap longer
  than a weekend or session break. Fail closed and log a `history_gap` DQ event.
- A test: the frame built after a restart equals the frame built from continuous bars.
- Tagging every shadow trade opened before the fix as `quarantined: history_gap`, so no promotion, cluster count or
  calibration reads them.
- A check of why `data-v1` stopped at 10-05.

### 1.2 Per-strategy behaviour against the research expectation (rates only)

Expected rates are counts of the frozen default rules on Dukascopy 2024-03..2025-09, weekdays. The window is the live
04:00-12:30 UTC span.

| Agent | Live (window) | Research rate in the same window | Reading |
|---|---|---|---|
| breakout 1h (founder) | 4 candidates, 0 taken; p 0.21-0.27 vs threshold 0.36 (p_raw 0.10-0.17) | 0.14/weekday (P(≥4) ≈ 2e-5) | **Mismatch: the history hole (1.1).** Signals impossible on contiguous bars |
| mean_reversion 15m (founder) | 3 candidates, all short, 0 taken; p 0.56-0.58 vs threshold 0.64 | 0.44/weekday (P(≥3) ≈ 0.01) | Mismatch, same cause (z against 5 October prices) |
| tsmom 1h, trend 1h, session_open 15m | 0 | 1.08, 0.35, 0.21 | Consistent (P(0) = 0.34, 0.70, 0.81). The frames are not clean anyway |
| intraday_momentum 15m | 0 | 0.00 (it fires 16-17 UTC) | Consistent |
| box_break_close 15m | 3 trades + 1 skipped by the cap of 3, all short, all within 61 min of a start | about 0.4 in 4.9 h (2.0/day raw; 0.5/day one at a time) | High, clustered, and inside the hole: inadmissible |
| box_break_close 30m / 1h / 4h | 0 / 0 / 0 | 0.97 / 0.47 / 0.12 raw per day | Consistent (30m has run for 30 min only) |
| sweep_absorb 15m | 0 (32 min) | 0.33/weekday in the window | Consistent |

p against thresholds: the founders' p sit far below their break-even thresholds. That is what an OOF AUC of about
0.50 (R-07) predicts, so founders will almost never be "taken". Their untaken outcomes still feed recalibration once
the frames are clean. The box `RuleModel` gives p = 1.0 ("fired"), so it is always taken.

Exits against labels: every closed trade follows the label mechanics.
- breakout: stop filled at the stop price; the trail is not armed because the MFE was 0.16 R, below 1 ATR.
- mean reversion: time exit at `bars_held` = `max_bars` + 1 = 13, as the labels define it; two targets filled at the
  exact target price.

No exit-path discrepancy is visible. The shadow book allows 3 open trades per agent (cap 3), while the labels are one
at a time. That is by ruling. The evaluator should read the one-at-a-time subset as the comparison with research and
the session-day clusters for the count.

### 1.3 Cost per trade (n = 1; no inference)

One probe filled: breakout-1h signal, 0.01 lot, raw account, 517 ms latency.
- spread $0.06;
- entry slippage $0.11 adverse (requested 4167.49, filled 4167.60);
- exit $0.04 better than the stop;
- commission £0.06.

The round trip is about **$0.21/oz**, against the prior of bar spread + $0.37/oz (about $0.57/oz). In R that is
about 0.014 R on the shadow trade's 1h R ($14.94). The prior would give about 0.04 R. One fill says nothing about
the mean.

Three things limit the probe as a cost measurement:
- **The probe's stop was clamped** from 1.0 ATR ($14.94) to $5.16 (£3.96, the £4 cap). Its exit (stopped at
  11:59) is not the shadow trade's exit, so its P&L must never be read as the agent's.
- **The probe budget is half of what ADR 0037 states.** `budget_refusal` uses the engine's `daily_loss_limit`, which
  is £20 (2% of £1,000), not £50. So 0.2 × £20 = £4, one clamped probe fills the day ("probe_loss_reached" from
  11:15), and we get about 1 cost observation a day.
- The probe ledger row has `taken: true`, but the shadow entry it mirrors was untaken (p 0.266 < 0.362). Either the
  row was written by older code or the untaken-probe cap is miscounted.

The two `box_break_close` probes were refused with `probe_loss_above_cap`, even though the clamp should make every
probe fit. That is for the trading-safety reviewer.

## 2. Pre-registrations

Budget state (registry fetched 2026-10-19):
- 2026Q4 has used 53 of the override's 55 trials. The 2 slots left are earmarked for H-48 (`sweep_absorb`) and H-49
  stage 2.
- `n_trials` is 87 and `n_trials_effective` is **108**, so the gate-1 DSR bar is a t of about 4.1.
- Holdout untouched.

### H-50 `box_break_close` 15m, the exact frozen roster rule, as a research trial (1 trial)

- **Why now.** The owner's box strategy runs as four roster agents, but a roster agent is promotable only with "a
  passed research trial of the exact configuration". None exists, so today the agents cannot be promoted under any
  shadow result. One trial reads 15.7 years: 2,404 events, one at a time, on 15m. At about 0.8 clusters a day, a
  shadow book would need about 12 years to collect that many. **This is the learning-speed lever.**
- **Config.** Family `box_break_close`, config `{}`, which is the 15m founder of the roster. Module constants as
  frozen in `shadow-roster-2026-10-19.md`: BREAK_DEPTH 0.2; MFI(12, tick counts) at or beyond 30/70 on t-4..t and
  turning; swing trend not against; not in the golden zone; stop 1.5 ATR(14) with the structural stop inside it;
  target 3.0 ATR (2R); 96 bars.
- **Economic reason.** Stop and take-profit orders cluster beyond S/R levels, so a close through a level tends to
  continue (Osler 2003, J. Finance 58(5); Osler 2005, JIMF 24(2); FX order book 1999-2000; gross mechanism; robust
  as an FX mechanism, no gold evidence). Money flow adds "which side carries the risk", but there is no
  peer-reviewed evidence for the MFI (folklore grade, `indicator-survey.md`). Against this: our own breakouts had no
  gross edge (R-04 +0.015 R, t 0.38; the B01-B03 15m breakouts, gross |t| ≤ 1.8), H-40 found no box or MFI flag
  that survives (OOF AUC 0.503), and technical rules show no net edge after snooping control (Bajgrowicz & Scaillet
  2012, JFE 106(3), DJIA 1897-2011, net; robust).
- **Data.** Dukascopy 1m `data-v1`, 2010-01-01..2025-09-30, cut before resampling, `mid`, tick counts. Calendar
  blackout only where the frame carries `in_blackout` (as in research.yml).
- **Counts (computed 2026-10-19, counts only).** On 15m: 9,147 raw signals and **2,404 one at a time** (raw signals
  per year: at least 451, median 585). For reference: 30m 1,444, 1h 907, 4h 319 one at a time.
- **Expected edge against cost.** The stop is 1.5 ATR15, about $2.3-4.5 over 2010-2025 and about $10-12 today.
  - Cost prior: about **0.15-0.30 R** historically and about 0.05 R today. The one measured fill is about 0.02 R
    (section 1.3, n = 1).
  - Gross needed to clear net > 0: about +0.2 R over the full sample.
  - Prior gross: 0 to +0.03 R.
  - **Stated now: expected STOP on net. P(continue) about 3%.** The value of the trial is a definitive answer to the
    owner's rule, plus the P4 gross screen.
- **research.yml inputs.** `specialist=box_break_close`, `variants='[{}]'`, `from_year=2010`, `to_year=2025`,
  `lot_fit=true`, `rule_ref=prereg-2026-10-19-live-review.md#h-50`, and a rationale citing this section.
  Precondition, implementer: `box_break_close` is `screening = True`, so confirm the workflow dispatches it as a
  rule-only screen, as for `box_course`, and that the replica runs on the whole series (`FULL_HISTORY`).
- **Reading rule (fixed before any outcome).**
  - **CONTINUE** only if all hold:
    1. P4: gross mean R t ≥ 2.0 on ≥ 1,000 events;
    2. rule-only net mean R > 0 under the cost prior (bar spread + $0.37/oz + swap prior);
    3. net mean R > 0 in 2024 and in 2025-01..09;
    4. DSR ≥ 0.95 at the registry `n_trials_effective` at the verdict (≥ 108).
  - **LEAD** if 1-3 hold and 4 fails. No promotion. The 15m agent's clean forward shadow becomes the test: a read at
    300 clean one-at-a-time trades, net mean R > 0 with t ≥ 2. That read is 1 trial, charged when made.
  - **STOP** if 1 fails or net ≤ 0, at any count, as the batch rule. On STOP, the rule is retired in
    `research.retired_families`, the four box agents stay shadow-only parity monitors with no promotion route, and
    probes stop mirroring them.
  - The count is known, so there is no inconclusive outcome.
- **Gates.** P4 screen, net > 0, the per-year rule, DSR at full K, holdout only for a CONTINUE (one look, reserved).
- **Cost.** 1 trial, with `k_eff` 1 on the row. It cannot be dispatched in 2026Q4 unless one of these happens:
  - (a) H-49 stage 1 fails and the research director reassigns its stage-2 slot;
  - (b) the owner raises the 2026Q4 override from 55 to 56 (**owner decision, flagged, not assumed**);
  - otherwise it goes into the first Q1 2027 open slot.

  Rank 1: the highest information per trial on the owner's question.

### H-51 money-flow lift and 30m confirmation inside the box rule (conditional, 2 trials)

- **Only if H-50 reads CONTINUE or LEAD.** Otherwise H-51 lapses with 0 trials. Money flow inside the box rule then
  stays unproven, and H-40's retirement of the MFI filters stands.
- **H-51a, MFI ablation.** The same rule with the money-flow extreme removed: `MFI_LOW` 101 and `MFI_HIGH` -1, the
  turn kept. 3,395 one at a time on 15m (counts 2026-10-19). Reading rule:
  - money flow **stays** in the rule only if the H-50 rule's gross mean R exceeds the ablation's by Welch t ≥ 2;
  - otherwise the next generation drops it (the simpler rule wins).
  - Needs a frozen-config knob (`mfi_extreme: bool`), which is an implementer change before dispatch. 1 trial.
- **H-51b, 30m neighbour.** Config `{"timeframe": "30m"}`, 1,444 one at a time. Reading rule: the same as H-50,
  plus a sign check, since the same economic claim must hold at the neighbouring horizon. 1 trial.
- 1h (907) and 4h (319) are below the P4 floor on 15.7 years and **cannot be research-passed alone**. A promotion
  route for them would need a pooled cross-timeframe screen. That is tooling the program does not have, so it is not
  proposed.
- **Cost.** 2 trials from the Q1 2027 open budget, decided at H-50's verdict. Rank 2.

### H-52 learning speed: admissible forward evidence and measured costs (0 trials; protocol with reading rules)

- **Rule.** Forward shadow evidence counts only after the history-gap fix is deployed (1.1). From then on, each
  clean day gets a parity check. For every roster agent and founder, recompute the frozen rule's candidates on that
  day's broker M1 (`bars_1m_broker`, with history contiguous for the rule's longest window) and compare them with the
  shadow book.
- **Reading rule, parity.**
  - The agent's forward evidence is admissible only if ≥ 95% of (bar, side) signals match both ways, and every
    matched closed trade's barrier and exit bar equal the label on the broker bars.
  - Below 95% on any day: that agent's trades for the day are quarantined, and the cause goes to the implementer
    within 24 h.
- **Reading rule, costs.** At ≥ 30 filled probes, the costs pipeline estimates the mean round trip in $/oz with a
  bootstrap 95% interval.
  - If the upper bound is below the prior ($0.37/oz + bar spread), the cost prior used in **new**
    pre-registrations becomes the measured upper bound. This is prospective only: a verdict already given is never
    re-scored, and R-06 is not reopened. R-06's gross t of 2.4-2.6 cannot clear the DSR bar of t ≈ 4.1 at any
    cost, so it stays retired.
  - If the upper bound is above the prior, the prior is raised to it for every open pre-registration.
- **Cost.** 0 trials. Rank 3. It costs no trial, but without it no forward number means anything.
- **Learning-speed flag (research director / CFO).** At 1 probe a day (section 1.3), 30 fills take about 6 weeks.
  Basing the probe budget on the £50 absolute limit, as ADR 0037 states, would allow about 2 a day. That is a risk
  limit, so it is not decided here.

## 3. When the data can judge anything

The promotion rule needs at least 40 session-day clusters per agent, counted from the first **clean** day after the
fix. Nothing before the fix counts. Expected clusters per weekday are the signal days on 2024-03..2025-09 divided by
413 weekdays:

| Agent | Clusters per weekday | 40 clusters after | Edge visible at 40 (t 2 / DSR t 4.1, SD ≈ 1.2 R) |
|---|---:|---|---|
| box 15m | 0.82 | about 10 weeks | ≥ 0.38 R / ≥ 0.78 R per trade |
| box 30m | 0.58 | about 14 weeks | same |
| box 1h | 0.37 | about 22 weeks | same |
| box 4h | 0.11 | about 70 weeks | same |
| sweep_absorb 15m | ≤ 0.78 (raw rate) | ≥ 10 weeks | same |

So 40 clusters can show only a very large edge. A realistic +0.1 R needs about 600 trades at t 2 and about 2,400 at
the DSR bar, which is years of shadow trading. **Forward shadow judges parity and costs within weeks. The edge
question is answered by the research trials (H-48, H-50), not by the shadow P&L.**

## 4. Ledger and rows owed (for the integrator)

- `hypotheses.md` rows owed:
  - H-50: pre-registered, 1 trial, unfunded in 2026Q4 (funding: (a), (b) or Q1);
  - H-51a/b: pre-registered, conditional, 2 trials;
  - H-52: pre-registered, 0 trials.
- Section C gets a line: "+1 (H-50) and, if conditional, +2 (H-51); DSR count at the verdict never below 108".
- Known to fail, not to be proposed again:
  - re-scoring R-06 tsmom 1h/4h under cheaper measured costs (the DSR bar);
  - 1h/4h box rules as standalone trials (below the event floor);
  - reading any shadow trade opened before the history-gap fix.

## Erratum (research director, 2026-10-19, after the H-50 verdict)

The "one at a time" counts in H-50, H-51 and section 4 (15m 2,404; 30m 1,444; 1h 907; 4h 319) hold each slot for
the full time limit. The research pipeline and the shadow book free the slot at the label exit, which gives 5,835 /
3,037 / 1,625 / 436. The H-50 trial ran the frozen rule, and its STOP stands. The "1h/4h below the event floor"
statement is wrong for 1h. See `prereg-2026-10-19-h50-followup.md` s.2. The text above is left as pre-registered.
