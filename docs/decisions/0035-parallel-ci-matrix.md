# 0035: Parallel CI matrix: weighted integration shards, smoke gate, deployable signal

Status: accepted with conditions (CTO, 2026-10-18; conditions met in this change)

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
3. Path filtering only on pull requests and fail-safe: prose-only (docs/** and *.md, except the RUNTIME_DOCS that runtime code, integration tests or e2e read, audited by a test) skips
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
- Every job installs uv.lock as written (`uv sync --frozen`); smoke fails a stale lock (`uv lock --check`). The
  change filter runs from the PR's base commit. A nightly run stays deferred: a scheduled failure would block goldbot-deploy on
  the main head through the `backend` check.
- Status: accepted with conditions by the CTO on 8b6941e3 (lock-file installs, base-commit filter, docs-read audit);
  all three are in this change.
- What would change this: a self-hosted runner (CI_RUNNER) with more cores would favour fewer, larger shards.

## Amendment 2026-10-19: a 3-minute run
- Goal: the whole PR run in about 3 min (release incl. deploy in 5). Measured before: smoke 46 s then the slowest of 4 integration shards 368 s.
- Nothing waits for smoke any more: it runs beside the shards and still fails the run (`backend` and `report-failure` need it).
- Integration 4 -> 10 shards, unit 4 -> 6; `frontend-checks` is folded into `lint` (renamed from `backend-lint`) so the run peaks at exactly 20 concurrent jobs (smoke, lint, e2e x2, unit x6, integration x10; `changes` finishes first).
- `tests/shard_weights.json` was re-measured (`pytest -m integration --durations=0`, 45 tests of 5 s or more): about 135 s of serial test time per integration shard (~35 s at 4 workers). The dry run moved to the lightest shard (5). No unit test exceeds 5 s, so unit shards balance by hash.
- `scripts/ci_local.sh` caps itself at `CI_LOCAL_MAX_JOBS` (default 12) concurrent lanes. tests/test_ci_workflow.py guards the job cap, smoke not gating shards, and the dry-run shard.
