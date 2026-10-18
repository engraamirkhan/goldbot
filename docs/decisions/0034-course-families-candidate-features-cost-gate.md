# 0034: Course families: candidate-borne features, per-candidate exits, cost gate read on a charged screen

Status: proposed (implementer, 2026-10-18; for the quant reviewer and the research director)

## Context
The box and price-action course families (H-37..H-45, `docs/research/box-mfi-families-2026-10-18.md`) were
pre-registered with three things the research pipeline could not express: a structural stop per candidate (R is the
distance to a box edge or a wick, not an ATR multiple), pooled rungs that mix signal timeframes (a 4h fakey and a 1h
fakey, or a 15m and a 1h box break, each with its own time exit), and H-40's filter discovery, whose 21 flag groups
are facts of each candidate (its box, its side, its break depth), not columns of the decision frame. The research
director also ruled (section 10, item 2) that H-37 be "cost-gated first: if gross expectancy per trade is below
0.85R on the pre-registered feature screen, retire it without a trial".

## Decision
1. **R per candidate** through the existing `Specialist.barrier_atr` hook (R on candidate bars, NaN elsewhere) with
   `BarrierSpec(stop_atr=1, target_atr=2)`: the pre-registered K-T fallback ("the target is exactly 2R") and K-X
   fallback (100% at the target) without touching `BarrierSpec`, the engine or the shadow book.
2. **Per-candidate time exit**: `triple_barrier` / `triple_barrier_delayed` read an optional `max_bars` column of the
   signals (absent: `spec.max_bars`, output unchanged).
3. **Next-open entry** (`Specialist.entry_at_next_open`, H-45): the labels fill at the first 1m open after the signal
   close, reusing `triple_barrier_delayed` with zero delay; research refuses to label it without the 1m bars.
4. **Candidate-borne features** (`Specialist.candidate_features`, `candidate_feature_groups`): `pipeline.prepare` adds
   them to the candidate rows; `DiscoveryConfig.pool = "candidate"` screens exactly them, grouped by the specialist's
   map, so K_eff is the pre-registered group count. The setting is left out of the dump at its default, so every
   existing discovery config hash holds.
5. **The cost gate is read on the charged screen, not run as a free look.** `screen.py` already states the rule: "the
   screen is a look at the data that selects rules, so each screened configuration is one trial". A gross mean R is
   such a look, so an uncharged gate would select a rule on outcomes without paying for it in K. H-37 carries
   `cost_gate_r = 0.85`; its screen reports "Cost gate: RETIRE" when the gross mean R is below it, and no follow-up
   trial is run. The threshold is the figure stated before the run, never re-derived from it.

## Consequences
- The families are research-only (`screening`): their trials are rule-only screens, they never become shadow
  founders, and the engine (which sizes from its own ATR and ignores `barrier_atr` and `max_bars` columns) never runs
  them. A PASS needs K-T/K-X-style engine work and the trading-safety review before any shadow or live use.
- H-37 costs one trial to retire by the gate. If the research director wants it retired with no trial, that is a
  change to the screen-as-trial rule and is the quant reviewer's call, recorded as an amendment to this decision.
- H-40's survivor rule names a second condition ("Welch t >= 2 inside the training folds") that the discovery code
  does not compute (it reports selection frequencies and stability only). Before H-40 runs, either that statistic is
  added to the discovery output or the research director amends the survivor rule to the coded one (group stability
  >= 0.6 at `group_top_k` 3).
