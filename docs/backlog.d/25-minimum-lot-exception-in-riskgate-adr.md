---
number: 25
rank: 2
title: Minimum-lot exception in RiskGate (ADR 0004)
stage: Position sizing · RUN
size: S
status: '**in review** (built 2026-10-10, off by default; trading-safety review; owner enables on the demo server)'
---
- **25. Minimum-lot exception (needs trading-safety review).** Value: lets tiny-live trade one minimum lot at small
  equity without loosening any limit (Position sizing · RUN). Acceptance:
  - New `risk.min_lot_risk_cap` in settings (validated 0 < x ≤ `max_risk_per_trade`, or null). With null, every
    existing gate test passes unchanged and a test shows decisions identical to today on a grid of equities and
    stops.
  - Applies only when `lots_raw < volume_min`: the gate allows exactly `volume_min` iff its realised risk ≤ the cap
    (half the cap in `SIZE_DOWN` or combined size-down), else refuses `min_lot_exceeds_risk`. Tests, tiny-live, 15m
    stop $10.90, GBP account at GBPUSD 1.33 (after 28): equity £1,000 allowed at 0.01 lot (0.82%); £700 refused
    (1.17%); £1,000 in `SIZE_DOWN` refused (cap 0.5%); a USD account at $1,000 refused (1.09%); `lots_raw ≥
    volume_min` unchanged; never more than `volume_min` under the exception.
  - Margin (max(broker, 1:20), 300% floor), combined notional, caps, stages, blackouts and spread checks still run
    after it: a test at $600 equity with a stop that fits the cap is refused `margin_level_floor`.
  - An allowed exception decision carries reason `min_lot_exception`, realised risk and the phase rate; it reaches
    the decisions log and the approval card's risk line. Exits untouched (test: exit path never calls the gate).
  - Reviews: trading-safety, code; DESIGN Sizing paragraph and TRACEABILITY row updated in the same change.
  - **Day-factor interplay (safety finding, `docs/research/opportunity-days.md` section 3.2).** Without this
    exception (setting null), and after 28, the gate's 1.2× tolerance (`gate.py:275-276`) allows the 15m min lot
    (true 0.82%) at £1,000 only when the multiplier is ≥ 1.37. So a 1.5× opportunity-day factor (item 30) turns
    trading on and off at this equity, rather than scaling size. Test, GBP account at £1,000, 15m stop $10.90:
    with the exception null, the decision under multiplier 1.0 is `min_lot_exceeds_risk` and under 1.5 it is
    allowed (realised 0.82% against a 0.75% target, both recorded on the decision and the approval card); with the
    exception at 1%, both are allowed and the decision does not depend on the multiplier.
  Depends on: 28 at £1,000 (without it the min lot computes as 1.09% and is refused). 19 (heat cap) must count
  exception trades in full when it lands.
  Status (2026-10-10): built (TRACEABILITY R33; tests/test_min_lot_exception.py), `null` in settings.yaml because
  settings have no per-account section; the owner sets `risk: { min_lot_risk_cap: 0.01 }` in the demo server's
  settings.local.yaml. Open: the approval card's risk line does not show the exception (ui-ux).
