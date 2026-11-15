# Pre-registration amendment: the red-box week (2026-11-15)

Principal software engineer, 2026-11-15, written before any data under this rule was read. It registers a demo
**learning-class** family (ADR 0061 amendment 2026-11-02): three arms (RB15, RB1H, RB4H) and three replay-only
controls (RBC15, RBC1H, RBC4H), on icm-probe, from the market open of Sunday 2026-11-15 22:00 UTC to Sunday 2026-11-22
22:00 UTC. It is unqualified demo evidence: never a qualification, never in a family ranking, never real money.

## Owner directive (2026-11-15), as given

- "For this week of trading, from tonight's open, trade every red box break and close to the next red box on 15m, 1h
  and 4h, on demo; after a week the team reviews which trades worked, how to make it profitable, risk, etc."
- Correction, the same day: the red boxes are the owner's box indicator that we rebuilt ourselves, the replica's
  support/resistance channels (`sr_channels`, `goldbot/features/smc_replica.py`), not an author's post levels.
- Confirmation, the same day: each arm reads the boxes of its **own** timeframe (15m boxes for the 15m arm, 1h for 1h,
  4h for 4h). Which higher-timeframe (1h/4h) box the price sits in is recorded on each trade as a review feature only.
- No extra filter: every break and close trades. The money-flow state, the swing trend, the golden-zone flag, the
  break depth, the session and the box height are recorded on every trade for the review.

## Rules

Code: `goldbot/research/arm_rules_redbox.py` (rule-code version 1), the same function for history and live.

1. **Boxes.** The replica's channels with the owner's chart settings (`smc_replica.DEFAULT_PARAMS`: pivot 5, width
   1% of the 300-bar range, 15 boxes, lookback 300) on the arm's own timeframe; the boxes in force at bar t as the
   `box_break_close` roster reads them. Live hands the rule 330 bars, more than the channel state's whole memory
   (`smc_replica.context_bars()` = 313), so live and history see the same boxes.
2. **Break and close.** Long when close[t-1] <= top < close[t] for a box; short when close[t-1] >= bottom > close[t].
   Several boxes crossed: the broken box is the farthest crossed. Entry at the next open (the engine's market order
   after the bar close).
3. **Stop.** The broken box's far side minus 0.1 x ATR14 of the arm's bars for a long (plus, for a short): the roster's
   structural stop and buffer (`box_break_close.BUFFER_ATR`). The roster's 1.5 ATR cap is NOT applied (it would be a
   filter). This is defined for every timeframe; no ATR scaling rule is needed.
4. **Target.** The next box beyond: the nearest box edge strictly beyond the signal close in the trade direction, the
   broken box excluded (a box beyond gives its near edge; a box the close sits inside gives its far edge). No such edge:
   the candidate is recorded skipped `no_next_box` and replayed with a hold-only target; it is never sent.
5. **Recorded, never read.** On every candidate, journalled `exploration_redbox_features` keyed by the candidate id:
   box top, bottom and height, break depth (% of box height beyond the broken edge), the next box edge, the money flow
   at t and whether the roster's money-flow confirmation held for the side, the swing trend, the golden-zone flag,
   whether the close sits inside another box, the box count, the UTC session (asia 22-07, london 07-12, overlap 12-16,
   newyork 16-22) and the 1h and 4h box holding the close (for the arms below them), from the engine's 1m bars.
6. **Hold.** The roster's HOLD_BARS: 15m 96 bars, 1h 48, 4h 24. The Friday weekend flat of the demo account applies.
7. **One open per arm** (`max_open: 1`). A break while the arm is open, either direction, is refused by the engine's
   slot rule and recorded with its reason; there is no reversal (the card defines none).
8. **Size and path.** 0.01 lot, every order through the exploration plumbing and the RiskGate (`cost_probe._place`,
   the only path to an order); every exit is automatic and never gated.
9. **Stop band.** A stop below $1.00 or above $40.00 is skipped and recorded (`stop_lt_floor`/`stop_gt_cap`), never
   resized. $40 at 0.01 lot is about £30, inside the arm's £32 per-trade cap with the round trip.
10. **What still refuses an entry** (pre-registered engine rules, unchanged, each signal recorded and replayed): the
    common filters (no entry 20:45-22:15 UTC, none on Friday from 19:00 UTC, tier-1 blackout where it refuses), the
    kill-only `window_0207` refusal (gold entries 02:00-07:00 UTC; prereg-amendment-2026-11-07-kill-only.md), the N-3
    HAR low-range-day abstention for gold learning arms (prereg-2026-11-07-n3-har.md), the daily entry budget, the
    learning slots and every RiskGate cap. This amendment does not loosen any of them; whether the owner's "every"
    should override `window_0207` or N-3 for this family is a separate decision for the research director.

## Control (replay-only)

Per arm, a control on the same timeframe with the same bracket construction (`per_trade_bracket: redbox`, the KBL
card's per-trade bracket ruling C10 applied to boxes): the identical rule on a seeded random ladder with the real
boxes' spacing. The boxes in force are sorted by bottom, their widths and the gaps between neighbours are permuted,
and the ladder is re-anchored at the lowest bottom plus U(-g, g), g the median |gap|; the seed is (arm, the box set to
the cent), so one box set gives one ladder. Every control candidate is recorded skipped `replay_only` in
`exploration_obs` and replayed by `exploration_data.label_candidates`; **no control places an order**.

## Stops and caps

- **Weekly cap:** £100 realised loss per arm since 2026-11-15 (`learning.loss_cap_gbp`), £300 for the family for the
  week. An arm at its cap takes no new entry (`learning_loss_cap`); its open position keeps every exit.
- **Daily cap:** the account's £220 day through the RiskGate, as for every entry; the account's weekly 6% stage too.
- **End:** no candidate decided at or after 2026-11-22 22:00 UTC (`params.end_utc`); `learning.until` 2026-11-22 is
  the backstop (the arm retires on 2026-11-23). After the end the arms get `retired_day` 2026-11-22 in the spec.
- **Also retired at:** 300 admissible fills (the learning read), as every learning arm.

## K

+6 in 2026Q4: one learning K row per arm and one per dedicated control (`scripts/record_learning_k_rows.py`, written on
the brain before the first fill). A control is a look at the data too, so it is counted.

## The review (after 2026-11-22 22:00 UTC)

1. Which trades worked and which failed, by timeframe, session, side and box type (box height, break depth, close
   inside another box, higher-timeframe box membership, money-flow confirmation, swing trend, golden zone).
2. Net P&L after costs (spread, commission, swap) against the control's replayed candidates, per arm and pooled.
3. MFE and MAE per trade (in R and USD, the exploration record), and how often the next-box target was reached.
4. How to make it profitable: which recorded features separate winners from losers (descriptive only; any rule built
   on them is a new hypothesis with its own pre-registration and K), stop and target geometry, timeframe choice.
5. Risk: worst day, worst run of losses, stops skipped by the band, the refusals' counts (window_0207, N-3, slots).
6. The pairs dropped where the arm was refused but its control recorded (refusals act on the arm's entry only).
