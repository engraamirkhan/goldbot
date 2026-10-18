# CI performance and redesign, 2026-10-18

Owner: sre. Decision record: ADR 0035 (docs/decisions/0035-parallel-ci-matrix.md). Workflow: `.github/workflows/ci.yml`;
local mirror: `scripts/ci_local.sh`; guards: `tests/test_ci_workflow.py`, `tests/test_ci_sharding.py`.

## 1. Measured (before)

Source: `gh run list --workflow ci --limit 30` and `gh run view <id> --json jobs` (runs 38944523870 to
39118330741, 2026-10-15 to 2026-10-18). Wall = run `createdAt` to `updatedAt`. All jobs started within 0.1 min of
the run (no queueing).

| | n | median | p90 | min | max |
|---|---:|---:|---:|---:|---:|
| run wall-clock (success + failure) | 28 | **16.7 min** | 19.8 min | 10.1 | 23.1 |

Per job, the 6 most recent runs (the current 9-job shape since 2026-10-17):

| job | median | max | longest step |
|---|---:|---:|---|
| backend-integration | **12.3 min** | 13.7 | `pytest -m integration -n auto` + dry run: 756 s; install 34 s |
| backend-unit (1) | 2.6 | 2.8 | tests 117 s; pip install 31 s |
| backend-unit (3) | 2.5 | 2.6 | tests 114 s |
| backend-unit (2) | 2.1 | 3.2 | tests 88 s |
| frontend | 2.0 | 9.0 | install 33 s, e2e 38 s, vitest 22 s |
| backend-unit (0) | 1.7 | 2.2 | tests 93 s |
| backend-lint | 1.7 | 1.9 | mypy 39 s, install 34 s |
| backend (aggregate, older 3-job shape: unit + integration in one job) | 13.5 | 23.1 | |

**Critical path: backend-integration, about 13 min of a 14-16 min run.** The unit shards finish in under 3 min,
so more unit shards would not shorten a run. The integration suite is 96 tests; 27 of them take 5 s or more and
the 5 engine replays take 49-79 s each (local, `--durations`). A plain hash split of those into 4 shards gave
323 / 316 / 139 / 143 s, a 2.3x spread.

Other findings:

- `concurrency: ci-${{ github.ref }}` with `cancel-in-progress: true` also cancelled **main** runs when two merges
  landed close together, leaving a main commit without the `backend` / `frontend` check runs goldbot-deploy needs.
- `paths-ignore: [**/*.md, docs/**]` meant a docs-only PR **never reported the required checks** (merge blocked on
  "expected"), and a docs edit could break unit tests that read docs (RUNBOOK.md, ADRs, first-deploy.md,
  docs/research/*.md; the research director and the API read docs/research at run time) with nothing running.
- Playwright `retries: 1` in CI let a flaky e2e test pass silently.
- `pip install` took 31-34 s in every job; mypy re-checked from cold every run.

## 2. Design (after)

```
changes ─┬─ backend-lint (mypy, api-contract) ───────────────────────┐
         ├─ frontend-checks (eslint, tsc, vitest) ────────────────────┤
smoke ───┼─ backend-unit x4 ──────────────────────────────────────────┼─ backend ─┐
(~1 min) ├─ backend-integration x4 (weighted; dry run in shard 0) ───┤           ├─ deployable (main)
         └─ e2e x2 (chromium, auth) ───────────────────────────────── frontend ──┘
report-failure: any failure -> one `ci` issue (unchanged), now with FLAKY/FAIL verdicts
```

| lever | change |
|---|---|
| shard the critical path | integration 1 -> 4 shards, each `-n auto`; heavy tests spread first by `tests/shard_weights.json` (LPT): local shard sums 208 / 205 / 199 / 201 s |
| unit | stays 4 shards: under 3 min already, and the account has 20 concurrent jobs (two overlapping runs at peak 13 jobs each already queue) |
| smoke first | pre-commit (docs checks), ruff and full test collection in about 1 min (merge_smoke.sh stays a local post-merge tool: its structural tests are unit tests); the test matrices and e2e wait for it, so a broken import costs one job instead of twelve |
| caches | installs are `uv sync --frozen` from uv.lock (smoke runs `uv lock --check`); uv cache keyed on uv.lock (main writes, PRs read), `web/node_modules` keyed on package-lock.json (npm ci only on a miss), mypy cache (content-hash checked), pre-commit cache. Playwright uses the runner's preinstalled Chrome: no browser download, nothing to cache |
| e2e | one job per Playwright project (each has its own server and TOTP clock) |
| concurrency | PRs: a new push cancels the PR's older run; main: one group per commit, never cancelled |
| path filter | PRs only, fail-safe: prose-only (docs/** and *.md, except RUNTIME_DOCS in scripts/ci_changes.py: docs/research/, docs/ROADMAP.md, docs/RUNBOOK.md, found by an audit test of what runtime code, integration tests and e2e read) skip lint, integration and the frontend jobs; smoke and the unit suite still run. The filter runs from the PR's base commit, so a PR cannot change it for itself. Pushes to main always run everything. The required checks always report |
| flaky report | a failed shard re-runs its failed tests once, alone; each is FLAKY (passed) or FAIL (failed again) in the job summary and the CI issue; the job stays failed. Playwright `failOnFlakyTests` in CI |
| summaries | every pytest shard writes counts, wall time and its 10 slowest tests to the job summary (`scripts/ci_summary.py`) |
| CD | `deployable` (main, both aggregates green): commit status `goldbot/deployable` and the owner's one-line `sudo goldbot-deploy <sha>` in the summary. It never deploys and holds no secret |

Expected wall-clock (estimate until the first runs; CI ran the integration suite about 3x slower than a loaded
12-core Mac, `756 s` vs `238-276 s` at `-n 4`): smoke about 1 min, then the slowest integration shard about 3.5-4.5
min (its floor is the longest single test, `test_engine_propose_mode_waits_for_approval`, about 80 s locally,
about 4 min on a runner), so **about 6 min per run instead of a 16.7 min median**. A prose-only PR: about 4 min.

Not done, with reasons:

- No more unit shards: not on the critical path; concurrency cap (above).
- No nightly scheduled run: a scheduled failure would mark the main head's `backend` check failed, which
  goldbot-deploy reads (filter=latest), and so block an owner deploy on dependency drift alone. Revisit now that CI installs from uv.lock.
- The engine replay tests set the integration floor: profiling `_run` in tests/test_engine.py (a 5-day replay at 1
  tick per minute) is the next speed-up after this one (implementer).

## 3. Operating it

| symptom | detection | recovery |
|---|---|---|
| one integration shard much slower than the others | shard summaries (wall time, slowest tests) | refresh weights from a run's `junit-backend-integration-*` artifacts or a local `pytest -m integration --junitxml=...`: `python scripts/ci_summary.py weights --min-seconds 5 --out tests/shard_weights.json <junit files>`; commit |
| FLAKY in a job summary or CI issue | `ci` issue lists `FLAKY <test>` | fix the race, or quarantine in its own reviewed change; never add retries |
| `backend` / `frontend` missing on a main commit | goldbot-deploy refuses ("must have passed CI") | re-run the workflow on main (`workflow_dispatch`); main runs are never cancelled now |
| `goldbot/deployable` status missing | commit page; deploy note | it is set only after both aggregates pass on main; read the run's `backend` / `frontend` results |
| CI red, local green | `scripts/ci_local.sh` (same matrix, read from ci.yml) | run it before pushing; it reproduces the env, shards and e2e projects |
| matrix size changed | `tests/test_ci_workflow.py` | a shard denominator that differs from the matrix length fails the unit suite |
