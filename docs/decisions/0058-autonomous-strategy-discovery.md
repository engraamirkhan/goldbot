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
