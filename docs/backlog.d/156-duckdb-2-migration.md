---
number: 156
title: Migrate the Parquet store to duckdb 2
stage: 'data (Dependabot #312 closed 2026-11-10; dependabot ignores duckdb majors until this lands)'
size: S
status: 'done (triage 2026-11-13): PR #373 (R125)'
---
- **156. Migrate the Parquet store to duckdb 2.** Value: duckdb 2.0 reads Parquet `ts_utc` written as pandas datetime64[ns, UTC] as TIMESTAMPTZ_NS, and `Store.read`'s `ts_utc >= ?` / `ts_utc < ?` filters then raise a BinderException (42 of 49 failures in a run of the 114 duckdb-touching test files on 2.0.0; all pass on 1.5.6). `.df()` also returns ts_utc naive (UTC wall time) where 1.x returned tz-aware, so `Store.sql()` callers would get naive timestamps. Acceptance, tests first: `CAST(ts_utc AS TIMESTAMPTZ)` (works on 1.5.6 and 2.0) in goldbot/data/store.py with partition and row-group pruning shown to still hold (EXPLAIN or read counts); every `Store.sql()` caller gets tz-aware UTC on both versions; the duckdb-touching suites green on 1.5.6 and 2.0; then one PR moves the bound to `<3`, updates uv.lock and removes the duckdb ignore in .github/dependabot.yml. The research and data workflows install with `pip install -e .` (unlocked), so the bound must not move before the code does.
