# 0007 Contingent trial slots and withdrawal

Status: accepted (quant ruling on the Q1 2027 reservation, 2026-10-10; quant re-review pending). No owner decision is
needed: the quarterly budget (20) and the gates are unchanged.

## Context
`research.reserved_trials_quarter: 13` was meant to hold the Q1 2027 plan (`docs/research/preregistration-2027Q1.md`).
Only 5 of its rows can be queued before 2027-01-01 (H-02, H-01, H-06, H-04, tsmom 4h continuity). H-02's survivor
rows (at most 4) can only be configured after H-02 runs, in the quarter, so they are ad-hoc pre-registrations
(`queue=False`) that never draw on the reservation (`director.reserved_trials`, `registry.check_budget_reserved`).
The quant check (the real reservation functions on the Q1 scenario) found that the reservation shrinks only by trials
run against queued rows: after the 5 queued trials, 13 - 5 = 8 trials stay held all quarter and can never be spent,
while the survivors compete for the 7 open trials with the director's pool and the label grid.

## Decision
- **The reservation is a priority device, not multiple-testing accounting.** It decides who may spend the quarter's
  budget first; the deflated Sharpe counts every trial row (and every discovery's K_eff) whatever slot it used.
  Reserving more or less never changes the trial count a result is deflated by.
- **Contingent slots** (`TrialRegistry.reserve_slot(parent_ref, kind, k, derivation_rule, target_quarter)`): a
  queued `preregistered` row with no config hash and a `slot` key, written before its quarter, holding one trial for
  a run chosen later by a deterministic rule written now. Kinds: `survivor` (the parent discovery's k-th survivor by
  recorded stability), `followup` (the k-th follow-up of a parent read "continue"), `holdout` (the holdout scoring of
  survivor trial k).
- **A slot holds only while its condition can still be met**, derived from existing rows (no extra row is written):
  survivor k is held while the parent has not run and live once it recorded >= k survivors (`results.discovery`
  `selected`, or `survivor_groups` for whole-group survivors, ranked by stability); holdout k is held until survivor
  trial k runs and live only if it passed its gates; anything else (fewer survivors, a failed gate, a withdrawn
  parent) frees it.
- **Using a slot:** an in-quarter ad-hoc pre-registration carries `slot_ref`; `check_budget_reserved` covers its run
  only if the slot is live and of the quarter, the config's `from_discovery` is the parent's trial number, and the
  claimed `survivor` is the k-th of the parent's recorded ranking (a holdout: survivor trial k's passing config).
  Anything else raises `TrialBudgetExceeded`, with no fallback to the open budget (withdraw the pre-registration to
  run from there). The chain slot -> ad-hoc row -> trial row is linked, and a slot is used by its first trial only.
- **Withdrawal** (`TrialRegistry.withdraw(ref, reason)`): a `withdrawn` row naming a pre-registration that has not
  run. It is not a trial and uses no budget; the named row leaves the queue.
- **Formula:** reserved = max(setting - run - withdrawn - expired slots, pending, 0).
- **Q1 2027:** `reserved_trials_quarter: 9` = 5 queued + 4 H-02 survivor slots (pre-registration amended 2026-10-10,
  before the freeze; no reading rule changes).
- Rows name each other by timestamp and config hash (`row_ref`), which `registry_sync` keeps; the registry stamps
  pre-registrations, slots and withdrawals one microsecond apart when its clock ticks coarsely, so names stay unique.

## Consequences
- With 4 survivors run, Q1 can spend all 20 trials (0 locked, against 8 under the old setting); with no survivors the
  4 slots are freed by their rule after H-02 runs.
- `from_discovery` holds the parent's trial number. `registry_sync` renumbers rows on merge, so a config written
  before a merge that reorders earlier trials would no longer match; merges keep time order, so this needs rows
  written out of order on two hosts, and the refusal fails closed (no reserved trial is spent).
- Group-unit discovery (quant ruling Q-3) refuses survivor groups whose members exceed `max_selected` instead of
  truncating them (the look is still recorded as the trial, with no survivors), and a group-unit config must set
  `group_top_k` explicitly.
