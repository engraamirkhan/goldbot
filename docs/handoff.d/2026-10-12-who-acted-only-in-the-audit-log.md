- Who decided or halted is kept only in the audit log (security follow-up to BACKLOG 52, TRACEABILITY A15;
  2026-10-12).
  - The decision file (`approvals/decisions/<id>.json`) and the halt and re-arm in `control.json` now record only
    the channel ("dashboard" or "telegram"). Both writers pass the channel, and the bus drops anything after ":"
    as well. This closes the "still on disk" note in the A14 bullet.
  - `/api/status` `halted_by`, the entry status and the halt health check report the channel to every role. An
    older control.json's "telegram:<id>" or "dashboard:<email>" is cut to the channel. The dashboard says "Entries
    halted via the dashboard" or "via Telegram". The API schema is unchanged, so the contract was not regenerated.
  - The Telegram service now writes a `decision` row (Telegram id, proposal, approve/reject, reason, time) and a
    `halt` row to the VPS audit log (state/aux.db, the table the dashboard and /mode write). The row is written
    after the action, so a failed audit write is logged and never undoes or hides the click. No API route serves
    the audit log.
  - Each engine start deletes decision files that have no pending file or are more than a day old.
  - Not changed: `mode_by` in control.json still names who switched mode (the owner sees it, other roles get the
    channel), and `state/deploy/approved.json` still records the Telegram id of a deploy tap.
  - Needs security and trading-safety review.
