# 0033: A repair row supersedes a wrongly failed trial row

Status: proposed (implementer, 2026-10-18; needs quant-reviewer approval before the registry is published)

## Context
Research batch 2026-10-18 (B01-B04): GitHub's expressions hold numbers as doubles, so `toJSON(matrix.cell)` handed the
cells `3` where the plan said `3.0`. `AgentIdentity.agent_id` hashes the JSON of the config, so the agent the plan
named differed from the one the cell ran, `cells._read_cell_rows` refused the rows as foreign, and `record` wrote a
`failed` trial row in their place: 12 trials (#21-28, #30-32, #35) carry no results although the cells ran.

The registry is append-only in practice: every writer (research.yml, preregister-quarter.yml, the VPS monthly loop)
publishes the keyed union (`registry_sync.merge_rows`) of its copy and the release's. Overwriting a row therefore does
not stick: any copy still holding the failed row (the VPS's) would union it back, adding 12 trials to K.

Options considered for the cause: (a) hash numbers by the specialist's typed config so 3 and 3.0 give one id; (b)
canonicalise at the cell boundary. (a) changes `agent_id` for every existing configuration with a whole float (shadow
founders, the queue, `passed_gates` all match on it) and there is no typed per-specialist config to parse through.

## Decision
1. Cause: `cells.plan` emits `canonical` variants (whole floats as ints, the fixed point of the matrix's encoding), so
   the cell runs exactly the variant the plan names; `_planned` canonicalises too, so a plan written earlier matches.
   CI cells already ran the int form, so no agent id of a recorded row changes.
2. Repair: a repaired row takes the failed row's timestamp (its slot in the time-ordered numbering, so the trial
   number and K hold) and carries `repair: {replaces: <failed row key>, trial, cell_ts, source, reason}`.
   `merge_rows` drops a row named by a `repair.replaces` only if that row is `failed`, is not itself a repair, carries
   the trial number `repair.trial` names, and the repair row's own `ts` is `replaces.ts` (it takes the failed row's
   slot), so a union of any copies is still idempotent and order-free, and a repair can never remove a real trial
   or move one in the numbering (quant review).
   Every repair row records its provenance: `run_id` (checked with `gh run view`: a successful `research` run),
   the run's `head_sha` and the `rows_sha256` of the cell's rows file; the script refuses a run that fails the check.
3. A cell's artifact may not carry `repair` (the record step fails such a cell), so only `scripts/registry_repair.py`
   writes one; it replaces one failed row with exactly one cell row from exactly one run that finished before the
   failed row was written, and refuses if any trial number or K would change. It never publishes.

## Consequences
- The registry keeps 35 trials and K = 35; the 12 trials gain their screen results; numbering is unchanged.
- A repaired row's `ts` is the record time, not the run time (`repair.cell_ts`); `quarter_trials` is unaffected (same
  quarter).
- Residual: a local `research_pass.py --variants '[{"x": 3.0}]'` still names a different agent than a CI run of
  `{"x": 3}`; the same rule can carry two ids across local and CI runs. Unchanged by this decision.
- Publishing is the standard `scripts/registry_release.sh save` after review; the union with the release copy then
  drops the 12 failed rows.
