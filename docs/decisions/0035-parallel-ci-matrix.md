# 0035: Parallel CI matrix: weighted integration shards, smoke gate, deployable signal

Status: proposed (sre, 2026-10-18; CTO to accept)

## Context
CI took a 16.7 min median (p90 19.8 min, 28 runs, docs/process/ci-performance-2026-10-18.md). One job,
backend-integration (96 tests, 756 s of pytest at `-n auto`), was about 13 min of it; the unit shards finished in under
3 min. The repo is public, so minutes are free; the limits that matter are wall-clock and the account's 20 concurrent
jobs. Three correctness gaps came with the old shape: main runs could cancel each other (`cancel-in-progress` on every
ref), docs-only PRs never reported the required checks (`paths-ignore`) although unit tests and the research director
read docs, and Playwright retries let a flaky e2e test pass.

## Decision
1. Shard the critical path, not everything: integration 4 shards with `-n auto` each; the known-heavy tests are spread
   first (longest-processing-time) by a committed weights file, `tests/shard_weights.json`; everything else by the
   existing node-id hash. Unit stays 4 shards (not on the critical path; concurrency cap).
2. A 1-minute `smoke` job (pre-commit including the docs checks, ruff, full test collection) gates the test matrices
   and e2e.
3. Path filtering only on pull requests and fail-safe: prose-only (docs/** and *.md, except docs/research/**) skips
   lint, integration and the frontend jobs; smoke and the unit suite always run; pushes to main run everything; the
   required `backend` / `frontend` aggregates always report.
4. Flakiness is reported, never masked: a failed shard re-runs its failed tests once, alone, and labels them FLAKY or
   FAIL; the job stays failed. Playwright `failOnFlakyTests` in CI.
5. CD stops at a signal: on main, when both aggregates pass, a `goldbot/deployable` commit status and the owner's
   one-line deploy command. CI never deploys and holds no secrets; goldbot-deploy keeps its own CI check.
6. `scripts/ci_local.sh` reads the matrix from ci.yml; `tests/test_ci_workflow.py` guards the invariants (required
   check names = goldbot-deploy's REQUIRED_CHECKS, shard denominators = matrix length, SHA pinning, token scopes).

## Consequences
- Expected run time about 6 min (smoke 1 + slowest integration shard 3.5-4.5); the floor is the longest engine replay
  test, so profiling tests/test_engine.py `_run` is the next lever.
- The weights file can go stale; that costs balance only, never a test. Refresh with `scripts/ci_summary.py weights`.
- Peak 13 concurrent jobs per run: two overlapping runs can queue briefly under the 20-job cap.
- Dependencies are still unlocked (no uv.lock): an upstream release can break an unrelated PR. Recommended follow-up:
  `uv lock` + `uv sync --locked`, then a nightly run (deferred: a scheduled failure would block goldbot-deploy on the
  main head through the `backend` check).
- What would change this: a self-hosted runner (CI_RUNNER) with more cores would favour fewer, larger shards.
