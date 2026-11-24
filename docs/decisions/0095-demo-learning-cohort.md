# 0095: Demo learning cohort: retired arms send capped demo orders

Status: accepted (2026-11-24, at merge of #440; trading-safety GO WITH CHANGES M1-M2, L1-L4 and quant SIGN WITH CHANGES C1-C4, all applied in review round 1)

## Context

Owner 2026-11-24: "it is quite concerning that it is not trading at all". Since PR #424 (only arms with evidence place
orders), GD-1 is the only arm that reaches the broker, at about two orders a day. The team (PD + research director +
CFO) decided that demo money is for learning. The ten retired arms the weekly self-retrain judges should send real
demo orders again, on icm-probe only, at 0.01 lot, as a capped cohort. Their controls stay replay_only.

The existing demo learning class (`role: learning`, ADR 0061 amendment 2026-11-02) does not fit:

- it books a K row per arm, and this cohort is K = 0 (no new read);
- it allows at most 45 days, and the cohort's test date is 2027-01-15;
- it requires a matched control configured on the same account;
- it would change the role of S1A/S1B/RBR15 (strategy) and F1A (cost).

Un-retiring the rows was rejected. `retired_day` is what the self-retrain splits on (OOS rows only after it), and what
keeps the learner's weights and the family rankings away from these arms.

## Decision

A **learning cohort** is a run-policy block on a demo probe_only account (accounts.yaml `exploration.cohort`,
`ops.accounts.LearningCohort`). It names retired gold arms registered on that account at the minimum lot and gives
them caps:

- a per-arm net loss cap since `since` (stops that arm for good);
- a weekly net loss cap for all of them, charged by exit (holds every arm until Monday 00:00 UTC);
- max open per arm, and its own fills a day and an hour (so it never takes the account's hour from other arms);
- an end date, at most 60 days away.

The engine (`engine/exploration_cohort.py`) admits a named arm at start only when it is retired, gold, not a control,
`control_mode: broker`, not hash-refused, invalid or dark, and inside since..until. For a member, the cohort's caps
replace the evidence stops it was retired by: spec retirement, unqualified refusal, the daily review's evidence-stop
retirements, cost-read completion, cost or edge-cap suspension, and learning-block start and read. Every other cap
still binds, the RiskGate first, and any other aux.db retirement still stops a member. Each cap check counts realised
loss, every close whose record is not written yet (at its stop loss plus cost, the rule `realised` uses), open risk at
the stops and the new trade, and again on the loss at the gate's stop. The check fails closed until the ledger has
been read, and while a given-up close of unknown arm is unrepaired. Every named arm is in the replay `recording` set, so
a stopped member, or one refused at start by the date window, records replay-only. Fills are stamped
`evidence_class: learning_cohort`; the trade ledger lists them as `not_evidence`, out of every ranking, read and
H-61/H-81 stop. A cohort arm registered on a second account
is refused at load. First use: the ten arms on icm-probe, GBP 15 per arm, GBP 50 a week, one open each, until
2027-01-15 (docs/research/prereg-amendment-2026-11-24-demo-learning-cohort.md).

## Consequences

- The account has a bounded way to trade retired arms for fills and costs without touching their evidence status.
  Unrecorded closes are charged before their records land, so an entry is judged on every loss the engine knows of.
  The caps can still be passed by the gap between a fill's loss at its stop and its realised loss (slippage through
  the stop, a weekend gap), as for every exploration cap. All of it sits inside the unchanged GBP 220 day and weekly
  limit.
- More override paths now exist in `engine/exploration.py` (each is guarded by membership, which is decided once at
  the start). Trading-safety review covers them; tests cover each.
- The fills are a selected subset of the candidates (caps, slots, RBR15's GBP 15 per-trade cap). They are execution
  evidence, never an edge read. Quant review covers that.
- What would change this: a cohort that keeps hitting its weekly cap with no cost insight gained (end it early), or a
  self-retrain pass on one of the ten (it then goes the learning-class route with its own K row and review).
