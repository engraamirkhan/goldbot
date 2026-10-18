- CI redesign (ADR 0035, docs/process/ci-performance-2026-10-18.md): a smoke job gates 4 unit + 4 weighted integration
  shards (tests/shard_weights.json), e2e runs per Playwright project, uv/node_modules/mypy caches, failed tests re-run
  once and labelled FLAKY/FAIL (the build stays red), PR-only prose filter, main runs never cancelled, and a
  `goldbot/deployable` status on green main (CI never deploys). Expected run about 6 min (was a 16.7 min median).
  `scripts/ci_local.sh` reads the matrix from ci.yml; `tests/test_ci_workflow.py` guards the invariants.
