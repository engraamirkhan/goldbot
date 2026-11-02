---
number: 142
rank: 8.9
title: Retire the population loop, decide the news shock blackout, prune offline research code (cleanup phase 2)
stage: 'Operations and research hygiene (ADR 0066 phase 2) · PROTECT · RUN'
size: M
status: 'ready 2026-11-02 (phase 1 = ADR 0066 cleanup release) · accountable: architect (plan), then implementer · reviews: trading-safety-reviewer, quant-reviewer, code-reviewer · due 2026-11-09'
---
- **142. Retire the population loop, decide the news shock blackout, prune offline research code (cleanup phase 2).** Value: RUN. ADR 0066 phase 1 removed the feeds and screens that nothing reads. Phase 2 removes the scheduled research machinery that no live strategy depends on. It also settles one safety rail that only looks active: the news shock blackout (D16) has never fired, because 4,764 polls in 30 days were all stored `scored: 0` (no Anthropic key is set). The scheduler, health and the deploy health window then watch only jobs that serve the main goal (ADR 0004; owner directive 2026-11-02, double capital weekly with net-profitable strategies). It helps neither FIND nor RUN an edge directly. It lowers the cost of running one: fewer jobs that can fail, wake the host or hide a real WARN. Expected profit impact is indirect and uncertain.

  Scope, in three parts that touch different files:
  - **(a) Population loop.** Retire the scheduler jobs `saturday_retrain`, `tournament`, `model_watch`, `recalibrate`, `research_director`, `gap_watch`, `attribution`, `cpcv_quarterly`, `monthly_research` and `autodisc_nightly`, with their settings, health checks and job code. Before anything is removed, the architect lists every live reader of what these jobs write (`state/attribution.json`, champion and calibrator files, CUSUM halts, `gaps`). If a running engine or the RiskGate reads one of them (for example `model_watch`'s CUSUM halt or `recalibrate`'s calibrator), that job stays or its reader goes in the same change, and trading-safety signs off on which. TRACEABILITY rows to update: G1-G6, G11, M29 and M39 (population, tournament, drift), and M16 (CPCV).
  - **(b) News shock blackout.** Pick one option and record it in an ADR, after trading-safety review: (1) fund scoring, which needs an Anthropic key and so is an owner spending decision (see "Needs the owner"); (2) replace the LLM score with a deterministic keyword or source rule that can fire without a key; (3) retire the shock blackout and update D16. The scheduled tier-1 and tier-2 calendar blackouts stay whichever option is chosen. Until it is decided, health must not show the shock blackout as active.
  - **(c) Offline research code.** Prune modules under `goldbot/research/` and `scripts/` that have no caller from a live path, a scheduled job or a research script used in the last 30 days. Pre-registration and trial records, the K counter and the ledger stay (research integrity).

  Acceptance:
  1. `config/settings.yaml` `scheduler` no longer lists the ten jobs. A test asserts that every remaining scheduler job resolves to a registered job function and that none of the ten is registered.
  2. The architect's reader list is in the plan, and each live reader is resolved (kept, or removed in the same change). The trading-safety-reviewer and the quant-reviewer each record a GO in the PR.
  3. The engine, RiskGate and exit tests pass unchanged. `scripts/dry_run.py 1` passes. A deploy health window on the brain shows no missing-job WARN for a retired job.
  4. D16 shows the chosen option, and an ADR records it. If option (2) is chosen, a test shows the rule fires on a fixture headline and blocks entries for `risk.shock_blackout_min` minutes, and never blocks exits. If option (3) is chosen, a test shows the shock blackout is gone and the calendar blackouts still fire.
  5. The pruned research modules are listed in the PR with their last caller. `ruff`, `mypy` and the touched tests are green, and `scripts/ci_local.sh` passes once on the release branch.
  6. No server data is deleted (`state/`, `data/`, backups). The rollback is a redeploy of the previous SHA.

  Dependencies: the ADR 0066 cleanup release is merged and deployed (phase 1). (b) option 1 needs the owner. TRACEABILITY: D16, G1-G6, G11, M16, M29, M39.
