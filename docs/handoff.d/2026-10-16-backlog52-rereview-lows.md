- **BACKLOG 52 security re-review, both LOW findings fixed (implementer).** `automode.audit_to` now opens the
  audit store with `auth.owner_email` from settings, as `owner_totp_ok` and the dashboard do. Before this, the
  Telegram service logged a false "auth.owner_email is not set" warning on its first audit write once dashboard
  users existed. A value that really is unset or mismatched still warns. The docstring now says the audit lives in
  aux.db. `docs/RUNBOOK.md` has a new rollback step for after any rollback below `04f6266`, once you have rolled
  forward: `grep` approvals/done for `decided_by`/`decided_via`, then run a scrub block that removes only those two
  fields and keeps the channel, the outcome, `decided_ts` and the file's modification time. A test runs the block
  exactly as printed. The step says not to delete done files: the auto-mode evidence, the digest and the
  performance report count them. Telegram decisions have an audit row only from `3817db7`.
