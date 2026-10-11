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
  this release cannot honour, so an unreadable file means halted, propose-and-approve); the bus `BusDecision` (an
  owner decision is never applied without a field this release does not know, such as a newer condition or size
  limit: it is skipped and the proposal expires; amendment after code review, 2026-10-11); `Settings` (already
  checked by the deploy script); order requests crossing the bridge (`_decode`: an order request is never guessed;
  responses are tolerant, see the bridge follow-up below).
  `RecordRepo` is strict by default; persisted operational state passes `tolerant=True`.
- **Restrictions fail closed on older releases.** Ignoring an unknown field is safe only when the field cannot
  restrict anything. A new restriction is therefore encoded so that an older release refuses the record, for example
  a new value of an existing `Literal` (an older release rejects the value), never a new boolean or optional field
  that an older release would drop and so ignore (S12).
- **No left-to-right or discriminated unions in tolerant models.** With unknown keys ignored, a `union_mode=
  "left_to_right"` union can read a newer variant as the first older member that validates, which silently gives a
  different record. A discriminated union cannot ignore a new tag. Smart-mode unions (the default) pick the member
  that matches the most fields and are fine. The guard checks the core schema of every model read tolerantly.
- **Guard.** `tests/test_persistence_compat.py` writes every persisted model with a field this release does not know
  and reads it through its real load path. An AST check fails when a `model_validate`/`model_validate_json` in
  `goldbot/` neither goes through `load_record` nor is on the allowlist (keyed by file, enclosing function and
  receiver) with its reason. It also fails when a strict model is read tolerantly (`load_record*`, called by any
  qualified name, or `RecordRepo(..., tolerant=<anything but False>)`), and when a tolerant read does not name its
  model class at the call (`cls`, `type(x)`); the one generic case is `RecordRepo._load` (`self.model`).
- **Nothing skipped silently (2026-10-11, review LOWs):** a decision refused for unknown fields is logged once per
  proposal id and counted in `schema_skew()` (`note_schema_skew(..., refused=True)`). A top-level table in the
  engine's orders file outside `sent`/`open`/`closing`/`records_lost` is logged and counted at start; it is not read,
  so a new table carrying client ids or broker orders needs a reviewed rollback story (S12).
- **Engine (follow-up, 2026-10-11):** `goldbot/engine/runner.py` `_load_orders` (SentOrder, OpenTrade,
  PendingClose) and `_archive_stale_proposals` now read with `load_record*`; the temporary guard exception is gone.
- **Bridge (follow-up, 2026-10-11):** the MT5 box is updated by hand and can run a newer release than the brain, so
  `goldbot/execution/bridge.py` `_decode(..., response=True)` (the brain reading results: Position, Tick,
  AccountInfo, SymbolInfo, BrokerTerms, TradingSession, OrderResult) uses `load_record` (source `bridge-response`).
  The bridge server decodes the brain's arguments with `response=False`, and request records (`_REQUEST_RECORDS`:
  OrderIntent) are strict in either direction, so an order request with an unknown field is refused. The protocol
  digest is unchanged (it covers method signatures and record field names, not how they are decoded), so
  `API_VERSION` stays 2. The guard lists `_decode`'s `cls` as a generic tolerant read and checks the response
  records separately (not strict, no left-to-right or discriminated unions).

## Consequences
- A rollback no longer drops closed trades, hides proposals or stops services because of a newer release's fields.
  `schema_skew()` and the log line show that skew is happening (expected only after a rollback). The engine
  publishes a bounded summary (`schema_skew` in engine_<account>.json) and health `schema_skew:<account>` warns the
  owner, failing when a decision was refused (2026-10-11 follow-up). The supervisor and the scheduler (aux.db
  `scheduler_jobs` -> `JobState`) publish the same block in `heartbeat_<service>.json` (written by `Heartbeat`, so
  the scheduler's beat thread keeps it fresh during a long job) and health reports `schema_skew:supervisor` and
  `schema_skew:scheduler` by the same rules (SRE review follow-up). Skew read by the Telegram, news and API
  processes is in their heartbeats too but not checked yet.
- **The refused-click FAIL is bounded in time (SRE review, 2026-10-11).** Options: FAIL until the engine restarts
  (the counts are per process), or FAIL for a window after the latest refusal. Chosen: `SkewSummary.last_refused_utc`
  (wall clock at `note_schema_skew(refused=True)`); health FAILs while now - last_refused_utc <= 1 h
  (`SKEW_REFUSED_FAIL_S`), then WARNs naming the refusal and its time until a restart; a new refusal FAILs and alerts
  again; a block without the field (an older engine) reads as FAIL. Why: a FAIL that cannot clear without a restart
  makes `health` exit 1 for days, hides new fails behind a deploy baseline, and teaches the owner to ignore it; one
  dropped click is an incident of about one approval window, after which the lasting fact (skew still present) is a
  warning. What would change it: refusals that matter beyond the click itself (e.g. a strict reader on an exit path).
  The engine writes its state only on ticks and bars, so after a weekend restart the old block stays until the open.
- A field added by the newer release is lost if the older release rewrites that record. S12 makes this safe by
  construction: the field's default applies when the newer release reads the record again.
- A rollback across a field added to `SentOrder`, `OpenTrade` or `PendingClose` no longer stops the older engine
  from starting (and so from managing exits). A wrong type in the orders file still fails the start loudly.
- The pydantic floor is now 2.12 (the first release with per-call `extra=`). `uv.lock` is not what the servers
  install from. The brain runs `uv pip install -e '.[live]'` (`goldbot-deploy.sh`, `brain_bootstrap.sh`), which
  resolves `pyproject.toml`, so the next forward deploy upgrades pydantic there; the Windows VPS (`vps_update.ps1`,
  `pip install -e ".[live]"`) does the same. The MT5 box keeps the pydantic its bootstrap installed, because deploys
  there use `pip install --no-deps`. `mt5_bootstrap.sh` pins `pydantic>=2.12` for new boxes, and an existing box is
  upgraded once by hand (RUNBOOK 0.5). Only the brain's side of the bridge calls `load_record`; the bridge server
  reads strictly, so an older pydantic on the MT5 box does not break it.
- What would change this decision: if a field ever needs to survive an older release's rewrite (for example
  audit data), preserve extras for that one model explicitly instead of relaxing the rule globally.
