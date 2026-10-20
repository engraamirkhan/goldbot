---
number: 91
rank: 27
title: 'Retired screened families: refuse open-budget rule_ref runs and count them in the shared exploration cap'
stage: Research (governance) · PROVE
size: S
status: 'proposed (research director, quant review of research/h50-followup, 2026-10-19) · due 2026-12-18, before any 2027Q1 dispatch · accountable: implementer · reviews: quant-reviewer'
---
- **91. Retired screened families: refuse open-budget rule_ref runs and count them in the shared exploration cap.**
  - Value: two governance gaps let a retired, screened family (`box_break_close` R-15, `sweep_absorb` R-14) take
    trials outside the retired families' shared exploration trial.
    1. `TrialRegistry.check_quarter_budget` (`goldbot/research/registry.py` 640-669) refuses a retired family only on
       a contingent slot. An open-budget research.yml run citing a `rule_ref` is not refused.
    2. `build_plan` (`goldbot/research/director.py` about 1486) builds `retired_map` from the allocated
       `research_families` only. A screened retired family is therefore outside `retired_exploration`, and the
       director can give the same quarter's shared trial to another retired family.
  - Until this is fixed, the rule is procedural: H-53 is the 2027Q1 shared retired-family exploration trial
    (`prereg-2026-10-19-h50-followup.md` s.3). The research director dispatches no other retired-family trial that
    quarter.
  - Acceptance:
    - a research.yml / research_pass run on a family in `research.retired_families` is refused unless no retired-family
      trial is stamped in the quarter yet (`retired_quarter_trials` < `RETIRED_EXPLORATION`);
    - the director's `retired_exploration` counts trials on every retired family, allocated or screened, and reports
      the quarter's shared trial as used once H-53's row exists;
    - tests cover both paths, including a screened family;
    - quant-reviewer signs off.
