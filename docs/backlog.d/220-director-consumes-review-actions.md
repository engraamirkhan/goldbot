---
number: 220
title: Research director consumes the weekly review's actions once
stage: 'research ops (review loop, architect plan 2026-11-13 item 2)'
size: S
status: 'done (sweep 2026-11-14, deployed with PRs through #393): PR #388 (R141) · accountable: implementer'
---
- **220. Research director consumes the weekly review's actions once.** Value: the review's advice reaches the next research cycle instead of a file nobody opens. `research_director` marks every unconsumed `review_actions` row with its slot (one transaction; a rerun of the slot gets the same set, a later slot none) and the plan lists them in `review_queue`. They never move a trial budget, a share or the focus order. Acceptance: `test_proposals_are_consumed_once`, `test_the_director_plan_carries_the_consumed_actions_and_its_budget_does_not_move`.
