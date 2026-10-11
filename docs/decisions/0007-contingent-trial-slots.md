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
- Group-unit discovery (quant ruling Q-3) never truncates a survivor group, and a group-unit config must set
  `group_top_k` explicitly (forward-while-fits since the amendment below).

## Amendment 2026-10-11 (quant re-review of 1f83a8a; before the freeze)
- **A slot covers only its derived configuration** (`director.slot_refusal`). Survivor or holdout: the parent
  discovery's family and every spec key (`config.specialist`, `config.spec`: label and candidates), `from_discovery`
  naming the parent, the claimed `survivor` the k-th of the pre-registered ranking, and `features` (less `side`)
  exactly that survivor's recorded members (`results.discovery.group_members`). Follow-up: the family and allowed
  configs fixed in the slot row before the quarter (`config_hashes` or `overrides`); a follow-up slot without them
  is refused. A slot never covers a retired family (the retired-family limit is never bypassed; `retired` defaults to
  the settings' list).
- **Slots follow the parent's reading rule.** A survivor slot is live only if the parent discovery's recorded
  reading verdict is "continue" with OOF AUC >= 0.53 and lower bound > 0.50 (the slot's `rule`), AND at least k
  survivors pass under that rule. For H-02 the survivors are its pre-registered hypothesis clusters
  (`survivor_pool`: H-03/05/07/08/10), which H-02 now groups by (`--families cluster`, `discovery.H02_CLUSTERS`), so
  each cluster's group stability is recorded and the ranking is by group stability among clusters >= 0.6, not by
  single features. Derivation-rule text amended in `preregistration-2027Q1.md`.
- **Evaluator verdicts** (`TrialRegistry.record_verdict`, status `verdict`, not a trial, first verdict final): a
  rule-only parent (H-01, H-04, tsmom 4h) writes no reading verdict, so its follow-up slots stay pending until the
  evaluator records one and go live only on "continue". A discovery's own verdict is never replaced.
- **Group mode** runs and records the walk-forward and its verdict first, then forwards whole groups in
  group-stability order while they fit within `max_selected`, recording the rest as `skipped_survivor_groups`
  (K_eff = groups screened still holds). It refuses before any look only when one group exceeds `max_selected`
  (in cluster mode with feature survivors: one cluster).
- A holdout slot expires once its survivor slot was used by a run that is not an evaluated trial of survivor k. No
  holdout slots are written for Q1.
- `from_discovery` holds the parent's `row_ref` (timestamp and config hash, truthy for `passed_gates`), so
  registry_sync renumbering cannot break a match. This supersedes the consequence noted above.
- `research.reserved_trials_quarter` is keyed by quarter (`{"2027Q1": 9}`, default 0), so a setting never applies to
  whatever quarter is current.
- **E-5 cap** (`research.open_budget_preregistered_only: ["2027Q1"]`): research-plan section 5 rules out further
  fast-tsmom horizons (R-06), yet the director's pool would default the 11 open trials to tsmom. In a capped quarter
  the research analyst spends the open budget only on an ad-hoc pre-registration naming a pre-registered rule
  (`preregister(rule_ref=)`, linked to the run) or on the retired families' one shared exploration trial; the label
  grid spends none.

## Amendment 2026-10-11 (second quant re-review; before the freeze)
- **H-02 clusters follow `hypotheses.md` exactly** (`discovery.H02_CLUSTERS`): H-03 = `macro_gvz*` + `macro_vrp`, an
  explicit VRP spread GVZ^2 - HAR-RV^2 built point-in-time inside the opt-in `macro_drivers` (version 3; the default
  feature set and `f-079deacc61` are unchanged; the macro version moved from `f-f999b359e2` to `f-e7d2dc11e1` in both
  pre-registrations); H-05 = `round_numbers`, `support_resistance`, `pdh/pdl/pwh/pwl_dist_atr`; H-07 = VIX and S&P
  stress; H-08 = `sweep_*` + session/Asia/London extreme distances; H-10 = `jumps` only (no news split before the
  freeze unless an opt-in `jump_news` feature and the calendar wiring land). `realised_vol`, `vol_estimators`,
  `swings` and `bos_*` go back to their families. Before any look each cluster must have a non-constant, non-NaN
  column (`check_cluster_columns`, recorded on the row).
- **Group measure: joint (grouped) permutation importance**, not summed or mean member importance. Summed gain
  rewards size: a group of many weak or noise columns accumulates split gain. Mean importance per member is
  size-neutral but deflates a group whose signal is split across near-copies, and dilutes a strong column with weak
  siblings, which reintroduces the bias the quant review of dab47c7 removed. Permuting all of a group's columns with
  one shared row permutation measures what the group as a whole contributes to the fitted model: dead members add
  nothing (size-neutral) and copies are removed together (redundancy-safe). It is computed per subsample on the
  training fold's rows outside the subsample (never a test row); `group_top_k` stays 3. Cost: one prediction per
  group per subsample; with one column per group (column mode) the column's own importance is used, which ranks the
  same. The rows outside a subsample can share label windows with it at week edges; that is the same for every group,
  so it inflates no group against another.
- A follow-up slot is covered only by a config whose hash is listed, or that equals the family's default config plus
  the fixed overrides exactly (no superset).
- `record_verdict` refuses a discovery row (its own recorded verdict reads it), and for a `screened` row computes the
  pre-registered decision from the recorded results (H-01: screen, net t >= 1.65, >= 3 positive net years incl.
  2021/22, long-only; H-04: screen and net mean R > 0; tsmom 4h: net mean R <= 0 retires; else the screen) and refuses
  a mismatch (`director.preregistered_decisions`; the row records `rule_checked`).
- A survivor-slot claim may hold only the parent's spec keys plus `from_discovery`, `survivor` and `features`.
- In an E-5 quarter a manual `research_pass` / `--discover` row records the rule it cites (`rule_ref`: `--rule-ref`,
  the pre-registration's own, its slot or the queued row); a capped run citing none is refused (`director.cited_rule`).
