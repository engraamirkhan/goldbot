# Pre-registrations H-57..H-59: forward learning arms (2026-10-25)

Research director's delegation plan, drafted by research-director-staff on 2026-10-25. Rules are frozen when this
file is merged and before any qualifying forward trade is read. Format follows `prereg-2026-10-19-live-review.md`.
Draft: the research director signs off; nothing here dispatches a trial.

**Discipline.** The shadow evidence that motivates these arms is hypothesis-generating only, never the test:
10-19..10-23 tsmom-1h n = 12, +0.75R, t 1.68; box 15m n = 7, +0.71R. Small, selected by being looked at, and the
first days carry the history-gap contamination of `prereg-2026-10-19-live-review.md` s.1.1. **Admissible sample:
only trades opened after max(history-gap fix deploy, freeze merge time) that pass the H-52 parity check count; the 12
snooped tsmom trades are excluded.** Untaken outcomes are never promotion evidence (architect ruling 2026-10-25). Measured costs (10-19..10-23):
round trip $0.09/oz, slippage -$0.07/oz. Per H-52 these enter new pre-registrations prospectively as the net-cost
input; the net figure below uses the measured cost per trade on the fill, not a prior.

Budget: 2027Q1 has 20 slots, 6 reserved (H-02 queue and its survivor slots, untouched), 14 open. H-57 and H-58
are charged 1 trial each to the open slots **at FREEZE (merge)**, not at the read. Interim reports give n only,
never mean R or t. H-59 is 0 trials, with its own budget (below).
True K is read at the verdict date: `n_trials_effective` plus the +1 of research-plan.md s.K. Floors: H-57 K >= 115,
H-58 K >= 116 (H-53 holds 112, O-2/O-3 hold 113).
**Shadow-arm costs:** spread once in the label, slippage max(0.15, M) per leg, plus commission and swap per
rollover held; favourable slippage is never credited. Each reading quotes the K it used; if K is higher at the verdict, the t bar is recomputed and the
stricter applies.

## H-57 tsmom 1h sub-threshold band, forward arm (1 trial)

- **Why.** The founder's break-even threshold is 0.36 on the calibrated p. Candidates just below it are untaken
  today, so they produce no outcome. The band is where the threshold choice, not the signal, decides whether we
  learn. This arm measures what the band is worth without moving the production threshold.
- **Rule (frozen).** Family `tsmom`, 1h, the frozen config and model as deployed. A **candidate** is a signal with
  calibrated p in **[0.30, 0.36)**, counted at fixed threshold **0.30**. Candidates at p >= 0.36 are the
  production book and are excluded from this arm. Entry at the next bar open, exits and stops exactly as the label
  (one at a time per agent, session-day clustering). Shadow only: the arm opens no order (the demo agent keeps
  threshold 0.36 unless a later rule changes it).
- **Read.** At **n = 60** admissible one-at-a-time trades, once, at no interim peek for a decision (interim counts
  are reported, never acted on).
- **Pass (all hold).**
  1. Mean net R > 0 after measured costs, in R on each trade's own ATR stop.
  2. t > t_{0.05/K, days-1} (one-sided, day-clustered: session-day sums, SE clustered by day), computed at the
     verdict K (floor 115). It is deliberately below the gate-1 DSR bar of about 4.1. A pass here is a **LEAD**,
     not a promotion; see below. **Power is near zero at n = 60**, so a fail is uninformative.
  3. Long and short reported separately with n, mean R and t each. They are descriptive: a sign flip is noted in
     the verdict and does not change pass or fail.
- **Fail.** Any of 1-2 fails at n = 60 -> STOP and the threshold stays 0.36. A fail reads **"not shown"**, never
  "retired".
- **Consequence of pass.** LEAD only. Promotion needs the gate-1 DSR bar and a research trial of the exact
  config. A pass raises the case for lowering the demo threshold, which is an exploration-rail and risk decision
  for the principals, not decided here.
- **Expected time.** Not known until the clean candidate rate is measured; the research director reports the
  clean rate after 10 clean weekdays. If the expected time to n = 60 exceeds 12 weeks, the arm is re-dated, not
  loosened.
- **Cost.** 1 trial, 2027Q1 open slot, charged at freeze. **Stated prior: P(pass) about 10%.** Gross edge in a
  low-p band is expected to be below the full-sample edge; the one-sided prior is that the band is noise.

