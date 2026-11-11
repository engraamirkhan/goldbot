- 2026-11-11 (branch feat/duckdb-2-ready; backlog 156, R125): the Parquet store runs on duckdb 2. On 2.0 every
  ts-filtered `Store.read` raised a BinderException (a pandas datetime64[ns, UTC] column reads as TIMESTAMPTZ_NS),
  which the engine's history and the cross-feed reconcile turned into empty reads (49 test failures, all from that one
  filter; backlog 155 already made the engine fail closed on it). The bounds are now ISO strings duckdb casts to the
  column's own type, so they stay in the Parquet scan (2.0 EXPLAIN ANALYZE: 1-2 of 20/80 row groups scanned) and pick
  the same rows on both majors. `Store.read` and `Store.sql` return every zoned column tz-aware UTC at microsecond
  resolution (2.x returned it naive, 1.x in the host's zone: `Store.sql` on the Mac gave Europe/London), with the
  session zone pinned to UTC. duckdb is bounded `<3`, uv.lock pins 2.x, Dependabot tracks duckdb majors again. Deploy:
  the brain and VPS get 2.x on their next `uv sync --frozen`; the research/data workflows (`pip install -e .`) get the
  newest 2.x. Release files and the local positioning/dq_events store read identically (rows, dtypes) on 1.5.6 and 2.x.
  Found, not changed: `Store.read` orders by ts_utc only, so rows sharing a ts_utc come back in a different order run to
  run on either major (positioning, dq_events); backlog 162 proposes a deterministic tie order.
