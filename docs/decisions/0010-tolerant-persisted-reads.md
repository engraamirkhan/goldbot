# 0010: Persisted state is read tolerantly, built strictly

Status: accepted (architect plan, 2026-10-11)

## Context
Every record is a pydantic `Record`/`FrozenRecord` with `extra="forbid"` (`goldbot/base.py`), so a misspelt field is
an error, not a silent default. The same rule also applied when state was read back from disk or aux.db. The deploy
tooling supports rollback (`goldbot-deploy --rollback`, docs/RUNBOOK.md 0.5). After a release that adds a field to a
persisted record (a proposal, a closed trade, the shadow book, a job state), the older release reads files that
release wrote, and `forbid` rejected them. Depending on the reader, the result was:
- the line was skipped: a closed trade no longer counts toward the phase gates (`load_closed_trades`);
- the item disappeared: a live proposal was missing from the dashboard and Telegram, and the owner could not decide
  it (`ApprovalBus.pending`);
- a constructor raised, so a service did not start at all: the shadow book, the model registry, the population, the
  scheduler.

So a rollback, which exists to make things safer, could stop the trading loop or quietly lose evidence. aux.db
already handles schema rollback (`compat_back_to`) and `settings.local.yaml` keys are checked before a rollback, but
nothing covered the record bodies.

## Decision
- **Read tolerantly, build strictly.** Code that builds a record still uses `extra="forbid"`. Persisted state is
  read with `goldbot.base.load_record(cls, data, *, source)` / `load_record_json(cls, text, *, source)`. These
  validate with the per-call `extra="allow"` (pydantic >= 2.12), find unknown fields at every nesting level
  (`model_extra` through nested models, lists and mappings), log a WARNING once per (source, model, fields), count
  them in `schema_skew()`, and validate again with `extra="ignore"`. **Only unknown keys are tolerated:** a wrong
  type, a missing required field or malformed JSON still fails exactly as before.
- **No preservation on rewrite.** An unknown field is dropped. If the older release writes the record again, the
  field is lost. Preserving it would need extras to survive `model_copy`, assignment and every writer, which is a
  larger and riskier change. Instead there is a new rule, docs/AGENT_STANDARDS.md S12: every new persisted field has a
  default that is safe when missing, so a record that went through an older release reads correctly on the newer one.
- **Strict allowlist (stays forbid and fails closed):** auth `User`/`Invite`/`PasswordReset` (a newer lock or MFA
  flag must never be ignored, so the login is refused); the bus `Control` (an unknown field could be a halt condition
  this release cannot honour, so an unreadable file means halted, propose-and-approve); `Settings` (already checked by
  the deploy script); bridge order requests (an order is never guessed). `RecordRepo` is strict by default; persisted
  operational state passes `tolerant=True`.
- **Guard.** `tests/test_persistence_compat.py` writes every persisted model with a field this release does not know
  and reads it through its real load path. An AST check fails when a `model_validate`/`model_validate_json` in
  `goldbot/` neither goes through `load_record` nor is on the allowlist with its reason.
- **Temporary exception:** `goldbot/engine/runner.py` (the approvals scan in `_archive_stale_proposals` and
  `_load_orders`) is not converted yet because another lane is editing it. It is listed in the guard and in HANDOFF
  as the follow-up.

## Consequences
- A rollback no longer drops closed trades, hides proposals or stops services because of a newer release's fields.
  `schema_skew()` and the log line show that skew is happening (expected only after a rollback).
- A field added by the newer release is lost if the older release rewrites that record. S12 makes this safe by
  construction: the field's default applies when the newer release reads the record again.
- Until the runner follow-up lands, a rollback across a change to `SentOrder`, `OpenTrade` or `PendingClose` makes
  the older engine refuse to start. That fails closed for entries, but exits are not managed while the engine is
  down (only the broker-side stop and target act). The runner follow-up should be the next change after the
  current runner lane merges.
- The pydantic floor is now 2.12 (the first release with per-call `extra=`). The VPS installs from `uv.lock`
  (2.14), so nothing changes there.
- What would change this decision: if a field ever needs to survive an older release's rewrite (for example
  audit data), preserve extras for that one model explicitly instead of relaxing the rule globally.
