- Backlog throughput plan (program-director, 2026-10-16, owner request): `docs/process/throughput-plan.md` measures
  the flow from git:
  - the last 25 items: median cycle ~24 h, with 4 items at 114–127 h;
  - 2.2 review rounds per item, and 75% of items needed 2 or more;
  - work waited up to 117 h off `main`, while a PR, once opened, sat 9–68 min;
  - 31 of 47 merges had conflict events, mostly in the rendered docs.

  It ranks 8 levers: (1) daily release with auto-merge on recorded reviews; (2) a shift-left review packet and a
  definition of ready; (3) parallel reviewers with one fix round; (4) scripted docs merge, ADR reservation and the
  dispatch checklist; (5) a review SLA; (6) a test-speed budget; (7) a weekly owner batch; (8) the `runner.py`
  split. Rollout runs 10-17 – 10-30, with an accountable role for each step. Forecast for the 13
  engineering-closeable items: ~10-22 under the plan, against ~10-30 today. No safety rail, review or gate changes.
