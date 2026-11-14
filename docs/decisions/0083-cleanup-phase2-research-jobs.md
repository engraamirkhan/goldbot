# 0083: Cleanup phase 2: retire four research jobs, keep the population loop, news shock by rules

Status: proposed (implementer, 2026-11-14; BACKLOG 142). Needs the trading-safety-reviewer's and the
quant-reviewer's GO before merge.

## Context
BACKLOG 142 (ADR 0066 phase 2) asked to retire ten scheduled research jobs (`saturday_retrain`, `tournament`,
`model_watch`, `recalibrate`, `research_director`, `gap_watch`, `attribution`, `cpcv_quarterly`,
`monthly_research`, `autodisc_nightly`), to settle the news shock blackout (D16), and to prune offline research code
with no consumer. Its rule: "If a running engine or the RiskGate reads one of them ..., that job stays or its reader
goes in the same change". The reader list, from the code on 2026-11-14:

| Job | Writes | Live reader | Downstream reader |
|---|---|---|---|
| `saturday_retrain` | model registry (promotes a challenger to champion) | engine `champions()` / `live_versions()` (ops/services/engine.py) | model_watch, recalibrate |
| `tournament` | population (live status, capital shares) | engine `population_view()` (live shares) | gap founders' slots |
| `model_watch` | registry rollback, state/model_watch.json | engine champions (a rollback changes the live model) | health `model_watch` |
| `recalibrate` | registry minor versions (probability map) | engine champions (p sizes an entry) | - |
| `research_director` | state/research_plan.json; consumes aux.db `review_actions` | none | weekly_review (R141, 2026-11-13) feeds it; `_reinstated` in the trial runner; agents' `read_research_plan` |
| `attribution` | state/attribution.json | none | weekly_review loss drivers, director evidence, agents' `read_attribution` |
| `gap_watch` | population shadow founders, state/gaps.json | none (founders are shadow, share 0) | none |
| `monthly_research` | trial registry rows | none | none (`research.label_grid_paused`: every run only syncs the registry) |
| `cpcv_quarterly` | trial evidence sidecar, state/cpcv_<q>.md | none | none (no trial passes the gates; `research_pass.py --cpcv` stays) |
| `autodisc_nightly` | state/autodisc/ | none | none: skips on the brain unless `research.autodisc.nightly_enabled`; the Mac's launchd runs `scripts/autodisc.py nightly` |

The news shock blackout has been decided since the item was written: ADR 0079 (R144, 2026-11-13) scores every
headline by the free deterministic `rules-v1` scorer when no LLM client exists, and a rule shock feeds the existing
`shock_window` (entries only, never an exit, capped per UTC day). That is BACKLOG 142 (b) option 2.