## H-58 `box_break_close` 15m and 30m at native stops, forward (1 trial)

- **Rule (frozen).** The roster agents `box_break_close` 15m and 30m exactly as in `shadow-roster-2026-10-19.md`:
  stop 1.5 ATR(14) with the structural stop inside it, target 3.0 ATR (2R), 96 bars max, one at a time per agent.
  **Native stops**: no clamp. Trades where the order stop was clamped by the demo £ cap (as the cost probe was) are
  **not** counted for P&L; the arm is read on the shadow label outcome of the unclamped rule.
- **Read.** At **n = 60** admissible one-at-a-time trades, 15m and 30m **pooled** as one arm (one trial); each
  timeframe also reported alone, descriptively.
- **Pass.** Same bar as H-57: mean net R > 0 after measured costs and day-clustered t > t_{0.05/K, days-1}
  (one-sided) at the verdict K, floor **116** (one higher than H-57). Power is near zero at n = 60; a fail reads
  "not shown". Long/short reported.
- **Fail.** Either fails at n = 60 -> STOP. The 15m/30m box agents stay parity monitors; the 1h/4h agents are
  unaffected.
- **Relation to H-50.** H-50 is the historical research trial of the same 15m rule and reads on 15.7 years. H-58 is
  the forward test and does not substitute for it: if H-50 STOPs, H-58 lapses with 0 trials; if H-50 is not yet
  run, H-58 may be read independently as a LEAD and never as a promotion.
- **1h and 4h.** Not part of this arm. They additionally need the 4h/1h history gap cleared (contiguous broker M1
  history for the rule's longest window) before any of their trades is admissible; 15m and 30m need only the
  H-52 parity check on their shorter windows. 1h (907) and 4h (319) are in any case below the P4 floor on 15.7
  years (H-51 note).
- **Cost.** 1 trial, 2027Q1 open slot, charged at freeze. Stated prior: P(pass) about 5% (H-50: gross prior 0
  to +0.03 R; the shadow n = 7, +0.71R is not evidence).

## H-59 own-stop exploration on a learning demo account (0 trials; safety rail)

- **What.** On a **demo** account used for learning, let a candidate that the production sizing would clamp or
  refuse run with its **own native stop**, at the **minimum lot**, and hold it to its own exit. Purpose: collect
  outcome rows where clamping makes today's probe P&L unreadable.
- **Rails (unchanged, not a research variable).** RiskGate is the only path to an order and is unchanged: 1%
  per-trade cap, daily and weekly loss limits, loss streak, SIZE_DOWN/HALTED all apply. Demo only: live and
  tiny-live are unaffected. Exits are never gated.
- **Stop rule (ADR 0031 Amendment A1 style).** H-59 has its **own** budget, separate from the A1 budget: at most
  **60** trades; stop for good when the running cumulative net R of these trades, in exit order, is at or below
  **-10R** at any point, even if later trades recover. Checked on every exit, not at evaluation time. A re-test is
  a new pre-registration.
- **Pauses.** As A1: ATR rule, SIZE_DOWN/HALTED, cost/model above 1.25 pause it while they hold.
- **Reading.** None for promotion: a 0-trial arm. Rows carry an `exploration` tag and are excluded from population
  evidence (`goldbot/research/population.py:111`); required test: a unit test that an `exploration`-tagged row never
  counts toward population promotion evidence. They enter only descriptive tables and are **never** pooled into
  H-57 or H-58 counts.
- **Cost.** 0 trials. Tagged so that no later selection can quietly use the rows.

## Open questions for the research director

1. Resolved at quant review 2026-10-25: the pass bar is t > t_{0.05/K, days−1} at the verdict K (about 3.3–3.5), still below the gate-1 DSR bar, so a pass is a LEAD only.
2. H-57 is a shadow arm at 0.30; it needs the engine to record p-band candidates as untaken shadow rows (check
   that this is journaled today, else an implementer item).
3. H-59 conflicts with A1 if the minimum-lot floor, not the stop, is what clamps; confirm with the
   trading-safety-reviewer that the own stop at minimum lot fits 1% per-trade on the £10,000 learning demo account for 15m-stop sizes of
   about $10-12.
4. The "14 open" is taken from the brief; the registry should be counted on the dispatch date.
