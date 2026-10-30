# 0058: Autonomous strategy discovery under a counted search ledger

Status: proposed (research-director, 2026-10-28; CTO consulted on compute placement). Design:
`docs/research/autonomous-discovery-2026-10-28.md`. Quant review is owed before phase 1's evaluator merges.

## Context
On 2026-10-28 the owner directed: "The system should be able to learn and devise novel new strategies that will just
work and profit." DESIGN "Bounded research loop" limits the loop to "bounded feature and parameter search, not
discovery of novel strategies" (`research/generator.py`). Every new strategy today needs hand-written rule code and a
hand-written pre-registration.

Our evidence: 20 historical trials this quarter, 0 passes. No published gold strategy is proven net of costs. K is
about 358 by the arm-id rule. The literature on data-mined trading rules (Sullivan, Timmermann and White 1999;
Bajgrowicz and Scaillet 2012) shows that a large search without a search-level null produces confident false
discoveries. A generator that runs unconstrained would make the owner's goal less likely to be met.

## Decision
1. **The DESIGN boundary is lifted, with conditions.** The system may generate novel strategies itself (a grammar/GP
   search, combinations of existing families, LLM hypotheses, meta-labelling, and mining of the owner's trades). It may
   do so only through the discovery funnel in the design doc.
2. **Every generated spec counts in K.** It is written to an append-only, hash-chained SQLite ledger
   (`state/discovery.db`) before it is evaluated. The registry's K includes each batch's `k_contrib`.
3. **Selection happens only on the explore window (2010-2017),** which is enforced in code. Advancing needs a beat of
   the same search's null (the 95th percentile of best-of-search on random-side data), White RC/SPA, PBO <= 0.30, a
   plateau test, a 1.5x cost stress, and a novelty check.
4. **The confirm window (2018-01 .. 2025-09)** is reached only by pre-registered registry trials (at most 8 a month,
   one budget slot each, reserved slots never used), read by the frozen rule in design section 3.3. The holdout is read
   once through the existing path. Specs from the owner's trades or from LLM proposals carry contamination flags and
   get a stricter forward read.
5. **Demo arms** are data rows run by one reviewed interpreter (`spec_rule`), loaded only on demo accounts, through
   the ADR 0045 weekly tier (at most 4 reviewed variants a week across all sources). **Live eligibility** uses the
   existing promotion, population and `passed_gates` gates unchanged.
6. **Compute:** the search and history reads run on the owner's Mac (where the local-only data lives). The brain
   runs only the interpreter and the forward read. GitHub Actions runs CI only. Incremental spend is £0; paid compute
   stays an owner decision.
7. **Never research variables:** RiskGate as the only path, the 1% per-trade cap, the daily loss limit, stops,
   owner-confirmed real-money entries, ungated exits, `icm-live`, and live sizing.

## Consequences
- The system can propose strategies nobody wrote. The expected yield is low (judgement prior: about 1 in 10^5 specs
  becomes live-eligible; a first confirm pass is more likely than not to take until 2027-Q1, and possibly longer). The
  scorecard reports funnel counts, including zero survivors.
- The DSR bar for discovered candidates rises with every spec searched. That is intended.
- 2027Q1 allocates 6 of its 14 open slots to discovery confirms. A December confirm batch needs a Q4 extension of +8
  by the program-director under the 2026-10-18 precedent.
- A new pre-registered stop rule: after 48 confirms with no pass, the funnel goes back to the quarterly SoA refresh,
  and the budget returns to hand-written hypotheses and exits research.
- Reviews owed: the quant-reviewer for the evaluator, the null and the confirm rule (phases 1-3); the
  trading-safety-reviewer and the quant-reviewer for the `spec_rule` interpreter (phase 4).
- Related: ADR 0004, 0038, 0043, 0045, 0048, 0055; DESIGN "Bounded research loop" (amended by this record).

## Amendment 2026-10-30 (pre-registered; quant review of phase 2)
Written before any real-data nightly search has run, so it binds every explore survivor from the first night on.

1. **Multiplicity across nights (F2).** Every night adds specs to one ledger, so the K that a survivor pays for is the
   cumulative K_search of all nights, not tonight's. The survivors report states the cumulative K_search (and the new
   specs of the night), the cumulative count of distinct explore survivors over all nights (from the ledger), and per
   survivor its DSR at the cumulative K_search. **Rule: advancing an explore survivor to a confirm trial requires
   DSR >= 0.95 (the gates' bar) at the cumulative K_search at the time it is judged, on 200+ events.** Below that it stays a recorded
   fact. Caveat: K is cumulative, but the cross-trial variance of the Sharpe ratios inside the DSR is tonight's batch
   only (the other nights' daily series are not kept), and `batch_dsr` takes its Sharpe variance from the
   floor-passers only (nightly.py), which reads the DSR somewhat high; a night with an unusually narrow batch reads a slightly higher
   DSR than a pooled variance would give.
2. **Plateau ratio (step 6).** Besides "at least 60% of the grid neighbours positive", the neighbours' mean fitness
   must be at least 0.5 x the peak's (a knife-edge optimum with barely positive neighbours fails).
3. **K_eff deferred.** The ONC clustering behind K_eff is not built. The DSR uses the raw K_search, which is at least
   K_eff, so the reading is conservative; K_eff stays reported as not computed until it is built and reviewed.
4. **Shift null and fitness (F1, F4).** The circular-shift null moves the signal by whole weeks in calendar time (whole
   days on a sample shorter than 40 weeks) and scores excess R over the drift baseline of the same hour and side; the
   G2 fitness is the min-fold t of that excess R. A pure calendar rule therefore cannot pass the shift null (whole-week mode; the day fallback is for synthetic/short
   samples only), and
   gold's drift does not count as skill against the driftless G0 null.
5. **Registry link is a phase-3 precondition (F3).** Decision 2's "the registry's K includes each batch's k_contrib"
   is not wired in phase 2 (strict xfail `tests/research/autodisc/test_registry_link.py`). Two reasons it waits:
   design 3.1 says independent human hypotheses keep the registry's K and only discovered candidates (and hypotheses
   inspired by them) read the DSR at the full K, so it is a separate count, not an addition to `n_trials_effective`;
   and a new registry row status synced to a host on an older build would read as a trial and take a budget slot
   (`is_trial` counts every unknown status). Phase 3 adds the `discovery_batch` row (no trial, no budget slot) with
   `n_trials_discovery` = registry K + sum of k_contrib, and no discovered spec reaches a confirm trial before it does.

## Amendment 2026-10-30 (phase 3: confirm and holdout; quant review owed before the first confirm read)
1. **The batch rows live in a sidecar.** `discovery_batch` rows are written to `<registry>.discovery.jsonl`, not the
   registry's rows: an older host counts every unknown status as a trial, and registry_sync would carry a new status to
   it. Options weighed: a reused non-trial status (`withdrawn`/`verdict`/`preregistered` all carry meaning that would
   move reservations or verdicts) or a sidecar; the sidecar was chosen. `record` refuses the status. The sidecar is
   rebuilt from the ledger when lost (`k_link.sync_discovery_k` tops it up to the ledger's K_search after every real
   night and before every confirm step), so the K never falls below the ledger's. It is local to the Mac, where every
   confirm read runs. What would change this: a confirm read on another host, which would need the sidecar synced.
2. **The confirm read is rule-only** on the outcome tensor (the explore evaluator and conservative cost model), not the
   `SpecSpecialist` -> `research_pass` model path of design 3.3. The design's gates are applied where they are defined
   on a trade list: DSR >= 0.95 on 200+ events at K = `n_trials_discovery` + the batch, and the positive years (3,
   one of 2021/2022). The model-only checks (1,500 candidates, per-fold sizes) do not apply. The rest of the frozen rule
   is design 3.3's: Holm at 5% over the month's batch (frozen when the batch is read; a read batch is closed), net mean
   R > 0 at 1.5x cost, and the confirm mean inside the explore 90% prediction interval (explore mean +- 1.645 x explore
   se x sqrt(1 + n_explore / n_confirm), frozen in the pre-registration). Consequence: a passer is never
   `passed_gates`; promotion and live eligibility are unchanged, and a passer can only become a demo arm. The calibrated
   model path is a later item, required before any discovered spec goes near live (ADR 0038).
3. **One read per stage per hash, journalled first.** Each confirm and holdout read is journalled (hash-chained
   `stage_reads`, unique per stage and hash) before it looks; a read that crashes is closed void, charged as a trial and
   stopped. A failed or voided read is final. The holdout read is only for a confirm passer whose verdict row says
   "continue", once (`holdout_scored`), never for a `holdout_contaminated` spec, and charged one budget slot as any
   holdout scoring is.
4. **Registration follows the stages.** `autodisc-register` registers confirm or holdout passers by default. An explore
   survivor registers only behind `--stage explore` (a demo-learning arm, journalled), and a spec with a failed or
   voided stage read never registers.
5. **Budget.** Confirm and holdout reads take open budget only (never reserved slots). 2026Q4 has no open slot, so no
   confirm read runs before a program-director extension or 2027Q1. That is unchanged and is an owner/PD matter.
6. **Review round 1 (quant conditions F1-F4, before the first confirm read).** F1: grammar v1's 864 specs (the
   2026-10-30 Mac runs, 288 per timeframe) are one fixed sidecar row, and the explore DSR is read at the registry's
   `n_trials_discovery`, not the ledger's K alone. F2: each survivor keeps its DSR inputs, and advancing re-reads the
   DSR at the registry's discovery K of that day. F3 (A4 cost floor): a survivor advances only if judged on a measured
   cost table with its own net mean R > 0; stage reads refuse priors (no fallback) and a read on other costs fails. F4:
   the holdout also needs the t clustered by session day >= HOLDOUT_MIN_T. A stage read refuses an absent or empty
   search ledger.
