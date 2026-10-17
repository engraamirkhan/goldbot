---
number: 68
rank: 9
title: 'Release hygiene: explain the CI-minutes record; clean up and retire worktrees'
stage: Engineering operations (CI cost, disk) · RUN
size: S
status: 'ready (decided 2026-10-16 from the CFO budget and the CTO tech-debt list) · accountable: release-manager · due 2026-10-19 (worktrees), 2026-10-23 (CI minutes)'
---
- **68. Release hygiene: CI minutes and worktrees.** Decided 2026-10-16 by program director + product owner.
  Value (RUN): (d) decides whether PR #81's saving of about 900 minutes a month is cash or only headroom, which the
  CFO needs for the budget and break-even; (f) frees about 28 GB on the Mac and stops lanes starting from stale
  trees. TRACEABILITY: none (process). Depends on: nothing.
  Acceptance:
  - (d) by Fri 2026-10-23: the release-manager explains ROADMAP section 2's "2,000 free minutes a month; October's
    ran out on 2026-10-06" for a public repo (standard GitHub-hosted runners are free for public repos), with
    evidence: the billing or usage reading (which repo, which runner type, whether the repo was private then), cited
    by date. The answer is written into ROADMAP section 2 and the GitHub Actions paragraph of
    `docs/finance/budget-2026-10.md`, and risk 7 is restated or closed accordingly.
  - (f) on Mon 2026-10-19: `git worktree list` counts before and after are recorded on #34 (counts only, no paths):
    about 60 registered, 54 merged, about 28 GB. Every worktree whose branch is merged into `main` or the integration
    branch is removed with `git worktree remove` and pruned; unmerged ones are listed with their owning role. Disk
    freed is reported.
  - (f) standing rule from then on: the release-manager removes a lane's worktree in the same step as its merge,
    written into the release-manager's Definition of done (`docs/AGENT_STANDARDS.md`) and the CTO's tech-debt list.
  - Product-owner acceptance: both records exist on #34 and the standing rule is in the standards.