## Decision
1. Retire `gap_watch`, `monthly_research`, `cpcv_quarterly` and `autodisc_nightly`: settings, scheduler entries,
   job code and tests. `goldbot/ops/gap_watch.py` is deleted (its `daily_realised_vol` moves to
   `research/attribution.py`, the attribution job's regime input) together with the `gaps:` settings section. Their
   keys join `config.RETIRED_KEYS`, so a server `settings.local.yaml` that still names one loads with a warning
   instead of stopping every service. The scheduler's `prune` (ADR 0066) drops their state, so health shows no
   `job:` line for them.
2. Keep `saturday_retrain`, `tournament`, `model_watch` and `recalibrate`. The engine reads what they write; removing
   them, or the engine's reader, changes the order path (which models trade and at what share), and `model_watch`
   is a safety rollback. Retiring the population loop needs its own change that first retires the engine's
   population and champion readers, with trading-safety sign-off.
3. Keep `research_director` and `attribution`. R141's `weekly_review`, one day old, reads attribution and is
   consumed by the director; retiring them would leave that loop without input or consumer. Revisit once the weekly
   review has run for a month.
4. News shock: option 2, as built by ADR 0079. D16 records it. No new code.
5. Not pruned: `Population.spawn_founder` and the reserved gap slots (the tournament's clone rule reads them),
   `research/cpcv.py` (`research_pass.py --cpcv`, autodisc's PBO) and `research/autodisc/nightly.py` (the Mac's run).

## Consequences
- Four fewer jobs that can fail or wake the brain; no trading, exit, RiskGate or gate path changes. The research
  budget, holdout and phase gate are untouched.
- No new gap founders: the shadow population grows only by the tournament's clones. CPCV of a gate-passing trial
  is run by hand (`scripts/research_pass.py --cpcv <trial#>`) when one exists.
- `goldbot/ops/checks/loop.py` still names `monthly_research` and `cpcv_quarterly` in its "no sync reported yet"
  hint (another lane owns that file): cosmetic, fixed there.
- What would change this: a gate-passing trial (bring the quarterly CPCV back), or the engine dropping its
  population and champion readers (then retire the population loop).
- Rollback: redeploy the previous SHA; no server data is deleted (`state/gaps.json`, `state/gap_ledger.jsonl`,
  `state/research_*.md` and `state/cpcv_*.md` stay where they are).

## Amendment 2026-11-14: CPCV moves from the quarterly job to the promotion gate

Context. Quant finding: the design requires CPCV/PBO evidence (research/cpcv.py: 6 groups, 2 test, PBO against the
family's other trials) before a gate-passing trial is promoted, but no promotion or phase-gate code read the
evidence, even while `cpcv_quarterly` ran. Retiring the job (decision 1) left the requirement with no producer at all.

Decision.
1. CPCV is enforced where it matters, at promotion, by `goldbot/research/cpcv_gate.py`. A gate-passing trial
   (`registry.promotion_refusal` is None) is promoted only with CPCV evidence stamped in the promotion's UTC quarter
   that ran (paths, not an error or a skip), ran on the trial's config hash and feature version, has no ADR 0003 veto
   (`cpcv.verdict` recomputed: PBO > `cpcv.PBO_FRAGILE` = 0.5, or more than half the paths with negative mean R), and
   computed PBO whenever the family has other walk-forward trials on the trial's timeframe. A trial with no such peer
   has nothing to compute PBO against; its paths verdict alone decides.
2. Missing evidence is produced on demand at promotion: the trial and its most recent peers (at most 8
   configurations, the old job's cap), the store's bars up to the holdout, the canonical broker's costs and swap,
   stale feature versions skipped. The result is attached to the trial only (evidence sidecar: no trial row, no
   budget slot, no deflated-Sharpe count). A run that raises or skips the trial attaches nothing and refuses; the
   next promotion attempt runs it again.
3. The promotion path wired now is the weekly `tournament` (shadow -> live, `Population.tournament`'s
   `research_passed`): an agent held back is listed with its reason under `cpcv_refused` in the job result.
4. Health check `cpcv_evidence` (goldbot/ops/checks/cpcv.py, tail block position 180) WARNs on any gate-passing
   trial without usable evidence for the current quarter, so a gap is seen before a promotion meets it.

5. Phase gate 1 requires CPCV too, read only (decided 2026-11-14 by the program director and the quant reviewer
   under the owner's delegation of routine research-integrity decisions; a tightening, no threshold changed). Gate
   `backtest_to_paper` has an item `cpcv`: `cpcv_gate.promotion_refusal` on the best trial without a runner, so the
   gate never runs CPCV and missing evidence leaves the item unmet. The evidence is produced by the tournament or by
   `scripts/research_pass.py --cpcv`. Today no trial passes the gates, so gate 1 was unmet already.
6. Quant review fixes: evidence with no backtest path of finite mean R, or with no stated feature version, does not
   count; an on-demand run without a configured holdout window raises instead of reading to the slot; a rerun of the
   trial's own configuration (same config hash) is not a PBO peer (it was no alternative to select among).

Consequences. A gate-passing configuration can reach neither live through the tournament nor paper through phase
gate 1 without CPCV/PBO; the first promotion attempt after a pass costs one CPCV run on the brain (minutes per
configuration, once a quarter). A family with several walk-forward configurations and too few rerunnable ones stays
in shadow until PBO can be computed. What would change this: a quant ruling on PBO for a selection set made only of
screens.
