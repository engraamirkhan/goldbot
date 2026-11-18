# Arm lifecycle: the automatic forward read and decision, and the hot-loaded arm registry (2026-11-18)

Status: **registered before any arm is read under it** (owner directive 2026-11-18: "the system must find, deploy,
measure, promote and retire strategies by itself"; research-director builds 2 and 3). Binding safety conditions:
trading-safety-reviewer requirements of 2026-11-18 (items 1-4 below). Code: `goldbot/research/arm_lifecycle.py`,
`goldbot/engine/arm_registry.py`, the nightly job `arm_lifecycle` (`goldbot/ops/jobs.py`). Thresholds are module
constants, never settings.

## 1. The forward read (build 2)

Per (broker, arm), every night at 06:25 UTC for the session day before, after the 06:15 daily review:

| Figure | Definition |
|---|---|
| net GBP, trades | closed filled trades of the arm in the host ledger (`exploration_trades`), every demo probe_only account of the broker; a close with no readable P&L counts as a loss at its stop plus its round trip |
| fills | the arm's gold fills with a fill label and an r2 replay label (`read_obs`, replay r2, evidence `posterior`); a learning arm's from its `since` |
| mean R | mean `label_fill_r` over those fills |
| live-minus-replay gap | mean of `fill_r - replay_r` over the arm's own fills (the S5 quantity) |
| excess over the matched replay twin | per fill: `fill_r - (twin + raise)`; twin = mean r2 replay label of the arm's matched control's candidates (taken or not) in the same UTC-hour cell, decided on or before the fill's session day; raise = max(0, the replay-parity scope's latest reported mean gap) |

Matched control: the strategy ranking's pairing (`api.strategy_labels`), a learning arm's `learning.control`. This
is HV Rule 3 as amended on 2026-11-15 (a control's replay labels stand in for its fills only while the replay-parity
monitor validates them).

Who is decided: roles strategy and learning, running, not spec-retired. Never decided: controls, cost arms, owner
experiments, and any fixed-window experiment registered to run to its end (`params.end_utc`: the red-box week arms
RB*/RBC* run to 2026-11-22 22:00 UTC).

## 2. The decision

The test is the one the replay-parity monitor uses (`eval2.forward.e_edge`: one term per session day, a running
maximum, anytime-valid, so the nightly re-read costs no false decision). Two one-sided streams per arm on the excess:
**worse** (H0: excess >= 0) and **better** (H0: excess <= 0).

| Constant | Value | Source |
|---|---|---|
| E_PER_STREAM | 20 | 1/q with ADR 0085's q = 0.05 (the parity monitor's and E_KILL_EXEC's form) |
| E_RETIRE | 40 | E_PER_STREAM x the arm's 2 streams (Bonferroni over the arm's two) |
| SIZE_UP bar | 40 x m | m = arms readable for SIZE_UP in the run (Bonferroni: a size-up widens risk) |
| MIN_FILLS / MIN_DAYS | 50 / 10 | EVAL-2 MIN_FILLS_ON_TRACK, S5_MIN_DAYS |
| MAX_UNMATCHED | 10 % | HV Rule 3 "not readable" |

* **RETIRE** when a kill rule fires (a learning arm's loss cap, its read at 300 admissible fills, its last day) or
  the twin is validated, the read has MIN_FILLS over MIN_DAYS, at most MAX_UNMATCHED unmatched, and the worse stream
  reaches E_RETIRE. Applied through the engine's own retirement (aux.db `exploration_arm_status`, read once a minute
  by `refresh_retired`): no new entry, every exit of an open position kept; R153 de-registers it once flat.
* **SIZE_UP** when the same validity holds, the arm is a qualified strategy arm (never a learning arm: "a learning
  arm trades the minimum lot"), the better stream reaches the SIZE_UP bar, and its mean R and net GBP are positive.
  **A proposal only** (journalled, `applied` false): trading-safety item 2 makes size part of the arm hash, a resize
  a NEW arm, and the registry refuses a lot above the minimum. A resize therefore goes through review.
* **HOLD** otherwise, with the reason (control unvalidated, insufficient, not readable, no evidence).

Every read and decision is journalled (aux.db `arm_lifecycle_reads`, one row per day, broker and arm).

Seam: the replay-parity monitor (`research/replay_parity.py`) lives on branch `feat/replay-only-controls` (e7d219a9,
not on main when this was written). Until it is deployed `parity_gaps` returns no scope, every twin reads
unvalidated and only the kill rules decide. Once merged the job reads it with no code change.

Power, stated plainly: at 3 fills a day the worse stream reaches 40 on a true 0.8 R deficit within about 30 session
days; a 0.1 R deficit is not detectable inside a quarter. This rule removes clearly bad arms, it does not rank close
ones. Before the parity monitor validates a scope (300 fills over 10 days per broker x band), nothing is decided on
evidence.

## 3. The hot-loaded arm registry (build 3)

aux.db `arm_registry` (migration 37), written only by `python -m goldbot.ops.run autodisc-register --to-db
STATE_DIR` after its journal row; append-only and hash-chained. The engine reads it at the start and once a minute
at a pass boundary (`exploration._fast`).

1. **Demo predicate** (`risk.gate.is_demo`): accounts.yaml `mode: demo`, no live gate in the phase file, the terminal
   reporting trade_mode "demo", probe_only; anything missing or unreadable is not demo. Checked at the load, on every
   re-read and at the signal. Every hot-loaded arm sends through purpose `exploration`, so the RiskGate fence checks
   the same facts again.
2. **Row checks**: the spec_arms.yaml checks (`exploration.spec_arm_row`: validation, frozen hash, the 2026-11-13
   ruling, the ADR 0061 qualification stamp), rule `spec_rule`, role strategy, `max_open` 1, params only {spec,
   spec_hash, prereg}, no size, growth, adds, purpose, exit or bracket field, a style with a pot, a per-trade cap in
   (0, the style's ceiling], gold's book, a live timeframe, a free id and key. A bad row refuses only its arm.
3. **Failure modes**: a broken chain refuses every hot-loaded entry and alerts (`arm_registry_chain_broken`); a read
   error keeps the last good load for 3 reads, then refuses; a load that fails part way applies nothing; after a
   restart nothing hot-loaded enters before a full validated load, and no position is closed as an orphan until one
   has succeeded (exits run from the position's own brackets). Retirement blocks entries only. The kill switch,
   halts, propose-only and drift bind hot-loaded arms as any arm.
4. **Forward evidence** (`forward:` / `adr0088:`) qualifies demo arms only: `qualification_refusal(live_class=True)`
   refuses it, and `phase_gate_refusal` rejects it.

## 4. What would change this

A quant ruling that the replay twin is not a fair stand-in for a fill (beyond the parity bound) would hold every
evidence decision. A trading-safety ruling that lets a registered resize run would turn SIZE_UP from a proposal into
a new hashed arm registered by the job.
