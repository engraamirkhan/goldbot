# 0057: A strategy arm's day absorbs three losses; icm-probe pot re-split to £168 / 14

Status: proposed (implementer, 2026-10-28). Quant approved with conditions C1-C5, applied 2026-10-28 (the floor's
basis below is C4); trading-safety passed.

## Context
On 2026-10-27 the exploration arms made 257 fills against a target of 500. `arm_budget` refused 86 candidates, 69 of
them on icm-probe. On icm-probe, 17 scalp strategy arms share the part of the £138 scalp pot that the controls leave
(S0 0.25, S0R 0.05, F1C 0.05, XC15 0.10, so 55%). Each arm gets 1/17 of that: 3.24% of £138 = £4.47. One 1m trade at
its $3.00 stop loses about £2.26 plus a round trip of about £0.12. So a single loss spends most of an arm's day, and
the next candidate is refused. The first-trade rule (2026-10-26) only guarantees one trade.

The brief proposed raising `SHARE_FLOOR` (goldbot/engine/exploration.py). That would not work:
- `SHARE_FLOOR` is only the fallback that applies when the bandit weights cannot be read. The live path uses
  `exploration_data.bounded_shares`.
- In both paths the floor is applied before the scaling by (1 - control shares). Equal shares of 17 arms (5.9% each)
  are already above the 5% floor.
- The shares must sum to 1 inside the strategy mass. 17 arms x 3 losses x £2.4-3.5 = £120-180 cannot fit in the
  £92 the controls leave, so no share floor can give every arm three losses.
- A higher share floor would also change the bandit propensities and loosen H-61's pre-registered bounds
  ("floor 5% per live arm, cap 30%").

Pre-registered inputs:
- prereg-2026-10-27-scalp-volume.md section 3.4 and amendment (a) 7.1: "icm-probe's scalp pot `max_open` goes
  12 -> 14, inside the unchanged `max_open_total` 17 ... The pot is re-split to £168 (section 3.4); the £198 and
  `budget_share` are unchanged".
- volume-500-plan-2026-10-26.md 2.3 B: "the probe sleeve keeps £30 (100 probes ≈ £12 drift, tiny variance), the
  scalp pot gets £168 (from £138)".

## Decision
1. **A room floor, not a share floor.** The `arm_budget` room of a strategy arm is now max(share x pot, floor)
   minus today's realised loss and open risk, where:
   - floor = min(ARM_ROOM_LOSSES x (the arm's per-trade cap + the round trip), SHARE_CAP x the pot), and
     ARM_ROOM_LOSSES = 3;
   - the cap at SHARE_CAP (0.30) of the pot stops a wide-stop arm from taking a disproportionate slice (quant C4).
     Example: an icm-demo 1h arm at £30 in the £68 intraday pot gets £20.4, not £93.
   - The floor rests on the per-trade cap, not on the trade being sent, so it does not depend on the candidate's own
     stop.
   - It extends the 2026-10-26 first-trade rule: an arm can now absorb three losses at its cap.
   - Controls keep their fixed share (they are the null, "never allocated by results").
   - Unchanged: the bandit shares and the propensities logged with every candidate and refusal, `SHARE_FLOOR`,
     H-61's `FLOOR_SHARE` and cap, and the `bandit_version`.
   - Still binding, in the order journalled: the per-trade cap (stop + round trip), the host budget (£198), the
     style pot, max_open per arm, pot and host, the -8% pause and the RiskGate.
2. **The pre-registered re-split on icm-probe** (config/accounts.yaml):
   - `exploration.budget_share` 0.6273 -> 0.7637 (£168.01); scalp pot £138 / 12 -> £168 / 14;
   - `cost_probe.budget_share` 0.2727 -> 0.1363 (£29.99). The sum stays 0.9 = £198;
   - `cost_probe.scheduled.budget_share` 0.25 -> 0.5, so the scheduled probes keep their £15;
   - `cost_probe.max_loss` 45 -> 10, a tightening forced by the load check (max_loss <= the sleeve). £10 is also
     the dynamic per-probe ceiling: the £30 sleeve over 3 slots.
   - Unchanged: `max_open_total` 17, `max_positions` 18, the per-arm £6.50 caps and the daily £220.

3. **The switch and the estimand** (quant C3; prereg-2026-10-27-scalp-volume.md amendment (d) 10.2):
   - Each engine journals `exploration_room_floor` at start, with the floor's terms. A host's first such row is the
     switch time, and fills sent before it are flagged pre-floor.
   - The primary read is rule-only over every admissible candidate. IPW cannot undo a refusal with mu = 0, so the
     filled subset is not the estimand.

## Consequences
- **Volume.** About 50-69 more fills a day on icm-probe (most of the 69 refusals), subject to the pot.
  - With the pot at £168, a 1m arm at £4.47 x 168/138 = £5.44 could still take only two losses.
  - With the floor at 3 x (£6.50 + about £0.12) = £19.9 (under 0.30 x £168 = £50.4), a scalp arm absorbs about 8
    one-minute losses or 5 five-minute ones.
  - The pot binds on a bad day: 17 x £19.9 + the controls' £76 exceeds £168. That is intended: the pot is the
    backstop, and its total is unchanged. Prereg 7.1 puts P(bind) at 1.9-3.5% at D1.5. The floor moves room between
    arms; it does not add any.
- **The bandit's weight is diluted at the bottom.** An arm the bandit ranks low still gets three losses a day. H-61's
  floor already intends that every live arm keeps being sampled. Reads use the logged propensities (share), and a
  room refusal is a fixed rule like the first-trade rule. Hence the rule-only primary read (decision 3).
- **Mirror probes.** With a £10 per-probe ceiling, a 1h mirror probe at about £16.5 at its stop is skipped. 4h
  mirrors already were. The plan's probe volume comes from scheduled probes (unchanged £15).
- **Rollback.** Config: restore the old values in config/accounts.yaml. Code: ARM_ROOM_LOSSES = 0 restores the
  share-only room (the first-trade rule stays). A rollback is journalled the same way (the start row says 0).
