- Who decided, halted, switched mode or approved a deploy is kept only in the audit log (security follow-up to
  BACKLOG 52, TRACEABILITY A15; 2026-10-12).
  - The decision file (`approvals/decisions/<id>.json`), and the halt, re-arm and mode in `control.json`, now record
    only the channel ("dashboard" or "telegram"). Both writers pass the channel, and the bus also drops anything
    after ":". An engine's kill switch still records "engine:<account>". This closes the "still on disk" note in
    the A14 bullet.
  - `/api/status` `halted_by`, the entry status, the halt health check and the auto-mode card's `mode_by` report
    the channel to every role, the owner included. An older control.json's "telegram:<id>" or "dashboard:<email>"
    is cut to the channel. The dashboard says "via the dashboard" or "via Telegram". The API schema is unchanged.
    The health golden was regenerated on purpose ("HALTED by owner" -> "HALTED via owner").
  - `state/deploy/approved.json` now holds only `{"sha": ...}`. The root deploy script reads only the sha, and a
    test checks that it still does.
  - The Telegram service writes `decision`, `halt` and `deploy_approve` rows, each with the Telegram id, to the VPS
    audit log (state/aux.db, the table the dashboard and /mode write). Review M1: each row is written on a worker
    thread after the owner has been answered, through one audit store opened off the event loop. A locked aux.db
    therefore never delays the answer or the proposal pump, and a failed write is logged without undoing anything.
    No API route serves the audit log.
  - Each engine start deletes decision files that have no pending file or are more than a day old.
  - Follow-up: BACKLOG 55 (`test_lockout_survives_a_restart` flaked once under load).
  - Needs security and trading-safety review.
