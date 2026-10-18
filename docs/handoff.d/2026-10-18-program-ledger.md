- Program ledger, lane A (backend): new database kind `program` (`state/program.db`, synchronous FULL, one forward-only
  migration, staged and restored by the existing backup because it stages every `*.db`). Append-only tables (knowledge,
  owner notes, decisions, research runs/verdicts mirror, work log, resume snapshots, artifacts): triggers refuse UPDATE and
  DELETE except setting `superseded_by_id` once; `owner_notes` is private by constraint. `goldbot/ledger` scans every text
  for secrets and account identifiers before storing (fail closed). CLI `python -m goldbot.ops.run ledger
  add|query|resume|export`; export writes public rows only to `docs/ledger/*.md` (generated). Not done here: registry import
  command, API/UI, a pre-commit check of docs/ledger.
