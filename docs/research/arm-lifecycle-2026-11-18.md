# Arm lifecycle: the kill-only read per demo arm, and the hot-loaded arm registry (2026-11-18)

Status: **registered before any arm is read under it** (owner directive 2026-11-18: "the system must find, deploy,
measure, promote and retire strategies by itself"; research-director builds 2 and 3), as revised by the quant,
trading-safety and code reviews of 2026-11-18. Code: `goldbot/research/arm_lifecycle.py`,
`goldbot/engine/arm_registry.py`, the nightly job `arm_lifecycle` (`goldbot/ops/jobs.py`). Thresholds are module
constants, never settings.

**Scope.** Build 2 is a KILL-ONLY lifecycle under HV Rule 3 (as amended 2026-11-15). It is NOT the ADR 0086 forward
read: it retires arms, it never promotes, qualifies or sizes one. Promotion and sizing stay with the reviewed paths.

## 1. The read (build 2)

Per (broker, arm), every night at 06:25 UTC for the session day before, after the 06:15 daily review:

| Figure | Definition |
|---|---|
| net GBP, trades | closed filled trades of the arm in the host ledger (`exploration_trades`), every demo probe_only account of the broker; a close with no readable P&L counts as a loss at its stop plus its round trip |
| fills, mean R | the arm's taken candidates with a fill label; mean `label_fill_r` |
| live-minus-replay gap | mean `fill_r - replay_r` over the arm's own fills (the S5 quantity), reported |
| excess over the matched replay twin | per REPLAYED candidate, taken and refused alike (r2 `label_sim_r` not null): `label_sim_r - twin` |

The twin: the mean r2 replay label of the arm's matched control's candidates (taken or not) in the same **(UTC hour,
side)** cell, decided on or before the candidate's session day (point in time). Replay against replay: no fill cost
enters either side. Matched control: the strategy ranking's pairing (`api.strategy_labels`), a learning arm's
`learning.control`. The replay-parity monitor only **voids** a read (its broker x band scope revoked or not PASS:
HOLD); it never shifts the excess.

**Frozen path.** Each (broker, arm, session day) term, the day's mean excess, is written once (aux.db
`arm_lifecycle_terms`, append-only by trigger), and only once the day's labels have settled: day D freezes when now >=
end(D) + the arm's longest hold (its candidates' time barrier or its row's `hold_bars`; a `spec_rule` arm without one,
its spec's time exit, else 10 days; a hold that reaches a weekend, or starts at the Friday close, gets the weekend
added) + 1 day. The number of D's candidates still unlabelled then is stored with it; if more than 5 % are, D is
stored void (no term) and logged. The e-process reads the stored, non-void terms only, so a later label or control
row never rewrites a past term, and no day is cut short while its labels are still arriving (which would truncate on
the outcome).

Who is read: roles strategy and learning, running, not spec-retired. Never: controls, cost arms, owner experiments.
A fixed-window experiment (`FIXED_WINDOW_PREREGS`, matched on `params.prereg`: today only
`prereg-amendment-2026-11-15-redbox-week`, the RB*/RBC* arms to 2026-11-22 22:00 UTC) is decided by its own kill rules
only, never on evidence. Every decided arm's existing kill rules are read (a learning arm's loss cap, its read at
300 admissible fills, its last day).

## 2. The decision

The test is the one the replay-parity monitor uses (`eval2.forward.e_edge`: one term per session day, a running
maximum, anytime-valid, so the nightly re-read costs no false decision), on the frozen terms. **Worse** (H0: excess
>= 0) decides. **Better** is journalled, labelled "reported, not evidence".

