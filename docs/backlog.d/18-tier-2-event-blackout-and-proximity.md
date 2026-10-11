---
number: 18
rank: 21
title: Tier-2 event blackout and proximity feature (G-3)
stage: Market and macro preparation · RUN
size: S
status: 'data part in review (2026-10-10: calendar-v1 tiers 1+2, opt-in features); blackout proposed (owner: adopt)'
---
- **18 (G-3).** Tier-2 USD events (PPI, retail sales, ISM, GDP, JOLTS, claims, FOMC minutes, Fed chair) get a
  −5/+15 min entry blackout from config, plus a `min_to_next_tier2` feature. A test per tier.
  *Data part done (2026-10-10; review fixes 2026-10-11; row D27, ADR 0008):* the point-in-time US calendar on
  release `calendar-v1`, 2010 onwards, tiers 1 and 2 (finding E-1, for H-16/H-17/H-18).
  - Tier 1: CPI, NFP and PCE from FRED; FOMC statement and press conference from the Fed's calendars.
  - Tier 2: PPI, retail sales, `gdp_advance` and the later `gdp` estimates, JOLTS and claims from FRED; FOMC minutes;
    ISM by rule.
  - Hand-checked cancellations; per-agency shutdown floors; per-type backfill; mass-cancellation, spacing and count
    guards.
  - Opt-in features `tier1_events` and `tier2_events` (`min_to_next_tier2_pit`, `min_since_last_tier2_pit`,
    `tier2_count_today`, and the tier-1 equivalents), loaded with `research_pass.py --us-calendar`.
  - Open:
    - the owner adds the `FRED_API_KEY` secret and runs the workflow;
    - a quant review of the first run's count table, before any trial cites the features;
    - promoting `gdp_advance` to tier 1 is an owner / trading-safety decision;
    - the RiskGate blackout waits for the owner's adopt decision.
  *Gate part built 2026-10-11, off by default (TRACEABILITY R36):* `risk.blackout.tier2: {enabled: false,
  before_min: 5, after_min: 15}` on the live Forex Factory calendar (calendar-v1 is not loaded live: the live feed
  carries tier 2). The G-3 list is tier 2 whatever impact the feed gives it (`TIER2_PATTERNS`; claims was tier 3 as
  Medium). Reason `tier2_blackout`; enabled with no calendar archived around now = `calendar_unavailable` (fail
  closed). Owner: adopt. Needs trading-safety review (and quant review of the claims re-tiering).
