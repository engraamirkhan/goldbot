---
number: 234
rank: 78
title: Measure rules-v1 shock precision on the brain's real headlines
stage: 'research (follow-up of R144)'
size: S
status: 'proposed; blocked 2026-11-13: brain read access refused in the agent sandbox, needs a session with brain access · accountable: ml-researcher · reviews: quant-reviewer, trading-safety-reviewer'
---
- **234. Measure rules-v1 shock precision on the brain's real headlines.** Value: the fixture is 48 hand-written headlines; it bounds gross errors, not live precision. Acceptance (safety+quant review 2026-11-13): run rules-v1 over the stored headlines and report shocks and blackout minutes per day; hand-label at least 150 collected headlines (every rule shock included) blind to the rule output, store them as a fixture (public headline text only), tune on one half and report precision/recall on the held-out half. Earlier scope: hand-label at least 300 collected headlines (shock, rates, dollar) on the brain (never published), report rule-shock precision/recall and blackout minutes per week; if precision < 0.8, raise `news.rule_shock_min_confidence` or narrow the lexicon as rules-v2 (a new `scorer` value, never a silent rewrite of rules-v1 rows).
