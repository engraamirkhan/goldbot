#!/usr/bin/env bash
# Brain. The ONLY command the Mac's ledger-sync key may run (authorized_keys command=, restrict): reads JSONL ledger rows
# on stdin, appends the valid ones to state/program.db and prints `ack N`. Ignores SSH_ORIGINAL_COMMAND, so the client
# cannot choose another command; bad rows are rejected by the ingest (secret scan, shape), never stored.
set -euo pipefail
cd "${GOLDBOT_HOME:-/opt/goldbot}"
exec "${GOLDBOT_PY:-/opt/goldbot/.venv/bin/python}" -m goldbot.ops.run ledger --state "${GOLDBOT_STATE:-/opt/goldbot/state}" ingest