| Constant | Value | Source |
|---|---|---|
| E_RETIRE | 40 | 1/q per stream with ADR 0085's q = 0.05, x the arm's 2 streams (Bonferroni) |
| MIN_N / MIN_DAYS | 50 / 10 | replayed candidates (EVAL-2 MIN_FILLS_ON_TRACK's size), S5_MIN_DAYS |
| MAX_UNMATCHED | 10 % | HV Rule 3 "not readable" |

* **RETIRE** when a kill rule fires, or the twin is validated, the read has MIN_N candidates over MIN_DAYS days, at
  most MAX_UNMATCHED unmatched, and the worse stream reaches E_RETIRE. Applied through the engine's own retirement
  (aux.db `exploration_arm_status`, read once a minute by `refresh_retired`): no new entry, every exit of an open
  position kept; R153 de-registers it once flat. A retirement is permanent for the broker.
* **HOLD** otherwise, with the reason (control unvalidated, insufficient, not readable, fixed window, no evidence).

Every read and decision is journalled (aux.db `arm_lifecycle_reads`, one row per day, broker and arm).

**False retirements.** Under the null (an arm no worse than its twin) the worse stream crosses 40 with probability at
most 1/40: about **2.5 per 100 null arms over their whole life**, whatever the number of nightly looks.

**Power** (quant review 2026-11-18; 3-4 replayed candidates a day, sd about 1 R):

| True deficit vs the twin | Median days to RETIRE |
|---|---|
| 0.8 R | about 30 |
| 0.3 R | about 100 or more |
| 0.1 R | out of reach |

This removes clearly bad arms; it does not rank close ones.

**Seam.** The replay-parity monitor (`research/replay_parity.py`) lives on branch `feat/replay-only-controls`
(e7d219a9, not on main when this was written). Until it is deployed `parity_gaps` returns no scope, every twin reads
unvalidated and only the kill rules decide. Once merged the job reads it with no code change.

## 3. The hot-loaded arm registry (build 3)

aux.db `arm_registry` (migration 37), written only by `python -m goldbot.ops.run autodisc-register --to-db
STATE_DIR` after its journal row; append-only and hash-chained. The engine reads it at the start and once a minute at
a pass boundary (`exploration._fast`), through its own aux.db handle (a deferred read, no migration, no write lock).

1. **Demo predicate** (`risk.gate.is_demo`): accounts.yaml `mode: demo`, no live gate in the phase file, the terminal
   reporting trade_mode "demo", probe_only; anything missing or unreadable is not demo. Checked at the load, on every
   re-read and at the signal. Every hot-loaded arm sends through purpose `exploration`, so the RiskGate fence checks
   the same facts again.
2. **Row checks**: the spec_arms.yaml checks (`exploration.spec_arm_row`: validation, frozen hash, the 2026-11-13
   ruling, the ADR 0061 qualification stamp), rule `spec_rule`, role strategy, `max_open` 1, params only {spec,
   spec_hash, prereg}, no size, growth, adds, purpose, exit or bracket field, a style with a pot, a per-trade cap in
   (0, the style's ceiling], gold's book, a live timeframe, a free id and key. A bad row refuses only its arm. Every
   verified load re-judges every row: a loaded arm whose row is now refused, or whose row is missing, is kept
   configured with its entries refused (never an orphan close).
3. **Integrity**: the highest verified tip (seq, digest) is kept per account OUTSIDE aux.db (core.db
   `arm_registry_seen`). A broken chain, fewer rows than the tip (a removed tail, an emptied table, an older aux.db
   copy) or another digest at its seq refuses every hot-loaded entry and is journalled (`arm_registry_chain_broken`).
   A read error keeps the last good load for 3 reads, then refuses; a load that fails part way applies nothing.
4. **Restart**: nothing hot-loaded enters before a full validated load. When the start cannot load, this account's
   own remembered arms (their own style) are kept with entries held (`start_held`); a retirement is never lifted (no
   subtraction from `retired`). For at most 3 minutes after the start no unknown position is closed as an orphan,
   never past its weekend backstop. The time barrier, stop, target, weekend cut and kill flatten run from the
   position's own persisted brackets and never read the registry.
5. **Health**: the engine publishes since when its registry has been unverified; health FAILs it past 5 minutes,
   whatever the reason (broken chain, stale, unread, not demo).
6. **Forward evidence** (`forward:` / `adr0088:`) qualifies demo arms only: `qualification_refusal(live_class=True)`
   refuses it where an arm is loaded for a non-demo account, and `phase_gate_refusal` rejects it. The engine never
   builds an exploration block for a non-demo account (`Exploration.start`), and the RiskGate fence refuses one.

**Discovered arms are not registered as learning arms (yet).** The review asked that `register_to_db` register a
discovered arm as a learning arm (role learning, learning block, control twin, kill rule) until ADR 0086's forward-read
row type exists. Not done here, on two facts: a learning block must name a K row of the arm that resolves in the trial
registry (`learning_refusal`), which means writing K rows, a trial-budget change outside this lane; and its control
must match its bracket (`control_match_refusal`: stop, target, hold on the row), which a `spec_rule` row does not
carry (its exits are inside the spec). Until a pre-registration lists the discovered arms' K rows and a per-trade
bracket match for `spec_rule` exists (as the red-box rule has), a hot-loaded discovered arm loads and is held by
ADR 0061 (unqualified: no entry).

## 4. What would change this

A quant ruling that the replay twin is not a fair stand-in beyond the parity bound would hold every evidence decision.
ADR 0086's forward-read row type would give discovered arms a demo evidence class and a promotion path; this lifecycle
stays kill-only.

## 5. Dated notes

- 2026-11-18 (research-director ruling): the engine retires a discovered learning arm on the broker at its FIRST void
  bracket at the entry (`engine.arm_registry.entry_refusal`, durable through `exploration_arm_status`), stricter than
  the amendment's 5.1 (5) "3 or more candidates, or any taken candidate"; it stands because failing closed costs only a
  £60-capped demo learning arm, while sending an order whose bracket may have been fixed after the decision would void
  the read it exists for. This amends 5.1 (5) for every discovered arm registered on or after 2026-11-22 22:00 UTC
  (pre-registration, before any such arm exists; quant-reviewer concurs 2026-11-18). The night journals it as
  'bracket integrity: void at entry (engine)'.
