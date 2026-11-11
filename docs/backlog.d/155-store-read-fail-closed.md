---
number: 155
rank: 40
title: Engine start fails closed when the history read fails
stage: 'engine + data (found by the duckdb 2.0 test of Dependabot #312, 2026-11-10)'
size: M
status: 'implemented, in review (2026-11-10, branch fix/history-read-fails-closed, R123) · accountable: implementer · reviews: trading-safety-reviewer, data-engineer'
---
- **155. Engine start fails closed when the history read fails.** Value: with duckdb 2.0 every `Store.read` with a ts filter raised (TIMESTAMPTZ_NS vs TIMESTAMP WITH TIME ZONE), yet the tests did not fail on the exception: the engine warm-started on `history_days 52.4` instead of > 700 (tests/test_start_safety.py), reconcile saw an empty span list and the key-zone, broker-bar, clock and capacity paths went on with short or empty history. A store read error is swallowed somewhere between `Store.read` and the engine's warm start, so any read failure (a library change, a corrupt part, a full disk) lets the engine trade on too little history instead of refusing entries. Acceptance, tests first: a fake store whose read raises makes the engine start refuse entries with a journalled reason (exits unaffected) and the health check fail, for warm start, key zones, broker bars, reconcile coverage and capacity; a short read (fewer days than the warm-start minimum) is refused the same way; no `except Exception` on a history read returns an empty frame silently; list every swallow site found with file:line.
