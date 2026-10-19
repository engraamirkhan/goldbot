# 0038: Rule models: deterministic rule agents in the registry without a probability

Status: proposed (implementer, 2026-10-19)

## Context
The owner's box strategy (`box_break_close`, docs/research/shadow-roster-2026-10-19.md) is a rule, not a trained
model. The engine and the shadow book trade an agent only through a registry model (DESIGN: "no champion -> no model
-> no proposal; no placeholder probabilities"). A rule agent needs a way into the shadow book that does not invent a
probability.

## Decision
`goldbot.research.rule_model.RuleModel`: `predict` returns 1.0, meaning "the rule fired", and `calibrated_p` is
`False`. `ModelRegistry.ensure_rule_challenger` registers one as a challenger (no backtest) for each roster rule agent
with no registry entry; the shadow-host engine calls it before loading its shadow set. `Engine._decide` refuses to size
a live entry from an uncalibrated model (`uncalibrated_rule_model`).

## Consequences
- Every rule candidate is recorded in the shadow book as taken, with no raw score, so the weekly recalibration skips it.
- No backtest on the entry: the weekly challenger gate cannot promote it. It expires after CHALLENGER_MAX_WEEKS like any
  challenger; the agent then follows the normal retraining path (a trained meta-label model on its candidates).
- Live trading of a rule needs a passed research trial and a calibrated model through the existing gates; the replica
  agents stay shadow only (`shadow_only`) regardless. By the trading-safety ruling of 2026-10-19 the demo cost probe
  may mirror a frozen roster rule agent on a RuleModel (`cost_probe._mirrorable`); TradingView agents never. Such a
  probe measures cost only and never counts as the agent's evidence.
