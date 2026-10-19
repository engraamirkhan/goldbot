# 0040: Retired roster rules keep running on demo, probes included

Status: accepted (research director, 2026-10-19). Needs the quant-reviewer's and the trading-safety reviewer's sign-off
before release.

## Context
The evaluator read STOP for H-48 (`sweep_absorb`, trial #88) and for H-50 (`box_break_close` 15m, trial #89). Both
are frozen roster rules (`shadow-roster-2026-10-19.md`) and both run as shadow agents on the demo shadow host. Their
STOP clauses come from `prereg-2026-10-19-live-review.md` H-50 and `rollout-plan-2026-10-19.md` s.3:
1. retire the rule in `research.retired_families`;
2. keep the agents as shadow parity monitors with no promotion route;
3. "probes stop mirroring them".

Since those clauses were written, the owner has set two directives (memories owner-strategy-live and
demo-for-learning):
- his box break-and-close + MFI strategy keeps running in the engine;
- the demo account exists to learn, so maximise closed demo trades within the loss caps.

The owner has asked that research decisions not come back to him.

## Decision
- Clauses 1 and 2 are applied as written:
  - `sweep_absorb` (R-14) and `box_break_close` (R-15) are added to `research.retired_families`;
  - no real-money route opens: these agents are `shadow_only` (live share forced to 0, `goldbot/engine/runner.py:1084`)
    and a `RuleModel` is refused as `uncalibrated_rule_model` (`runner.py:1088`); `passed_gates` alone would not stop a
    retired family whose later trial (for example H-53) passed;
  - H-51a/b lapse.
- Clause 3 is **not applied**. The cost probe keeps mirroring the roster agents' shadow entries, unchanged
  (`cost_probe._mirrorable`).
- The deviation is disclosed in `prereg-2026-10-19-h50-followup.md` s.1 and in the R-15 row.

## Consequences
- **Ambition gained.** More demo fills.
  - Probes are the only measured-cost source, and H-52 needs 30 of them.
  - The owner's own strategy stays visible on demo.
- **Safety unchanged.** Every probe limit stays as it is:
  - 0.01 lot;
  - demo-only lock (account, terminal, auto mode);
  - RiskGate;
  - per-probe loss cap and the probe budget share of the daily limit;
  - own magic number.

  No real-money path is opened. Exits stay automatic.
- **Cost accepted.** Small, bounded demo losses on rules with negative net expectancy (about -0.3 R and -0.55 R per
  trade at full cost). These losses are demo money inside the probe budget.
- **Integrity.**
  - Probes stay out of `closed_trades`, promotion and every verdict, so no retired rule's probe P&L is ever read as
    evidence.
  - If the program-director or CFO later wants the probe budget for a non-retired agent, the round-robin can exclude
    `research.retired_families`. That is a one-line change in `_mirrorable` and needs a trading-safety review.
- **Reversal.** Set clause 3 back by the change above. No other state depends on this decision.
