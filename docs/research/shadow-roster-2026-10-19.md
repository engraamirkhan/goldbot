# Shadow roster, 2026-10-19 (pre-registration, frozen)

Owner directive 2026-10-19 ("the system needs to learn, so more trades need to be opened and closed") and the research
director's ruling of the same day: research rule families run as SHADOW agents (capital weight 0) on 15m/1h/4h.
Written before any shadow trade of these agents. Code: `goldbot.research.population.SHADOW_ROSTER`.

## Rules
- One parameter set per family (its pre-registered/default form); no per-agent tuning. Roster families are
  `frozen_config`: the tournament never clones them with mutations.
- Roster capped at 40 (family x timeframe x params). Only roster agents are promotable from this program.
- Promotion: at most one promotion per family per quarter; net-of-cost expectancy > 0; deflated Sharpe above the gate
  (0.95) at full K; holdout out-of-sample pass; the evaluator's verdict before any follow-up. Never best-of-N by P&L.
- K: every roster agent is a population member, so the tournament's DSR uses `n_pop = len(self.members)`
  (goldbot/research/population.py, `Population.tournament`), which counts every roster config. Promotion to live also
  needs a passed research trial of the exact configuration (`research_passed`, ops/jobs.py `tournament`).
- DSR K_eff is computed over roster agents clustered by return correlation; the three timeframes of one family count
  as one cluster for the promotion decision. The 40-trade minimum counts per-agent per-session-day trade clusters
  (trades of one agent on one session day are one cluster), not raw trades.
- Demo cost probes may mirror a roster rule agent's taken shadow entries (trading-safety ruling 2026-10-19,
  `cost_probe._mirrorable`): cost measurement only, never the agent's evidence.
- Replica agents stay shadow only (`Specialist.shadow_only`); their registry model is a `RuleModel` (ADR 0038).

## Roster (3 of 40)

| agent | family | timeframe | parameters |
|---|---|---|---|
| founder, config `{}` | box_break_close | 15m | module constants of goldbot/specialists/box_break_close.py: BREAK_DEPTH 0.2, mfi 30/70 over 5 bars turning, swing trend not against, not in the golden zone, stop 1.5 ATR(14) beyond the box (structural stop within it), target 3.0 ATR (2R), 96 bars |
| `{"timeframe": "1h"}` | box_break_close | 1h | same, 48 bars |
| `{"timeframe": "4h"}` | box_break_close | 4h | same, 24 bars |

Agent ids are `AgentIdentity(family, config).agent_id` (the population lists them as "roster founder").

## Not admitted yet (TODO, each needs its own lane)
- box_break, box_retest, box_rip, fakey, trend_level_signal, box_course (H-37..H-45): no live state path. They read
  the 1h/4h/daily context bars (the engine passes none to candidates), seeded ATR/EMA and a whole-year tick-coverage
  flag (research value differs from a live year-to-date one), 4h boxes need >300 4h bars (the engine keeps ~240), and
  their structural R (`barrier_atr`) is not what the engine/shadow book size with (ATR x stop_atr); trend_level_signal
  enters at the next open. Running them now would make shadow outcomes differ from their research labels.
- sweep_absorb (H-48), eq_liquidity_run (H-49): no code on the branch.
- price_action groups: no live state path (research only).
- The clustered K_eff and the session-day trade clusters above are the rule for the promotion decision; the
  tournament code still uses n_pop and raw trades (stricter on K, looser on trade count): a follow-up lane implements
  them before any roster promotion is decided.
