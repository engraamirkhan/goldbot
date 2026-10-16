- Who decided, halted, switched mode or approved a deploy is kept only in the audit log (security follow-up to
  BACKLOG 52, TRACEABILITY A15; 2026-10-12).
  - The decision file (`approvals/decisions/<id>.json`), and the halt, re-arm and mode in `control.json`, now record
    only the channel ("dashboard" or "telegram"). Both writers pass the channel, and the bus also drops anything
    after ":". An engine's kill switch still records "engine:<account>". This closes the "still on disk" note in
    the A14 bullet.
  - `/api/status` `halted_by`, the entry status, the halt health check and the auto-mode card's `mode_by` report
    the channel to every role, the owner included. An older control.json's "telegram:<id>" or "dashboard:<email>"
    is cut to the channel, and every rewrite of control.json (halt, re-arm, mode) now stores `by`, `rearm_by` and
    `mode_by` as the channel too instead of copying an old identity forward ("engine:<account>" is kept; `rearm_id`
    is untouched). The dashboard says "via the dashboard" or "via Telegram". The API schema is unchanged.
    The health golden was regenerated on purpose ("HALTED by owner" -> "HALTED via owner").
  - `state/deploy/approved.json` now holds only `{"sha": ...}`. The root deploy script reads only the sha, and a
    test checks that it still does.
  - The Telegram service writes `decision`, `halt` and `deploy_approve` rows, each with the Telegram id, to the VPS
    audit log (state/aux.db, the table the dashboard and /mode write). Review M1: each row is written on a worker
    thread through one audit store opened off the event loop, so a locked aux.db never delays the answer or the
    proposal pump. Second review: the row is scheduled as soon as the decision, halt or deploy approval stands,
    before the owner is answered, so an answer that times out never loses it; its `ts` is the moment of the action; a
    locked aux.db is retried once, and a write that still fails is logged (event, channel, proposal or sha, never
    who) without undoing anything; the service drains pending rows at shutdown, for at most 10 s.
    No API route serves the audit log.
  - Each engine start deletes decision files that have no pending file or are more than a day old.
  - Follow-up: BACKLOG 55 (`test_lockout_survives_a_restart` flaked once under load).
  - Needs security and trading-safety review.
