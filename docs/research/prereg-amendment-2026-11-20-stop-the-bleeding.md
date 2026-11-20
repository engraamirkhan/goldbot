# Amendment 2026-11-20: stop the bleeding (every gold control replay-only, nr4_break 5m stays retired, no 5m Asia)

Status: **in force with the release that carries it** (owner goal 2026-11-20, "stop known demo losses that carry no
information"; system audit 2026-11-20, performance-analyst and finance-auditor lanes; PD + PO). Amends
prereg-amendment-2026-11-15-replay-only-controls.md (section 1 point 4), prereg-amendment-2026-11-10-execution-controls.md
and prereg-amendment-2026-11-07-kill-only.md. The originals are not edited. Kill-only: every change below can only
refuse an entry. Pending: quant-reviewer and trading-safety-reviewer sign-off.

## Why (evidence, demo GBP, exploration ledger pulled 2026-11-20 ~10:55 UTC)

- **Broker-traded controls are the biggest leak and add no information over a replay twin.** VEX15, VEX1H and
  IEX1H lost **-GBP 471.27 on 99 fills from 2026-11-13 to 2026-11-20** (VEX15 -227.90 n 73, VEX1H -125.99 n 14,
  IEX1H -117.38 n 12) at GBP 9-19 a R. Controls were 67 % of trades and -GBP 981 since 1 Nov (-0.124 R, CI -0.194 ..
  -0.055). Controls to signal fills since 9 Nov: 2.0 : 1 (owner rule A4: at most 1 : 1). The 15 Nov why-fail audit
  ordered them replaced by replay twins by 11-22. Their cost of record (GBP 0.35 a fill, about 0.02-0.04 R) is
  measured on more than 150 fills; their R is direction (VEX15 -0.129 R, CI -0.48 .. +0.23), which is what a
  replay labels without paying for it.
- **nr4_break 5m**: -0.333 R (n 56, CI -0.650 .. -0.016), negative on both brokers (Z6D -0.319, Z6V -0.347).
- **Gold 5m Asia** (decision before 07:00 UTC): -0.167 R (n 512, CI -0.228 .. -0.107, t -6.2), **negative before
  costs** (gross -0.129 R): not a cost problem. [02:00, 07:00) is already refused (window_0207, 2026-11-07);
  [00:00, 02:00) is not: 192 fills since 1 Nov, -GBP 36.76.

## 1. Every gold control on a trading account is replay_only (amends 2026-11-15 section 1 point 4)

- `control_mode: replay_only` on **VEX15, VEX1H, IEX1H, S0R, RBC15, RBC1H, RBC4H** (S0 is already). Not hashed
  (run policy). None is retired, so each keeps recording every candidate for its replay twin.
- The 11-15 rule was "an unpaired gold random control is replay_only". It is now **every** gold control registered
  on an enabled account, paired or not, with one dated exception (GS1C, below). Reasons per row:
  - VEX15/VEX1H/IEX1H: the execution reads keep the fills they have (cost per R, entry-leg fill against replay);
    rule 3's sample stop and the 2026-12-09 end are unchanged, and no new fill is added to them. Section 4 rule 1
    (cost stop) cannot fire without fills, which is the intended state.
    Rule 3's read is truncated at the 2026-11-20 sample: it is reported with each cell's n, and no cell under 100
    fills becomes a cost of record. Rule 2 (entry fill against replay) stops adding data on 2026-11-20 (quant C1).
  - **GS1C stays broker-traded until GS1's owner-experiment end (2026-11-27), then must be replay_only.** It is
    paired 1:1 with GS1 (A4 holds on icm-demo: 1 control, 1 signal arm), it is not a leak (+GBP 118.64 on 26 fills
    since 2026-11-02; -GBP 7.04 on 7 since 11-13; expected saving about GBP 2.5 a week), and switching it would
    change the GS1 read's matched-control method in its last week: the growth read (`research.growth_ledger`) scores
    a refused GS1C candidate as a shadow fill at the replay's `label_sim_r`, net of the **modelled** costs, while
    GS1's fills are net of the **realised** costs. The audit measured the modelled cost at about 2.8 x the realised,
    so shadow GS1C would read worse than broker GS1C and bias the paired excess **in GS1's favour**. Not acceptable
    for a read 7 days from its end. The exception lapses on its own: the test that pins this section fails from
    2026-11-28 (the day after `research.arms.OWNER_EXPERIMENTS`' GS1 end) until GS1C is replay_only or retired, so
    GS1C can never become an unpaired broker control after GS1 stops entering.
  - S0R: its registered read reached 100 stop exits on 2026-11-19 (221 fills: 100 stop, 115 target, 6 hold) and
    it stopped itself. Replay-only only makes that stop independent of a read count; a section-4 extension to
    n = 200 would need its own amendment anyway.
  - RBC15/RBC1H/RBC4H: replay-only by their rule already (every candidate skipped `replay_only`); the mode adds the
    `_send` guard, so the rule changing can never put them on the broker.
- Not changed: the ADR 0062 weekend lane's WKS0/WKI0 (vantage-247, disabled). They measure a broker-made weekend
  quote, which no replay can. The test that pins this section fails if vantage-247 is enabled while they are
  broker-traded, so enabling it needs its own dated decision.
- **Controls to signal arms, per account:** broker-traded controls never outnumber the account's signal arms
  (tested on the config). Today: icm-demo 1 (GS1C) to 1 (GS1), icm-probe 0, vantage-demo 0.

## 2. nr4_break 5m stays retired

- Z6D and Z6V carry `retired_day: 2026-11-06` (A5 history-bounded entries); their last fill was 2026-11-06 14:50
  UTC. Nothing to change in config. A test now fails if any nr4_break 5m row of the spec is not retired, so a new
  id cannot reopen it without editing that test and this amendment. The autodisc grammar's daily `nr4`/`nr7`
  regime condition is a different variable and is not affected.

## 3. No gold 5m entry decided in Asia (amends 2026-11-07 kill-only, section 3)

- **Rule:** every in-scope gold 5m exploration signal with decision time in **[00:00, 07:00) UTC** is refused
  (`exploration_refused:asia_5m`; `goldbot/engine/entry_refusals.py` `asia_5m`), as an engine-level refusal after
  the rule produced the signal and before any room, slot or cap check, and shadowed through the untaken-candidate
  path (`exploration_obs`, `blocked_reason` asia_5m). Scope is window_0207's (gold, every role but cost and the cost
  arms' controls, not the weekend lane) narrowed to timeframe 5m. Decision time is the rule's own decision close.
- Ordered after window_0207, so [02:00, 07:00) keeps its name and its counts; asia_5m names [00:00, 02:00).
- Static (arm, configuration and decision time only): the daily budget's session shares count only the signals it
  lets through, as for window_0207.
- 21:00-24:00 UTC ("asia(late)" in the audit) is not in the rule: the evidence is for decisions before 07:00 only.
- Today no 5m arm is configured to enter on any enabled account (the last 5m fill was 2026-11-13 12:20 UTC); the
  rule binds the next 5m arm, including a hot-loaded discovered one (5m is a live timeframe).

- **Quant C2:** a later 5m read cannot use the Asia cut as evidence of edge. Any 5m backtest or read applies the cut
  ex-ante to both the arm and its control, and the 512 fills from 2026-11-01 to 2026-11-13 are never reused as
  evidence for any 5m arm.
- **Quant C3:** lifting the cut needs its own pre-registered read.

## 4. What does not change

- K, the quarterly trial budget, the holdout, the phase gate, every threshold and reading rule. No arm is added,
  removed or re-registered; no config_hash, rule, timeframe, params or max_open changes.
- Exits are never gated: an open position of any arm named here keeps its stop, target, hold and every exit (a
  replay-only switch and a refusal act on entries only). RiskGate, the daily loss limits and every cap are untouched.

## Expected effect

- In expectation a random control costs its round trip, about GBP 0.35 a fill: at the 2026-11-13..20 rate (99
  fills a week) that is about **GBP 35 a week** saved (S0R had already stopped; GS1C is kept to 2026-11-27).
- With the controls' measured gross since 1 Nov (-0.027 R a fill) on top of the cost, about -0.06 R a fill at GBP
  9.5 (15m) and GBP 19 (1h) a R: about **GBP 70 a week**. So the expected saving is **GBP 35-70 a week**.
- The realised leak was GBP 471 that week, almost all direction at 15m/1h stops: about 3 standard deviations
  (GBP 140 a week on those three arms, from their per-fill P&L sd of GBP 11.5 / 19.6 / 19.7) beyond the cost alone.
  Removing them removes that variance from the demo P&L; it does not save GBP 471 every week in expectation.
- asia_5m: about GBP 0 now (no 5m arm enters); at the 1-13 Nov 5m rate it would have saved about GBP 18 a week.

## Bias statement

Written after the 2026-11-06..20 fills and their P&L were seen. Acceptable because it only removes broker volume
and entries (kill-only), keeps every control's candidates for replay, and changes no threshold, reading rule,
window or matched-control method of any read (GS1C is kept broker-traded for exactly that reason, section 1).

## Rollback

Set the rows' `control_mode` back to `broker` (unhashed) or drop `ASIA_5M` from `entry_refusals.REFUSALS`, and
restart the engines. Either needs a dated amendment.
