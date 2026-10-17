---
number: 71
rank: 3
title: Approved orders the broker or the MT5 box refuses read NOT PLACED, with a notice
stage: Execution and approvals (entry outcome truth) · PROTECT · RUN
size: M
status: 'returned 2026-10-17 by the product owner (built 0b61b2a+fe1a93f, review round d067a19; in PR #85). Met: tests/test_not_placed.py and the runner refusal tests in tests/test_engine_safety.py pass, API regenerated, fills and ambiguous results still read APPROVED. Gaps: (1) no trading-safety, code or ui-ux verdict on d067a19 is on record (E1); (2) the TRACEABILITY A row was not added; (3) runner.py is 3504 lines, over the PR 0 ratchet of 3492, so tests/test_engine_structure.py::test_runner_py_stays_under_its_line_cap fails at bdd89f7 and at the PR #85 head 22a9055 (the CTO rules: re-baseline once with a reason, or trim) · accountable: implementer · due 2026-10-18'
---
- **71. Approved orders the broker or the MT5 box refuses read NOT PLACED, with a notice (trading-safety LOW-R1
  follow-up to 66).** Decided 2026-10-17 by program director + product owner (owner delegated). Value (RUN, protect):
  today only the bridge's ceiling refusal turns an approved card into NOT PLACED; every other definite reject
  (`DEFINITE_REJECT_RETCODES`: 10019 no money, 10016 invalid stops, 10018 market closed, the MT5 box's
  `bridge_stale_order`) archives as ✅ APPROVED with a green "order sent" note, so the owner believes a trade is open
  when none is. It finds no edge; it stops a false safety state on the path the demo runs from day one (propose mode).
  This item had no backlog row although its copy was final; it is the prerequisite of 72 and 73 (same table, same
  card and API field). Depends on: nothing new (66's ceiling refusal is on `main`); file order behind 62 and 43
  (runner.py). Files: new `goldbot/execution/not_placed.py` (the one wording table), `goldbot/engine/runner.py`
  `_on_decision` and the notice, `goldbot/telegram/bot.py` `outcome_text`, `goldbot/api/schema.py`
  (`DecidedProposal.not_placed`), `goldbot/api/app.py`, the generated OpenAPI and `web/src/api`,
  `web/src/screens/ProposalCard.tsx`, `docs/ux/approval-card.md` (the state row). TRACEABILITY: new A row (the
  implementer adds it; card outcome after approval), X6 (refusals logged and alerted).
  Acceptance (the tests listed in `docs/ux/order-not-placed.md` "Tests the implementer writes first", written first and
  failing on `main`):
  - `tests/test_not_placed.py`: every retcode in `DEFINITE_REJECT_RETCODES` has a row or reaches the fallback; the
    fallback names the code; no string holds a proposal id, `bridge_`, `broker_refused`, the word "retcode" or the
    raw broker message.
  - Runner: an approved order refused 10019 archives `gate_refusal == ["broker_refused:10019"]` and queues exactly the
    doc's notice once; `bridge_stale_order` archives `["bridge_stale_order"]`, never `broker_refused:10006`; the
    ceiling refusal is byte-for-byte unchanged; a fill and every ambiguous result (10008, 10010, 10011, 10012, 10031,
    -1, an undefined code) still archive APPROVED (72 changes that, not this item).
  - `outcome_text`: the doc's four worked card examples byte for byte. `ProposalCard.test.tsx`: the four dashboard
    examples (anchored regexes); an older API without `not_placed` given `broker_refused:N` shows "Refused by the
    broker (code N); nothing was sent." and never "The RiskGate blocked the order". `STATE_NAME.refused` reads
    "not placed".
  - API contract regenerated (`export_openapi.py`, `gen:api`) and committed; e2e unchanged or updated.
  - Reviews on record at the merge: trading-safety-reviewer (the archive path and that the notice never changes the
    outcome), code-reviewer, ui-ux-designer (the build matches the doc's words; no re-review of the copy itself).
