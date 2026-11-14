---
number: 21
title: Per-agent loss-streak and entry-rate throttle (G-6)
stage: Discipline (system analogue) · RUN
size: S
status: 'auto-trigger (sweep 2026-11-14): built (PR #73), off on demo by design. The trading-safety-reviewer sets it in any first live candidate''s go-live settings diff · accountable: trading-safety-reviewer'
---
- **21 (G-6).** After 3 consecutive full stop-outs in a risk day, or more than N entries in a session, an agent
  makes no entries until the next session. Logged and alerted. Never affects exits.
  **Built 2026-10-11, off by default (TRACEABILITY R35):** `risk.throttle: {enabled: false, loss_streak: 3,
  cooldown_bars: 4, max_entries_per_day_per_agent: 3}`; the clearing rule is the cooldown (bars of the agent's
  timeframe) or a winning shadow trade, and the rate is per risk day (00:00 UTC), per agent per account. Owner: set
  the thresholds and enable (playbook section 7, item 4). Needs trading-safety review.
  Sweep 2026-10-16 (product owner): Built, off by default (PR #73, main d28a482; c8e9f07); parked: owner sets the thresholds.
