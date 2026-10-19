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

## Amendment 2026-10-19 (later the same day): candidate-borne barriers, sweep_absorb admitted

Written before any shadow trade of the added agent. The roster is now **4 of 40**; K for the promotion DSR is
`n_pop = len(self.members)` as above, so it rises by one with this agent (its own cluster: one family, one timeframe).

- Live state path (`goldbot/engine/shadow.py`): a candidate that carries its own R (`risk_usd`, what its
  `barrier_atr` gives the labels), target in R (`target_atr`) or time exit (`max_bars`) is shadow-traded on exactly
  those (`candidate_barriers`; the cost threshold in the same R), and a `barriers_on_1m` family's barriers are read on
  the decision bar's 1m bars (`ShadowBook.on_bar(minutes=)`, as `triple_barrier_1m`: the stop first only inside one
  minute; without the minutes, the decision bar with the stop first, the conservative side). Tests reproduce
  `one_at_a_time(triple_barrier_1m(...))` trade by trade (tests/test_shadow_candidate_barriers.py).
- Such an agent is `shadow_only` (a candidate-borne R or 1m barriers: the live path sizes with label_spec x ATR(14), so
  it could not trade live as labelled). A demo cost probe may mirror it under the existing ruling (frozen roster rule
  agent with a RuleModel); the probe's stop is the shadow trade's structural stop.
- Admitted: `sweep_absorb` (H-48), 15m, its pre-registered configuration `{"rung": "prereg"}`, frozen
  (`frozen_config`), RuleModel. Its rule reads only the 15m bars (previous day / previous session levels, ATR(14)
  converged on the engine's 800-bar tail: tested equal to the whole series) and the `in_blackout` column when the frame
  has one, so the engine's decision frame is its full input.

| agent | family | timeframe | parameters |
|---|---|---|---|
| `{"rung": "prereg"}` | sweep_absorb | 15m | module constants of goldbot/specialists/sweep_absorb.py: pierce < 0.5 ATR(14) of pdh/pdl/previous session extreme, reclaimed within 2 bars, London/New York, stop beyond the sweep extreme + 0.25 ATR (0.6-1.0 ATR), target 2R, 16 bars, 1m barriers |

## Not admitted yet (TODO, each needs its own lane)
- box_break, box_retest, box_rip, fakey, trend_level_signal, box_course (H-37..H-45): no live state path. They read
  the 1h/4h/daily context bars (the engine passes none to candidates), seeded ATR/EMA and a whole-year tick-coverage
  flag (research value differs from a live year-to-date one), 4h boxes need >300 4h bars (the engine keeps ~240), and
  their structural R (`barrier_atr`) is not what the engine/shadow book size with (ATR x stop_atr); trend_level_signal
  enters at the next open. Running them now would make shadow outcomes differ from their research labels.
- sweep_absorb (H-48): admitted by the amendment above.
- eq_liquidity_run (H-49): its code exists and its barriers would now reach the shadow book, but its pre-registration
  runs stage 2 only after the stage-1 base-rate result passes (`dispatch_refusal`, docs/research/kb5-stage1.json not
  committed): it is not shadow-traded before that gate.
- Box-course families: the candidate-borne R (`risk_usd`, `max_bars`) is no longer a blocker for the shadow book's
  barriers, but the rest stands (context bars, >300 4h bars, seeded ATR/EMA, the coverage flag, the selected rung, the
  next-open entry of trend_level_signal).
- price_action groups: no live state path (research only).
- The clustered K_eff and the session-day trade clusters above are the rule for the promotion decision; the
  tournament code still uses n_pop and raw trades (stricter on K, looser on trade count): a follow-up lane implements
  them before any roster promotion is decided.
