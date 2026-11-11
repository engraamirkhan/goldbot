---
number: 158
rank: 63
title: Quarantine spans read fails closed
stage: 'data (found while doing backlog 155, 2026-11-10)'
size: S
status: 'proposed · accountable: data-engineer · reviews: quant-reviewer, code-reviewer'
---
- **158. Quarantine spans read fails closed.** Value: `goldbot/data/quarantine.py` `_recorded` (line 99) catches every exception from its `dq_events` read and returns no spans. A store read that fails (duckdb 2.0, a corrupt part) silently drops every quarantine span. The D12 reconcile then counts quarantined minutes as divergences, `record_span` writes duplicate spans, and research evidence exclusions (`research/evidence_exclusions.py`) keep spiky minutes they should leave out. Acceptance, tests first: a read that raises propagates (or is reported as an error by every caller); a missing table or a schema without the span columns still yields no spans; each caller (crossfeed reconcile, jobs, evidence exclusions, telegram labs) has a test with a raising store.
