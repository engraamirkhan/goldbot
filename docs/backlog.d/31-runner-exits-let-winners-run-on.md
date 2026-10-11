---
number: 31
rank: 31
title: 'Runner exits: let winners run on opportunity days (ADR 0004 north star)'
stage: Trade management, exit · FIND
size: S–M
status: proposed (after 6; hypothesis via the trial registry)
---
- **31. Runner exits (hypothesis; quant and trading-safety review).** Value: a £50 day at £1,000 is a ~6 R winner;
  only trailing or scale-out exits catch those (FIND). Acceptance:
  - A pre-registered variant set (scale-out at 1–1.5 R plus an ATR trail, held only on flagged opportunity days)
    registered through the trial registry, judged on net R and the right tail (share of ≥ 3 R trades), with
    shadow-book parity to the labels. Registered as H-17's exit pair: V0 fixed target vs V1 scale-and-run
    (`opportunity-days.md` section 3.3). V2 and V3 are diagnostics.
  - **Runner mechanics (code).** `ExitPolicy` gains:
    - `flat_at_local: (zone, "HH:MM") | None`: the rest of the position exits at the first bar close at or after
      that local time on the entry's trading day;
    - `trail_ref: "best" | "swing"`: with `swing`, the stop moves behind the last swing low/high *confirmed* since
      entry (fractal lag as `features.structure` swings, minus `trail_atr` × ATR as a buffer), never loosening.
  - Both run through `policy_step`, so `triple_barrier`, `ShadowBook` and `runner._manage_policies` agree. A parity
    test on one fixture bar path gives the same exit bar and price in all three.
  - DST tests for the clock flat (summer and winter New York).
  - Existing policies are unchanged (all current exit-policy tests pass).
  - Live `modify` only tightens. A clock flat is an automatic close that never waits for approval.
  - Exits stay automatic and are never gated. Depends on: 6 (live exit policies), 30.
